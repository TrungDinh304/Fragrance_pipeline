"""Lệnh `silver` — nén kho thô thành bảng Parquet đã khử trùng, có kiểu.

    python -m perfume_intel silver
    python -m perfume_intel silver --sql "SELECT accord, COUNT(*) FROM
                                          perfume_accords GROUP BY 1"

Chạy offline, không ra mạng. Chạy lại bất cứ lúc nào: luôn dựng lại từ bronze
nên không có trạng thái tích luỹ để lệch.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .. import config
from ..core import bronze

log = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("silver",
                       help="Nén kho thô thành bảng Parquet (tầng silver)")
    p.add_argument("--community", type=Path,
                   help="Thư mục bronze Fragrantica "
                        "(mặc định: data/raw/fragrantica)")
    p.add_argument("--out", type=Path,
                   help=f"Nơi ghi Parquet (mặc định: {config.SILVER_DIR})")
    p.add_argument("--sql", metavar="CÂU_LỆNH",
                   help="Chạy một câu SQL trên bảng silver rồi in kết quả "
                        "(không dựng lại bảng)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def _print_table(rows: list[tuple], columns: list[str]) -> None:
    if not rows:
        print("(không có dòng nào)")
        return
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows))
              for i, c in enumerate(columns)]
    widths = [min(w, 42) for w in widths]
    print("  ".join(str(c)[:w].ljust(w) for c, w in zip(columns, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows[:200]:
        print("  ".join(str(v)[:w].ljust(w) for v, w in zip(row, widths)))
    if len(rows) > 200:
        print(f"... còn {len(rows) - 200} dòng")


def run(args: argparse.Namespace) -> int:
    from ..warehouse import silver

    out_dir = args.out or config.SILVER_DIR

    if args.sql:
        if not Path(out_dir).exists():
            log.error("Chưa có bảng silver ở %s. Chạy `perfume-intel silver` "
                      "trước.", out_dir)
            return 1
        con = silver.connect()
        try:
            silver.read_silver(con, out_dir)
            result = con.execute(args.sql)
            _print_table(result.fetchall(),
                         [d[0] for d in result.description])
        finally:
            con.close()
        return 0

    community = args.community or config.raw_dir("fragrantica")
    if not bronze.available(Path(community)):
        log.error("Chưa có dữ liệu bronze: %s", community)
        return 1

    report = silver.build(community=community, out_dir=out_dir)
    print()
    print(f"Tổng {report.total():,} dòng -> {report.out_dir}"
          .replace(",", "."))
    print("Hỏi thử:  python -m perfume_intel silver --sql "
          "\"SELECT brand, COUNT(*) c FROM perfumes GROUP BY 1 "
          "ORDER BY c DESC LIMIT 5\"")
    return 0
