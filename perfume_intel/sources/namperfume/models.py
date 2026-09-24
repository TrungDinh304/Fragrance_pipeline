"""Kiểu dữ liệu cho một sản phẩm namperfume.net."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

CSV_COLUMNS = [
    "sku", "name", "brand", "gioi_tinh", "xuat_xu", "nong_do", "nhom_huong",
    "phong_cach", "standard_size", "nam_phat_hanh", "price",
    "url", "des_url", "scraped_at",
]


@dataclass
class NamProduct:
    url: str
    # URL đích lấy từ cột des_url của file CSV đầu vào; chỉ gắn kèm, không crawl.
    des_url: str | None = None
    name: str | None = None
    brand: str | None = None
    sku: str | None = None
    gioi_tinh: str | None = None          # Nam / Nữ / Unisex
    xuat_xu: str | None = None            # Xuất xứ
    nong_do: str | None = None            # Nồng độ, vd "Eau de Parfum"
    nhom_huong: str | None = None         # Nhóm hương
    phong_cach: str | None = None         # Phong cách
    nam_phat_hanh: str | None = None      # Năm phát hành
    standard_size: list[str] = field(default_factory=list)   # vd ["90ml", "50ml"]
    price: str | None = None              # giá của variant đang chọn
    scraped_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_flat_dict(self) -> dict[str, Any]:
        d = self.to_dict()
        d["standard_size"] = "; ".join(d["standard_size"])
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "NamProduct":
        """Dựng lại NamProduct từ một dòng JSONL, bỏ qua khoá lạ."""
        return cls(**{k: v for k, v in raw.items() if k in _FIELDS})


_FIELDS = {f.name for f in fields(NamProduct)}
