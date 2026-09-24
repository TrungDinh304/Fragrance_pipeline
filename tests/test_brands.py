"""Test crawl danh mục hãng, chạy offline trên HTML thật đã lưu.

    python tests/test_brands.py
"""

import logging
import tempfile
from pathlib import Path

from runner import run  # noqa: E402  (dat sys.path)

from perfume_intel.core import storage  # noqa: E402
from perfume_intel.pipelines import brands as pipeline  # noqa: E402
from perfume_intel.sources.fragrantica.models import (  # noqa: E402
    BRAND_CSV_COLUMNS, Brand)
from perfume_intel.sources.fragrantica.parsers import (  # noqa: E402
    parse_alphabet_links, parse_brands_for_letter, parse_extra_sections,
    parse_popular_brands)

# Test retry chạy đúng nhánh chờ, nhưng không việc gì phải chờ thật.
pipeline.PAGE_RETRY_WAIT = 0
# Các test lỗi cố ý sinh WARNING/ERROR — không cần đổ ra màn hình.
logging.getLogger("perfume_intel").setLevel(logging.CRITICAL)

FIXTURES = Path(__file__).parent / "fixtures"
INDEX = FIXTURES / "designers_index.html"
LETTER_PAGE = FIXTURES / "designers_letter_5.html"      # trang chứa F, G, H


def index_html():
    return INDEX.read_text(encoding="utf-8")


def letter_html():
    return LETTER_PAGE.read_text(encoding="utf-8")


# ------------------------------------------------------------ Most Popular Brands
def test_popular_brands():
    popular = parse_popular_brands(index_html())

    assert len(popular) == 60
    assert popular[0].brand_name == "Lattafa Perfumes"
    assert popular[0].popular_rank == 1
    assert popular[0].brand_url.endswith("/designers/Lattafa-Perfumes.html")
    # Thứ hạng phải liên tục 1..n, không nhảy cóc.
    assert [b.popular_rank for b in popular] == list(range(1, len(popular) + 1))


def test_popular_chi_lay_link_hang():
    """Khối footer còn có link chai nước hoa; chỉ /designers/*.html được lấy."""
    assert all("/designers/" in b.brand_url for b in parse_popular_brands(index_html()))
    assert all("/perfume/" not in b.brand_url for b in parse_popular_brands(index_html()))


def test_popular_chua_co_chu_cai():
    """Khối footer không nói hãng thuộc chữ nào -> alphabet để trống."""
    assert all(b.alphabet is None for b in parse_popular_brands(index_html()))


# ---------------------------------------------------------------- mục lục A-Z
def test_du_26_chu_cai():
    pairs = parse_alphabet_links(index_html())

    assert [letter for letter, _ in pairs] == [chr(c) for c in range(65, 91)]


def test_href_doc_tu_dom_khong_tu_dung():
    """Nhiều chữ cái dùng chung một trang — href thật mới biết điều đó."""
    pairs = dict(parse_alphabet_links(index_html()))

    assert pairs["A"].endswith("/designers-1/#A")
    # F, G, H cùng nằm trên /designers-5/: tự dựng URL theo chữ cái sẽ sai.
    for letter in ("F", "G", "H"):
        assert "/designers-5/" in pairs[letter]
        assert pairs[letter].endswith(f"#{letter}")


def test_gom_chu_cai_theo_trang():
    """26 chữ cái chỉ nằm trên 11 trang -> mỗi trang chỉ tải một lần."""
    pages = pipeline._pages_by_letter(parse_alphabet_links(index_html()))

    assert len(pages) == 11
    assert sum(len(v) for v in pages.values()) == 26
    page5 = next(v for k, v in pages.items() if k.endswith("/designers-5/"))
    assert [letter for letter, _ in page5] == ["F", "G", "H"]


# ------------------------------------------------------- cắt hãng theo chữ cái
def test_cat_dung_section_cua_tung_chu():
    """Một trang chứa F, G, H — mỗi chữ phải ra đúng phần của nó."""
    html = letter_html()
    f = parse_brands_for_letter(html, "F")
    g = parse_brands_for_letter(html, "G")
    h = parse_brands_for_letter(html, "H")

    assert f and g and h
    assert f[0].brand_name == "F. Millot"
    assert g[0].brand_name == "G Parfums"
    assert h[0].brand_name == "H&M"
    # Không được gộp nhầm: 3 tập hãng phải rời nhau hoàn toàn.
    urls_f = {b.brand_url for b in f}
    urls_g = {b.brand_url for b in g}
    urls_h = {b.brand_url for b in h}
    assert not (urls_f & urls_g) and not (urls_g & urls_h) and not (urls_f & urls_h)


def test_gan_dung_chu_cai():
    brands = parse_brands_for_letter(letter_html(), "G")

    assert brands and all(b.alphabet == "G" for b in brands)


def test_chi_lay_hang_khong_lay_chai():
    for brand in parse_brands_for_letter(letter_html(), "F"):
        assert "/designers/" in brand.brand_url
        assert "/perfume/" not in brand.brand_url


def test_chu_cai_khong_co_tren_trang():
    """Chữ cái không thuộc trang này -> danh sách rỗng, không văng lỗi."""
    assert parse_brands_for_letter(letter_html(), "A") == []


def test_url_tuyet_doi_va_bo_fragment():
    brand = parse_brands_for_letter(letter_html(), "F")[0]

    assert brand.brand_url.startswith("https://www.fragrantica.com/")
    assert "#" not in brand.brand_url


# ------------------------------------------------------------------- gộp & khử trùng
def test_khu_trung_theo_url():
    a = Brand(brand_url="https://x/designers/Dior.html", brand_name="Dior",
              popular_rank=2)
    b = Brand(brand_url="https://x/designers/Dior.html", brand_name="Dior",
              alphabet="D")
    merged = pipeline.merge([[a], [b]])

    assert len(merged) == 1
    # Gộp chứ không đè: giữ được cả thứ hạng lẫn chữ cái.
    assert merged[0].popular_rank == 2 and merged[0].alphabet == "D"


def test_khu_trung_bo_qua_khac_biet_dinh_dang_url():
    """Dấu / cuối và hoa thường không được tính thành hai hãng khác nhau."""
    merged = pipeline.merge([
        [Brand(brand_url="https://x/designers/Dior.html", brand_name="Dior")],
        [Brand(brand_url="https://x/designers/dior.html/", alphabet="D")],
    ])

    assert len(merged) == 1 and merged[0].alphabet == "D"


def test_thu_tu_theo_chu_cai_roi_ten():
    merged = pipeline.merge([[
        Brand(brand_url="u3", brand_name="Zara", alphabet="Z"),
        Brand(brand_url="u4", brand_name="chua ro", popular_rank=1),
        Brand(brand_url="u1", brand_name="Bvlgari", alphabet="B"),
        Brand(brand_url="u2", brand_name="Armani", alphabet="A"),
    ]])

    # A -> B -> Z, hãng chưa rõ chữ cái xếp cuối.
    assert [b.brand_name for b in merged] == ["Armani", "Bvlgari", "Zara",
                                              "chua ro"]


# ------------------------------------------------------------------ toàn luồng
class FakeFetcher:
    """Fetcher giả: trả HTML đã lưu, đếm số lần tải, mô phỏng lỗi mạng."""

    def __init__(self, fail: set[str] | None = None, fail_times: int = 99):
        self.fail = fail or set()
        self.fail_times = fail_times
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        if any(bad in url for bad in self.fail):
            if sum(1 for c in self.calls if any(b in c for b in self.fail)
                   ) <= self.fail_times:
                return None
        if url.endswith("/designers/"):
            return index_html()
        if "/designers-5/" in url:
            return letter_html()
        return "<html><body>khong co chu cai nao</body></html>"

    def close(self):
        pass


def test_toan_luong_chi_tai_moi_trang_mot_lan():
    fetcher = FakeFetcher()
    report = pipeline.crawl(fetcher, letters="FGH")

    # 1 trang danh mục + 1 trang chứa cả F, G, H.
    assert len(fetcher.calls) == 2
    assert report.by_letter == {"F": 300, "G": 240, "H": 253}
    assert not report.missing_letters and report.ok


def test_toan_luong_gop_ca_popular_lan_muc_luc():
    report = pipeline.crawl(FakeFetcher(), letters="FGH")
    by_url = {b.brand_url: b for b in report.brands}

    assert report.popular == 60
    # Hãng phổ biến vẫn có mặt dù không thuộc F/G/H.
    dior = next(b for b in report.brands if b.brand_name == "Dior")
    assert dior.popular_rank == 2
    # Không có URL nào lặp lại.
    assert len(by_url) == len(report.brands)


def test_bao_loi_khi_thieu_chu_cai():
    """Trang tải được nhưng không cắt ra hãng nào -> phải báo thiếu, không im lặng."""
    report = pipeline.crawl(FakeFetcher(), letters="ABF")

    # A và B nằm trên trang khác, FakeFetcher trả HTML rỗng cho chúng.
    assert sorted(report.missing_letters) == ["A", "B"]
    assert not report.ok
    assert report.by_letter["F"] == 300


def test_retry_roi_thanh_cong():
    """Hỏng 2 lần đầu rồi được -> vẫn ra dữ liệu, không mất chữ cái nào."""
    fetcher = FakeFetcher(fail={"/designers-5/"}, fail_times=2)
    report = pipeline.crawl(fetcher, letters="F")

    assert report.by_letter["F"] == 300
    assert not report.missing_letters
    assert sum(1 for c in fetcher.calls if "/designers-5/" in c) == 3


def test_trang_hong_han_thi_bao_that_bai():
    fetcher = FakeFetcher(fail={"/designers-5/"})
    report = pipeline.crawl(fetcher, letters="FGH")

    assert sorted(report.missing_letters) == ["F", "G", "H"]
    assert report.failed_pages and not report.ok
    # Hỏng hẳn vẫn giữ được phần lấy từ footer.
    assert report.brands and report.popular == 60


def test_khong_tai_duoc_trang_danh_muc():
    report = pipeline.crawl(FakeFetcher(fail={"/designers/"}))

    assert not report.brands
    assert report.missing_letters == [chr(c) for c in range(65, 91)]


# ------------------------------------------------- section ngoài mục lục A-Z
def test_bo_qua_section_da_co_trong_a_z():
    """F, G, H đã nằm trong mục lục -> không được lấy lại lần nữa."""
    extra = parse_extra_sections(letter_html(), skip=set("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))

    assert extra == {}


def test_lay_section_chu_co_dau():
    """Trang F/G/H không có chữ có dấu; bỏ F khỏi skip thì phải thấy lại F."""
    extra = parse_extra_sections(letter_html(), skip=set("GH"))

    assert set(extra) == {"F"}
    assert len(extra["F"]) == 300
    assert all(b.alphabet == "F" for b in extra["F"])


def test_only_az_bo_qua_section_phu():
    """--only-az giữ đúng hành vi bám mục lục A-Z."""
    a = pipeline.crawl(FakeFetcher(), letters="F", only_az=True)
    b = pipeline.crawl(FakeFetcher(), letters="F", only_az=False)

    assert a.extra_sections == {}
    # Trang fixture không có section chữ có dấu -> hai cách cho cùng kết quả.
    assert len(a.brands) == len(b.brands)


# ----------------------------------------------------------------- xuất file
def test_xuat_jsonl_va_csv():
    report = pipeline.crawl(FakeFetcher(), letters="F")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "brands"
        storage.save_jsonl(report.brands, out.with_suffix(".jsonl"))
        storage.save_csv(report.brands, out.with_suffix(".csv"),
                         columns=BRAND_CSV_COLUMNS)

        lai = storage.load_records(out.with_suffix(".jsonl"), Brand)
        header = out.with_suffix(".csv").read_text(
            encoding="utf-8-sig").splitlines()[0]

    assert len(lai) == len(report.brands)
    assert lai[0].brand_url == report.brands[0].brand_url
    assert header.split(",") == BRAND_CSV_COLUMNS


if __name__ == "__main__":
    raise SystemExit(run(globals()))
