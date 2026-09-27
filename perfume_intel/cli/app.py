"""Điểm vào CLI: gom các lệnh con lại và điều phối.

    python -m perfume_intel crawl data/inputs/fragrantica --render
    python -m perfume_intel analyze
"""

from __future__ import annotations

import argparse
import sys

from . import (analyze_cmd, brands_cmd, crawl_cmd, daily_cmd, links_cmd,
               mini_cmd, products_cmd, queue_cmd)
from .options import setup_logging

COMMANDS = (crawl_cmd, brands_cmd, products_cmd, links_cmd, mini_cmd,
            analyze_cmd, queue_cmd, daily_cmd)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="perfume-intel",
        description="Crawl dữ liệu nước hoa và phân tích thị trường từ tín "
                    "hiệu cộng đồng.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    for module in COMMANDS:
        module.add_parser(sub)
    return p


def _force_utf8_console() -> None:
    """Console Windows mặc định là cp1252 — chữ tiếng Việt sẽ làm chết chương
    trình ngay ở dòng `--help` đầu tiên. Ép UTF-8 trước khi in bất cứ thứ gì."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    args = build_parser().parse_args(argv)
    setup_logging(getattr(args, "verbose", False))
    return args.func(args)
