"""Chuẩn hoá giá trị dùng chung cho mọi nguồn dữ liệu."""

from __future__ import annotations

import re
from datetime import date

# Fragrantica trả tiếng Anh ("men", "women and men"), namperfume trả sẵn tiếng
# Việt trong data-gender. Gom cả hai về cùng một bộ giá trị.
GENDER_MAP = {
    "men": "Nam",
    "man": "Nam",
    "male": "Nam",
    "nam": "Nam",
    "women": "Nữ",
    "woman": "Nữ",
    "female": "Nữ",
    "nu": "Nữ",
    "nữ": "Nữ",
    "women and men": "Unisex",
    "men and women": "Unisex",
    "unisex": "Unisex",
}


def normalize_gender(value: str | None) -> str | None:
    """'men' -> 'Nam', 'women' -> 'Nữ', 'women and men' -> 'Unisex'.

    Giá trị lạ được giữ nguyên văn (thay vì bỏ trống) để còn nhìn thấy mà bổ
    sung vào `GENDER_MAP`.
    """
    if not value:
        return None
    key = re.sub(r"\s+", " ", value).strip().lower()
    return GENDER_MAP.get(key, value.strip() or None)


def output_stem(brand: str, site: str, when: date | None = None) -> str:
    """Tên file kết quả: '<Tên Hãng>_<site>_<ddmmyy>'.

    Vd: output_stem("Abercrombie", "fragrantica") -> 'Abercrombie_fragrantica_150826'
    """
    when = when or date.today()
    return f"{brand}_{site}_{when.strftime('%d%m%y')}"


def url_key(url: str | None) -> str:
    """Khoá so khớp URL: bỏ khoảng trắng, phần #..., dấu / cuối và phân biệt hoa thường.

    Cùng một chai nhưng file này ghi `.../Fierce-3508.html`, file kia ghi
    `.../fierce-3508.html/` thì vẫn phải khớp nhau.
    """
    if not url:
        return ""
    return url.strip().split("#")[0].rstrip("/").lower()
