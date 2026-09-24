"""Crawl danh sách sản phẩm của từng hãng từ trang /designers/<Hãng>.html.

Đây là bước NỐI giữa hai thứ đã có:

    brands  ->  brand_products  ->  crawl
    (hãng)      (chai nào của hãng)   (chi tiết từng chai)

Ra bản ghi mục lục: chai nào thuộc hãng nào, nằm trong collection nào. Muốn
tháp hương / accord / vote thì lấy `perfume_url` rồi chạy lệnh `crawl`.

Không cần `--render`: khối `#brands` do Vue dựng, nhưng markup đã nằm sẵn trong
`<template>` của HTML tĩnh (xem `_tpl_text` trong parsers.py). Đã đối chiếu bản
`requests` với bản browser đã cuộn hết, so từng trường của từng chai:

    Afnan   137 chai /  19 collection  -> trùng khít
    Avon  1.379 chai / 150 collection  -> trùng khít   (HTML tĩnh 9,6 MB)

Avon là phép thử đáng tin: nếu trang có lazy-load hay phân trang trong danh
sách thì hãng lớn nhất phải lộ ra trước. `test_render_khong_them_gi` giữ kết
luận này khỏi mục nát — nó so bản tĩnh với bản render đã lưu ở tests/fixtures.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..core import storage
from ..core.http import Fetcher
from ..core.text import url_key
from ..sources.fragrantica.models import BrandPerfume
from ..sources.fragrantica.parsers import parse_brand_perfumes

log = logging.getLogger(__name__)

# Mỗi hãng một trang; hỏng một trang là mất trọn danh sách chai của hãng đó.
PAGE_RETRIES = 3
PAGE_RETRY_WAIT = 10.0


@dataclass
class BrandProductsReport:
    """Kết quả một lần crawl, kèm những hãng đã hỏng."""

    perfumes: list[BrandPerfume] = field(default_factory=list)
    brands_done: int = 0
    brands_skipped: int = 0
    empty_brands: list[str] = field(default_factory=list)
    failed_brands: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.perfumes) and not self.failed_brands


def _get_with_retry(fetcher: Fetcher, url: str,
                    attempts: int = PAGE_RETRIES) -> str | None:
    for attempt in range(1, attempts + 1):
        html = fetcher.get(url)
        if html:
            return html
        if attempt < attempts:
            log.warning("Không tải được %s (lần %d/%d) — chờ %.0fs rồi thử lại.",
                        url, attempt, attempts, PAGE_RETRY_WAIT)
            time.sleep(PAGE_RETRY_WAIT)
    log.error("Bỏ cuộc sau %d lần thử: %s", attempts, url)
    return None


def load_brands(path: Path) -> list[tuple[str, str | None]]:
    """Đọc file danh mục hãng (.jsonl do lệnh `brands` sinh ra) -> [(url, tên)]."""
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for raw in storage.read_jsonl(path):
        url = raw.get("brand_url")
        key = url_key(url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((url, raw.get("brand_name")))
    return out


def done_brand_urls(path: Path) -> set[str]:
    """Hãng đã có trong file kết quả — dùng cho `--resume`.

    Bản ghi ở đây là từng CHAI nên không dùng được `storage.load_scraped_urls`
    (nó đọc khoá `url`); phải gom theo `brand_url`.
    """
    return {key for key in (url_key(raw.get("brand_url"))
                            for raw in storage.read_jsonl(path)) if key}


def crawl(fetcher: Fetcher, brands: list[tuple[str, str | None]],
          base_url: str = config.BASE_URL,
          skip: set[str] | None = None,
          on_brand=None) -> BrandProductsReport:
    """Crawl từng trang hãng -> danh sách chai.

    `skip` là các `brand_url` đã crawl (đã chuẩn hoá bằng `url_key`).
    `on_brand(brand_url, perfumes)` được gọi ngay sau mỗi hãng để ghi dần —
    crawl vài nghìn hãng mà mất mạng giữa chừng thì không mất phần đã làm.
    """
    report = BrandProductsReport()
    skip = skip or set()
    todo = [(u, n) for u, n in brands if url_key(u) not in skip]
    report.brands_skipped = len(brands) - len(todo)

    if report.brands_skipped:
        log.info("Bỏ qua %d hãng đã crawl, còn %d hãng.",
                 report.brands_skipped, len(todo))

    for i, (brand_url, brand_name) in enumerate(todo, 1):
        label = brand_name or brand_url
        html = _get_with_retry(fetcher, brand_url)
        if html is None:
            report.failed_brands.append(brand_url)
            continue

        perfumes = parse_brand_perfumes(html, brand_url, base_url)
        # Tên hãng trong file danh mục đáng tin hơn chữ trên thẻ chai.
        if brand_name:
            for p in perfumes:
                p.brand_name = p.brand_name or brand_name

        report.brands_done += 1
        report.perfumes.extend(perfumes)

        if perfumes:
            collections = len({p.collection for p in perfumes if p.collection})
            log.info("[%d/%d] %-40s %3d chai, %2d collection",
                     i, len(todo), label[:40], len(perfumes), collections)
        else:
            # Hãng thật sự chưa có chai nào, hoặc trang đã đổi cấu trúc.
            report.empty_brands.append(brand_url)
            log.warning("[%d/%d] %-40s không có chai nào.",
                        i, len(todo), label[:40])

        if on_brand:
            on_brand(brand_url, perfumes)

    log.info("Xong: %d chai từ %d hãng (%d hãng rỗng, %d hãng lỗi).",
             len(report.perfumes), report.brands_done,
             len(report.empty_brands), len(report.failed_brands))
    if report.failed_brands:
        log.warning("Hãng không tải được: %s",
                    ", ".join(report.failed_brands[:10])
                    + (" ..." if len(report.failed_brands) > 10 else ""))
    return report
