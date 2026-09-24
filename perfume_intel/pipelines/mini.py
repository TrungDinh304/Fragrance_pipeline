"""Ghép dữ liệu đã crawl với danh sách bản mini.

Không truy cập mạng: quét các file .jsonl đã crawl (vd `data/raw/fragrantica/`),
tìm bản ghi có `url` trùng `source_url` trong file CSV rồi ghi lại y nguyên
thông tin sản phẩm nhưng thay `des_url` bằng link bản mini trong CSV.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core import storage
from ..core.text import url_key

log = logging.getLogger(__name__)


def index_records(source: Path) -> dict[str, dict]:
    """Quét file/thư mục JSONL -> map url đã chuẩn hoá -> bản ghi.

    Mỗi hãng thường có nhiều file theo ngày (`Chanel_fragrantica_150826.jsonl`,
    `..._170826.jsonl`), nên khi trùng URL thì giữ bản mới nhất theo
    `scraped_at`, hoà thì theo thời điểm sửa file.
    """
    files = storage.iter_jsonl_files(source)
    index: dict[str, dict] = {}
    stamps: dict[str, tuple[str, float]] = {}

    for path in files:
        mtime = path.stat().st_mtime
        for raw in storage.read_jsonl(path):
            key = url_key(raw.get("url"))
            if not key:
                continue
            stamp = (raw.get("scraped_at") or "", mtime)
            if key not in index or stamp > stamps[key]:
                index[key], stamps[key] = raw, stamp

    log.info("Quét %d file JSONL trong %s -> %d sản phẩm.",
             len(files), source, len(index))
    return index


def collect(pairs: list[tuple[str, str | None]], index: dict[str, dict],
            ) -> tuple[list[dict], list[str]]:
    """Ghép từng dòng CSV với bản ghi đã crawl.

    Trả về (danh sách bản ghi đã gắn `des_url` bản mini, danh sách URL nguồn
    không tìm thấy trong dữ liệu đã crawl). Giữ thứ tự như trong CSV; hai dòng
    trùng cả nguồn lẫn đích chỉ lấy một.
    """
    records: list[dict] = []
    missing: list[str] = []
    seen: set[tuple[str, str | None]] = set()

    for source, dest in pairs:
        key = url_key(source)
        raw = index.get(key)
        if raw is None:
            missing.append(source)
            continue
        if (key, dest) in seen:
            continue
        seen.add((key, dest))
        records.append({**raw, "des_url": dest})

    return records, missing
