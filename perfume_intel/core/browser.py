"""Tải trang bằng browser thật (Playwright).

Cần cho các widget Vue render phía client — nổi bật là "when to wear"
(mùa / ngày-đêm): dữ liệu nằm trong biến JS `status` đã mã hoá AES, chỉ hiện ra
DOM sau khi JS chạy, nên requests + BeautifulSoup không thấy gì.

Cài đặt:
    pip install playwright
    python -m playwright install chromium
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .. import config
from .http import Fetcher, _parse_retry_after

log = logging.getLogger(__name__)

WAIT_TIMEOUT = 20_000
# Trang nhiều quảng cáo (Fragrantica) rất nặng; 30s (config.TIMEOUT) đôi khi
# không đủ để mở xong.
GOTO_TIMEOUT = 60_000

# Thử lần lượt các browser thật cài trên máy trước khi rơi về Chromium đóng gói.
BROWSER_CHANNELS = ("chrome", "msedge")

# Dùng mãi một context thì sau vài trang các lazy-section ngừng tải hẳn — đo
# thực tế: 8 trang liên tiếp chỉ được 4 (trang 5 trở đi mất sạch, khối
# LONGEVITY không render), còn làm mới context mỗi trang thì được 8/8.
# Mở context mới rất rẻ (không phải khởi động lại browser).
CONTEXT_MAX_PAGES = 3

# Trang có thể cao tới ~80.000px (đo trên Fragrantica) và còn phình thêm khi quảng cáo tải, nên
# cuộn theo ngân sách thời gian thay vì theo số bước cố định.
SCROLL_BUDGET = 25.0
SCROLL_PIXELS = 1200
SCROLL_PAUSE = 250


class BrowserFetcher(Fetcher):
    """Cùng interface với `Fetcher` nhưng trả về HTML sau khi JS chạy.

    Cache để riêng ở `<cache>/rendered/` vì nội dung khác hẳn bản requests.
    """

    # Chi tiết chai hầu như không đổi, mà render lại tốn ~7 giây — giữ lâu hơn
    # nhiều so với trang khám phá (xem config.CACHE_TTL_*).
    default_cache_ttl = config.CACHE_TTL_RENDERED

    def __init__(self, headless: bool = True, scroll: bool = False,
                 wait_selector: str | None = None,
                 ready_js: str | None = None,
                 channels: tuple[str, ...] = BROWSER_CHANNELS,
                 context_max_pages: int = CONTEXT_MAX_PAGES, **kwargs) -> None:
        """Lớp này không biết gì về site cụ thể — mọi thứ riêng đều là tham số.

        `wait_selector`: chờ phần tử này xuất hiện rồi mới lấy HTML (None = lấy
        ngay). `scroll`: có cuộn trang cho các khối lazy-load tải hay không.
        `ready_js`: đoạn JS trả về true khi dữ liệu cần đã render — có thì dừng
        cuộn ngay lúc đó thay vì cuộn hết ngân sách thời gian.
        """
        kwargs.setdefault("cache_dir", Path(config.CACHE_DIR) / "rendered")
        super().__init__(**kwargs)
        self.headless = headless
        self.scroll = scroll
        self.wait_selector = wait_selector
        self.ready_js = ready_js
        self.channels = channels
        self.context_max_pages = context_max_pages
        self._context = None
        self._pages_in_context = 0
        self._pw = None
        self._browser = None
        self._page = None

    # ------------------------------------------------------------------ browser
    def _ensure_page(self):
        """Mở browser một lần rồi dùng lại cho mọi URL.

        Playwright chỉ được start() MỘT lần cho cả vòng đời object; gọi lần hai
        khi instance cũ còn sống sẽ lỗi "Sync API inside the asyncio loop".
        Khi bị chặn ta chỉ mở lại browser, giữ nguyên instance Playwright.
        """
        if self._page is not None and self._pages_in_context < self.context_max_pages:
            return self._page

        # Hết hạn mức trang cho context này -> mở context mới (browser giữ nguyên).
        if self._page is not None:
            self._close_context()

        if self._pw is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "Cần Playwright để render trang. Cài bằng:\n"
                    "    pip install playwright\n"
                    "    python -m playwright install chromium"
                ) from exc
            self._pw = sync_playwright().start()

        if self._browser is None:
            self._browser = self._launch_browser()

        self._context = self._browser.new_context(
            user_agent=config.USER_AGENT,
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self._page = self._context.new_page()
        self._pages_in_context = 0
        return self._page

    def _close_context(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                log.debug("Lỗi khi đóng context", exc_info=True)
        self._context = self._page = None
        self._pages_in_context = 0

    def _launch_browser(self):
        """Ưu tiên browser thật của máy; Chromium đóng gói hay bị Cloudflare chặn.

        Đo thực tế trên 10 trang Fragrantica: Chromium đóng gói qua được 1/10
        (9 lần 403), Edge thật qua 10/10. Cloudflare nhận diện được bản Chromium
        của Playwright, đổi UA hay tắt cờ webdriver đều không cứu được.
        """
        from playwright.sync_api import Error as PlaywrightError

        args = ["--disable-blink-features=AutomationControlled"]
        for channel in (*self.channels, None):
            try:
                browser = self._pw.chromium.launch(
                    headless=self.headless, args=args,
                    **({"channel": channel} if channel else {}))
            except PlaywrightError:
                log.debug("Không dùng được channel %r", channel)
                continue
            log.info("Browser: %s", channel or "chromium đóng gói "
                     "(dễ bị Cloudflare chặn — nên cài Chrome hoặc Edge)")
            return browser
        raise RuntimeError("Không mở được browser nào.")

    def _fetch_once(self, url: str) -> tuple[int, str]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        page = self._ensure_page()
        self._pages_in_context += 1
        try:
            resp = page.goto(url, wait_until="domcontentloaded",
                             timeout=GOTO_TIMEOUT)
        except PlaywrightTimeout:
            # Page có thể đang kẹt giữa chừng -> bỏ context, lần thử sau dùng cái mới.
            log.warning("Hết giờ khi mở %s — thử lại với context mới.", url)
            self._close_context()
            return 408, ""
        except PlaywrightError as exc:
            log.warning("Lỗi browser tại %s: %s", url, exc)
            return 500, ""

        status = resp.status if resp else 200
        self._retry_after = self._cf_mitigated = None
        if resp is not None:
            try:
                headers = resp.headers
                self._retry_after = _parse_retry_after(headers.get("retry-after"))
                self._cf_mitigated = (headers.get("cf-mitigated") or "").lower() or None
            except Exception:
                log.debug("Không đọc được header phản hồi", exc_info=True)

        if status == 429:      # để Fetcher.get xử lý: nghỉ dài rồi dừng hẳn
            self._close_context()
            return status, ""

        if status == 403:
            # Cloudflare chặn theo phiên: cùng một URL mở bằng phiên mới lại vào
            # được. Bỏ phiên hiện tại để lần thử sau dùng context sạch.
            log.info("Bị chặn HTTP %s — mở lại phiên browser mới.", status)
            self._reset_page()
            return status, ""

        if status == 200:
            if self.wait_selector:
                try:
                    page.wait_for_selector(self.wait_selector,
                                           timeout=WAIT_TIMEOUT)
                except PlaywrightTimeout:
                    # Thiếu widget vẫn lấy được phần còn lại của trang.
                    log.info("Không thấy %r ở %s", self.wait_selector, url)
            if self.scroll:
                self._scroll_to_load(page, url)
        return status, page.content()

    def _scroll_to_load(self, page, url: str) -> None:
        """Cuộn dần cho các khối lazy-load tải, dừng ngay khi `ready_js` báo đủ.

        Phải cuộn DẦN: nhảy thẳng xuống cuối trang không kích hoạt được các
        lazy-section nằm giữa (đo trên Fragrantica: 0/6 trang lấy được).
        """
        deadline = time.monotonic() + SCROLL_BUDGET
        while time.monotonic() < deadline:
            if self.ready_js and page.evaluate(self.ready_js):
                return
            page.mouse.wheel(0, SCROLL_PIXELS)
            page.wait_for_timeout(SCROLL_PAUSE)

        if self.ready_js and not page.evaluate(self.ready_js):
            log.info("Cuộn hết %.0fs vẫn chưa thấy đủ dữ liệu ở %s",
                     SCROLL_BUDGET, url)

    def _reset_page(self) -> None:
        """Đóng browser hiện tại; `_ensure_page` sẽ mở phiên mới khi cần."""
        self._close_context()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                log.debug("Lỗi khi đóng browser", exc_info=True)
        self._browser = None

    # -------------------------------------------------------------------- dọn
    def close(self) -> None:
        for obj, name in ((self._browser, "browser"), (self._pw, "playwright")):
            if obj is None:
                continue
            try:
                obj.close() if name == "browser" else obj.stop()
            except Exception:                       # đóng lỗi không nên làm hỏng job
                log.debug("Lỗi khi đóng %s", name, exc_info=True)
        self._page = self._browser = self._pw = None
        super().close()
