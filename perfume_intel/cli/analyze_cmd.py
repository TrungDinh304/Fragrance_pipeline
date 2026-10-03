"""Lệnh `analyze` — tính chỉ số thị trường từ dữ liệu cộng đồng đã crawl."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .. import config
from ..analytics import dataset, report
from ..analytics.metrics import METRICS

log = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("analyze",
                       help="Phân tích thị trường từ dữ liệu cộng đồng đã crawl")
    p.add_argument("--community", type=Path,
                   help="Thư mục .jsonl Fragrantica "
                        "(mặc định: data/raw/fragrantica)")
    p.add_argument("--out", type=Path,
                   help="Thư mục ghi kết quả "
                        "(mặc định: data/processed/<YYYYMMDD>)")
    p.add_argument("--metric", action="append", choices=sorted(METRICS),
                   help="Chỉ tính một số chỉ số; lặp lại cờ để chọn nhiều "
                        f"(mặc định: tất cả — {', '.join(sorted(METRICS))})")
    p.add_argument("--no-html", dest="html", action="store_false",
                   help="Không sinh report.html (mặc định là có)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    community = args.community or config.raw_dir("fragrantica")
    if not community.exists():
        log.error("Chưa có dữ liệu để phân tích: %s\n"
                  "Chạy `perfume-intel crawl <thư mục CSV>` trước.", community)
        return 1

    rows = dataset.build(community)
    if not rows:
        log.error("Không đọc được bản ghi nào trong %s", community)
        return 1

    out_dir = report.run(rows, args.out, only=args.metric,
                         html=args.html, community=community)
    log.info("Tổng quan: %s",
             json.dumps(report.overview(rows), ensure_ascii=False))
    log.info("Kết quả -> %s/", out_dir)
    return 0
