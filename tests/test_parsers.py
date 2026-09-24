"""Test parser trên HTML thật đã lưu (chạy offline, không cần mạng).

    python -m pytest tests/ -v
hoặc  python tests/test_parsers.py
"""

import csv
import sys
import tempfile
from pathlib import Path

from runner import run  # noqa: E402  (dat sys.path)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date  # noqa: E402

from perfume_intel.core.text import (  # noqa: E402
    normalize_gender, output_stem, url_key)
from perfume_intel import config  # noqa: E402
from perfume_intel.core import storage  # noqa: E402
from perfume_intel.core.http import (  # noqa: E402
    RATE_LIMIT_BACKOFF, RETRY_STATUSES, Fetcher, RateLimited,
    _parse_retry_after)
from perfume_intel.core.csv_input import (  # noqa: E402
    iter_csv_files, read_url_pairs, read_urls)

from perfume_intel.sources.fragrantica.models import CSV_COLUMNS  # noqa: E402
from perfume_intel.sources.fragrantica.parsers import (  # noqa: E402
    extract_perfume_links, parse_perfume, LONGEVITY_LABELS, SILLAGE_LABELS,
    _parse_accords, _parse_family, _parse_performance, _parse_vote_count,
    _parse_year, _performance_bars, _performance_card, _soup)

FIXTURE = Path(__file__).parent / "fixtures" / "sauvage.html"
URL = "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html"

BURBERRY = Path(__file__).parent / "fixtures" / "burberry_london.html"
BURBERRY_URL = "https://www.fragrantica.com/perfume/Burberry/London-for-Men-804.html"

# Bản HTML sau khi browser chạy JS — mới có widget "when to wear".
RENDERED = Path(__file__).parent / "fixtures" / "burberry_london_rendered.html"


def load():
    return parse_perfume(FIXTURE.read_text(encoding="utf-8"), URL)


def load_burberry():
    return parse_perfume(BURBERRY.read_text(encoding="utf-8"), BURBERRY_URL)


def load_rendered():
    return parse_perfume(RENDERED.read_text(encoding="utf-8"), BURBERRY_URL)


def test_identity():
    p = load()
    assert p.perfume_id == "31861"
    assert p.brand == "Dior"
    assert "Sauvage" in p.name
    assert p.gender == "Nam"          # 'men' đã được ánh xạ
    assert p.year == 2015


def test_gender_mapping():
    """men -> Nam, women -> Nữ, women and men -> Unisex."""
    assert normalize_gender("men") == "Nam"
    assert normalize_gender("women") == "Nữ"
    assert normalize_gender("women and men") == "Unisex"
    assert normalize_gender("men and women") == "Unisex"
    assert normalize_gender("Unisex") == "Unisex"
    # namperfume vốn đã ghi tiếng Việt -> giữ nguyên
    assert normalize_gender("Nữ") == "Nữ"
    assert normalize_gender("Nam") == "Nam"
    assert normalize_gender(None) is None
    assert normalize_gender("  MEN  ") == "Nam"
    # giá trị lạ giữ nguyên văn để còn phát hiện mà bổ sung
    assert normalize_gender("children") == "children"


def test_gender_from_fixtures():
    assert load_burberry().gender == "Nam"      # 'for men'
    assert parse_perfume(
        "<html><body><h1>X Y for women</h1></body></html>", URL).gender == "Nữ"
    assert parse_perfume(
        "<html><body><h1>X Y for women and men</h1></body></html>",
        URL).gender == "Unisex"


def _year_from(title: str, description: str = "") -> int | None:
    """Dựng trang tối giản đúng dạng thật của Fragrantica để test năm."""
    html = (f"<html><head><title>{title}</title></head>"
            f"<body><span itemprop='description'>{description}</span></body></html>")
    return _parse_year(_soup(html), description or None)


def test_year_name_contains_year():
    """Tên chứa số 4 chữ số không được nhận nhầm thành năm phát hành."""
    # Ra mắt 2001, không phải 1969.
    assert _year_from(
        "1969 Parfum de Revolte Histoires de Parfums perfume - a fragrance for women 2001",
        "1969 Parfum de Revolte by Histoires de Parfums is a fragrance for women. "
        "1969 Parfum de Revolte was launched in 2001.") == 2001

    # Ra mắt 2003, số 1947 là tên tri ân năm thành lập Dior.
    assert _year_from(
        "Chris 1947 Dior perfume - a fragrance for women 2003",
        "Chris 1947 by Dior is a Floral Green fragrance for women. "
        "Chris 1947 was launched in 2003.") == 2003


def test_year_without_description():
    """Không có description thì lấy năm CUỐI trong title, không phải năm đầu."""
    assert _year_from(
        "1969 Parfum de Revolte Histoires de Parfums perfume - "
        "a fragrance for women 2001") == 2001
    assert _year_from("Sauvage Dior cologne - a fragrance for men 2015") == 2015


def test_year_edge_cases():
    assert _year_from("1725 Histoires de Parfums cologne - for men 2001") == 2001
    assert _year_from("Nuoc hoa khong co nam") is None
    # Câu 'launched in' được ưu tiên hơn số lạ trong title.
    assert _year_from("2020 Vision Brand - for women 1998",
                      "2020 Vision was launched in 1998.") == 1998


def test_family_from_fixtures():
    assert load().fragrance_family == "Aromatic Fougere"
    assert load_burberry().fragrance_family == "Oriental Spicy"
    assert load_rendered().fragrance_family == "Oriental Spicy"


def test_family_longest_match_wins():
    """'Floral Fruity Gourmand' không được cắt còn 'Floral' hay 'Floral Fruity'."""
    assert _parse_family(
        "X by Y is a Floral Fruity Gourmand fragrance for women.") \
        == "Floral Fruity Gourmand"
    assert _parse_family("X by Y is a Floral Fruity fragrance for women.") \
        == "Floral Fruity"
    assert _parse_family("X by Y is a Floral fragrance for women.") == "Floral"
    assert _parse_family("X by Y is a Woody Floral Musk fragrance for men.") \
        == "Woody Floral Musk"


def test_family_article_and_case():
    """Chấp nhận cả 'is an', và chuẩn hoá lại cách viết hoa."""
    assert _parse_family("X by Y is an Oriental Woody fragrance for men.") \
        == "Oriental Woody"
    assert _parse_family("X by Y is a AROMATIC FOUGERE fragrance for men.") \
        == "Aromatic Fougere"


def test_family_absent_or_unknown():
    """Không khớp danh sách thì để trống, không đoán."""
    # Fragrantica không xếp nhóm cho chai này.
    assert _parse_family(
        "Atlas by Lattafa Perfumes is a fragrance for women and men.") is None
    assert _parse_family("X by Y is a new fragrance for women.") is None
    assert _parse_family(None) is None
    # Nhóm ngoài danh sách -> bỏ trống.
    assert _parse_family("X by Y is a Amber Spicy fragrance for men.") is None
    assert _parse_family("X by Y is a Limited Edition fragrance for men.") is None


def test_family_blank_in_output():
    """Bỏ trống phải ra ô rỗng trong CSV và null trong JSON."""
    p = load()
    p.fragrance_family = None
    assert p.to_flat_dict()["fragrance_family"] is None

    out = Path(tempfile.gettempdir()) / "frag_family_blank.csv"
    storage.save_csv([p], out, columns=CSV_COLUMNS)
    row = next(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert row["fragrance_family"] == ""


def test_family_in_output():
    p = load()
    assert p.to_dict()["fragrance_family"] == "Aromatic Fougere"
    assert p.to_flat_dict()["fragrance_family"] == "Aromatic Fougere"
    assert "fragrance_family" in CSV_COLUMNS


def test_rating():
    p = load()
    assert p.rating and 0 < p.rating <= 5
    assert p.rating_count and p.rating_count > 1000


def test_accords():
    p = load()
    assert len(p.accords) >= 3
    assert p.accords[0].name == "fresh spicy"
    assert p.accords[0].width == 100.0
    # accord phải giảm dần theo độ mạnh
    widths = [a.width for a in p.accords if a.width]
    assert widths == sorted(widths, reverse=True)


def test_notes():
    p = load()
    # Lấy nhãn hiển thị ("Calabrian bergamot"), không phải slug trong href ("Bergamot").
    assert any("bergamot" in n.lower() for n in p.top_notes)
    assert "Ambroxan" in p.base_notes
    assert p.middle_notes


def test_perfumers():
    p = load()
    assert any("Demachy" in n for n in p.perfumers)


def test_accord_metrics():
    """Đủ 10 accord của Burberry London kèm width + opacity + màu hex."""
    p = load_burberry()
    assert [a.name for a in p.accords][:4] == [
        "tobacco", "cinnamon", "sweet", "warm spicy"]
    assert len(p.accords) == 10

    tobacco, cinnamon = p.accords[0], p.accords[1]
    assert tobacco.width == 100.0 and tobacco.opacity == 100.0
    assert tobacco.color == "#ad7727"
    assert cinnamon.width == 87.22
    assert cinnamon.opacity == 89.04           # '89.041514139105%'
    assert all(a.opacity is not None for a in p.accords)


def test_accord_browser_rendered_format():
    """Bản browser render dùng rgb() và opacity 0-1 -> vẫn quy về thang 0-100."""
    html = """<h6>main accords</h6>
    <div class="flex flex-col w-full max-w-[280px] md:max-w-[320px]">
      <div class="w-full"><div style="color: rgb(255,255,255);
        background: rgb(173, 119, 39); opacity: 1; width: 100%;">
        <span class="truncate">tobacco</span></div></div>
      <div class="w-full"><div style="color: rgb(255,255,255);
        background: rgb(210, 105, 30); opacity: 0.890415; width: 87.2151%;">
        <span class="truncate">cinnamon</span></div></div>
    </div>"""
    accords = _parse_accords(_soup(html))
    assert [a.name for a in accords] == ["tobacco", "cinnamon"]
    assert accords[0].opacity == 100.0 and accords[0].color == "#ad7727"
    assert accords[1].opacity == 89.04 and accords[1].width == 87.22
    assert accords[1].color == "#d2691e"


def test_accord_fallback_without_heading():
    """Mất tiêu đề 'main accords' vẫn lấy được nhờ selector khối bar."""
    html = """<div class="flex flex-col w-full max-w-[280px] md:max-w-[320px]">
      <div class="w-full"><div style="background: #ad7727; opacity: 100%;
        width: 100%;"><span class="truncate">tobacco</span></div></div>
    </div>"""
    accords = _parse_accords(_soup(html))
    assert len(accords) == 1 and accords[0].name == "tobacco"


def test_csv_flattening_keeps_metrics():
    p = load_burberry()
    flat = p.to_flat_dict()
    assert flat["accords"].startswith("tobacco; cinnamon")
    assert flat["accords_detail"].startswith("tobacco:100.0|100.0; cinnamon:87.22|89.04")


def test_when_to_wear():
    """Widget mùa + ngày/đêm, chỉ có trong HTML đã render."""
    p = load_rendered()
    names = [w.name for w in p.when_to_wear]
    assert names == ["winter", "spring", "summer", "fall", "day", "night"]

    by_name = {w.name: w for w in p.when_to_wear}
    assert by_name["winter"].percent == 100.0        # cột cao nhất
    assert by_name["winter"].votes_label == "6.3k"
    assert by_name["winter"].votes == 6300
    assert by_name["summer"].votes == 499            # số nguyên, không có 'k'
    assert all(0 < w.percent <= 100 for w in p.when_to_wear)


def test_when_to_wear_absent_without_render():
    """HTML gốc chưa chạy JS thì không có dữ liệu này (đã mã hoá)."""
    assert load_burberry().when_to_wear == []


def test_vote_count_units():
    assert _parse_vote_count("499") == 499
    assert _parse_vote_count("6.3k") == 6300
    assert _parse_vote_count("1.2M") == 1_200_000
    assert _parse_vote_count("1,234") == 1234
    assert _parse_vote_count("") is None
    assert _parse_vote_count("n/a") is None


def test_rendered_pyramid_matches_raw():
    """Bản render phải cho tháp hương y hệt bản requests, không gộp vào general."""
    raw, rendered = load_burberry(), load_rendered()
    assert rendered.top_notes == raw.top_notes
    assert rendered.middle_notes == raw.middle_notes
    assert rendered.base_notes == raw.base_notes
    assert rendered.general_notes == []       # không còn mục 'Notes' rác
    assert rendered.brand == raw.brand and rendered.year == raw.year


def _write_csv(name: str, content: str) -> Path:
    path = Path(tempfile.gettempdir()) / name
    path.write_text(content, encoding="utf-8")
    return path


def test_csv_with_header():
    p = _write_csv("frag_a.csv", (
        "name,url,note\n"
        "London,https://www.fragrantica.com/perfume/Burberry/London-for-Men-804.html,ok\n"
        "Sauvage,https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html,ok\n"))
    urls = read_urls(p)
    assert len(urls) == 2
    assert urls[0].endswith("London-for-Men-804.html")


def test_csv_semicolon_and_bom():
    """Excel tiếng Việt: dấu ';' + BOM."""
    p = Path(tempfile.gettempdir()) / "frag_b.csv"
    p.write_text(
        "ten;url\n"
        "London;https://www.fragrantica.com/perfume/Burberry/London-for-Men-804.html\n",
        encoding="utf-8-sig")
    assert read_urls(p) == [
        "https://www.fragrantica.com/perfume/Burberry/London-for-Men-804.html"]


def test_csv_no_header_and_relative_url():
    """URL tương đối chỉ nối thành link đầy đủ khi gọi kèm base_url của site.

    `core.csv_input` không biết đang đọc file của site nào nên không tự đoán
    tên miền; luồng crawl luôn truyền `scraper.base_url` vào.
    """
    p = _write_csv("frag_c.csv", (
        "/perfume/Dior/Sauvage-31861.html\n"
        "https://www.fragrantica.com/perfume/Chanel/Coco-Mademoiselle-611.html\n"))
    urls = read_urls(p, base_url=config.BASE_URL)
    assert urls[0] == "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html"
    assert len(urls) == 2


def test_csv_dedupe_and_skip_blank():
    p = _write_csv("frag_d.csv", (
        "url\n"
        "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html\n"
        "\n"
        "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html#reviews\n"
        "khong-phai-url\n"))
    assert read_urls(p) == [
        "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html"]


def test_csv_pick_column_by_name():
    p = _write_csv("frag_e.csv", (
        "trang_chu,link_san_pham\n"
        "https://www.fragrantica.com/,"
        "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html\n"))
    urls = read_urls(p, column="link_san_pham")
    assert urls == ["https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html"]

    try:
        read_urls(p, column="khong_ton_tai")
    except ValueError as exc:
        assert "link_san_pham" in str(exc)      # báo rõ các cột đang có
    else:
        raise AssertionError("phải báo lỗi khi tên cột sai")


def test_csv_source_and_dest_columns():
    """Đúng cấu trúc data/input.csv: crawl source_url, gắn kèm des_url."""
    p = _write_csv("frag_g.csv", (
        "source_url,des_url\n"
        "https://www.fragrantica.com/perfume/Yves-Saint-Laurent/Libre-56077.html,"
        "https://yupi.vn/products/yves-saint-laurent-libre-eau-de-parfum\n"))
    pairs = read_url_pairs(p)
    assert pairs == [(
        "https://www.fragrantica.com/perfume/Yves-Saint-Laurent/Libre-56077.html",
        "https://yupi.vn/products/yves-saint-laurent-libre-eau-de-parfum")]
    # URL đích không được lẫn vào danh sách crawl.
    assert read_urls(p) == [pairs[0][0]]


def test_csv_dest_column_optional():
    """Thiếu ô đích hoặc không có cột đích thì des_url = None, vẫn crawl bình thường."""
    p = _write_csv("frag_h.csv", (
        "source_url,des_url\n"
        "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html,\n"
        "https://www.fragrantica.com/perfume/Creed/Aventus-9828.html,https://yupi.vn/p/aventus\n"))
    pairs = read_url_pairs(p)
    assert pairs[0][1] is None
    assert pairs[1][1] == "https://yupi.vn/p/aventus"

    q = _write_csv("frag_i.csv", (
        "url\nhttps://www.fragrantica.com/perfume/Dior/Sauvage-31861.html\n"))
    assert read_url_pairs(q)[0][1] is None


def test_csv_dest_column_custom_name():
    p = _write_csv("frag_j.csv", (
        "link_nguon;link_san_pham_yupi\n"
        "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html;https://yupi.vn/p/sauvage\n"))
    pairs = read_url_pairs(p, dest_column="link_san_pham_yupi")
    assert pairs[0][1] == "https://yupi.vn/p/sauvage"

    try:
        read_url_pairs(p, dest_column="khong_co")
    except ValueError as exc:
        assert "link_san_pham_yupi" in str(exc)
    else:
        raise AssertionError("phải báo lỗi khi tên cột đích sai")


def test_des_url_written_to_output():
    """des_url phải có mặt trong cả JSONL lẫn CSV."""
    p = load_burberry()
    p.des_url = "https://yupi.vn/p/london"
    flat = p.to_flat_dict()
    assert flat["des_url"] == "https://yupi.vn/p/london"
    assert "des_url" in CSV_COLUMNS
    assert p.to_dict()["des_url"] == "https://yupi.vn/p/london"


def test_csv_prefer_host_bo_qua_cot():
    """Link cần crawl không nằm ở cột 'source_url' -> vẫn phải chọn đúng.

    Đúng dạng file trong input/Fragrantica: source_url là link yupi.vn, còn link
    Fragrantica lẫn trong ô des_url sau một dấu Tab.
    """
    p = _write_csv("frag_k.csv", (
        "source_url,des_url\n"
        "https://yupi.vn/products/abercrombie-fitch-fierce-cologne,"
        "Abercrombie & Fitch Fierce Cologne site:fragrantica.com\t"
        "https://www.fragrantica.com/perfume/Abercrombie-Fitch/Fierce-3508.html\n"))

    # Không có prefer_host: chọn theo tên cột -> ra nhầm link yupi.vn.
    assert "yupi.vn" in read_url_pairs(p)[0][0]

    pairs = read_url_pairs(p, prefer_host="fragrantica.com")
    assert pairs == [(
        "https://www.fragrantica.com/perfume/Abercrombie-Fitch/Fierce-3508.html",
        "https://yupi.vn/products/abercrombie-fitch-fierce-cologne")]


def test_csv_prefer_host_bo_dong_khong_khop():
    p = _write_csv("frag_l.csv", (
        "source_url,des_url\n"
        "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html,https://yupi.vn/a\n"
        "https://yupi.vn/b,https://shopee.vn/c\n"))       # không có link Fragrantica
    pairs = read_url_pairs(p, prefer_host="fragrantica.com")
    assert len(pairs) == 1
    assert pairs[0][0].endswith("Sauvage-31861.html")


def test_csv_prefer_host_cho_namperfume():
    p = _write_csv("frag_m.csv", (
        "source_url,des_url\n"
        "https://namperfume.net/products/x,https://yupi.vn/products/x\n"))
    pairs = read_url_pairs(p, prefer_host="namperfume.net")
    assert pairs == [("https://namperfume.net/products/x",
                      "https://yupi.vn/products/x")]


def test_retry_after():
    assert _parse_retry_after("120") == 120.0
    assert _parse_retry_after("  30  ") == 30.0
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None   # dạng date
    assert _parse_retry_after("-5") == 0.0


def test_429_dung_han_thay_vi_dam_tiep():
    """429 là site bảo chậm lại: nghỉ dài, hết lượt thì dừng cả mẻ."""
    f = Fetcher(use_cache=False, respect_robots=False)
    f._retry_after = 0            # để test không phải chờ thật
    assert 429 in RETRY_STATUSES
    # Lần cuối phải ném RateLimited chứ không im lặng bỏ qua URL.
    try:
        f._wait_out_rate_limit("http://x", attempt=len(RATE_LIMIT_BACKOFF))
    except RateLimited as exc:
        assert "429" in str(exc) and "--delay" in str(exc)
    else:
        raise AssertionError("phải ném RateLimited ở lần thử cuối")
    f.close()


def test_output_stem():
    """Tên file kết quả: <Tên Hãng>_<site>_<ddmmyy>, không có dấu ngoặc."""
    d = date(2026, 8, 15)
    assert output_stem("Abercrombie", "fragrantica", d) == \
        "Abercrombie_fragrantica_150826"
    assert output_stem("Dior", "namperfume", d) == "Dior_namperfume_150826"
    # ngày/tháng 1 chữ số vẫn phải đủ 2 ký tự
    assert output_stem("X", "fragrantica", date(2026, 1, 5)) == "X_fragrantica_050126"
    assert "(" not in output_stem("X", "fragrantica", d)


def test_iter_csv_files():
    folder = Path(tempfile.gettempdir()) / "frag_folder_test"
    (folder / "con").mkdir(parents=True, exist_ok=True)
    (folder / "B.csv").write_text("url\n", encoding="utf-8")
    (folder / "A.csv").write_text("url\n", encoding="utf-8")
    (folder / "ghi_chu.txt").write_text("bo qua", encoding="utf-8")
    (folder / "con" / "C.csv").write_text("url\n", encoding="utf-8")

    names = [p.name for p in iter_csv_files(folder)]
    assert names == ["A.csv", "B.csv", "C.csv"]        # sắp xếp, có cả thư mục con
    assert iter_csv_files(folder / "A.csv") == [folder / "A.csv"]


def test_csv_reads_back_our_own_output():
    """File CSV do chính crawler xuất ra phải dùng lại được làm đầu vào."""
    p = _write_csv("frag_f.csv", (
        "perfume_id,name,url\n"
        "804,London,https://www.fragrantica.com/perfume/Burberry/London-for-Men-804.html\n"))
    assert read_urls(p) == [
        "https://www.fragrantica.com/perfume/Burberry/London-for-Men-804.html"]


PERFORMANCE = Path(__file__).parent / "fixtures" / "performance_burberry.html"


def load_performance():
    return _parse_performance(_soup(PERFORMANCE.read_text(encoding="utf-8")))


def test_performance_top_labels():
    """Chỉ lấy title của chỉ số được vote nhiều nhất."""
    perf = load_performance()
    assert perf["longevity"] == "moderate"     # 3.7k, cao nhất trong 5 mức
    assert perf["sillage"] == "moderate"       # 4k, cao nhất trong 4 mức
    assert set(perf) == {"longevity", "sillage"}


def test_performance_top_is_max_not_first_or_last():
    """Nhãn nhiều vote nhất, không phải nhãn đầu bảng hay nhãn 'lâu' nhất."""
    soup = _soup(PERFORMANCE.read_text(encoding="utf-8"))
    bars = _performance_bars(_performance_card(soup, "longevity"),
                             LONGEVITY_LABELS)
    assert [b.name for b in bars] == [
        "very weak", "weak", "moderate", "long lasting", "eternal"]
    assert load_performance()["longevity"] == max(bars, key=lambda b: b.votes).name
    assert load_performance()["longevity"] not in (bars[0].name, bars[-1].name)


def test_performance_moderate_scoped_per_card():
    """'moderate' có ở cả 2 thang -> phải đọc trong phạm vi từng card."""
    soup = _soup(PERFORMANCE.read_text(encoding="utf-8"))
    lg = {b.name: b.votes for b in _performance_bars(
        _performance_card(soup, "longevity"), LONGEVITY_LABELS)}
    sl = {b.name: b.votes for b in _performance_bars(
        _performance_card(soup, "sillage"), SILLAGE_LABELS)}
    assert lg["moderate"] == 3700 and sl["moderate"] == 4000
    assert "eternal" not in sl and "enormous" not in lg


def test_performance_absent_without_scroll():
    """Trang chưa cuộn tới thì khối này chưa tải -> để trống."""
    p = load_rendered()
    assert p.longevity is None and p.sillage is None


def test_extract_links():
    links = extract_perfume_links(FIXTURE.read_text(encoding="utf-8"))
    assert len(links) > 5
    assert all(link.startswith("https://www.fragrantica.com/perfume/") for link in links)


if __name__ == "__main__":
    raise SystemExit(run(globals()))
