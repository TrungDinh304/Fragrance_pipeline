"""Lệnh `lake` — xem và điều khiển data lake (MinIO/S3) bằng tay.

    lake status          hai bên đang có gì, lệch nhau chỗ nào
    lake init            tạo bucket (chạy một lần)
    lake push            niêm mọi thứ local lên lake
    lake pull            kéo mọi thứ lake về máy này

Lúc chạy bình thường thì không cần lệnh này: crawl tự niêm sau mỗi hãng và
`bronze.scan` tự kéo về. Nó có mặt cho ba tình huống thật:

  - **di trú lần đầu**: đưa kho đã crawl sẵn lên MinIO (`push`).
  - **máy mới / container mới**: lấy dữ liệu về để chạy phân tích (`pull`).
  - **lượt crawl bị ngắt giữa hãng**: file còn trong spool, niêm nốt (`push`).
"""

from __future__ import annotations

import argparse
import logging

from .. import config
from ..core import lake
from ..core.objects import ObjectNotFound, S3Store, StoreUnavailable

log = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("lake",
                       help="Xem và điều khiển data lake (MinIO/S3)")
    p.add_argument("action", choices=["status", "init", "push", "pull"],
                   nargs="?", default="status",
                   help="Mặc định: status")
    p.add_argument("--layer", choices=[*lake.LAYERS, "all"], default="all",
                   help="Tầng nào (mặc định: cả hai)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Chỉ in ra sẽ làm gì, không ghi gì")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def _goi(so_byte: int | None) -> str:
    if so_byte is None:
        return "?"
    x = float(so_byte)
    for don_vi in ("B", "KB", "MB", "GB"):
        if x < 1024 or don_vi == "GB":
            return f"{x:,.1f} {don_vi}".replace(",", ".")
        x /= 1024.0
    return f"{x:.1f} GB"


def _layers(args) -> list[str]:
    return list(lake.LAYERS) if args.layer == "all" else [args.layer]


def _tat(action: str) -> int:
    """Báo lake đang tắt, kèm đúng cách bật. Không coi đây là lỗi của người dùng."""
    log.error(
        "Lake đang TẮT nên `lake %s` không có việc gì làm.\n"
        "Bật bằng cách đặt các biến môi trường sau (xem .env.example):\n"
        "    LAKE=s3\n"
        "    S3_ENDPOINT=http://localhost:9000\n"
        "    S3_ACCESS_KEY=...   S3_SECRET_KEY=...\n"
        "Hoặc chạy qua Docker: `docker compose up -d minio` rồi "
        "`docker compose run --rm cli lake %s`.", action, action)
    return 1


# --------------------------------------------------------------------- status
def _print_status(layer: str) -> None:
    st = lake.status(layer)
    print(f"  {layer:<7} local: {st['local_files']:>6} file  "
          f"{_goi(st['local_bytes']):>10}", end="")
    if not st["enabled"]:
        print("      (lake tắt)")
        return
    if st["error"]:
        print(f"      lake: KHÔNG ĐỌC ĐƯỢC — {st['error']}")
        return
    print(f"      lake: {st['lake_files']:>6} file  "
          f"{_goi(st['lake_bytes']):>10}")


def _status(args) -> int:
    print(f"Lake: {lake.describe()}")
    print(f"Spool/cache: {config.RAW_DIR}")
    print()
    for layer in _layers(args):
        _print_status(layer)
    print()
    if not lake.enabled():
        print("Lake tắt: `data/raw/` và `data/silver/` ĐANG LÀ bản gốc.")
    else:
        print("Lake bật: bản chính thức nằm trên lake; local là spool ghi trước "
              "+ cache đọc.")
    print("`data/state/crawl_state.db` luôn chỉ của máy này — SQLite không "
          "chạy được trên S3.")
    return 0


# ----------------------------------------------------------------------- init
def _init(args) -> int:
    if not lake.enabled():
        return _tat("init")
    for layer in _layers(args):
        kho = lake.store(layer)
        if not isinstance(kho, S3Store):          # pragma: no cover
            continue
        if args.dry_run:
            print(f"  (thử) sẽ tạo bucket nếu chưa có: {kho}")
            continue
        try:
            kho.ensure_bucket()
        except (StoreUnavailable, ObjectNotFound) as exc:
            log.error("Không tạo được bucket cho tầng %s: %s", layer, exc)
            return 1
        print(f"  {layer:<7} sẵn sàng: {kho}")
    return 0


# ----------------------------------------------------------------------- push
def _push(args) -> int:
    if not lake.enabled():
        return _tat("push")
    for layer in _layers(args):
        root = lake.root_of(layer)
        if args.dry_run:
            _push_dry_run(layer, root)
            continue
        niem, bo_qua = lake.seal_dir(root, layer)
        print(f"  {layer:<7} niêm {niem} file, lake đã có đủ {bo_qua} file.")
    return 0


def _push_dry_run(layer: str, root) -> None:
    """In ra những file SẼ được niêm, không ghi gì.

    Dùng cùng luật so sánh với `seal_dir` (dài hơn thì thắng) để con số thử
    không nói khác con số thật.
    """
    duoi = lake.DATA_SUFFIXES if layer == lake.BRONZE else (".parquet",)
    try:
        da_co = {i.key: i.size for i in lake.store(layer).list()}
    except (StoreUnavailable, ObjectNotFound) as exc:
        log.error("Không đọc được lake: %s", exc)
        return
    se_niem = []
    for path in sorted(root.rglob("*")) if root.is_dir() else []:
        if not path.is_file() or path.suffix.lower() not in duoi:
            continue
        key = lake.key_for(path, layer)
        if key and da_co.get(key, -1) < path.stat().st_size:
            se_niem.append((key, path.stat().st_size))
    tong = sum(n for _k, n in se_niem)
    print(f"  {layer:<7} (thử) sẽ niêm {len(se_niem)} file, {_goi(tong)}:")
    for key, size in se_niem[:10]:
        print(f"      {key}  ({_goi(size)})")
    if len(se_niem) > 10:
        print(f"      ... và {len(se_niem) - 10} file nữa")


# ----------------------------------------------------------------------- pull
def _pull(args) -> int:
    if not lake.enabled():
        return _tat("pull")
    if args.dry_run:
        log.info("`pull --dry-run`: dùng `lake status` để xem lệch bao nhiêu file.")
        return 0
    # `force=True` vì người dùng gọi tay thì ý là "đồng bộ NGAY", không phải
    # "đồng bộ nếu tiến trình này chưa từng đồng bộ".
    lake.forget_sync()
    for layer in _layers(args):
        tai = lake.ensure_local(lake.root_of(layer), layer, force=True)
        print(f"  {layer:<7} lấy về {tai} file.")
    return 0


def run(args: argparse.Namespace) -> int:
    return {"status": _status, "init": _init,
            "push": _push, "pull": _pull}[args.action](args)
