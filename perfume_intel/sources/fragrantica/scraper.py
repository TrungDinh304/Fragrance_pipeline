"""Scraper cho fragrantica.com."""

from __future__ import annotations

import logging
from urllib.parse import quote

from ... import config
from ..base import SiteScraper
from .models import CSV_COLUMNS, Perfume
from .parsers import extract_perfume_links, parse_perfume

log = logging.getLogger(__name__)

HOST = "fragrantica.com"
SITE = "fragrantica"
# Chờ tới khi widget "when to wear" render xong (chỉ dùng với --render).
WAIT_SELECTOR = "div[index] span.tabular-nums"

# Khối độ lưu hương / độ toả hương nằm trong <lazy-section-new
# section-id="performance"> nên chỉ tải khi người dùng cuộn tới.
#
# Không chờ chữ "LONGEVITY": khung skeleton rỗng cũng đã có sẵn chữ đó, chờ nó
# sẽ dừng quá sớm và parser ra rỗng. Phải chờ đúng dữ liệu — nhãn 'eternal' chỉ
# có ở thang longevity, 'enormous' chỉ có ở thang sillage, cả hai chỉ xuất hiện
# khi các thanh bar đã vẽ xong.
PERFORMANCE_READY_JS = """() => {
  const t = [...document.querySelectorAll('span')]
      .map(s => s.textContent.trim().toLowerCase());
  return t.includes('eternal') && t.includes('enormous');
}"""


class FragranticaScraper(SiteScraper):
    site = SITE
    host = HOST
    base_url = config.BASE_URL
    record_cls = Perfume
    csv_columns = CSV_COLUMNS
    wait_selector = WAIT_SELECTOR
    scroll = True      # phải cuộn thì khối độ lưu/toả hương mới tải
    ready_js = PERFORMANCE_READY_JS

    def parse(self, html: str, url: str) -> Perfume:
        return parse_perfume(html, url)

    def summary(self, perfume: Perfume) -> str:
        return (f"{(perfume.name or '?')[:55]:<55} | {perfume.brand} "
                f"| {perfume.rating} sao")

    # -------------------------------------------------------------- khám phá URL
    def discover_from_url(self, url: str, limit: int | None = None) -> list[str]:
        html = self.fetcher.get(url)
        if html is None:
            return []
        links = extract_perfume_links(html)
        log.info("Tìm thấy %d link nước hoa tại %s", len(links), url)
        return links[:limit] if limit else links

    def discover_by_designer(self, designer: str,
                             limit: int | None = None) -> list[str]:
        slug = designer if designer.endswith(".html") else f"{quote(designer)}.html"
        return self.discover_from_url(f"{config.BASE_URL}/designers/{slug}", limit)
