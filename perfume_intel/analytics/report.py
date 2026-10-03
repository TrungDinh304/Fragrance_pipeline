"""Chạy các chỉ số rồi ghi ra `data/processed/`.

Ba thứ ra cùng lúc, vì ba kiểu dùng khác nhau:

  - mỗi chỉ số một file CSV — mở bằng Excel, đổ vào chỗ khác;
  - `summary.json` — số tổng quan, dễ cho máy đọc;
  - `report.html` — sáu hình để NGƯỜI đọc, tự chứa, không cần mạng.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from .. import config
from . import charts, dataset, html_report
from .dataset import Row
from .metrics import METRICS, coverage

log = logging.getLogger(__name__)


def _write_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(records[0]) if records else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def overview(rows: list[Row]) -> dict[str, Any]:
    """Vài con số nói ngay được tập dữ liệu đang có gì."""
    rated = [r for r in rows if r.rating_count]
    return {
        "perfumes": len(rows),
        "brands": len({r.brand for r in rows if r.brand}),
        "with_rating": len(rated),
        "total_rating_votes": sum(r.rating_count or 0 for r in rated),
        "with_season_votes": sum(1 for r in rows if r.seasons),
        "with_performance": sum(1 for r in rows if r.longevity or r.sillage),
        # Chưa crawl được own/had/want; số này để theo dõi khi parser bổ sung xong.
        "with_ownership": sum(1 for r in rows if r.have_it is not None),
    }


def write_html(rows: list[Row], out_dir: Path, source: str = "",
               catalog=None) -> Path:
    """Sinh `report.html`.

    Tách riêng khỏi `run` để test gọi được mà không phải ghi cả bộ CSV.
    """
    figures = charts.build_all(rows)
    page = html_report.render(figures, charts.headline(rows, catalog),
                              source=source)
    path = out_dir / "report.html"
    path.write_text(page, encoding="utf-8")
    drawn = sum(1 for f in figures if not f.empty)
    log.info("Báo cáo HTML  %d/%d hình -> %s", drawn, len(figures), path)
    for figure in figures:
        if figure.empty:
            log.warning("Hình %r để trống: %s", figure.key,
                        figure.empty.splitlines()[0])
    return path


def run(rows: list[Row], out_dir: Path | None = None,
        only: list[str] | None = None, html: bool = True,
        community: Path | None = None) -> Path:
    """Tính mọi chỉ số (hoặc chỉ những cái trong `only`) và ghi ra thư mục."""
    out_dir = Path(out_dir or config.PROCESSED_DIR / date.today().strftime("%Y%m%d"))
    out_dir.mkdir(parents=True, exist_ok=True)

    names = only or list(METRICS)
    unknown = [n for n in names if n not in METRICS]
    if unknown:
        raise ValueError(f"Chỉ số không có: {', '.join(unknown)}. "
                         f"Hiện có: {', '.join(METRICS)}.")

    catalog = dataset.load_catalog(community) if community else dataset.Catalog()

    summary: dict[str, Any] = {"overview": overview(rows), "metrics": {}}
    if not catalog.empty:
        rowset = coverage(rows, catalog)
        _write_csv(rowset, out_dir / "coverage.csv")
        summary["metrics"]["coverage"] = len(rowset)
        summary["overview"]["catalog_brands"] = len(catalog.brands)
        summary["overview"]["catalog_perfumes"] = sum(
            len(v) for v in catalog.products.values())
        log.info("%-12s %4d dòng -> %s", "coverage", len(rowset),
                 out_dir / "coverage.csv")
    for name in names:
        records = METRICS[name](rows)
        _write_csv(records, out_dir / f"{name}.csv")
        summary["metrics"][name] = len(records)
        log.info("%-12s %4d dòng -> %s", name, len(records), out_dir / f"{name}.csv")

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Tổng quan -> %s", out_dir / "summary.json")

    if html:
        write_html(rows, out_dir, catalog=catalog)
    return out_dir
