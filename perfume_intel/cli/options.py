"""Các tuỳ chọn dùng chung của CLI và cách dựng fetcher từ chúng."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .. import config
from ..core.http import Fetcher
from ..pipelines.crawl import CrawlOptions
from ..sources.base import SiteScraper


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def output_args() -> argparse.ArgumentParser:
    """Nhóm tuỳ chọn về nơi ghi và định dạng kết quả."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--out", type=Path,
                   help="Đường dẫn file kết quả, không cần đuôi "
                        "(mặc định: data/raw/<site>/<hãng>_<site>_<ddmmyy>)")
    p.add_argument("--format", choices=["csv", "jsonl", "both"], default="jsonl")
    p.add_argument("--limit", type=int, help="Giới hạn số bản ghi")
    p.add_argument("--url-column", dest="url_column",
                   help="Tên cột chứa URL nguồn trong CSV (mặc định: tự dò)")
    p.add_argument("--dest-column", dest="dest_column",
                   help="Tên cột chứa URL đích, gắn kèm vào kết quả "
                        "(mặc định: tự dò cột des_url)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def fetch_args(resume: bool = True) -> argparse.ArgumentParser:
    """Nhóm tuỳ chọn về cách tải trang.

    `resume=False` cho lệnh chạy một mạch (vd `brands`): ở đó `--resume` /
    `--recrawl` không có nghĩa gì nên đừng bày ra trong `--help`.
    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"),
                   default=[config.DELAY_MIN, config.DELAY_MAX],
                   help="Khoảng nghỉ ngẫu nhiên giữa các request (giây)")
    p.add_argument("--no-cache", action="store_true",
                   help="Không dùng cache HTML")
    if resume:
        p.add_argument("--resume", action="store_true",
                       help="Bỏ qua URL đã có trong file .jsonl kết quả")
        p.add_argument("--recrawl", action="store_true",
                       help="Khi crawl cả thư mục: crawl lại cả những hãng đã "
                            "có file kết quả (mặc định chỉ crawl hãng chưa có)")
    p.add_argument("--ignore-robots", action="store_true",
                   help="Bỏ kiểm tra robots.txt (không khuyến khích)")
    p.add_argument("--render", action="store_true",
                   help="Tải bằng browser thật (Playwright) — chậm hơn nhưng "
                        "mới lấy được 'when to wear', độ lưu hương, độ toả hương")
    return p


def crawl_options(args: argparse.Namespace) -> CrawlOptions:
    return CrawlOptions(
        out=getattr(args, "out", None),
        format=getattr(args, "format", "jsonl"),
        limit=getattr(args, "limit", None),
        resume=getattr(args, "resume", False),
        recrawl=getattr(args, "recrawl", False),
        url_column=getattr(args, "url_column", None),
        dest_column=getattr(args, "dest_column", None),
    )


def build_fetcher(args: argparse.Namespace, scraper_cls: type[SiteScraper]) -> Fetcher:
    """Fetcher thường, hoặc BrowserFetcher với selector riêng của site khi --render."""
    common = dict(use_cache=not args.no_cache, delay=tuple(args.delay),
                  respect_robots=not args.ignore_robots)
    if not args.render:
        return Fetcher(**common)

    # Import muộn: Playwright là phụ thuộc tuỳ chọn.
    from ..core.browser import BrowserFetcher
    return BrowserFetcher(wait_selector=scraper_cls.wait_selector,
                          scroll=scraper_cls.scroll,
                          ready_js=scraper_cls.ready_js, **common)
