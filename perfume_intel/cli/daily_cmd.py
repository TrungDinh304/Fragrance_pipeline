"""Lệnh `daily` — chạy một lát ngân sách. Đây là lệnh mà bộ lên lịch gọi.

Mặc định: tối đa 2 hãng và 150 request chi tiết mỗi lần chạy. Hãng lớn tự tràn
sang ngày sau vì tiến độ được đánh dấu ở mức từng chai.
"""

from __future__ import annotations

import argparse
import logging

from .. import config
from ..core.http import Blocked, RateLimited
from ..pipelines import daily
from ..pipelines.state import open_state
from ..sources.fragrantica.scraper import FragranticaScraper
from .options import build_fetcher, fetch_args
from pathlib import Path

log = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("daily", parents=[fetch_args(resume=False)],
                       help="Chạy một lát ngân sách theo lịch (nhỏ giọt mỗi ngày)")
    p.add_argument("--budget", type=int, default=config.DAILY_BUDGET,
                   help=f"TỔNG số request tối đa mỗi lần chạy, tính cả request "
                        f"lấy mục lục (mặc định {config.DAILY_BUDGET})")
    p.add_argument("--brands", type=int, default=config.DAILY_BRANDS,
                   help=f"Số hãng tối đa mỗi lần chạy "
                        f"(mặc định {config.DAILY_BRANDS})")
    p.add_argument("--min-comments", dest="min_comments", type=int,
                   default=config.DAILY_MIN_COMMENTS, metavar="N",
                   help=f"Bỏ chai có dưới N bình luận (mặc định "
                        f"{config.DAILY_MIN_COMMENTS}; đặt 0 để crawl tất cả). "
                        f"Ngưỡng 5 giữ 96%% lượng bình luận với 31%% số request")
    p.add_argument("--out", type=Path, help="Thư mục ghi kết quả "
                                           "(mặc định: data/raw/fragrantica)")
    p.add_argument("--format", choices=["csv", "jsonl", "both"], default="jsonl")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Chỉ in việc sẽ làm, không ra mạng")
    p.add_argument("--db", type=Path,
                   help=f"Đường dẫn sổ (mặc định: {config.STATE_DB})")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def _dry_run(state, budget: int, max_brands: int,
             min_comments: int) -> int:
    brands = state.next_brands(max_brands, min_comments)
    if not brands:
        print("Không có việc: hàng đợi trống, hoặc mọi hãng đang nghỉ.")
        return 0

    nguong = (f", bỏ chai dưới {min_comments} bình luận"
              if min_comments else "")
    print(f"Sẽ làm (ngân sách {budget} request, tối đa {max_brands} hãng"
          f"{nguong}):")
    con = budget
    for b in brands:
        if not b.products_done:
            print(f"  {b.label[:40]:<42} lấy mục lục (1 request), rồi crawl "
                  f"chi tiết trong phần ngân sách còn lại")
            con -= 1
            continue
        lay = min(b.pending, max(0, con))
        print(f"  {b.label[:40]:<42} {lay:>4}/{b.pending} chai còn nợ")
        con -= lay
        if con <= 0:
            print(f"  {'':<42} → hết ngân sách, phần còn lại để lần sau")
            break
    return 0


def run(args: argparse.Namespace) -> int:
    state = open_state(args.db)

    if args.dry_run:
        return _dry_run(state, args.budget, args.brands, args.min_comments)

    # Chi tiết chai cần --render mới có when_to_wear / độ lưu / độ toả hương.
    # Cùng một fetcher dùng cho cả mục lục (trang hãng là HTML tĩnh, browser
    # không thêm gì nhưng cũng không sai).
    if not args.render:
        log.warning("Chạy không có --render: sẽ thiếu when_to_wear, độ lưu hương "
                    "và độ toả hương. Thêm --render nếu muốn dữ liệu đầy đủ.")

    scraper = FragranticaScraper(build_fetcher(args, FragranticaScraper))
    try:
        report = daily.run_once(state, scraper, budget=args.budget,
                                max_brands=args.brands, out_dir=args.out,
                                fmt=args.format,
                                min_comments=args.min_comments)
    except (RateLimited, Blocked) as exc:
        # run_once đã tự bắt và ghi cooldown; tới đây là trường hợp lọt lưới.
        log.error("%s", exc)
        return 2
    finally:
        scraper.close()

    if report.brands:
        print()
        for b in report.brands:
            con = f", còn nợ {b.pending_left}" if b.pending_left else ", xong hãng này"
            print(f"  {b.label[:40]:<42} +{b.perfumes_done} chai"
                  f"{f' ({b.perfumes_failed} lỗi)' if b.perfumes_failed else ''}{con}")

    # Bị chặn là lỗi thật: bộ lên lịch cần biết để báo, không nuốt im.
    return 0 if report.ok else 2
