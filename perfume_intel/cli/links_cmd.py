"""Lệnh `links` — chỉ liệt kê link nước hoa có trong một trang Fragrantica.

Dùng để xem trước sẽ crawl những gì, hoặc để đổ ra file rồi crawl sau:
    perfume-intel links https://www.fragrantica.com/designers/Dior.html > dior.txt
"""

from __future__ import annotations

import argparse

from ..pipelines.state import record_block
from ..core.http import Blocked, RateLimited
from ..sources.fragrantica.scraper import FragranticaScraper
from .options import build_fetcher, fetch_args


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("links", parents=[fetch_args()],
                       help="Liệt kê link nước hoa từ một trang (không crawl chi tiết)")
    p.add_argument("url")
    p.add_argument("--limit", type=int)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    scraper = FragranticaScraper(build_fetcher(args, FragranticaScraper))
    try:
        for link in scraper.discover_from_url(args.url, args.limit):
            print(link)
        return 0
    except (RateLimited, Blocked) as exc:
        print(exc)
        record_block(scraper.site, exc, "links")
        return 2
    finally:
        scraper.close()
