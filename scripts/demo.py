"""Demo: crawl vài chai nước hoa, in tháp hương ra màn hình và xuất file.

Mặc định dùng browser thật (Playwright) để lấy đủ cả 'when to wear', độ lưu
hương và độ toả hương -> chậm hơn. Thêm --no-render nếu chỉ cần dữ liệu cơ bản.

    python scripts/demo.py                            # 3 chai mẫu -> data/demo.*
    python scripts/demo.py <url> [<url> ...]          # chai bạn chọn
    python scripts/demo.py --no-render                # chạy nhanh, bỏ dữ liệu vote
    python scripts/demo.py --out data/bo_suu_tap      # đổi nơi lưu
    python scripts/demo.py --format txt               # chỉ xuất báo cáo text
    python scripts/demo.py --no-save                  # chỉ xem, không ghi file
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Chạy thẳng file này mà không cần cài package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from perfume_intel.core import storage                       # noqa: E402
from perfume_intel.core.http import Fetcher                  # noqa: E402
from perfume_intel.sources.fragrantica.models import CSV_COLUMNS  # noqa: E402
from perfume_intel.sources.fragrantica.scraper import (      # noqa: E402
    FragranticaScraper, PERFORMANCE_READY_JS, WAIT_SELECTOR)

# Console Windows mặc định không phải UTF-8 -> tên note có dấu và ký tự vẽ bar
# sẽ làm chết chương trình nếu không ép encoding.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SAMPLES = [
    "https://www.fragrantica.com/perfume/Burberry/London-for-Men-804.html",
    "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html",
    "https://www.fragrantica.com/perfume/Chanel/Coco-Mademoiselle-611.html",
]

W = 62


def bar(pct: float | None, width: int = 20) -> str:
    if not pct:
        return ""
    filled = max(1, round(pct / 100 * width))
    return "█" * filled + "·" * (width - filled)


def render(p) -> str:
    """Dựng khối báo cáo cho 1 chai (dùng chung cho màn hình và file txt)."""
    out: list[str] = ["", "=" * W, f"  {p.name}  —  {p.brand}"]

    meta = [str(p.year or "?"), p.gender or "?"]
    if p.fragrance_family:
        meta.append(p.fragrance_family)
    if p.rating:
        meta.append(f"{p.rating}/5 ({p.rating_count:,} vote)")
    out.append(f"  {' | '.join(meta)}")
    if p.perfumers:
        out.append(f"  Perfumer: {', '.join(p.perfumers)}")
    out.append(f"  {p.url}")
    out.append("=" * W)

    if p.accords:
        out.append("\n  MAIN ACCORDS")
        for a in p.accords:
            out.append(f"    {a.name:<14} {bar(a.width)} {a.width:>6}%")

    pyramid = [
        ("TOP NOTES    (hương đầu)", p.top_notes),
        ("MIDDLE NOTES (hương giữa)", p.middle_notes),
        ("BASE NOTES   (hương cuối)", p.base_notes),
    ]
    if any(notes for _, notes in pyramid):
        out.append("\n  THÁP HƯƠNG")
        for label, notes in pyramid:
            if not notes:
                continue
            out.append(f"    {label}  [{len(notes)}]")
            out.append(f"      {', '.join(notes)}")
    if p.general_notes:
        out.append(f"\n  NOTES (không chia tầng) [{len(p.general_notes)}]")
        out.append(f"    {', '.join(p.general_notes)}")

    if p.when_to_wear:
        out.append("\n  WHEN TO WEAR")
        for w in p.when_to_wear:
            out.append(f"    {w.name:<8} {bar(w.percent)} "
                       f"{w.percent:>6}%  ({w.votes_label} vote)")

    if p.longevity or p.sillage:
        out.append("\n  ĐÁNH GIÁ")
        out.append(f"    Độ lưu hương : {p.longevity or '-'}")
        out.append(f"    Độ toả hương : {p.sillage or '-'}")

    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Demo crawl Fragrantica: xem tháp hương + xuất file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("urls", nargs="*", help="URL nước hoa (bỏ trống = dùng chai mẫu)")
    p.add_argument("--out", default="data/scratch/demo",
                   help="Đường dẫn file kết quả, không cần đuôi (mặc định: data/scratch/demo)")
    p.add_argument("--format", choices=["txt", "csv", "jsonl", "all"], default="all",
                   help="Định dạng xuất (mặc định: all)")
    p.add_argument("--no-save", action="store_true", help="Chỉ in, không ghi file")
    # Demo mặc định render để có đủ dữ liệu; --no-render để chạy nhanh.
    p.add_argument("--no-render", dest="render", action="store_false",
                   help="Không dùng browser: nhanh hơn nhiều nhưng thiếu "
                        "'when to wear', độ lưu hương và độ toả hương")
    p.add_argument("--render", dest="render", action="store_true",
                   help=argparse.SUPPRESS)      # giữ cho lệnh cũ vẫn chạy
    p.set_defaults(render=True)
    return p


def export(perfumes, report: str, out_base: Path, fmt: str) -> list[Path]:
    written: list[Path] = []
    out_base.parent.mkdir(parents=True, exist_ok=True)

    if fmt in ("txt", "all"):
        path = out_base.with_suffix(".txt")
        path.write_text(report, encoding="utf-8")
        written.append(path)
    if fmt in ("jsonl", "all"):
        path = out_base.with_suffix(".jsonl")
        storage.save_jsonl(perfumes, path)
        written.append(path)
    if fmt in ("csv", "all"):
        path = out_base.with_suffix(".csv")
        storage.save_csv(perfumes, path, columns=CSV_COLUMNS)
        written.append(path)
    return written


def main() -> int:
    args = build_parser().parse_args()
    urls = args.urls or SAMPLES

    if args.render:
        from perfume_intel.core.browser import BrowserFetcher
        fetcher = BrowserFetcher(delay=(1.5, 3.0), scroll=True,
                                 wait_selector=WAIT_SELECTOR,
                                 ready_js=PERFORMANCE_READY_JS)
    else:
        fetcher = Fetcher(delay=(1.5, 3.0))
    scraper = FragranticaScraper(fetcher)
    blocks: list[str] = []
    perfumes = []
    try:
        for url in urls:
            perfume = scraper.scrape_one(url)
            if not perfume:
                print(f"\n  [LỖI] không lấy được: {url}")
                continue
            perfumes.append(perfume)
            block = render(perfume)
            print(block)
            blocks.append(block)
    finally:
        scraper.close()

    print("\n" + "=" * W)
    print(f"  Xong: {len(perfumes)}/{len(urls)} chai.")
    if not perfumes:
        return 1

    if args.no_save:
        return 0

    report = "\n".join(blocks) + f"\n\n{'=' * W}\n  Tổng: {len(perfumes)} chai.\n"
    for path in export(perfumes, report, Path(args.out), args.format):
        print(f"  -> {path}  ({path.stat().st_size:,} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
