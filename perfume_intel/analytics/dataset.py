"""Nạp dữ liệu đã crawl thành một tập phẳng để phân tích.

Đầu vào là các file .jsonl trong `data/raw/<site>/`, mỗi hãng một file theo
ngày. Ở đây chỉ làm ba việc, không tính toán gì:

  1. gom mọi file lại, cùng một URL thì giữ bản crawl mới nhất;
  2. ghép bản ghi Fragrantica với bản ghi namperfume tương ứng (qua `des_url`);
  3. làm phẳng các trường lồng nhau (accords, when_to_wear) thành cột.

Mọi chỉ số đều tính từ `Row` ở `metrics.py`, nên thêm tín hiệu cộng đồng mới
chỉ cần thêm trường vào đây.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .. import config
from ..core import storage
from ..core.text import url_key

log = logging.getLogger(__name__)

SEASONS = ("winter", "spring", "summer", "fall")
DAY_NIGHT = ("day", "night")

# Tín hiệu nhu cầu của cộng đồng Fragrantica (I have it / I had it / I want it).
# Parser hiện chưa lấy được các số này — xem chú thích ở `Row.demand_*`.
OWNERSHIP_FIELDS = ("have_it", "had_it", "want_it")


@dataclass
class Row:
    """Một chai nước hoa cùng toàn bộ tín hiệu cộng đồng của nó."""

    url: str
    name: str | None = None
    brand: str | None = None
    gender: str | None = None
    year: int | None = None
    fragrance_family: str | None = None

    # --- tín hiệu cộng đồng đã có ---
    rating: float | None = None
    rating_count: int | None = None
    accords: dict[str, float] = field(default_factory=dict)   # tên -> width 0-100
    seasons: dict[str, float] = field(default_factory=dict)   # mùa -> % vote
    day_night: dict[str, float] = field(default_factory=dict)
    longevity: str | None = None
    sillage: str | None = None
    notes: list[str] = field(default_factory=list)

    # --- tín hiệu nhu cầu: CHƯA CÓ DỮ LIỆU ---
    # Fragrantica hiển thị số người "have it / had it / want it" nhưng khối này
    # render sau khi đăng nhập nên HTML đã lưu không có. Muốn dùng phải bổ sung
    # parser ở sources/fragrantica/parsers.py rồi crawl lại; tới lúc đó chỉ cần
    # đổ số vào đây, phần metrics phía sau không phải sửa.
    have_it: int | None = None
    had_it: int | None = None
    want_it: int | None = None

    # --- đối chiếu thị trường VN (namperfume) ---
    des_url: str | None = None
    listed: bool = False               # có bán trên namperfume không
    price: str | None = None
    sizes: list[str] = field(default_factory=list)
    scraped_at: str | None = None

    @property
    def top_accord(self) -> str | None:
        return max(self.accords, key=self.accords.get) if self.accords else None

    @property
    def top_season(self) -> str | None:
        return max(self.seasons, key=self.seasons.get) if self.seasons else None


def _latest_by_url(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Gom nhiều file JSONL -> map url chuẩn hoá -> bản ghi mới nhất.

    Cùng một hãng thường có nhiều file theo ngày; bản mới nhất tính theo
    `scraped_at`, hoà thì theo thời điểm sửa file.
    """
    index: dict[str, dict[str, Any]] = {}
    stamps: dict[str, tuple[str, float]] = {}

    for path in paths:
        mtime = path.stat().st_mtime
        for raw in storage.read_jsonl(path):
            key = url_key(raw.get("url"))
            if not key:
                continue
            stamp = (raw.get("scraped_at") or "", mtime)
            if key not in index or stamp > stamps[key]:
                index[key], stamps[key] = raw, stamp
    return index


def load_raw(source: Path) -> dict[str, dict[str, Any]]:
    """Đọc một file/thư mục .jsonl -> map url -> bản ghi mới nhất."""
    files = storage.iter_jsonl_files(source)
    index = _latest_by_url(files)
    log.info("Quét %d file JSONL trong %s -> %d bản ghi.",
             len(files), source, len(index))
    return index


def _to_row(raw: dict[str, Any]) -> Row:
    accords = {a["name"]: a.get("width") or 0.0
               for a in raw.get("accords") or [] if a.get("name")}
    votes = {w["name"].lower(): w.get("percent") or 0.0
             for w in raw.get("when_to_wear") or [] if w.get("name")}
    notes = [n for key in ("top_notes", "middle_notes", "base_notes",
                           "general_notes")
             for n in raw.get(key) or []]

    return Row(
        url=raw.get("url") or "",
        name=raw.get("name"),
        brand=raw.get("brand"),
        gender=raw.get("gender"),
        year=raw.get("year"),
        fragrance_family=raw.get("fragrance_family"),
        rating=raw.get("rating"),
        rating_count=raw.get("rating_count"),
        accords=accords,
        seasons={s: votes[s] for s in SEASONS if s in votes},
        day_night={d: votes[d] for d in DAY_NIGHT if d in votes},
        longevity=raw.get("longevity"),
        sillage=raw.get("sillage"),
        notes=notes,
        have_it=raw.get("have_it"),
        had_it=raw.get("had_it"),
        want_it=raw.get("want_it"),
        des_url=raw.get("des_url"),
        scraped_at=raw.get("scraped_at"),
    )


def _attach_market(row: Row, market: dict[str, dict[str, Any]]) -> Row:
    """Gắn giá/size từ namperfume vào chai tương ứng, nếu có."""
    product = market.get(url_key(row.des_url))
    if product is None:
        return row
    row.listed = True
    row.price = product.get("price")
    row.sizes = list(product.get("standard_size") or [])
    return row


def build(community: Path | None = None, market: Path | None = None) -> list[Row]:
    """Dựng tập dữ liệu phân tích.

    `community` là thư mục .jsonl Fragrantica (mặc định `data/raw/fragrantica`),
    `market` là thư mục .jsonl namperfume — để trống thì bỏ qua phần đối chiếu
    giá, mọi `Row.listed` sẽ là False.
    """
    community = community or config.raw_dir("fragrantica")
    rows = [_to_row(raw) for raw in load_raw(community).values()]

    if market is not None and Path(market).exists():
        products = load_raw(Path(market))
        rows = [_attach_market(r, products) for r in rows]
        log.info("Đối chiếu namperfume: %d/%d chai có bán.",
                 sum(1 for r in rows if r.listed), len(rows))

    log.info("Tập phân tích: %d chai, %d hãng.",
             len(rows), len({r.brand for r in rows if r.brand}))
    return rows
