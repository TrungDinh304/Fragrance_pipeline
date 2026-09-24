"""Test phần quyết định một mẻ crawl dài ngày sống hay chết.

Mỗi test ở đây canh một lỗi CỤ THỂ đã biết, và tất cả đều thuộc loại "hỏng mà
không báo gì" — chạy vài ngày mới lộ, lúc đó đã mất cả tuần crawl.

    python tests/test_resume_cache.py
"""

import logging
import tempfile
import time
from pathlib import Path

from runner import run  # noqa: E402  (dat sys.path)

from perfume_intel import config  # noqa: E402
from perfume_intel.core import storage  # noqa: E402
from perfume_intel.core.http import Fetcher, RateLimited  # noqa: E402
from perfume_intel.core.text import url_key  # noqa: E402
from perfume_intel.pipelines.crawl import (  # noqa: E402
    CrawlOptions, done_file_for, out_base_for)
from perfume_intel.sources.base import SiteScraper  # noqa: E402
from perfume_intel.sources.fragrantica.models import Perfume  # noqa: E402

logging.getLogger("perfume_intel").setLevel(logging.CRITICAL)

URL = "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html"


# ------------------------------------------------- S1b: khoá resume hai phía
class _CountingFetcher:
    """Fetcher giả, ghi lại đúng những URL bị đem đi tải."""

    def __init__(self):
        self.fetched: list[str] = []

    def get(self, url):
        self.fetched.append(url)
        return "<html><body>ok</body></html>"

    def close(self):
        pass


class RecordingScraper(SiteScraper):
    """Dùng `SiteScraper.scrape_many` THẬT.

    Quan trọng: không được chép lại logic bỏ qua vào đây. Test tự chép logic
    thì hỏng `sources/base.py` nó vẫn xanh — đã thử và đúng là như vậy.
    """

    site = "test"
    host = "x"
    base_url = "https://x"
    record_cls = Perfume
    csv_columns = ["url"]

    def __init__(self):
        self.counter = _CountingFetcher()
        super().__init__(self.counter)

    @property
    def fetched(self):
        return self.counter.fetched

    def parse(self, html, url):
        return Perfume(url=url)


def test_resume_khoa_chuan_hoa_ca_hai_phia():
    """`load_scraped_urls` và `scrape_many` phải dùng CÙNG dạng khoá.

    Đây là lỗi đắt nhất trong cả đợt refactor: chuẩn hoá một phía thôi thì mọi
    phép tra cứu trượt, `--resume` lặng lẽ crawl lại từ đầu, và không có một
    dòng lỗi nào. Trên danh mục 150k chai là 8-12 ngày đổ sông.
    """
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.jsonl"
        storage.save_jsonl([Perfume(url=URL)], path)
        skip = storage.load_scraped_urls(path)

    # Phía đọc trả khoá đã chuẩn hoá...
    assert skip == {url_key(URL)}
    # ...và phía so khớp cũng phải chuẩn hoá thì mới bỏ qua được.
    assert url_key(URL) in skip


def test_resume_khop_du_lech_hoa_thuong_va_dau_gach_cuoi():
    """Cùng một chai viết khác định dạng vẫn phải được bỏ qua."""
    bien_the = [URL, URL.upper(), URL + "/", URL + "#reviews"]

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.jsonl"
        storage.save_jsonl([Perfume(url=URL)], path)
        skip = storage.load_scraped_urls(path)

    scraper = RecordingScraper()
    scraper.scrape_many(bien_the, skip=skip)

    assert scraper.fetched == [], f"tải lại thừa: {scraper.fetched}"


def test_resume_van_crawl_url_moi():
    """Chuẩn hoá không được làm chai mới bị bỏ sót."""
    moi = "https://www.fragrantica.com/perfume/Dior/Fahrenheit-1.html"

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.jsonl"
        storage.save_jsonl([Perfume(url=URL)], path)
        skip = storage.load_scraped_urls(path)

    scraper = RecordingScraper()
    scraper.scrape_many([URL, moi], skip=skip)

    assert scraper.fetched == [moi]


def test_load_scraped_urls_bo_qua_ban_ghi_khong_co_url():
    """File trộn schema (Brand/BrandPerfume) không được sinh khoá rỗng."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "mixed.jsonl"
        path.write_text(
            '{"url": "%s"}\n{"brand_url": "https://x/designers/Dior.html"}\n'
            '{"perfume_url": "https://x/perfume/A-1.html"}\n' % URL,
            encoding="utf-8")
        skip = storage.load_scraped_urls(path)

    assert skip == {url_key(URL)}
    assert "" not in skip


# ------------------------------------------- S1c: tên hãng có ký tự glob
def test_ten_hang_co_ky_tu_glob():
    """`Amouage [Library]` phải khớp chính file của nó.

    Không escape thì `[...]` bị glob hiểu thành character class, mẫu không bao
    giờ khớp, và cả hãng bị crawl lại từ đầu mà không báo gì.
    """
    for brand in ("Amouage [Library]", "Viktor & Rolf", "Victoria's Secret"):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            base = out_base_for(out_dir, brand, "fragrantica", resume=False)
            base.with_suffix(".jsonl").write_text("", encoding="utf-8")

            # Đã có file -> phải bị coi là "đã xong".
            done = done_file_for(out_dir, brand, "fragrantica", CrawlOptions())
            assert done is not None, f"không tìm lại được file của {brand!r}"
            # --resume phải dùng lại đúng file đó chứ không mở file mới.
            again = out_base_for(out_dir, brand, "fragrantica", resume=True)
            assert again == base


def test_ten_hang_khong_khop_cheo_nhau():
    """Escape không được làm hai hãng khác nhau khớp lẫn vào nhau."""
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td)
        base = out_base_for(out_dir, "Dior", "fragrantica", resume=False)
        base.with_suffix(".jsonl").write_text("", encoding="utf-8")

        assert done_file_for(out_dir, "Dio?", "fragrantica", CrawlOptions()) is None
        assert done_file_for(out_dir, "Di*", "fragrantica", CrawlOptions()) is None


# ------------------------------------------------- S2a: ổ đầy không được giết
class UnwritableCacheFetcher(Fetcher):
    """Cache không ghi được — mô phỏng ổ đầy đúng chỗ nó thật sự hỏng: lúc GHI.

    Trỏ thư mục cache của URL vào một đường dẫn mà cha của nó là FILE, nên
    `path.parent.mkdir()` trong `_store_in_cache` ném `NotADirectoryError`
    (một `OSError`) — y như `ENOSPC` ngoài đời.
    """

    def __init__(self, blocker: Path, **kwargs):
        super().__init__(**kwargs)
        self._blocker = blocker

    def allowed(self, url):
        return True

    def _throttle(self):
        pass

    def _fetch_once(self, url):
        return 200, "<html><body>ok</body></html>"

    def _cache_path(self, url):
        return self._blocker / "khong-ghi-duoc.html"


def test_o_dia_day_khong_giet_me_crawl():
    """`OSError` lúc ghi cache phải tắt cache, KHÔNG ném ra ngoài.

    Không chỗ nào phía trên bắt `OSError`: `scrape_one` chỉ bọc `self.parse`,
    còn CLI chỉ bắt RateLimited/Blocked. Ném ra là chết cả mẻ 10 ngày ở giờ 220.
    """
    with tempfile.TemporaryDirectory() as td:
        blocker = Path(td) / "la-mot-file"
        blocker.write_text("", encoding="utf-8")     # cha là file, không phải thư mục

        fetcher = UnwritableCacheFetcher(blocker, cache_dir=Path(td))
        html = fetcher.get("https://x/a.html")

    assert html is not None and "ok" in html     # vẫn trả HTML về
    assert fetcher.use_cache is False            # và tự tắt cache


# --------------------------------------------------------- S2b: TTL của cache
class CountingFetcher(Fetcher):
    """Đếm số lần thực sự ra mạng."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hits = 0

    def allowed(self, url):
        return True

    def _throttle(self):
        pass

    def _fetch_once(self, url):
        self.hits += 1
        return 200, f"<html><body>lan {self.hits}</body></html>"


def test_cache_con_han_thi_khong_tai_lai():
    with tempfile.TemporaryDirectory() as td:
        fetcher = CountingFetcher(cache_dir=Path(td), cache_ttl=3600)
        fetcher.get("https://x/a.html")
        fetcher.get("https://x/a.html")

    assert fetcher.hits == 1


def test_cache_qua_han_thi_tai_lai():
    """Cache vĩnh viễn nghĩa là không bao giờ thấy chai mới trên trang hãng."""
    with tempfile.TemporaryDirectory() as td:
        fetcher = CountingFetcher(cache_dir=Path(td), cache_ttl=3600)
        fetcher.get("https://x/a.html")

        # Đẩy mtime lùi quá TTL.
        cached = fetcher._cache_path("https://x/a.html")
        old = time.time() - 7200
        import os
        os.utime(cached, (old, old))

        fetcher.get("https://x/a.html")

    assert fetcher.hits == 2


def test_browser_fetcher_giu_cache_lau_hon():
    """Chi tiết chai render mất ~7s nên phải giữ lâu hơn trang khám phá nhiều."""
    from perfume_intel.core.browser import BrowserFetcher

    assert Fetcher.default_cache_ttl == config.CACHE_TTL_DISCOVERY
    assert BrowserFetcher.default_cache_ttl == config.CACHE_TTL_RENDERED
    assert BrowserFetcher.default_cache_ttl > Fetcher.default_cache_ttl


def test_doc_duoc_cache_phang_cu():
    """350 MB cache sẵn có nằm phẳng — đổi sang chia thư mục không được vứt nó."""
    with tempfile.TemporaryDirectory() as td:
        fetcher = CountingFetcher(cache_dir=Path(td), cache_ttl=3600)
        legacy = fetcher._legacy_cache_path("https://x/a.html")
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("<html>cu</html>", encoding="utf-8")

        html = fetcher.get("https://x/a.html")

    assert html == "<html>cu</html>"
    assert fetcher.hits == 0


# --------------------------------------------------------- S2d: trần dung lượng
def test_xoa_lru_giu_duoi_tran():
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td)
        fetcher = Fetcher(cache_dir=cache, cache_max_bytes=3000)

        # 10 file 1 KB, mtime tăng dần -> tổng 10 KB, vượt xa trần 3 KB.
        for i in range(10):
            shard = cache / f"{i:02d}"
            shard.mkdir(exist_ok=True)
            f = shard / f"{i:02d}{'0' * 18}.html"
            f.write_text("x" * 1000, encoding="utf-8")
            import os
            os.utime(f, (1000 + i, 1000 + i))

        fetcher._sweep_cache()
        con_lai = sorted(p.name for p in fetcher._iter_cache_files())
        tong = sum(p.stat().st_size for p in fetcher._iter_cache_files())

    assert tong <= 3000
    # Xoá cũ nhất trước -> file còn lại phải là những file mới nhất.
    assert con_lai[0].startswith("07") or con_lai[0].startswith("08")


def test_sweep_khong_dung_toi_cache_rendered():
    """Fetcher thường không được xoá cache render (nằm lồng bên trong).

    `rendered/` tốn ~7 giây/file để dựng lại; xoá nhầm là đốt hàng giờ.
    """
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td)
        rendered = cache / "rendered"
        rendered.mkdir()
        (rendered / "abc.html").write_text("dat tien", encoding="utf-8")

        shard = cache / "ab"
        shard.mkdir()
        (shard / "ab0000000000000000.html").write_text("re", encoding="utf-8")

        fetcher = Fetcher(cache_dir=cache)
        thay = {p.name for p in fetcher._iter_cache_files()}

    assert thay == {"ab0000000000000000.html"}


# ------------------------------------------------- S1d: chặn sớm khi bị 429
def test_chan_som_khi_429_lien_tuc():
    """Đi hết thang backoff tốn 21 phút MỖI url — dừng sớm khi site chặn thật."""
    with tempfile.TemporaryDirectory() as td:
        fetcher = Fetcher(cache_dir=Path(td))
        for _ in range(config.RATE_LIMIT_TRIP_COUNT - 1):
            fetcher._trip_rate_limit("https://x/a.html")   # chưa được ném

        try:
            fetcher._trip_rate_limit("https://x/a.html")
        except RateLimited as exc:
            assert "429" in str(exc) and "--delay" in str(exc)
        else:
            raise AssertionError("phải ném RateLimited khi vượt ngưỡng")


def test_429_thua_thot_khong_chan():
    """429 lác đác ngoài cửa sổ thời gian thì không được dừng cả mẻ."""
    with tempfile.TemporaryDirectory() as td:
        fetcher = Fetcher(cache_dir=Path(td))
        cu = time.time() - config.RATE_LIMIT_TRIP_WINDOW - 10
        fetcher._rate_limit_hits = [cu] * (config.RATE_LIMIT_TRIP_COUNT + 5)

        fetcher._trip_rate_limit("https://x/a.html")       # không ném

    assert len(fetcher._rate_limit_hits) == 1


if __name__ == "__main__":
    raise SystemExit(run(globals()))
