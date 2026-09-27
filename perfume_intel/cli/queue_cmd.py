"""Lệnh `queue` — nạp hàng đợi và xem tiến độ crawl.

Đây là mặt đồng hồ của phần lên lịch: nạp danh mục hãng vào sổ, rồi bất cứ lúc
nào cũng xem được đã đi tới đâu, hãng nào đang dở, lần chạy gần nhất ra sao.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .. import config
from ..core import storage
from ..pipelines.state import open_state
from ..sources.fragrantica.models import BrandPerfume

log = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("queue",
                       help="Nạp hàng đợi crawl và xem tiến độ")
    p.add_argument("--seed", type=Path, metavar="FILE",
                   help="Nạp danh mục hãng từ file .jsonl của lệnh `brands`")
    p.add_argument("--brand", metavar="TÊN|URL",
                   help="Xem tiến độ chi tiết của một hãng")
    p.add_argument("--runs", type=int, nargs="?", const=10, metavar="N",
                   help="Xem N lần chạy gần nhất (mặc định 10)")
    p.add_argument("--import-existing", dest="import_existing", nargs="?",
                   const=str(config.raw_dir("fragrantica")), metavar="THƯ_MỤC",
                   help="Ghi nhận dữ liệu đã crawl từ trước vào sổ, để lịch "
                        "không crawl lại (mặc định: data/raw/fragrantica)")
    p.add_argument("--reset-failed", dest="reset_failed", action="store_true",
                   help="Cho các chai lỗi về lại pending và mở mọi hãng đang nghỉ")
    p.add_argument("--db", type=Path, help=f"Đường dẫn sổ (mặc định: {config.STATE_DB})")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def _print_progress(state) -> None:
    p = state.progress()
    if not p["brands_total"]:
        print("Hàng đợi trống. Nạp bằng:")
        print("  python -m perfume_intel queue --seed "
              "data/raw/fragrantica/brands_fragrantica_<ddmmyy>.jsonl")
        return

    hang_pct = 100 * p["brands_finished"] / p["brands_total"]
    print(f"HÃNG      {p['brands_finished']:>6} / {p['brands_total']:<6} xong hẳn "
          f"({hang_pct:.1f}%)")
    print(f"          {p['brands_products_done']:>6} đã có mục lục chai")
    if p["brands_blocked"]:
        print(f"          {p['brands_blocked']:>6} đang nghỉ (lỗi/bị chặn)")

    print()
    if p["perfumes_total"]:
        chai_pct = 100 * p["perfumes_done"] / p["perfumes_total"]
        print(f"CHAI      {p['perfumes_done']:>6} / {p['perfumes_total']:<6} đã có "
              f"chi tiết ({chai_pct:.1f}%)")
        print(f"          {p['perfumes_pending']:>6} còn nợ")
        if p["perfumes_failed"]:
            print(f"          {p['perfumes_failed']:>6} lỗi "
                  f"(mở lại bằng --reset-failed)")
    else:
        print("CHAI      chưa có mục lục nào — lần chạy `daily` đầu tiên sẽ lấy")

    doing = state.in_progress_brands(5)
    if doing:
        print()
        print("ĐANG DỞ")
        for b in doing:
            ten = b["brand_name"] or b["brand_url"]
            print(f"  {ten[:34]:<36} {b['done']:>4}/{b['perfume_total']:<4} chai")

    # Ước lượng dựa trên nhịp thật của 7 lần chạy gần nhất, không phải lý thuyết.
    runs = [r for r in state.recent_runs(7) if r.get("perfumes_done")]
    if runs and p["perfumes_pending"]:
        nhip = sum(r["perfumes_done"] for r in runs) / len(runs)
        if nhip > 0:
            ngay = p["perfumes_pending"] / nhip
            print()
            print(f"NHỊP      {nhip:.0f} chai/lần chạy ({len(runs)} lần gần nhất)")
            print(f"          còn ~{ngay:.0f} lần chạy nữa cho phần đang có trong sổ")


def _print_brand(state, brand: str) -> int:
    d = state.brand_detail(brand)
    if d is None:
        log.error("Không có hãng nào khớp %r trong sổ.", brand)
        return 1
    print(f"{d['brand_name'] or '(chưa rõ tên)'}")
    print(f"  url          {d['brand_url']}")
    print(f"  chữ cái      {d['alphabet'] or '-'}"
          f"   thứ hạng phổ biến: {d['popular_rank'] or '-'}")
    print(f"  mục lục      {d['products_status']}"
          f"{'  (' + d['products_at'] + ')' if d['products_at'] else ''}")
    print(f"  chai         {d['perfumes_done']} xong / "
          f"{d['perfumes_pending']} còn nợ / {d['perfumes_failed']} lỗi"
          f"  — tổng {d['perfume_total']}")
    if d["blocked_until"]:
        print(f"  đang nghỉ    tới {d['blocked_until']}  (lỗi {d['fail_count']} lần)")
    if d["last_error"]:
        print(f"  lỗi gần nhất {d['last_error']}")
    return 0


def _print_runs(state, limit: int) -> None:
    runs = state.recent_runs(limit)
    if not runs:
        print("Chưa có lần chạy nào.")
        return
    print(f"{'lần chạy':<17} {'chai':>5} {'lỗi':>4} {'req':>5}  kết thúc vì")
    for r in runs:
        print(f"{r['run_id']:<17} {r['perfumes_done']:>5} {r['perfumes_failed']:>4} "
              f"{r['requests_used']:>5}  {r['stopped_reason'] or '(đang chạy)'}")


def _import_existing(state, folder: Path) -> int:
    """Ghi nhận dữ liệu crawl từ trước — mục lục và chi tiết — vào sổ.

    Không có bước này thì lịch sẽ crawl lại 618 chai đã nằm sẵn trên đĩa.
    Phân loại theo hình dạng bản ghi, không theo tên file: tên hãng có thể chứa
    dấu nháy và `&` nên không đáng tin làm khoá.
    """
    if not folder.exists():
        log.error("Không tìm thấy thư mục: %s", folder)
        return 1

    files = storage.iter_jsonl_files(folder)
    theo_hang: dict[str, list] = {}
    chi_tiet: list[str] = []

    for path in files:
        for raw in storage.read_jsonl(path):
            if raw.get("perfume_url"):                    # BrandPerfume: mục lục
                brand_url = raw.get("brand_url")
                if brand_url:
                    theo_hang.setdefault(brand_url, []).append(
                        BrandPerfume.from_dict(raw))
            elif raw.get("url") and not raw.get("brand_url"):   # Perfume: chi tiết
                chi_tiet.append(raw["url"])

    hang_moi = chai_moi = 0
    for brand_url, perfumes in theo_hang.items():
        key = state.brand_key_for(brand_url)
        if key is None:
            continue                      # hãng không có trong danh mục đã nạp
        chai_moi += state.seed_perfumes(key, perfumes)
        state.mark_products(key, ok=True)
        hang_moi += 1

    da_xong = state.mark_done_by_url(chi_tiet)
    log.info("Quét %d file: %d hãng có mục lục (%d chai nạp vào sổ), "
             "%d chai được ghi nhận đã xong.",
             len(files), hang_moi, chai_moi, da_xong)
    if chi_tiet and not da_xong:
        log.info("(%d chai có chi tiết nhưng chưa nằm trong sổ — sẽ được ghi "
                 "nhận khi mục lục của hãng đó được lấy.)", len(chi_tiet))
    return 0


def run(args: argparse.Namespace) -> int:
    state = open_state(args.db)

    if args.seed:
        if not args.seed.exists():
            log.error("Không tìm thấy file: %s", args.seed)
            return 1
        rows = list(storage.read_jsonl(args.seed))
        brands = [r for r in rows if r.get("brand_url") and not r.get("perfume_url")]
        if not brands:
            log.error("%s không có bản ghi hãng nào (cần khoá brand_url).",
                      args.seed.name)
            return 1
        added, seen = state.seed_brands(brands)
        log.info("Nạp %s: %d hãng mới, %d hãng đã có (chỉ cập nhật metadata).",
                 args.seed.name, added, seen)
        print()

    if args.import_existing:
        rc = _import_existing(state, Path(args.import_existing))
        if rc:
            return rc
        print()

    if args.reset_failed:
        chai, hang = state.reset_failed()
        log.info("Mở lại %d chai lỗi và %d hãng đang nghỉ.", chai, hang)
        print()

    if args.brand:
        return _print_brand(state, args.brand)

    if args.runs:
        _print_runs(state, args.runs)
        return 0

    _print_progress(state)
    return 0
