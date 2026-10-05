"""Lệnh `brands` — crawl danh mục hãng từ /designers/ (không crawl chai nào)."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .. import config
from ..core.bronze import BRAND
from ..core import lake, storage
from ..pipelines.state import record_block
from ..core.http import Blocked, Fetcher, RateLimited
from ..core.text import output_stem
from ..pipelines import brands
from ..sources.fragrantica.models import BRAND_CSV_COLUMNS
from ..sources.fragrantica.scraper import SITE
from .options import fetch_args, fetcher_kwargs

log = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("brands", parents=[fetch_args(resume=False)],
                       help="Crawl danh sách toàn bộ hãng trên Fragrantica")
    p.add_argument("--out", type=Path,
                   help="Đường dẫn file kết quả, không cần đuôi "
                        "(mặc định: data/raw/fragrantica/brands_fragrantica_<ddmmyy>)")
    p.add_argument("--format", choices=["csv", "jsonl", "both"], default="both",
                   help="Định dạng xuất (mặc định: both)")
    p.add_argument("--letters",
                   help="Chỉ crawl một số chữ cái, vd --letters ABC "
                        "(mặc định: đủ A-Z)")
    p.add_argument("--only-az", dest="only_az", action="store_true",
                   help="Chỉ lấy đúng 26 chữ cái trong mục lục, bỏ các section "
                        "chữ có dấu (À, É, Ô, Œ...) — thiếu vài chục hãng")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    # Danh mục hãng là HTML tĩnh, không cần browser: --render ở đây vô nghĩa.
    if args.render:
        log.info("Danh mục hãng không cần --render, bỏ qua cờ này.")

    fetcher = Fetcher(**fetcher_kwargs(args))
    try:
        report = brands.crawl(fetcher, letters=args.letters,
                              only_az=args.only_az)
    except (RateLimited, Blocked) as exc:
        log.error("%s", exc)
        # Ghi vào sổ để MỌI lệnh khác (nhất là lịch `daily`) biết mà tránh ra.
        record_block(SITE, exc, "brands")
        return 2
    except KeyboardInterrupt:
        log.warning("Đã dừng theo yêu cầu người dùng.")
        return 130
    finally:
        fetcher.close()

    if not report.brands:
        log.error("Không lấy được hãng nào.")
        return 1

    out_base = args.out or (config.raw_dir(SITE, BRAND)
                            / output_stem("brands", SITE))
    if args.format in ("jsonl", "both"):
        storage.save_jsonl(report.brands, out_base.with_suffix(".jsonl"))
    if args.format in ("csv", "both"):
        storage.save_csv(report.brands, out_base.with_suffix(".csv"),
                         columns=BRAND_CSV_COLUMNS)
    # Danh mục hãng là đầu vào của `make products`; để nó chỉ nằm trên một máy
    # thì máy khác chạy `products` với `--from-brands` rỗng.
    lake.seal_base(out_base)

    with_letter = sum(1 for b in report.brands if b.alphabet)
    log.info("Tổng: %d hãng (%d có chữ cái, %d nằm trong Most Popular Brands).",
             len(report.brands), with_letter,
             sum(1 for b in report.brands if b.popular_rank))

    # Thiếu chữ cái là lỗi thật: dữ liệu khuyết mà báo thành công thì nguy hiểm.
    return 0 if report.ok else 1
