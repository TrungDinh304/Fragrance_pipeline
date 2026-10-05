"""Bố cục kho thô: mỗi LOẠI bản ghi một thư mục riêng.

Vấn đề có thật mà module này sinh ra để giải:

    data/raw/fragrantica/ đang chứa lẫn lộn BA loại bản ghi khác hẳn nhau —
    chi tiết chai, danh mục hãng, mục lục chai của từng hãng. Đo trên kho hiện
    tại: 25.465 dòng, trong đó 16.464 dòng danh mục hãng và 8.212 dòng mục lục.
    `dataset` chỉ quan tâm chi tiết chai nên khoá theo trường `url`; hai loại
    kia không có trường đó nên bị bỏ — 24.676/25.465 dòng, tức 97%, biến mất
    mà KHÔNG một dòng log nào.

    Không phải mất dữ liệu (file vẫn nguyên), nhưng là mất tín hiệu: số chai mà
    một hãng thật sự có nằm đúng trong mấy dòng bị bỏ đó. Thiếu nó thì không
    trả lời được câu "phân tích này đang dựa trên bao nhiêu phần trăm của hãng".

Hai lớp bảo vệ, cố ý làm cả hai:

  1. **Tách thư mục** — mỗi loại một chỗ, nên đọc một loại thì không nhìn thấy
     loại khác.
  2. **Phân loại theo HÌNH DẠNG bản ghi, và ĐẾM phần bị bỏ** — vì tách thư mục
     chỉ đúng khi mọi thứ đã được xếp đúng chỗ. Phân loại theo hình dạng vẫn
     chạy đúng trên kho cũ chưa dọn, và quan trọng hơn: nó BÁO RA khi bỏ thứ
     gì, thay vì im lặng như trước.

Nhờ lớp 2, dọn kho là việc TUỲ CHỌN: file phẳng kiểu cũ nằm ở thư mục gốc vẫn
đọc được bình thường. Xem `scripts/migrate_bronze.py` khi muốn dọn.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import lake, storage

log = logging.getLogger(__name__)

# Tên thư mục con, trùng tên lệnh CLI sinh ra chúng: `crawl` -> perfumes,
# `brands` -> brands, `products` -> products.
PERFUME = "perfumes"
BRAND = "brands"
BRAND_PERFUME = "products"
KINDS = (PERFUME, BRAND, BRAND_PERFUME)

VI = {PERFUME: "chi tiết chai", BRAND: "danh mục hãng",
      BRAND_PERFUME: "mục lục chai của hãng"}


def classify(record: Mapping[str, Any]) -> str | None:
    """Loại của một bản ghi, suy từ khoá chính của nó.

    THỨ TỰ KIỂM TRA LÀ QUAN TRỌNG: bản ghi mục lục có CẢ `perfume_url` lẫn
    `brand_url`, nên phải hỏi `perfume_url` trước, nếu không nó bị xếp nhầm
    thành danh mục hãng.

    Ba trường này cũng chính là khoá chính của ba model (`Perfume.url`,
    `Brand.brand_url`, `BrandPerfume.perfume_url`), nên bản ghi nào thiếu cả ba
    là bản ghi hỏng chứ không phải loại thứ tư.
    """
    if record.get("perfume_url"):
        return BRAND_PERFUME
    if record.get("brand_url"):
        return BRAND
    if record.get("url"):
        return PERFUME
    return None


def dir_for(root: Path, kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"Loại không có: {kind!r}. Chỉ có: {', '.join(KINDS)}.")
    return Path(root) / kind


def iter_files(root: Path, kind: str) -> list[Path]:
    """File có thể chứa bản ghi loại này.

    Gồm thư mục riêng của loại (đệ quy) CỘNG các file .jsonl nằm phẳng ngay ở
    thư mục gốc — đó là bố cục cũ, còn nguyên giá trị. Cố ý KHÔNG quét đệ quy
    toàn bộ gốc: làm vậy sẽ nuốt luôn thư mục của hai loại kia và quay về đúng
    mớ lẫn lộn mà module này sinh ra để dọn.
    """
    root = Path(root)
    if not root.exists():
        return []
    if root.is_file():
        return [root]

    files = list(dir_for(root, kind).rglob("*.jsonl")) if dir_for(root, kind).is_dir() else []
    legacy = [p for p in root.glob("*.jsonl") if p.is_file()]
    # Thư mục con KHÔNG phải của ba loại đã biết (vd thư mục lưu trữ cũ) vẫn
    # được quét, để dữ liệu dọn tay vào đó không bị bỏ quên.
    for child in root.iterdir():
        if child.is_dir() and child.name not in KINDS:
            legacy += [p for p in child.rglob("*.jsonl") if p.is_file()]
    return sorted(set(files) | set(legacy))


@dataclass
class Scan:
    """Kết quả một lần quét, kèm thống kê phần bị bỏ."""
    kind: str
    records: list[dict[str, Any]] = field(default_factory=list)
    files: int = 0
    other: Counter = field(default_factory=Counter)   # loại khác -> số dòng
    unknown: int = 0                                  # không nhận ra hình dạng

    @property
    def skipped(self) -> int:
        return sum(self.other.values()) + self.unknown


def scan(root: Path, kind: str) -> Scan:
    """Đọc mọi bản ghi thuộc `kind` dưới `root`, đếm những gì đã bỏ qua.

    Đây là chỗ DUY NHẤT kéo dữ liệu từ lake về, đúng như luật trong
    `docs/ARCHITECTURE.md` ("chỉ `core/bronze.py` đọc kho thô"). Khi lake tắt,
    hoặc khi `root` nằm ngoài `data/raw/` (mọi test đều vậy), đây là một hàm rỗng.
    """
    lake.ensure_local(root)
    result = Scan(kind=kind)
    for path in iter_files(root, kind):
        result.files += 1
        for record in storage.read_jsonl(path):
            found = classify(record)
            if found == kind:
                result.records.append(record)
            elif found is None:
                result.unknown += 1
            else:
                result.other[found] += 1
    return result


def log_scan(result: Scan, where: Path | str = "") -> None:
    """In kết quả quét. Phần BỊ BỎ cũng được nói ra, đó là cả ý nghĩa của nó."""
    log.info("Quét %d file%s -> %d bản ghi %s.",
             result.files, f" trong {where}" if where else "",
             len(result.records), VI.get(result.kind, result.kind))
    if result.other:
        detail = ", ".join(f"{n} {VI.get(k, k)}" for k, n in
                           sorted(result.other.items()))
        log.info("  (bỏ qua %d dòng thuộc loại khác: %s)",
                 sum(result.other.values()), detail)
    if result.unknown:
        log.warning("  %d dòng không có khoá chính nào (url / brand_url / "
                    "perfume_url) — bản ghi hỏng, không phải loại mới.",
                    result.unknown)


def available(source: Path) -> bool:
    """Có dữ liệu để đọc ở `source` không — hỏi CẢ lake, không chỉ ổ đĩa này.

    VÌ SAO KHÔNG DÙNG THẲNG `Path.exists()`
    Đã dính thật khi thử trên một máy sạch: mọi lệnh đọc đều kiểm
    `community.exists()` TRƯỚC khi tới `scan()`, nên trên container vừa dựng nó
    báo "Chưa có dữ liệu" rồi thoát — trong khi 125 file đang nằm nguyên trên
    MinIO. Không một dòng lỗi nào sai, chỉ là trả lời câu khác.

    Nhận cả thư mục lẫn file. File (vd `brands_fragrantica_021026.jsonl` mà
    `make products` cần) thì đồng bộ thư mục chứa nó, vì lake làm việc theo
    prefix chứ không theo từng object.
    """
    source = Path(source)
    thu_muc = source if (source.is_dir() or not source.suffix) else source.parent
    lake.ensure_local(thu_muc)
    return source.exists()


def read(root: Path, kind: str, quiet: bool = False) -> list[dict[str, Any]]:
    result = scan(root, kind)
    if not quiet:
        log_scan(result, root)
    return result.records


def split(records: Iterable[Mapping[str, Any]]) -> dict[str, list[dict]]:
    """Chia một đống bản ghi lẫn lộn theo loại. Dùng khi dọn kho."""
    out: dict[str, list[dict]] = {k: [] for k in KINDS}
    out["?"] = []
    for record in records:
        out[classify(record) or "?"].append(dict(record))
    return out
