"""Kiểu dữ liệu cho một chai nước hoa."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

# Sản phẩm của một hãng, lấy từ trang hãng (#brands) — mỗi dòng là một chai
# trong một collection.
BRAND_PERFUME_CSV_COLUMNS = [
    "brand_name", "brand_url", "collection", "collection_anchor",
    "perfume_id", "perfume_name", "perfume_url", "year", "gender",
    "comments", "scraped_at",
]

# Danh mục hãng (crawl từ /designers/) — khác hẳn bản ghi một chai nước hoa.
BRAND_CSV_COLUMNS = [
    "brand_name", "brand_url", "alphabet", "popular_rank", "scraped_at",
]

CSV_COLUMNS = [
    "perfume_id", "name", "brand", "gender", "year", "fragrance_family",
    "rating", "rating_count", "accords", "accords_detail",
    "top_notes", "middle_notes", "base_notes", "general_notes",
    "perfumers", "when_to_wear", "longevity", "sillage", "description", "image",
    "url", "des_url", "scraped_at",
]


@dataclass
class Accord:
    """Một accord (nhóm mùi chính) kèm chỉ số độ mạnh.

    width/opacity đều quy về thang 0-100 để so sánh được giữa các chai,
    bất kể trang trả về '89.04%' (HTML gốc) hay '0.890415' (bản browser render).
    """
    name: str
    width: float | None = None     # % độ dài thanh bar = độ mạnh accord
    opacity: float | None = None   # % độ đậm màu, đi kèm width
    color: str | None = None       # mã hex, vd '#ad7727'


@dataclass
class WearVote:
    """Một cột/thanh bar do người dùng vote.

    Dùng chung cho "when to wear" (mùa, ngày/đêm), độ lưu hương và độ toả hương
    vì cấu trúc giống nhau: nhãn + % + số vote.

    `votes` là số quy đổi từ nhãn rút gọn trên trang ('6.3k' -> 6300) nên chỉ
    gần đúng; `votes_label` giữ nguyên chuỗi gốc.
    """
    name: str                      # winter/spring/summer/fall/day/night
    percent: float | None = None   # % so với cột cao nhất (0-100)
    votes: int | None = None
    votes_label: str | None = None


@dataclass
class Perfume:
    url: str
    # URL đích do người dùng cung cấp (vd: trang sản phẩm bên mình), chỉ gắn kèm
    # vào kết quả để đối chiếu — crawler không truy cập link này.
    des_url: str | None = None
    perfume_id: str | None = None
    name: str | None = None
    brand: str | None = None
    gender: str | None = None         # Nam / Nữ / Unisex
    year: int | None = None
    fragrance_family: str | None = None   # vd: "Aromatic Fougere"
    rating: float | None = None
    rating_count: int | None = None
    accords: list[Accord] = field(default_factory=list)
    top_notes: list[str] = field(default_factory=list)
    middle_notes: list[str] = field(default_factory=list)
    base_notes: list[str] = field(default_factory=list)
    general_notes: list[str] = field(default_factory=list)  # khi không chia tầng
    perfumers: list[str] = field(default_factory=list)
    # Chỉ có dữ liệu khi crawl bằng BrowserFetcher (--render).
    when_to_wear: list[WearVote] = field(default_factory=list)
    longevity: str | None = None      # title độ lưu hương được vote nhiều nhất
    sillage: str | None = None        # title độ toả hương được vote nhiều nhất
    description: str | None = None
    image: str | None = None
    scraped_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_flat_dict(self) -> dict[str, Any]:
        """Bản phẳng dùng để ghi CSV.

        `accords` giữ tên thuần (dễ lọc), `accords_detail` kèm chỉ số
        dạng 'tobacco:100|89.04' = 'tên:width|opacity'.
        """
        d = self.to_dict()
        accords = d["accords"]
        d["accords"] = "; ".join(a["name"] for a in accords)
        d["accords_detail"] = "; ".join(
            f"{a['name']}:{a['width']}|{a['opacity']}" for a in accords)
        d["when_to_wear"] = "; ".join(
            f"{w['name']}:{w['percent']}%|{w['votes_label']}"
            for w in d["when_to_wear"])
        for key in ("top_notes", "middle_notes", "base_notes",
                    "general_notes", "perfumers"):
            d[key] = "; ".join(d[key])
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Perfume":
        """Dựng lại Perfume từ một dòng JSONL.

        Bỏ qua khoá lạ để file cũ (crawl bằng bản trước, thiếu/thừa trường) vẫn
        đọc được thay vì văng TypeError.
        """
        data = {k: v for k, v in raw.items() if k in _FIELDS}
        data["accords"] = [Accord(**a) for a in data.get("accords") or []]
        data["when_to_wear"] = [WearVote(**w)
                                for w in data.get("when_to_wear") or []]
        return cls(**data)


_FIELDS = {f.name for f in fields(Perfume)}


@dataclass
class Brand:
    """Một hãng trong danh mục /designers/ — không kèm chai nào.

    `alphabet` là chữ cái mà hãng được xếp vào trong mục lục A-Z (None nếu chỉ
    thấy hãng này ở khối "Most Popular Brands" mà chưa gặp trong mục lục).

    `popular_rank` là thứ hạng trong khối "Most Popular Brands" ở footer —
    chính nó là một tín hiệu cộng đồng, nên giữ lại thay vì chỉ đánh dấu
    có/không.
    """

    brand_url: str
    brand_name: str | None = None
    alphabet: str | None = None
    popular_rank: int | None = None
    scraped_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_flat_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Brand":
        return cls(**{k: v for k, v in raw.items() if k in _BRAND_FIELDS})


_BRAND_FIELDS = {f.name for f in fields(Brand)}


@dataclass
class BrandPerfume:
    """Một chai trong danh sách sản phẩm của hãng.

    Đây là bản ghi MỤC LỤC, không phải dữ liệu đầy đủ của chai: nó nói chai nào
    thuộc hãng nào, nằm trong collection nào. Muốn tháp hương / accord / vote
    thì lấy `perfume_url` rồi chạy lệnh `crawl`.

    `collection` để trống nghĩa là chai không thuộc collection nào có tên —
    trên trang chúng nằm dưới mục "All Fragrances".
    """

    perfume_url: str
    perfume_id: str | None = None
    perfume_name: str | None = None
    brand_name: str | None = None
    brand_url: str | None = None
    collection: str | None = None
    collection_anchor: str | None = None
    year: int | None = None
    gender: str | None = None          # Nam / Nữ / Unisex
    comments: int | None = None        # số bình luận hiện trên thẻ
    scraped_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_flat_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BrandPerfume":
        return cls(**{k: v for k, v in raw.items() if k in _BRAND_PERFUME_FIELDS})


_BRAND_PERFUME_FIELDS = {f.name for f in fields(BrandPerfume)}
