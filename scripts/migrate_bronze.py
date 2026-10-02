"""Dọn kho thô về bố cục mỗi loại một thư mục.

    python scripts/migrate_bronze.py                # xem sẽ làm gì, KHÔNG đụng file
    python scripts/migrate_bronze.py --apply        # làm thật
    python scripts/migrate_bronze.py --undo         # trả file về chỗ cũ

Trước:                              Sau:
    data/raw/fragrantica/               data/raw/fragrantica/
        Dior_fragrantica_270926.jsonl       perfumes/Dior_fragrantica_270926.jsonl
        brands_fragrantica_270926.jsonl     brands/brands_fragrantica_270926.jsonl
        brand_products_...jsonl             products/brand_products_...jsonl

VIỆC NÀY LÀ TUỲ CHỌN. `core/bronze.py` vẫn đọc được file phẳng kiểu cũ, nên
không chạy script này thì cũng không hỏng gì — chỉ là kho thô còn lẫn lộn.

Script chỉ DI CHUYỂN file, không sửa nội dung, và chỉ di chuyển file mà toàn bộ
các dòng cùng một loại. File lẫn nhiều loại trong cùng một file sẽ bị bỏ qua và
báo ra, vì chẻ nó thành nhiều file là sửa dữ liệu chứ không còn là dọn chỗ nữa.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from perfume_intel import config                      # noqa: E402
from perfume_intel.core import bronze, storage        # noqa: E402


def _kinds_in(path: Path) -> Counter:
    found: Counter = Counter()
    for record in storage.read_jsonl(path):
        found[bronze.classify(record) or "?"] += 1
    return found


def plan(root: Path) -> tuple[list[tuple[Path, Path]], list[tuple[Path, Counter]]]:
    """(danh sách chuyển được, danh sách phải bỏ qua kèm lý do)."""
    moves: list[tuple[Path, Path]] = []
    mixed: list[tuple[Path, Counter]] = []
    for path in sorted(root.glob("*.jsonl")):
        if not path.is_file():
            continue
        found = _kinds_in(path)
        real = {k: n for k, n in found.items() if k != "?"}
        if len(real) != 1:
            mixed.append((path, found))
            continue
        kind = next(iter(real))
        moves.append((path, bronze.dir_for(root, kind) / path.name))
    return moves, mixed


def undo(root: Path) -> list[tuple[Path, Path]]:
    moves = []
    for kind in bronze.KINDS:
        folder = bronze.dir_for(root, kind)
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.jsonl")):
            moves.append((path, root / path.name))
    return moves


def apply(moves: list[tuple[Path, Path]]) -> int:
    done = 0
    for src, dst in moves:
        if dst.exists():
            print(f"  BỎ QUA  {src.name} — đích đã có file trùng tên")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        done += 1
    return done


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site", action="append",
                   help="Site cần dọn (lặp lại được; mặc định: fragrantica, "
                        "namperfume)")
    p.add_argument("--apply", action="store_true",
                   help="Làm thật. Không có cờ này thì chỉ in ra dự định.")
    p.add_argument("--undo", action="store_true",
                   help="Trả file từ các thư mục con về lại thư mục gốc")
    args = p.parse_args(argv)

    sites = args.site or ["fragrantica", "namperfume"]
    total = 0
    for site in sites:
        root = config.raw_dir(site)
        if not root.is_dir():
            print(f"{site}: không có {root}, bỏ qua.")
            continue

        print(f"\n=== {site} ({root}) ===")
        if args.undo:
            moves, mixed = undo(root), []
        else:
            moves, mixed = plan(root)

        if not moves:
            print("  Không có gì để chuyển.")
        for src, dst in moves:
            print(f"  {src.name:52} -> {dst.parent.name}/")
        for path, found in mixed:
            detail = ", ".join(f"{n} {bronze.VI.get(k, k)}"
                               for k, n in sorted(found.items()))
            print(f"  BỎ QUA  {path.name} — lẫn nhiều loại ({detail}). "
                  f"Tách tay rồi chạy lại.")
        total += len(moves)

        if args.apply and moves:
            moved = apply(moves)
            print(f"  Đã chuyển {moved}/{len(moves)} file.")

    if not args.apply:
        print(f"\nTổng {total} file sẽ được chuyển. "
              f"Đây mới chỉ là bản xem trước — thêm --apply để làm thật.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
