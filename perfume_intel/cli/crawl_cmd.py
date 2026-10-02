"""Lệnh `crawl` — lấy dữ liệu từ một site về `data/raw/<site>/`.

Cùng một lệnh dùng cho mọi site; `--site` chọn scraper. Nguồn URL có thể là:
URL trực tiếp, một file CSV/text, cả một thư mục CSV, hoặc trang hãng
(`--designer`, chỉ Fragrantica).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .. import config
from ..core.bronze import PERFUME
from ..core import csv_input
from ..core.csv_input import read_urls_file
from ..core.http import Blocked, RateLimited
from ..pipelines.crawl import CrawlOptions, crawl_directory, crawl_urls, read_pairs
from ..sources.base import SiteScraper
from ..sources.fragrantica.scraper import FragranticaScraper
from ..sources.namperfume.scraper import NamperfumeScraper
from .options import build_fetcher, crawl_options, fetch_args, output_args

log = logging.getLogger(__name__)

SCRAPERS: dict[str, type[SiteScraper]] = {
    FragranticaScraper.site: FragranticaScraper,
    NamperfumeScraper.site: NamperfumeScraper,
}


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("crawl", parents=[output_args(), fetch_args()],
                       help="Crawl dữ liệu sản phẩm từ một site")
    p.add_argument("source", nargs="*",
                   help="URL, file .csv/.txt, hoặc thư mục chứa .csv")
    p.add_argument("--site", choices=sorted(SCRAPERS), default=FragranticaScraper.site,
                   help="Site cần crawl (mặc định: fragrantica)")
    p.add_argument("--designer",
                   help="Crawl toàn bộ nước hoa của một hãng trên Fragrantica, "
                        "vd: Dior, Chanel, Lattafa-Perfumes")
    p.set_defaults(func=run)


# File do lệnh `products` sinh ra có HAI cột link Fragrantica: `brand_url` (trang
# hãng) đứng trước `perfume_url` (trang chai). Cách tự dò của `csv_input` bám
# theo tên miền nên sẽ vớ phải cột đầu tiên và lặng lẽ crawl trang hãng như thể
# nó là một chai. Nhận ra file kiểu này thì chỉ thẳng cột đúng.
PRODUCTS_URL_COLUMN = "perfume_url"


def _products_csv_column(path: Path, opts: CrawlOptions) -> str | None:
    """'perfume_url' nếu `path` là file kết quả của lệnh `products`."""
    if opts.url_column:
        return None                      # người dùng đã chỉ định, tôn trọng
    try:
        header = path.read_text(encoding="utf-8-sig").splitlines()[0]
    except (OSError, IndexError):
        return None
    names = {c.strip().strip('"').lower() for c in header.split(",")}
    if {"brand_url", PRODUCTS_URL_COLUMN} <= names:
        log.info("%s là file của lệnh `products` -> crawl theo cột %r.",
                 path.name, PRODUCTS_URL_COLUMN)
        return PRODUCTS_URL_COLUMN
    return None


def _read_csv_pairs(scraper: SiteScraper, path: Path, opts: CrawlOptions):
    """Như `read_pairs` nhưng tự tránh bẫy cột của file `products`."""
    column = _products_csv_column(path, opts)
    if column is None:
        return read_pairs(scraper, path, opts)
    return csv_input.read_url_pairs(path, column, opts.dest_column,
                                    base_url=scraper.base_url)


def _resolve_urls(args, scraper: SiteScraper, opts: CrawlOptions,
                  ) -> tuple[list[str], dict[str, str]] | None:
    """Xác định URL cần crawl -> (danh sách url, map url nguồn -> url đích)."""
    urls: list[str] = []
    des_urls: dict[str, str] = {}

    if args.designer:
        if not isinstance(scraper, FragranticaScraper):
            log.error("--designer chỉ dùng được với --site fragrantica.")
            return None
        urls += scraper.discover_by_designer(args.designer, opts.limit)

    for item in args.source:
        if item.startswith(("http://", "https://")):
            urls.append(item)
            continue

        path = Path(item)
        if not path.exists():
            log.error("Không tìm thấy: %s", path)
            return None
        if path.suffix.lower() == ".csv":
            try:
                pairs = _read_csv_pairs(scraper, path, opts)
            except ValueError as exc:
                log.error("%s", exc)
                return None
            urls += [u for u, _ in pairs]
            des_urls.update({u: d for u, d in pairs if d})
        else:
            urls += read_urls_file(path)

    return (urls[: opts.limit] if opts.limit else urls), des_urls


def run(args: argparse.Namespace) -> int:
    scraper_cls = SCRAPERS[args.site]
    opts = crawl_options(args)
    scraper = scraper_cls(build_fetcher(args, scraper_cls))

    try:
        # Một thư mục -> mỗi file .csv bên trong ra một cặp file kết quả.
        folders = [Path(s) for s in args.source if Path(s).is_dir()]
        if folders:
            if len(args.source) > 1:
                log.error("Chỉ nhận 1 thư mục mỗi lần chạy.")
                return 1
            out_dir = opts.out or config.raw_dir(scraper.site, PERFUME)
            return crawl_directory(scraper, folders[0], out_dir, opts)

        if not args.source and not args.designer:
            log.error("Cần ít nhất 1 URL, file, thư mục, hoặc --designer.")
            return 1

        resolved = _resolve_urls(args, scraper, opts)
        if resolved is None:
            return 1
        urls, des_urls = resolved
        if not urls:
            log.error("Không có URL nào để crawl.")
            return 1

        out_base = (opts.out
                    or config.raw_dir(scraper.site, PERFUME) / scraper.site)
        count, attempted = crawl_urls(scraper, urls, des_urls, out_base, opts)
        log.info("Xong: %d/%d bản ghi.", count, attempted)
        if attempted == 0:
            # --resume và mọi URL đều đã crawl -> không có gì để làm.
            log.info("Tất cả URL đã có sẵn trong %s.",
                     out_base.with_suffix(".jsonl"))
            return 0
        return 0 if count else 1
    except (RateLimited, Blocked) as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("Đã dừng theo yêu cầu người dùng.")
        return 130
    finally:
        scraper.close()
