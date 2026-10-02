"""Lệnh `products` — lấy danh sách chai của từng hãng từ trang hãng.

Chỉ lấy MỤC LỤC (chai nào thuộc hãng nào, collection nào), không mở trang từng
chai. Muốn dữ liệu đầy đủ của chai thì đưa cột `perfume_url` sang lệnh `crawl`.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .. import config
from ..core.bronze import BRAND_PERFUME
from ..core import storage
from ..core.http import Blocked, Fetcher, RateLimited
from ..core.text import output_stem
from ..pipelines import brand_products
from ..sources.fragrantica.models import BRAND_PERFUME_CSV_COLUMNS, BrandPerfume
from ..sources.fragrantica.scraper import SITE
from .options import fetch_args, fetcher_kwargs

log = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("products", parents=[fetch_args(resume=False)],
                       help="Lấy danh sách chai của hãng từ trang hãng")
    p.add_argument("brand_urls", nargs="*",
                   help="URL trang hãng, vd "
                        "https://www.fragrantica.com/designers/Afnan.html")
    p.add_argument("--from-brands", dest="from_brands", type=Path,
                   help="File .jsonl do lệnh `brands` sinh ra, lấy hãng từ đó")
    p.add_argument("--out", type=Path,
                   help="Đường dẫn file kết quả, không cần đuôi (mặc định: "
                        "data/raw/fragrantica/brand_products_fragrantica_<ddmmyy>)")
    p.add_argument("--format", choices=["csv", "jsonl", "both"], default="both")
    p.add_argument("--limit", type=int, help="Giới hạn số HÃNG xử lý")
    p.add_argument("--resume", action="store_true",
                   help="Bỏ qua hãng đã có trong file .jsonl kết quả")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def _brands_from(args) -> list[tuple[str, str | None]] | None:
    brands: list[tuple[str, str | None]] = [(u, None) for u in args.brand_urls]

    if args.from_brands:
        if not args.from_brands.exists():
            log.error("Không tìm thấy file danh mục hãng: %s", args.from_brands)
            return None
        loaded = brand_products.load_brands(args.from_brands)
        if not loaded:
            log.error("%s không có hãng nào đọc được.", args.from_brands.name)
            return None
        log.info("Đọc %d hãng từ %s", len(loaded), args.from_brands.name)
        brands += loaded

    if not brands:
        log.error("Cần ít nhất 1 URL hãng, hoặc --from-brands <file.jsonl>.")
        return None
    return brands[: args.limit] if args.limit else brands


def run(args: argparse.Namespace) -> int:
    # Trang hãng đã có sẵn dữ liệu trong HTML tĩnh, browser không thêm được gì.
    if args.render:
        log.info("Trang hãng không cần --render, bỏ qua cờ này.")

    brands = _brands_from(args)
    if brands is None:
        return 1

    out_base = args.out or (config.raw_dir(SITE, BRAND_PERFUME)
                            / output_stem("brand_products", SITE))
    jsonl_path = out_base.with_suffix(".jsonl")
    write_jsonl = args.format in ("jsonl", "both")

    skip = brand_products.done_brand_urls(jsonl_path) if args.resume else set()
    if write_jsonl and not args.resume:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path.write_text("", encoding="utf-8")

    # Ghi ngay sau mỗi hãng: crawl vài nghìn hãng mà đứt giữa chừng vẫn còn dữ liệu.
    def on_brand(_brand_url: str, perfumes: list[BrandPerfume]) -> None:
        if write_jsonl and perfumes:
            storage.save_jsonl(perfumes, jsonl_path, append=True)

    fetcher = Fetcher(**fetcher_kwargs(args))
    try:
        report = brand_products.crawl(fetcher, brands, skip=skip,
                                      on_brand=on_brand)
    except (RateLimited, Blocked) as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("Đã dừng theo yêu cầu người dùng.")
        return 130
    finally:
        fetcher.close()

    if args.format in ("csv", "both"):
        # Khi resume, CSV phải gồm cả phần cũ trong JSONL chứ không chỉ phần mới.
        export = (storage.load_records(jsonl_path, BrandPerfume)
                  if (write_jsonl and args.resume) else report.perfumes)
        storage.save_csv(export, out_base.with_suffix(".csv"),
                         columns=BRAND_PERFUME_CSV_COLUMNS)

    if not report.perfumes and not report.brands_skipped:
        log.error("Không lấy được chai nào.")
        return 1

    collections = len({p.collection for p in report.perfumes if p.collection})
    log.info("Tổng: %d chai / %d hãng / %d collection có tên.",
             len(report.perfumes), report.brands_done, collections)
    return 0 if report.ok or report.brands_skipped else 1
