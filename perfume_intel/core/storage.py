"""Đọc/ghi JSONL & CSV — không biết gì về cấu trúc bản ghi của từng site.

Mọi bản ghi chỉ cần có `to_dict()` / `to_flat_dict()` và một `from_dict()`
dạng classmethod là dùng được ở đây.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)


def save_jsonl(records: list, path: Path, append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    log.info("Đã ghi %d bản ghi -> %s", len(records), path)


def save_csv(records: list, path: Path, columns: list[str]) -> None:
    """Ghi CSV theo đúng thứ tự cột `columns`; khoá thừa trong bản ghi bị bỏ qua."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_flat_dict())
    log.info("Đã ghi %d bản ghi -> %s", len(records), path)


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Sinh từng dict trong file JSONL, bỏ qua dòng hỏng."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def iter_jsonl_files(path: Path) -> list[Path]:
    """1 file -> [file]; 1 thư mục -> mọi .jsonl bên trong (kể cả thư mục con)."""
    if path.is_dir():
        return sorted(p for p in path.rglob("*.jsonl") if p.is_file())
    return [path]


def load_records(path: Path, record_cls) -> list:
    """Đọc JSONL thành list dataclass của site tương ứng."""
    return [record_cls.from_dict(raw) for raw in read_jsonl(path)]


def load_scraped_urls(path: Path) -> set[str]:
    """URL đã có trong file kết quả — dùng để `--resume` không crawl lại."""
    return {url for url in (raw.get("url") for raw in read_jsonl(path)) if url}
