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


# Ghi chu cho ba test ben duoi: tu khi `DAILY_MIN_COMMENTS` mac dinh la 5, moi
# loi goi `run_once` khong khai `min_comments` se BO phan chai it binh luan.
# Nhung test kiem ngan sach / tran ngay / lich su chay phai khai ro
# `min_comments=0`, neu khong chung dang kiem lan hai thu cung mot luc.
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
            r = daily.run_once(s.state, FakeScraper(), budget=4, max_brands=1, min_comments=0,
                               out_dir=s.dir)
            tong += r.perfumes_done

        assert tong == 10
        assert s.state.progress()["perfumes_pending"] == 0
        assert s.state.pending_perfumes(key, 5) == []


def test_khong_crawl_lai_chai_da_co():
    """Lần chạy thứ hai không được tiêu request cho chai đã xong."""
    with _Store() as s:
        _seeded(s, 4)
        daily.run_once(s.state, FakeScraper(), budget=4, max_brands=1, min_comments=0,
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
                                min_comments=0, out_dir=s.dir)

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
        daily.run_once(s.state, FakeScraper(), budget=2, max_brands=1, min_comments=0,
                       out_dir=s.dir)
        daily.run_once(s.state, FakeScraper(), budget=2, max_brands=1, min_comments=0,
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


# ------------------------------------------------- ngưỡng bình luận
def _mixed(n_cao=3, n_thap=4):
    """Chai chia hai nhóm: nhóm nhiều bình luận và nhóm gần như không ai bàn."""
    cao = [BrandPerfume(
        perfume_url=f"https://www.fragrantica.com/perfume/Afnan/Hi{i}-{i}.html",
        perfume_id=f"h{i}", perfume_name=f"Hot {i}", brand_name="Afnan",
        brand_url=AFNAN, comments=100 - i) for i in range(n_cao)]
    thap = [BrandPerfume(
        perfume_url=f"https://www.fragrantica.com/perfume/Afnan/Lo{i}-{i}.html",
        perfume_id=f"l{i}", perfume_name=f"Cold {i}", brand_name="Afnan",
        brand_url=AFNAN, comments=i) for i in range(n_thap)]      # 0,1,2,3
    return cao + thap


def test_nguong_binh_luan_cat_phan_duoi():
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, _mixed())

        assert len(s.state.pending_perfumes(key, 99, min_comments=0)) == 7
        assert len(s.state.pending_perfumes(key, 99, min_comments=5)) == 3


def test_nguong_khong_doi_thu_tu_uu_tien():
    """Ngưỡng chỉ CẮT phần đuôi, không đảo thứ tự: chai nhiều bình luận vẫn
    đi trước."""
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, _mixed())
        todo = s.state.pending_perfumes(key, 3, min_comments=5)
        assert [k for k, _ in todo] == [
            k for k, _ in s.state.pending_perfumes(key, 3, min_comments=0)]


def test_hang_chi_con_chai_duoi_nguong_thi_khong_duoc_chon():
    """Đây là chỗ dễ sai nhất của cả tính năng.

    Nếu chỉ lọc ở `pending_perfumes` mà quên lọc ở `next_brands`, thì một hãng
    còn 300 chai pending nhưng toàn dưới ngưỡng vẫn được chọn, rồi lượt chạy
    tiêu mất một suất hãng mà không crawl được gì — và `--dry-run` báo số nợ sai.
    """
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, _mixed(n_cao=2, n_thap=5))
        s.state.mark_products(key, ok=True)
        for pkey, _ in s.state.pending_perfumes(key, 99, min_comments=5):
            s.state.mark_perfume(pkey, ok=True)

        # Vẫn còn 5 chai pending, nhưng đều dưới ngưỡng.
        assert s.state.progress()["perfumes_pending"] == 5
        chon = [b.brand_name for b in s.state.next_brands(5, min_comments=5)]
        assert "Afnan" not in chon, f"vẫn chọn hãng không còn việc: {chon}"
        # Hạ ngưỡng xuống 0 thì nó phải quay lại hàng đợi.
        assert "Afnan" in [b.brand_name
                           for b in s.state.next_brands(5, min_comments=0)]


def test_so_con_no_bao_theo_dung_nguong():
    """`--dry-run` in `brand.pending`; số đó phải là phần THẬT SỰ sẽ crawl."""
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, _mixed(n_cao=3, n_thap=4))
        s.state.mark_products(key, ok=True)
        work = {b.brand_name: b for b in s.state.next_brands(5, min_comments=5)}
        assert work["Afnan"].pending == 3, work["Afnan"].pending


def test_progress_tach_phan_bi_nguong_bo_qua():
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, _mixed(n_cao=3, n_thap=4))
        p = s.state.progress(min_comments=5)
        assert p["pending_above"] == 3
        assert p["pending_below"] == 4
        assert p["pending_above"] + p["pending_below"] == p["perfumes_pending"]


def test_nguong_0_giu_nguyen_hanh_vi_cu():
    """Hạ về 0 phải trả lại đúng hành vi trước khi có tính năng này."""
    with _Store() as s:
        s.state.seed_brands(brand_rows())
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, _mixed())
        p = s.state.progress(min_comments=0)
        assert p["pending_above"] == p["perfumes_pending"]
        assert p["pending_below"] == 0


def test_mac_dinh_la_5():
    """Mặc định đổi hành vi, nên nó phải được canh bằng một test."""
    assert config.DAILY_MIN_COMMENTS == 5


def test_daily_khong_crawl_chai_duoi_nguong():
    """Chạy thật một lát ngân sách: chai dưới ngưỡng phải còn nguyên pending."""
    with _Store() as s:
        # CHỈ nạp Afnan: `brand_rows()` còn có Dior với popular_rank 2, mà ưu
        # tiên xếp theo thứ hạng phổ biến nên Dior được chọn trước — rồi
        # FakeFetcher trả fixture Afnan cho trang designer của Dior và nạp
        # nhầm 137 chai. Lỗi của fixture, không phải của tính năng.
        s.state.seed_brands([{"brand_url": AFNAN, "brand_name": "Afnan",
                              "popular_rank": 1}])
        key = s.state.brand_key_for(AFNAN)
        s.state.seed_perfumes(key, _mixed(n_cao=2, n_thap=4))
        s.state.mark_products(key, ok=True)

        scraper = FakeScraper()
        report = daily.run_once(s.state, scraper, budget=10, max_brands=1,
                                out_dir=s.dir / "out", min_comments=5)
        assert report.ok
        p = s.state.progress(min_comments=5)
        assert p["perfumes_done"] == 2, f"crawl nhầm số lượng: {p}"
        assert p["pending_below"] == 4, "chai dưới ngưỡng bị đụng tới"


# ------------------------------------- site nghỉ chung (mọi lệnh cùng thấy)
def test_ghi_nhan_site_nghi_va_doc_lai_duoc():
    with _Store() as s:
        assert s.state.site_cooldown("fragrantica") is None
        s.state.block_site("fragrantica", 2, "HTTP 429", "products")
        c = s.state.site_cooldown("fragrantica")
        assert c["reason"] == "HTTP 429"
        assert c["source"] == "products", "mất dấu lệnh nào gây ra"


def test_het_han_thi_khong_con_nghi():
    with _Store() as s:
        s.state.block_site("fragrantica", -1, "đã hết từ lâu", "test")
        assert s.state.site_cooldown("fragrantica") is None


def test_lan_chan_nang_hon_thang():
    """Rút ngắn thời gian nghỉ là thứ duy nhất ở đây có thể gây hại thật: một
    lệnh ghi 12 giờ, lệnh sau ghi 1 giờ, mà lại nghe lệnh sau thì coi như không
    có cơ chế nghỉ."""
    with _Store() as s:
        dai = s.state.block_site("fragrantica", 12, "nặng", "daily")
        ngan = s.state.block_site("fragrantica", 1, "nhẹ", "products")
        assert ngan == dai, "lần chặn ngắn hơn đã ghi đè lần dài hơn"
        assert s.state.site_cooldown("fragrantica")["reason"] == "nặng"
        # còn dài hơn nữa thì phải nhận
        hon = s.state.block_site("fragrantica", 24, "nặng hơn", "daily")
        assert hon > dai


def test_daily_bo_luot_khi_site_dang_nghi():
    """Đây là cả lý do của tính năng: một mẻ `products` dính 429 thì lượt
    `daily` ngay sau đó phải TRÁNH RA, chứ không lao vào tiếp.
    """
    with _Store() as s:
        _seeded(s, 10)
        s.state.block_site(daily.SITE, 6, "HTTP 429", "products")

        scraper = FakeScraper()
        report = daily.run_once(s.state, scraper, budget=10, max_brands=1,
                                min_comments=0, out_dir=s.dir / "out")

        assert report.stopped_reason == "site đang nghỉ"
        assert not report.ok, "bỏ lượt vì bị chặn mà vẫn báo ok"
        assert scraper.fake.calls == [],             f"ĐÃ RA MẠNG dù site đang nghỉ: {scraper.fake.calls}"
        assert s.state.progress()["perfumes_done"] == 0


def test_bo_luot_van_duoc_ghi_vao_lich_su():
    """Lượt bị bỏ phải để lại dấu, nếu không thì nhìn sổ tưởng lịch chết."""
    with _Store() as s:
        _seeded(s, 5)
        s.state.block_site(daily.SITE, 6, "HTTP 429", "products")
        daily.run_once(s.state, FakeScraper(), budget=5, max_brands=1,
                       min_comments=0, out_dir=s.dir / "out")
        runs = s.state.recent_runs(1)
        assert runs and runs[0]["stopped_reason"] == "site đang nghỉ"
        assert runs[0]["finished_at"], "lượt bỏ mà không đóng sổ"


def test_het_nghi_thi_chay_lai_binh_thuong():
    with _Store() as s:
        _seeded(s, 5)
        s.state.block_site(daily.SITE, -1, "đã hết", "products")
        report = daily.run_once(s.state, FakeScraper(), budget=3, max_brands=1,
                                min_comments=0, out_dir=s.dir / "out")
        assert report.ok
        assert s.state.progress()["perfumes_done"] == 3


def test_daily_bi_chan_thi_ghi_cho_lenh_khac_biet():
    """Chiều ngược lại: `daily` dính 429 cũng phải để lại ghi chú, để mẻ
    `products` chạy sau đó thấy."""
    with _Store() as s:
        _seeded(s, 5)
        scraper = FakeScraper(raise_after=0)
        daily.run_once(s.state, scraper, budget=5, max_brands=1,
                       min_comments=0, out_dir=s.dir / "out")
        assert s.state.site_cooldown(daily.SITE),             "daily bị chặn nhưng không ghi gì vào sổ chung"


def test_xoa_duoc_ghi_chu_nghi():
    with _Store() as s:
        s.state.block_site("fragrantica", 6, "HTTP 429", "products")
        assert s.state.clear_site_cooldown() == 1
        assert s.state.site_cooldown("fragrantica") is None


def test_record_block_khong_lam_chet_lenh_dang_chay():
    """Ghi sổ hỏng thì chỉ cảnh báo — mất một ghi chú còn hơn mất cả mẻ dữ liệu
    vừa crawl.

    Đường dẫn "không tồn tại" KHÔNG đủ để test cái này: `open_state` tự tạo
    thư mục nên nó sẽ thành công. Phải ép một lỗi thật — ở đây là đòi mở sổ
    bên trong một FILE, nên hệ điều hành trả NotADirectoryError.
    """
    from perfume_intel.pipelines import state as state_mod
    with tempfile.TemporaryDirectory() as td:
        chan = Path(td) / "toi-la-file"
        chan.write_text("x", encoding="utf-8")
        got = state_mod.record_block("fragrantica", RateLimited("429"), "test",
                                     db=chan / "s.db")
    assert got is None, "ghi sổ hỏng mà vẫn báo thành công"


if __name__ == "__main__":
    raise SystemExit(run(globals()))
