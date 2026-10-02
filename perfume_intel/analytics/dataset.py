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
from ..core import bronze, storage
from ..core.text import url_key

log = logging.getLogger(__name__)

SEASONS = ("winter", "spring", "summer", "fall")
DAY_NIGHT = ("day", "night")

# Tháp hương, theo thứ tự trang hiển thị. `general` là khi trang không chia tầng
# (8% số chai) — coi như tầng giữa khi cần đánh trọng số.
NOTE_LAYERS = ("top", "middle", "base", "general")

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
    # Cùng dữ liệu với `notes` nhưng GIỮ TẦNG, vì tầng mang thông tin thật: note
    # base quyết định dư hương sau vài giờ, note top bay trong mươi phút. Hai
    # chai trùng base giống nhau hơn nhiều so với hai chai trùng top, nên
    # `vectors/features.py` đánh trọng số khác nhau cho từng tầng.
    notes_by_layer: dict[str, list[str]] = field(default_factory=dict)

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


def _latest_of(records: Iterable[dict[str, Any]],
               key_field: str = "url") -> dict[str, dict[str, Any]]:
    """Khử trùng theo khoá URL, giữ bản crawl mới nhất.

    Cùng một chai thường xuất hiện ở nhiều file theo ngày; bản mới nhất tính
    theo `scraped_at`. Thứ tự file do `bronze.iter_files` sắp xếp nên hoà thì
    bản đọc sau thắng — đủ ổn định để chạy lại cho cùng kết quả.
    """
    index: dict[str, dict[str, Any]] = {}
    stamps: dict[str, str] = {}
    for raw in records:
        key = url_key(raw.get(key_field))
        if not key:
            continue
        stamp = raw.get("scraped_at") or ""
        if key not in index or stamp >= stamps[key]:
            index[key], stamps[key] = raw, stamp
    return index


def load_raw(source: Path) -> dict[str, dict[str, Any]]:
    """Đọc chi tiết chai -> map url -> bản ghi mới nhất.

    Chỉ lấy bản ghi LOẠI CHI TIẾT CHAI. Trước đây hàm này đọc mọi file rồi khoá
    theo `url`; danh mục hãng và mục lục chai không có trường đó nên rơi hết —
    24.676/25.465 dòng, 97%, không một dòng log. Giờ việc lọc là hiển ngôn và
    `bronze.log_scan` nói rõ đã bỏ bao nhiêu dòng thuộc loại nào.
    """
    result = bronze.scan(source, bronze.PERFUME)
    bronze.log_scan(result, source)
    index = _latest_of(result.records)
    if len(index) != len(result.records):
        log.info("  (%d bản ghi là bản crawl lại của cùng một chai)",
                 len(result.records) - len(index))
    return index


def _to_row(raw: dict[str, Any]) -> Row:
    accords = {a["name"]: a.get("width") or 0.0
               for a in raw.get("accords") or [] if a.get("name")}
    votes = {w["name"].lower(): w.get("percent") or 0.0
             for w in raw.get("when_to_wear") or [] if w.get("name")}
    notes_by_layer = {layer: list(raw.get(f"{layer}_notes") or [])
                      for layer in NOTE_LAYERS}
    notes_by_layer = {k: v for k, v in notes_by_layer.items() if v}
    notes = [n for layer in NOTE_LAYERS for n in notes_by_layer.get(layer, ())]

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
        notes_by_layer=notes_by_layer,
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


# ------------------------------------------------------------------- danh mục
@dataclass
class Catalog:
    """Danh mục hãng và mục lục chai — phần dữ liệu trước đây bị bỏ im lặng.

    Đây là thứ trả lời được câu mà `list[Row]` một mình không trả lời nổi:
    *phân tích này đang dựa trên bao nhiêu phần trăm của hãng?* Một hãng có 1.379
    chai mà mới crawl 12 thì mọi kết luận về hãng đó chỉ là về 12 chai ấy.
    """

    brands: dict[str, dict[str, Any]] = field(default_factory=dict)
    # brand_url chuẩn hoá -> danh sách chai trong mục lục của hãng đó
    products: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _name_cache: dict[str, str] | None = field(default=None, repr=False,
                                               compare=False)

    @property
    def empty(self) -> bool:
        return not self.brands and not self.products

    def total_for(self, brand_url: str | None) -> int:
        """Số chai hãng này thật sự có, theo mục lục đã crawl."""
        return len(self.products.get(url_key(brand_url) or "", ()))

    def url_of(self, brand_name: str | None) -> str | None:
        """Tên hãng -> brand_url. Tên là thứ duy nhất `Row` mang theo."""
        if not brand_name:
            return None
        return self._by_name().get(brand_name.strip().lower())

    def _by_name(self) -> dict[str, str]:
        """Bảng tra tên hãng -> khoá brand_url, dựng một lần rồi giữ lại.

        Khoá lấy từ CHÍNH trường `brand_url` của bản ghi, không lấy khoá của
        dict ngoài. Hai thứ đó trùng nhau khi `load_catalog` dựng ra, nhưng dựa
        vào sự trùng đó là buộc mọi phía gọi phải khoá dict y hệt — và khi ai đó
        dựng `Catalog` bằng tay thì nó trả về sai mà không báo gì.
        """
        if self._name_cache is None:
            cache: dict[str, str] = {}
            for key, record in self.brands.items():
                name = (record.get("brand_name") or "").strip().lower()
                if name:
                    cache.setdefault(name, url_key(record.get("brand_url")) or key)
            # Mục lục cũng mang tên hãng, dùng nốt cho hãng chưa có trong danh mục.
            for key, items in self.products.items():
                for item in items:
                    name = (item.get("brand_name") or "").strip().lower()
                    if name:
                        cache.setdefault(name,
                                         url_key(item.get("brand_url")) or key)
                    break
            self._name_cache = cache
        return self._name_cache


def load_catalog(source: Path | None = None) -> Catalog:
    """Đọc danh mục hãng + mục lục chai từ kho thô."""
    source = source or config.raw_dir("fragrantica")
    brands_scan = bronze.scan(source, bronze.BRAND)
    products_scan = bronze.scan(source, bronze.BRAND_PERFUME)

    brands = _latest_of(brands_scan.records, key_field="brand_url")
    products: dict[str, list[dict[str, Any]]] = {}
    for record in _latest_of(products_scan.records,
                             key_field="perfume_url").values():
        key = url_key(record.get("brand_url"))
        if key:
            products.setdefault(key, []).append(record)

    log.info("Danh mục: %d hãng, %d chai trong mục lục của %d hãng.",
             len(brands), sum(len(v) for v in products.values()), len(products))
    return Catalog(brands=brands, products=products)
