"""Lệnh `mini` — ghép danh sách bản mini với dữ liệu đã crawl (không cần mạng)."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .. import config
from ..core import bronze
from ..core import csv_input, storage
from ..pipelines import mini
from ..sources.fragrantica.models import CSV_COLUMNS, Perfume
from ..sources.fragrantica.scraper import HOST as FRAGRANTICA_HOST

log = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("mini",
                       help="Ghép dữ liệu đã crawl với danh sách bản mini "
                            "(offline)")
    p.add_argument("path", type=Path,
                   help="File CSV có cột source_url + des_url của bản mini")
    p.add_argument("--scan", type=Path, default=None,
                   help="File/thư mục .jsonl đã crawl để lấy thông tin sản phẩm "
                        "(mặc định: data/raw/fragrantica)")
    p.add_argument("--out", type=Path,
                   help="Đường dẫn file kết quả, không cần đuôi "
                        "(mặc định: data/processed/mini/<tên CSV>)")
    p.add_argument("--format", choices=["csv", "jsonl", "both"], default="jsonl")
    p.add_argument("--limit", type=int, help="Giới hạn số dòng CSV xử lý")
    p.add_argument("--url-column", dest="url_column",
                   help="Tên cột chứa URL nguồn (mặc định: tự dò)")
    p.add_argument("--dest-column", dest="dest_column",
                   help="Tên cột chứa URL bản mini (mặc định: tự dò cột des_url)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    scan = args.scan or config.raw_dir("fragrantica")
    if not args.path.exists():
        log.error("Không tìm thấy file CSV: %s", args.path)
        return 1
    if not bronze.available(scan):
        log.error("Không tìm thấy dữ liệu đã crawl: %s", scan)
        return 1

    try:
        # unique=False: một chai full có thể ứng với nhiều bản mini khác nhau.
        pairs = csv_input.read_url_pairs(
            args.path, args.url_column, args.dest_column,
            prefer_host=FRAGRANTICA_HOST, unique=False)
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    if args.limit:
        pairs = pairs[: args.limit]
    if not pairs:
        log.error("%s không có dòng nào dùng được.", args.path.name)
        return 1

    no_dest = sum(1 for _, dest in pairs if not dest)
    if no_dest:
        log.warning("%d dòng không có URL bản mini — des_url sẽ để trống.", no_dest)

    index = mini.index_records(scan)
    if not index:
        log.error("Không đọc được bản ghi nào trong %s", scan)
        return 1

    records, missing = mini.collect(pairs, index)
    perfumes = [Perfume.from_dict(raw) for raw in records]

    out_base = args.out or (config.PROCESSED_DIR / "mini" / args.path.stem.lower())
    if args.format in ("jsonl", "both"):
        storage.save_jsonl(perfumes, out_base.with_suffix(".jsonl"))
    if args.format in ("csv", "both"):
        storage.save_csv(perfumes, out_base.with_suffix(".csv"),
                         columns=CSV_COLUMNS)

    log.info("Xong: ghép được %d/%d dòng CSV.", len(records), len(pairs))
    if missing:
        log.warning("%d URL nguồn chưa có trong dữ liệu đã crawl "
                    "(cần crawl bổ sung):", len(missing))
        for url in missing[:20]:
            log.warning("  %s", url)
        if len(missing) > 20:
            log.warning("  ... và %d URL nữa.", len(missing) - 20)
    return 0 if records else 1
