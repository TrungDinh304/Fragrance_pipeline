"""Test phần lên lịch: sổ theo dõi + một lát ngân sách mỗi lần chạy.

Chạy offline hoàn toàn. Trọng tâm là những hành vi mà việc nhỏ giọt nhiều ngày
phụ thuộc vào: tôn trọng ngân sách, chia hãng lớn ra nhiều ngày, đánh dấu đúng,
và không bao giờ crawl lại chai đã có.

    python tests/test_schedule.py
"""

import logging
import tempfile
from pathlib import Path

from runner import run  # noqa: E402  (dat sys.path)

from perfume_intel import config  # noqa: E402
from perfume_intel.core import storage  # noqa: E402
from perfume_intel.core.http import Blocked, RateLimited  # noqa: E402
from perfume_intel.pipelines import daily  # noqa: E402
from perfume_intel.pipelines.state import CrawlState, DONE, FAILED  # noqa: E402
from perfume_intel.sources.fragrantica.models import (  # noqa: E402
    BrandPerfume, Perfume)
from perfume_intel.sources.fragrantica.scraper import (  # noqa: E402
    FragranticaScraper)

logging.getLogger("perfume_intel").setLevel(logging.CRITICAL)

FIXTURE = Path(__file__).parent / "fixtures" / "designer_afnan.html"
AFNAN = "https://www.fragrantica.com/designers/Afnan.html"
DIOR = "https://www.fragrantica.com/designers/Dior.html"


def afnan_html():
    return FIXTURE.read_text(encoding="utf-8")


def brand_rows():
    return [
        {"brand_url": AFNAN, "brand_name": "Afnan", "alphabet": "A",
         "popular_rank": None},
        {"brand_url": DIOR, "brand_name": "Dior", "alphabet": "D",
         "popular_rank": 2},
    ]


def perfume_rows(n, brand="Afnan", comments_desc=True):
    """n chai giả, `comments` giảm dần để kiểm thứ tự ưu tiên."""
    return [BrandPerfume(
        perfume_url=f"https://www.fragrantica.com/perfume/{brand}/P{i}-{i}.html",
        perfume_id=str(i), perfume_name=f"{brand} {i}", brand_name=brand,
        brand_url=AFNAN, comments=(n - i) if comments_desc else 0)
        for i in range(n)]


class _Store:
    """Mở sổ trên file tạm, tự dọn."""

    def __enter__(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.state = CrawlState(self.dir / "s.db")
        return self

    def __exit__(self, *exc):
        # SQLite trên Windows giữ handle; bỏ qua lỗi dọn để test không đỏ oan.
        try:
            self._td.cleanup()
        except OSError:
            pass


# ------------------------------------------------------------------ sổ: nạp
def test_nap_hang_va_khong_dam_tien_do():
    """Nạp lại danh mục không được xoá tiến độ đã có."""
    with _Store() as s:
        added, seen = s.state.seed_brands(brand_rows())
        assert (added, seen) == (2, 0)

        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, perfume_rows(3))
        todo = s.state.pending_perfumes(key, 10)
        s.state.mark_perfume(todo[0][0], ok=True)

        # nạp lại lần hai
        added2, seen2 = s.state.seed_brands(brand_rows())
        assert (added2, seen2) == (0, 2)
        assert s.state.progress()["perfumes_done"] == 1


def test_nap_lai_muc_luc_giu_tien_do():
    """Làm mới mục lục của hãng không được đặt lại status của chai."""
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, perfume_rows(3))
        todo = s.state.pending_perfumes(key, 10)
        s.state.mark_perfume(todo[0][0], ok=True)

        added = s.state.seed_perfumes(key, perfume_rows(3))

        assert added == 0
        assert s.state.progress()["perfumes_done"] == 1


# ------------------------------------------------------------ sổ: chọn việc
def test_uu_tien_hang_pho_bien_truoc():
    """Thứ hạng 'Most Popular Brands' là tín hiệu cộng đồng — làm trước."""
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        assert [b.brand_name for b in s.state.next_brands(2)] == ["Dior", "Afnan"]


def test_chai_nhieu_binh_luan_truoc():
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, perfume_rows(5))

        todo = s.state.pending_perfumes(key, 3)

        assert len(todo) == 3
        assert todo[0][1].endswith("P0-0.html")      # comments cao nhất


def test_bo_qua_hang_dang_nghi():
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        s.state.block_all(hours=5, reason="thử")

        assert s.state.next_brands(5) == []


def test_bo_qua_hang_da_xong_han():
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, perfume_rows(2))
        s.state.mark_products(key, ok=True)
        for k, _u in s.state.pending_perfumes(key, 10):
            s.state.mark_perfume(k, ok=True)

        assert "Afnan" not in [b.brand_name for b in s.state.next_brands(5)]


# ---------------------------------------------------------- sổ: đánh dấu, nghỉ
def test_nghi_lau_dan_khi_hang_loi_lien_tuc():
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)

        moc = []
        for _ in range(3):
            s.state.mark_products(key, ok=False, error="hỏng")
            moc.append(s.state.brand_detail(AFNAN)["blocked_until"])

        assert all(moc)
        assert moc[0] < moc[1] < moc[2], "thời gian nghỉ phải dài dần"


def test_lam_lai_duoc_sau_khi_reset():
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, perfume_rows(2))
        todo = s.state.pending_perfumes(key, 10)
        s.state.mark_perfume(todo[0][0], ok=False, error="lỗi")
        s.state.block_all(1, "chặn")

        chai, hang = s.state.reset_failed()

        assert chai == 1 and hang >= 1
        assert s.state.progress()["perfumes_failed"] == 0
        assert s.state.next_brands(5), "phải làm lại được sau reset"


def test_ghi_nhan_theo_url_chi_voi_chai_co_trong_so():
    """`--import-existing` chỉ đánh dấu chai đã có trong sổ, không bịa thêm."""
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, perfume_rows(3))
        urls = [u for _k, u in s.state.pending_perfumes(key, 10)]

        n = s.state.mark_done_by_url(urls[:2] + ["https://x/perfume/La/Z-9.html"])

        assert n == 2
        assert s.state.progress()["perfumes_done"] == 2
        assert s.state.progress()["perfumes_total"] == 3      # không thêm chai lạ


def test_khoa_url_chuan_hoa():
    """Dấu / cuối và hoa thường không được tính thành chai khác."""
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, perfume_rows(1))
        url = s.state.pending_perfumes(key, 1)[0][1]

        assert s.state.mark_done_by_url([url.upper() + "/"]) == 1


# ------------------------------------------------------------- một lần chạy
class FakeFetcher:
    """Trả HTML giả, đếm request, mô phỏng chặn."""

    def __init__(self, raise_after: int | None = None, exc=None):
        self.calls: list[str] = []
        self.raise_after = raise_after
        self.exc = exc or RateLimited("429 thử nghiệm")

    def get(self, url):
        self.calls.append(url)
        if self.raise_after is not None and len(self.calls) > self.raise_after:
            raise self.exc
        if "/designers/" in url:
            return afnan_html()
        return "<html><body>chi tiết</body></html>"

    def close(self):
        pass


class FakeScraper(FragranticaScraper):
    """Dùng luồng thật, chỉ thay phần bóc tách (parser đã có test riêng)."""

    def __init__(self, **kwargs):
        self.fake = FakeFetcher(**kwargs)
        super().__init__(self.fake)

    def parse(self, html, url):
        return Perfume(url=url, name="chai", brand="Afnan")


def _seeded(store, n_perfumes=10):
    """Sổ đã có Afnan kèm mục lục, để tập trung kiểm phần chi tiết."""
    store.state.seed_brands([{"brand_url": AFNAN, "brand_name": "Afnan",
                              "popular_rank": 1}])
    key = store.state.brand_key_for(AFNAN)
    store.state.seed_perfumes(key, perfume_rows(n_perfumes))
    store.state.mark_products(key, ok=True)
    return key


def test_ton_trong_ngan_sach():
    with _Store() as s:
        _seeded(s, 10)
        scraper = FakeScraper()

        report = daily.run_once(s.state, scraper, budget=4, max_brands=1,
                                out_dir=s.dir)

        assert report.perfumes_done == 4
        assert report.requests_used == 4
        assert report.stopped_reason == "hết ngân sách"


def test_hang_lon_tran_sang_lan_sau():
    """Đây là hành vi cốt lõi: 10 chai, ngân sách 4 -> ba lần chạy là xong."""
    with _Store() as s:
        key = _seeded(s, 10)

        tong = 0
        for _ in range(3):
            r = daily.run_once(s.state, FakeScraper(), budget=4, max_brands=1,
                               out_dir=s.dir)
            tong += r.perfumes_done

        assert tong == 10
        assert s.state.progress()["perfumes_pending"] == 0
        assert s.state.pending_perfumes(key, 5) == []


def test_khong_crawl_lai_chai_da_co():
    """Lần chạy thứ hai không được tiêu request cho chai đã xong."""
    with _Store() as s:
        _seeded(s, 4)
        daily.run_once(s.state, FakeScraper(), budget=4, max_brands=1,
                       out_dir=s.dir)

        lan2 = FakeScraper()
        r2 = daily.run_once(s.state, lan2, budget=4, max_brands=1, out_dir=s.dir)

        assert r2.perfumes_done == 0
        assert lan2.fake.calls == [], "không được ra mạng lần nữa"


def test_chai_co_san_tren_dia_duoc_ghi_nhan_khong_phai_loi():
    """Dữ liệu crawl từ trước phải thành 'xong', không phải 'lỗi'.

    `crawl_urls` bỏ qua chai đã có trong file nên chúng không tới `on_item`.
    Không xử lý riêng thì chúng bị đánh dấu FAILED và bị thử lại mãi.
    """
    with _Store() as s:
        key = _seeded(s, 3)
        urls = [u for _k, u in s.state.pending_perfumes(key, 3)]

        # Giả lập file kết quả đã có sẵn 2 chai, sổ thì chưa biết.
        from perfume_intel.pipelines.crawl import out_base_for
        from perfume_intel.sources.fragrantica.scraper import SITE
        base = out_base_for(s.dir, "Afnan", SITE, resume=False)
        storage.save_jsonl([Perfume(url=u) for u in urls[:2]],
                           base.with_suffix(".jsonl"))

        scraper = FakeScraper()
        report = daily.run_once(s.state, scraper, budget=10, max_brands=1,
                                out_dir=s.dir)

        p = s.state.progress()
        assert p["perfumes_done"] == 3
        assert p["perfumes_failed"] == 0, "chai có sẵn bị đánh dấu lỗi"
        # Chỉ chai thứ ba thật sự phải tải.
        assert len(scraper.fake.calls) == 1
        assert report.perfumes_done == 1


def test_lay_muc_luc_truoc_khi_crawl_chi_tiet():
    with _Store() as s:
        s.state.seed_brands([{"brand_url": AFNAN, "brand_name": "Afnan",
                              "popular_rank": 1}])
        scraper = FakeScraper()

        report = daily.run_once(s.state, scraper, budget=5, max_brands=1,
                                out_dir=s.dir)

        assert report.brands[0].products_fetched
        # Mục lục thật của Afnan có 137 chai.
        assert s.state.progress()["perfumes_total"] == 137
        # Ngân sách 5 = 1 request mục lục + 4 chi tiết. Ngân sách tính cả
        # request lấy mục lục, vì site đếm mọi request chứ không riêng chi tiết.
        assert report.perfumes_done == 4
        assert scraper.fake.calls[0] == AFNAN
        assert len(scraper.fake.calls) == 5


def test_bi_chan_thi_cho_moi_hang_nghi():
    """429 là tín hiệu ở mức thiết bị — nghỉ cả hàng đợi, không nhảy hãng khác."""
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        _seeded(s, 5)
        scraper = FakeScraper(raise_after=1)

        report = daily.run_once(s.state, scraper, budget=10, max_brands=2,
                                out_dir=s.dir)

        assert report.stopped_reason == "bị chặn"
        assert not report.ok
        assert s.state.next_brands(5) == [], "mọi hãng phải đang nghỉ"


def test_cloudflare_cung_cho_nghi():
    with _Store() as s:
        _seeded(s, 5)
        scraper = FakeScraper(raise_after=0, exc=Blocked("cf-mitigated: challenge"))

        report = daily.run_once(s.state, scraper, budget=10, max_brands=1,
                                out_dir=s.dir)

        assert report.stopped_reason == "bị chặn"
        assert s.state.brand_detail(AFNAN)["blocked_until"]


def test_hang_doi_trong_thi_khong_ra_mang():
    with _Store() as s:
        scraper = FakeScraper()

        report = daily.run_once(s.state, scraper, budget=10, max_brands=2,
                                out_dir=s.dir)

        assert report.perfumes_done == 0
        assert scraper.fake.calls == []
        assert report.ok


# ------------------------------------------------------------- lịch sử chạy
def test_ghi_lai_tung_lan_chay():
    with _Store() as s:
        _seeded(s, 6)
        daily.run_once(s.state, FakeScraper(), budget=2, max_brands=1,
                       out_dir=s.dir)
        daily.run_once(s.state, FakeScraper(), budget=2, max_brands=1,
                       out_dir=s.dir)

        runs = s.state.recent_runs(5)

        assert len(runs) == 2
        assert all(r["finished_at"] for r in runs)
        assert sum(r["perfumes_done"] for r in runs) == 4
        assert all(r["stopped_reason"] for r in runs)


def test_tien_do_dem_dung():
    with _Store() as s:
        key = _seeded(s, 5)
        todo = s.state.pending_perfumes(key, 5)
        s.state.mark_perfume(todo[0][0], ok=True)
        s.state.mark_perfume(todo[1][0], ok=False, error="x")

        p = s.state.progress()

        assert p["perfumes_total"] == 5
        assert p["perfumes_done"] == 1
        assert p["perfumes_failed"] == 1
        assert p["perfumes_pending"] == 3
        assert p["brands_products_done"] == 1


if __name__ == "__main__":
    raise SystemExit(run(globals()))
