"""Tầng tải HTML: session, throttle, retry, cache đĩa, kiểm tra robots.txt."""

from __future__ import annotations

import hashlib
import logging
import random
import time
import urllib.robotparser as robotparser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from .. import config

log = logging.getLogger(__name__)

# Các mã cần thử lại thay vì bỏ qua. 408 là *timeout* nên vốn là lỗi tạm thời,
# dù nằm trong dải 4xx — trước đây bị gộp vào nhóm "bỏ qua luôn" nên trang chỉ
# tải chậm một lần là mất hẳn.
RETRY_STATUSES = (403, 408, 429, 500, 502, 503, 504)

# 429 = server bảo thẳng "chậm lại". Không được lùi vài giây rồi đâm tiếp: chờ
# lâu hẳn, và nếu vẫn bị thì dừng cả mẻ chứ đừng nện tiếp vào site.
RATE_LIMIT_BACKOFF = (60, 300, 900)      # giây


class RateLimited(RuntimeError):
    """Server trả 429 nhiều lần liên tiếp — dừng hẳn thay vì crawl tiếp."""


class Blocked(RuntimeError):
    """Cloudflare bật chế độ thử thách cho client này — thử lại là vô ích."""


def _parse_retry_after(value: str | None) -> float | None:
    """Header Retry-After dạng số giây (dạng HTTP-date thì bỏ qua)."""
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None


class Fetcher:
    #: Tuổi thọ mặc định của cache. Lớp con đổi được (xem BrowserFetcher).
    default_cache_ttl: float = config.CACHE_TTL_DISCOVERY

    def __init__(
        self,
        cache_dir: Path | None = None,
        use_cache: bool = True,
        delay: tuple[float, float] = (config.DELAY_MIN, config.DELAY_MAX),
        respect_robots: bool = True,
        cache_ttl: float | None = None,
        cache_max_bytes: int | None = None,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update(config.DEFAULT_HEADERS)
        self.cache_dir = Path(cache_dir or config.CACHE_DIR)
        self.use_cache = use_cache
        self.delay = delay
        self.respect_robots = respect_robots
        self.cache_ttl = (self.default_cache_ttl if cache_ttl is None
                          else cache_ttl)
        self.cache_max_bytes = (config.CACHE_MAX_BYTES if cache_max_bytes is None
                                else cache_max_bytes)
        self._last_request = 0.0
        self._retry_after: float | None = None
        self._cf_mitigated: str | None = None
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._rate_limit_hits: list[float] = []
        self._writes_since_sweep = 0

        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ robots
    def _load_robots(self, url: str) -> robotparser.RobotFileParser:
        """robots.txt của chính tên miền đang crawl (mỗi host tải 1 lần)."""
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = robotparser.RobotFileParser()
            try:
                resp = self.session.get(urljoin(base, "/robots.txt"),
                                        timeout=config.TIMEOUT)
                rp.parse(resp.text.splitlines())
                log.info("Đã tải robots.txt của %s", parts.netloc)
            except requests.RequestException as exc:
                log.warning("Không tải được robots.txt của %s (%s) — bỏ qua kiểm tra.",
                            parts.netloc, exc)
                rp.allow_all = True
            self._robots[base] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        return self._load_robots(url).can_fetch(config.USER_AGENT, url)

    # ------------------------------------------------------------------- cache
    def _cache_path(self, url: str) -> Path:
        """Nơi GHI cache: chia thư mục con theo 2 ký tự đầu của digest.

        150k file phẳng trong một thư mục NTFS là tự chuốc khổ, và chia lại giữa
        chừng một mẻ crawl 10 ngày còn tệ hơn — nên chia ngay từ đầu.
        """
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / digest[:2] / f"{digest}.html"

    def _legacy_cache_path(self, url: str) -> Path:
        """Vị trí phẳng cũ — vẫn ĐỌC được để 350 MB cache sẵn có không mất trắng."""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.html"

    def _fresh_cache_file(self, url: str) -> Path | None:
        """File cache còn trong hạn `cache_ttl`, hoặc None."""
        now = time.time()
        for path in (self._cache_path(url), self._legacy_cache_path(url)):
            try:
                if now - path.stat().st_mtime < self.cache_ttl:
                    return path
            except OSError:          # không tồn tại, hoặc không đọc được
                continue
        return None

    def _iter_cache_files(self):
        """Mọi file cache THUỘC VỀ fetcher này.

        Chỉ nhận file ở gốc và trong thư mục shard 2 ký tự hex — nhờ vậy fetcher
        thường không quét sang `rendered/` của BrowserFetcher (nằm lồng bên
        trong) và không xoá nhầm cache đắt tiền của nhau.
        """
        try:
            entries = list(self.cache_dir.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_file() and entry.suffix == ".html":
                yield entry
            elif (entry.is_dir() and len(entry.name) == 2
                    and all(c in "0123456789abcdef" for c in entry.name)):
                yield from entry.glob("*.html")

    def _sweep_cache(self) -> None:
        """Quá trần thì xoá dần theo mtime cũ nhất cho tới khi còn 90% trần."""
        files: list[tuple[float, int, Path]] = []
        total = 0
        for path in self._iter_cache_files():
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size

        if total <= self.cache_max_bytes:
            return

        target = int(self.cache_max_bytes * 0.9)
        files.sort()                          # cũ nhất trước
        removed = freed = 0
        for _mtime, size, path in files:
            if total <= target:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            freed += size
            removed += 1

        log.info("Cache vượt trần — đã xoá %d file cũ nhất, giải phóng %.1f GB.",
                 removed, freed / 1024 ** 3)

    # ------------------------------------------------------------------ throttle
    def _throttle(self) -> None:
        wait = random.uniform(*self.delay) - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()

    # ----------------------------------------------------------------- tải 1 lần
    def _fetch_once(self, url: str) -> tuple[int, str]:
        """Tải 1 lần, trả về (status_code, html).

        Lớp con ghi đè hàm này để đổi cách tải (vd: render bằng browser)
        mà vẫn dùng lại cache / robots / throttle / retry ở đây.
        Đặt `self._retry_after` nếu server có gửi header Retry-After.
        """
        resp = self.session.get(url, timeout=config.TIMEOUT)
        self._retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
        self._cf_mitigated = (resp.headers.get("cf-mitigated") or "").lower() or None
        return resp.status_code, resp.text

    def _store_in_cache(self, url: str, html: str) -> None:
        """Ghi cache. Hỏng thì TẮT cache chứ không làm chết mẻ crawl.

        Đây là dòng quan trọng nhất của tầng cache: ổ đầy ở giờ thứ 220 của một
        mẻ 10 ngày mà ném `OSError` ra ngoài thì không chỗ nào bắt — `scrape_one`
        chỉ bọc `self.parse`, còn CLI chỉ bắt RateLimited/Blocked.
        """
        if not self.use_cache:
            return
        path = self._cache_path(url)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
        except OSError as exc:
            log.error("Không ghi được cache (%s) — chạy tiếp với cache TẮT. "
                      "Kiểm tra dung lượng ổ đĩa.", exc)
            self.use_cache = False
            return

        self._writes_since_sweep += 1
        if self._writes_since_sweep >= config.CACHE_SWEEP_EVERY:
            self._writes_since_sweep = 0
            self._sweep_cache()

    # ---------------------------------------------------------------- 429
    def _trip_rate_limit(self, url: str) -> None:
        """Dính 429 quá dày trong một cửa sổ ngắn -> dừng ngay, khỏi ngủ tiếp.

        Đi hết `RATE_LIMIT_BACKOFF` tốn 21 phút cho MỖI url. Khi site đã chặn
        thật thì đó là 21 phút vứt đi, nhân với số url còn lại.
        """
        now = time.time()
        window = config.RATE_LIMIT_TRIP_WINDOW
        self._rate_limit_hits = [t for t in self._rate_limit_hits
                                 if now - t < window]
        self._rate_limit_hits.append(now)
        if len(self._rate_limit_hits) < config.RATE_LIMIT_TRIP_COUNT:
            return
        raise RateLimited(
            f"Dính HTTP 429 {len(self._rate_limit_hits)} lần trong "
            f"{window // 60:.0f} phút ({url}).\n"
            "Site đang chặn thật — dừng sớm thay vì chờ hết thang backoff. "
            "Nghỉ vài giờ rồi chạy lại với --delay lớn hơn (vd: --delay 15 30) "
            "và --resume để chỉ lấy phần còn thiếu.")

    def _wait_out_rate_limit(self, url: str, attempt: int) -> None:
        """Bị 429 thì nghỉ dài, hết lượt thì dừng cả mẻ."""
        self._trip_rate_limit(url)           # có thể ném RateLimited ngay
        if attempt >= len(RATE_LIMIT_BACKOFF):
            raise RateLimited(
                f"Server vẫn trả 429 sau {attempt} lần chờ ({url}).\n"
                "Site đang chặn vì tần suất quá dày. Hãy dừng lại vài giờ, rồi "
                "chạy lại với --delay lớn hơn (vd: --delay 10 20). Dữ liệu đã "
                "crawl vẫn còn, dùng RESUME=1 để chạy tiếp phần thiếu.")

        cho = self._retry_after or RATE_LIMIT_BACKOFF[attempt - 1]
        log.warning("HTTP 429 (quá nhiều request) tại %s — nghỉ %.0f phút "
                    "rồi thử lại (%d/%d).",
                    url, cho / 60, attempt, len(RATE_LIMIT_BACKOFF))
        time.sleep(cho)

    # --------------------------------------------------------------------- get
    def get(self, url: str) -> str | None:
        """Trả về HTML của `url`, hoặc None nếu thất bại / bị robots.txt chặn."""
        if not self.allowed(url):
            log.warning("robots.txt không cho phép: %s", url)
            return None

        if self.use_cache:
            fresh = self._fresh_cache_file(url)
            if fresh is not None:
                log.debug("cache hit: %s", url)
                try:
                    return fresh.read_text(encoding="utf-8")
                except OSError as exc:
                    # File hỏng/bị khoá: coi như miss rồi tải lại, đừng chết.
                    log.warning("Không đọc được cache %s (%s) — tải lại.",
                                fresh.name, exc)

        for attempt in range(1, config.MAX_RETRIES + 1):
            self._throttle()
            try:
                status, html = self._fetch_once(url)
                if status == 200:
                    if "<html" not in html[:2000].lower():
                        log.error("Phản hồi không phải HTML tại %s "
                                  "(nén lạ hoặc bị chặn?) — bỏ qua.", url)
                        return None
                    self._store_in_cache(url, html)
                    return html

                if status == 429:
                    self._wait_out_rate_limit(url, attempt)
                    continue

                # Cloudflare đang bắt vượt thử thách: retry hay mở lại browser
                # đều không qua được, chỉ tổ nện thêm vào site.
                if status == 403 and self._cf_mitigated == "challenge":
                    raise Blocked(
                        "Cloudflare đang bắt vượt thử thách (cf-mitigated: "
                        f"challenge) tại {url}.\n"
                        "Thử lại tự động không qua được. Nguyên nhân thường là "
                        "crawl quá dày trước đó. Hãy dừng vài giờ, rồi chạy lại "
                        "với --delay lớn (vd: --delay 15 30) và RESUME=1 để chỉ "
                        "lấy phần còn thiếu.")

                if status in RETRY_STATUSES:
                    sleep_for = config.BACKOFF_FACTOR ** attempt
                    log.warning("HTTP %s tại %s — chờ %.0fs rồi thử lại (%d/%d)",
                                status, url, sleep_for, attempt, config.MAX_RETRIES)
                    time.sleep(sleep_for)
                    continue

                if 400 <= status < 500:
                    log.error("HTTP %s tại %s — bỏ qua.", status, url)
                    return None

                log.warning("HTTP %s tại %s", status, url)
            except requests.RequestException as exc:
                log.warning("Lỗi mạng %s tại %s (%d/%d)",
                            type(exc).__name__, url, attempt, config.MAX_RETRIES)
                time.sleep(config.BACKOFF_FACTOR ** attempt)

        log.error("Bỏ cuộc sau %d lần thử: %s", config.MAX_RETRIES, url)
        return None

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
