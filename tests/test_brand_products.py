"""Test lấy sản phẩm của hãng, chạy offline trên HTML thật đã lưu.

    python tests/test_brand_products.py
"""

import json
import logging
import tempfile
from pathlib import Path

from runner import run  # noqa: E402  (dat sys.path)

from perfume_intel.core import storage  # noqa: E402
from perfume_intel.pipelines import brand_products as pipeline  # noqa: E402
from perfume_intel.sources.fragrantica.models import (  # noqa: E402
    BRAND_PERFUME_CSV_COLUMNS, BrandPerfume)
from perfume_intel.sources.fragrantica.parsers import (  # noqa: E402
    parse_brand_collections, parse_brand_perfumes)

pipeline.PAGE_RETRY_WAIT = 0
logging.getLogger("perfume_intel").setLevel(logging.CRITICAL)

FIXTURE = Path(__file__).parent / "fixtures" / "designer_afnan.html"
# Cùng trang đó nhưng lấy bằng browser thật, đã cuộn hết — để chứng minh
# `--render` không thêm được gì (xem test_render_khong_them_gi).
FIXTURE_RENDERED = (Path(__file__).parent / "fixtures"
                    / "designer_afnan_rendered.html")
BRAND_URL = "https://www.fragrantica.com/designers/Afnan.html"


def afnan():
    return FIXTURE.read_text(encoding="utf-8")


def afnan_rendered():
    return FIXTURE_RENDERED.read_text(encoding="utf-8")


def parsed():
    return parse_brand_perfumes(afnan(), BRAND_URL)


# ------------------------------------------------------------------- parser
def test_lay_du_chai():
    perfumes = parsed()

    assert len(perfumes) == 137
    assert len({p.perfume_url for p in perfumes}) == 137


def test_khong_can_render():
    """HTML tĩnh đã đủ: chữ nằm trong <template> vẫn đọc được.

    Nếu ai đó sửa parser về `get_text()` mặc định, tên chai và tên collection
    sẽ rỗng sạch — test này bắt đúng lỗi đó.
    """
    perfumes = parsed()

    assert all(p.perfume_name for p in perfumes)
    assert any(p.collection for p in perfumes)


def test_render_khong_them_gi():
    """Bản requests và bản browser-đã-cuộn-hết phải ra y hệt nhau.

    Đây là lý do lệnh `products` không cần `--render`. Nếu Fragrantica chuyển
    danh sách sang lazy-load thật, test này đỏ trước khi dữ liệu bị thiếu âm
    thầm. (Đã kiểm thêm ngoài test trên Avon — 1.379 chai, 150 collection —
    cũng trùng khít.)
    """
    def norm(rows):
        out = []
        for r in rows:
            d = dict(r.__dict__)
            d.pop("scraped_at")          # timestamp đương nhiên khác
            out.append(d)
        return sorted(out, key=lambda d: (d["perfume_url"], d["collection"] or ""))

    static = norm(parse_brand_perfumes(afnan(), BRAND_URL))
    rendered = norm(parse_brand_perfumes(afnan_rendered(), BRAND_URL))

    assert len(static) == len(rendered) == 137
    assert static == rendered


def test_render_cung_ra_dung_collection():
    assert parse_brand_collections(afnan_rendered()) == parse_brand_collections(afnan())


def test_cac_truong_cua_mot_chai():
    p = next(x for x in parsed() if x.perfume_id == "66850")

    assert p.perfume_name == "Adwaa Al Sharq"
    assert p.brand_name == "Afnan"
    assert p.brand_url == BRAND_URL
    assert p.perfume_url.endswith("/perfume/Afnan/Adwaa-Al-Sharq-66850.html")
    assert p.year == 2019
    assert p.gender == "Unisex"
    assert p.comments == 42


def test_gioi_tinh_chuan_hoa():
    genders = {p.gender for p in parsed()}

    # 'female'/'male'/'unisex' trên trang -> đúng bộ giá trị của project.
    assert genders == {"Nam", "Nữ", "Unisex"}


def test_nam_0000_thanh_rong():
    """Trang ghi năm chưa rõ là '0000', không được để lọt thành số 0."""
    years = [p.year for p in parsed()]

    assert 0 not in years
    assert None in years
    assert all(y is None or 1900 < y < 2100 for y in years)


def test_url_tuyet_doi():
    assert all(p.perfume_url.startswith("https://www.fragrantica.com/perfume/")
               for p in parsed())


# -------------------------------------------------------------- collection
def test_nhom_theo_collection():
    perfumes = parsed()
    by_collection: dict[str | None, int] = {}
    for p in perfumes:
        by_collection[p.collection] = by_collection.get(p.collection, 0) + 1

    assert by_collection["9AM 9PM"] == 8
    assert by_collection["Orientals"] == 17
    assert by_collection["Supremacy"] == 12


def test_all_fragrances_khong_phai_ten_collection():
    """Chai dưới 'All Fragrances' là chai KHÔNG thuộc dòng nào -> để trống."""
    perfumes = parsed()
    khong_collection = [p for p in perfumes if p.collection is None]

    assert len(khong_collection) == 31
    assert all(p.collection_anchor is None for p in khong_collection)
    assert not any(p.collection == "All Fragrances" for p in perfumes)


def test_collection_va_anchor_di_cung_nhau():
    for p in parsed():
        assert (p.collection is None) == (p.collection_anchor is None)


def test_danh_sach_collection():
    collections = parse_brand_collections(afnan())
    names = [name for name, _ in collections]

    assert len(collections) == 19
    assert "All Fragrances" not in names
    assert ("9AM 9PM", "9AM-9PM") in collections


def test_moi_chai_thuoc_dung_mot_nhom():
    """Bản tĩnh có 2 view (lưới + danh sách) — không được đếm đôi."""
    perfumes = parsed()
    cap = [(p.perfume_url, p.collection) for p in perfumes]

    assert len(cap) == len(set(cap))


# ------------------------------------------------------------------ pipeline
class FakeFetcher:
    def __init__(self, fail: set[str] | None = None, fail_times: int = 99,
                 body: str | None = None):
        self.fail = fail or set()
        self.fail_times = fail_times
        self.body = body
        self.calls: list[str] = []

    def get(self, url):
        self.calls.append(url)
        if any(b in url for b in self.fail):
            n = sum(1 for c in self.calls if any(b in c for b in self.fail))
            if n <= self.fail_times:
                return None
        return afnan() if self.body is None else self.body

    def close(self):
        pass


def test_crawl_mot_hang():
    report = pipeline.crawl(FakeFetcher(), [(BRAND_URL, "Afnan")])

    assert len(report.perfumes) == 137
    assert report.brands_done == 1
    assert not report.failed_brands and report.ok


def test_ten_hang_tu_danh_muc_duoc_giu():
    report = pipeline.crawl(FakeFetcher(), [(BRAND_URL, "Afnan Perfumes")])

    # Thẻ chai đã ghi 'Afnan' nên giữ nguyên, không đè bằng tên trong danh mục.
    assert {p.brand_name for p in report.perfumes} == {"Afnan"}
    assert {p.brand_url for p in report.perfumes} == {BRAND_URL}


def test_retry_roi_thanh_cong():
    fetcher = FakeFetcher(fail={"Afnan"}, fail_times=2)
    report = pipeline.crawl(fetcher, [(BRAND_URL, "Afnan")])

    assert len(report.perfumes) == 137
    assert len(fetcher.calls) == 3
    assert not report.failed_brands


def test_hang_tai_hong():
    report = pipeline.crawl(FakeFetcher(fail={"Afnan"}), [(BRAND_URL, "Afnan")])

    assert report.failed_brands == [BRAND_URL]
    assert not report.perfumes and not report.ok


def test_hang_khong_co_chai():
    report = pipeline.crawl(FakeFetcher(body="<html><body>trong</body></html>"),
                            [(BRAND_URL, "Afnan")])

    assert report.empty_brands == [BRAND_URL]
    assert report.brands_done == 1


def test_resume_bo_qua_hang_da_crawl():
    fetcher = FakeFetcher()
    report = pipeline.crawl(fetcher, [(BRAND_URL, "Afnan")],
                            skip={"https://www.fragrantica.com/designers/afnan.html"})

    assert fetcher.calls == []
    assert report.brands_skipped == 1 and not report.perfumes


def test_ghi_dan_sau_moi_hang():
    ghi: list[tuple[str, int]] = []
    pipeline.crawl(FakeFetcher(), [(BRAND_URL, "Afnan")],
                   on_brand=lambda u, ps: ghi.append((u, len(ps))))

    assert ghi == [(BRAND_URL, 137)]


# ------------------------------------------------------- đọc danh mục & resume
def _write_brands(tmp: Path) -> Path:
    path = tmp / "brands.jsonl"
    rows = [
        {"brand_url": BRAND_URL, "brand_name": "Afnan"},
        {"brand_url": BRAND_URL + "/", "brand_name": "Afnan trung"},
        {"brand_url": "https://x/designers/Dior.html", "brand_name": "Dior"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def test_doc_danh_muc_hang_va_khu_trung():
    with tempfile.TemporaryDirectory() as td:
        brands = pipeline.load_brands(_write_brands(Path(td)))

    # Dấu / cuối không tạo thành hãng thứ hai.
    assert [u for u, _ in brands] == [BRAND_URL, "https://x/designers/Dior.html"]


def test_done_brand_urls_doc_theo_brand_url():
    """File kết quả là từng CHAI, nên phải gom theo brand_url chứ không phải url."""
    report = pipeline.crawl(FakeFetcher(), [(BRAND_URL, "Afnan")])
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "sp.jsonl"
        storage.save_jsonl(report.perfumes, out)
        done = pipeline.done_brand_urls(out)

    assert done == {"https://www.fragrantica.com/designers/afnan.html"}


# ----------------------------------- nối sang lệnh crawl (bẫy chọn nhầm cột)
def test_crawl_chon_dung_cot_perfume_url():
    """File `products` có 2 cột link Fragrantica — không được crawl nhầm trang hãng.

    `csv_input` tự dò theo tên miền nên sẽ vớ phải `brand_url` đứng trước. Lệnh
    `crawl` phải nhận ra file kiểu này và bám cột `perfume_url`.
    """
    from perfume_intel.cli.crawl_cmd import _products_csv_column, _read_csv_pairs
    from perfume_intel.pipelines.crawl import CrawlOptions
    from perfume_intel.sources.fragrantica.scraper import FragranticaScraper

    report = pipeline.crawl(FakeFetcher(), [(BRAND_URL, "Afnan")])
    scraper = FragranticaScraper.__new__(FragranticaScraper)   # không cần fetcher
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "products.csv"
        storage.save_csv(report.perfumes, csv_path,
                         columns=BRAND_PERFUME_CSV_COLUMNS)

        opts = CrawlOptions()
        assert _products_csv_column(csv_path, opts) == "perfume_url"
        pairs = _read_csv_pairs(scraper, csv_path, opts)

        # Tự dò (cách cũ) chỉ ra đúng 1 URL, và là trang hãng — chính là cái bẫy.
        from perfume_intel.core.csv_input import read_url_pairs
        tu_do = read_url_pairs(csv_path, prefer_host="fragrantica.com")

    assert len(pairs) == 137
    assert all("/perfume/" in u for u, _ in pairs)
    assert len(tu_do) == 1 and "/designers/" in tu_do[0][0]


def test_nguoi_dung_chi_dinh_cot_thi_ton_trong():
    from perfume_intel.cli.crawl_cmd import _products_csv_column
    from perfume_intel.pipelines.crawl import CrawlOptions

    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "products.csv"
        csv_path.write_text("brand_url,perfume_url\n", encoding="utf-8")
        assert _products_csv_column(csv_path,
                                    CrawlOptions(url_column="brand_url")) is None


def test_csv_thuong_khong_bi_nham_la_products():
    from perfume_intel.cli.crawl_cmd import _products_csv_column
    from perfume_intel.pipelines.crawl import CrawlOptions

    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "thuong.csv"
        csv_path.write_text("source_url,des_url\n", encoding="utf-8")
        assert _products_csv_column(csv_path, CrawlOptions()) is None


# ----------------------------------------------------------------- xuất file
def test_xuat_jsonl_va_csv():
    report = pipeline.crawl(FakeFetcher(), [(BRAND_URL, "Afnan")])
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "sp"
        storage.save_jsonl(report.perfumes, out.with_suffix(".jsonl"))
        storage.save_csv(report.perfumes, out.with_suffix(".csv"),
                         columns=BRAND_PERFUME_CSV_COLUMNS)
        lai = storage.load_records(out.with_suffix(".jsonl"), BrandPerfume)
        header = out.with_suffix(".csv").read_text(
            encoding="utf-8-sig").splitlines()[0]

    assert len(lai) == 137
    assert lai[0].perfume_url == report.perfumes[0].perfume_url
    assert header.split(",") == BRAND_PERFUME_CSV_COLUMNS


if __name__ == "__main__":
    raise SystemExit(run(globals()))
