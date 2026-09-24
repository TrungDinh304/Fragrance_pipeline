"""Khung chung cho scraper của từng site.

Vòng lặp crawl (bỏ qua URL đã có, parse, log, gắn des_url, gọi callback) giống
hệt nhau giữa các site; chỉ khác cách bóc tách HTML và dòng log tóm tắt. Lớp
`SiteScraper` giữ phần chung, site cụ thể chỉ cần khai báo metadata + `parse()`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..core.http import Fetcher

log = logging.getLogger(__name__)


class SiteScraper:
    """Lớp cơ sở: fetch -> parse -> bản ghi dataclass của site.

    Lớp con khai báo `site`/`host`/`base_url`/`record_cls`/`csv_columns` và
    cài đặt `parse()`.
    """

    site: str = ""              # nhãn trong tên file kết quả
    host: str = ""              # tên miền để nhận URL của site trong file CSV
    base_url: str = ""
    record_cls: type = object
    csv_columns: list[str] = []
    # Chỉ dùng khi crawl có --render (xem core/browser.py).
    wait_selector: str | None = None
    scroll: bool = False
    ready_js: str | None = None

    def __init__(self, fetcher: Fetcher | None = None) -> None:
        self.fetcher = fetcher or Fetcher()

    # ------------------------------------------------------------- lớp con cài
    def parse(self, html: str, url: str) -> Any:
        raise NotImplementedError

    def summary(self, record: Any) -> str:
        """Dòng log một câu cho mỗi bản ghi lấy được."""
        return str(getattr(record, "name", None) or "?")

    # --------------------------------------------------------------- dùng chung
    def scrape_one(self, url: str) -> Any | None:
        html = self.fetcher.get(url)
        if html is None:
            return None
        try:
            record = self.parse(html, url)
        except Exception:                   # parser không được làm chết cả job
            log.exception("Lỗi khi bóc tách %s", url)
            return None
        log.info("OK  %s", self.summary(record))
        return record

    def scrape_many(
        self,
        urls: list[str],
        skip: set[str] | None = None,
        on_item: Callable[[Any], None] | None = None,
        des_urls: dict[str, str] | None = None,
    ) -> list:
        """Crawl nhiều URL.

        `des_urls` map url nguồn -> url đích; gắn vào bản ghi TRƯỚC khi gọi
        `on_item` để file ghi ra đã có sẵn cột này.
        """
        skip = skip or set()
        todo = [u for u in urls if u not in skip]
        log.info("Bắt đầu crawl %d URL (bỏ qua %d đã có).",
                 len(todo), len(urls) - len(todo))

        results: list = []
        for i, url in enumerate(todo, 1):
            log.info("[%d/%d] %s", i, len(todo), url)
            record = self.scrape_one(url)
            if record is None:
                continue
            if des_urls:
                record.des_url = des_urls.get(url)
            results.append(record)
            if on_item:
                on_item(record)
        return results

    def close(self) -> None:
        self.fetcher.close()

    def __enter__(self) -> "SiteScraper":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
