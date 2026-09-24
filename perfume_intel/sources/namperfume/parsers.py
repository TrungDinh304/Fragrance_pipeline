"""Bóc tách trang sản phẩm namperfume.net.

Cấu trúc thật:
  - Thuộc tính: <dl class="product-attribute-list__item dl-horizontal">
                  <dt><b>Xuất xứ</b></dt><dd>Pháp</dd></dl>
    (trang lặp lại 2 lần cho bản desktop và mobile -> lấy lần xuất hiện đầu)
  - Nồng độ   : <div class="selected-variant--title">Eau de Parfum 90ml</div>
  - Size      : <div class="product-variant-select clearfix">
                  <div class="product-variant-select-title">Standard Size</div>
                  <ul><li class="product-variant-item" data-variant-title="...">
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ...core.text import normalize_gender

from .models import NamProduct

# "90ml", "10 ml", "1.5ml" — dùng để tách size khỏi nồng độ.
SIZE_RE = re.compile(r"^\d+(?:[.,]\d+)?\s*ml$", re.I)
SIZE_IN_TEXT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*ml\b", re.I)

ATTR_ITEM_SELECTOR = ".product-attribute-list__item"
VARIANT_BLOCK_SELECTOR = ".product-variant-select"
VARIANT_TITLE_SELECTOR = ".product-variant-select-title"
VARIANT_ITEM_SELECTOR = "li.product-variant-item"
STANDARD_SIZE_TITLE = "standard size"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip() or None


def parse_attributes(soup: BeautifulSoup) -> dict[str, str]:
    """Đọc toàn bộ <dl> thuộc tính thành dict {nhãn: giá trị}."""
    attrs: dict[str, str] = {}
    for dl in soup.select(ATTR_ITEM_SELECTOR):
        dt, dd = dl.find("dt"), dl.find("dd")
        if dt is None or dd is None:
            continue
        key = _clean(dt.get_text(" ", strip=True))
        value = _clean(dd.get_text(" ", strip=True))
        if key and value:
            attrs.setdefault(key, value)      # bản desktop đứng trước, giữ nó
    return attrs


def split_variant_title(raw: str | None) -> tuple[str | None, str | None]:
    """Tách "90ml / Eau de Parfum" -> ("90ml", "Eau de Parfum").

    Thứ tự KHÔNG cố định giữa các sản phẩm: có trang ghi "Eau de Parfum/105ml".
    Vì vậy phần nào khớp dạng số + 'ml' thì là size, phần còn lại là nồng độ.
    """
    if not raw:
        return None, None
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    size = next((p for p in parts if SIZE_RE.match(p)), None)
    conc = next((p for p in parts if not SIZE_RE.match(p)), None)
    return size, conc


def _standard_size_block(soup: BeautifulSoup):
    """Khối variant có tiêu đề 'Standard Size'; không có thì lấy khối đầu tiên."""
    blocks = soup.select(VARIANT_BLOCK_SELECTOR)
    for block in blocks:
        title = block.select_one(VARIANT_TITLE_SELECTOR)
        if title and STANDARD_SIZE_TITLE in title.get_text(strip=True).lower():
            return block
    return blocks[0] if blocks else None


def parse_standard_size(soup: BeautifulSoup) -> list[str]:
    block = _standard_size_block(soup)
    if block is None:
        return []

    sizes: list[str] = []
    for li in block.select(VARIANT_ITEM_SELECTOR):
        size, _ = split_variant_title(li.get("data-variant-title"))
        if not size:                       # dự phòng: lấy từ chữ hiển thị
            m = SIZE_IN_TEXT_RE.search(
                li.get("data-title") or li.get_text(" ", strip=True))
            size = m.group(0).replace(" ", "") if m else None
        if size and size not in sizes:
            sizes.append(size)
    return sizes


def _selected_variant(soup: BeautifulSoup):
    block = _standard_size_block(soup)
    if block is None:
        return None
    return (block.select_one(f"{VARIANT_ITEM_SELECTOR}.actived")
            or block.select_one(VARIANT_ITEM_SELECTOR))


def parse_concentration(soup: BeautifulSoup) -> str | None:
    """Nồng độ: ưu tiên variant đang chọn, sau đó tới .selected-variant--title."""
    selected = _selected_variant(soup)
    if selected is not None:
        _, conc = split_variant_title(selected.get("data-variant-title"))
        if conc:
            return conc

    title = _clean(_text_of(soup, ".selected-variant--title"))
    if not title:
        return None
    # "Eau de Parfum 90ml" -> "Eau de Parfum"
    return _clean(SIZE_IN_TEXT_RE.sub("", title))


def parse_gender(soup: BeautifulSoup) -> str | None:
    """Giới tính nằm ở data-gender của variant (trang ghi sẵn Nam/Nữ/Unisex)."""
    selected = _selected_variant(soup)
    if selected is None:
        selected = soup.select_one(VARIANT_ITEM_SELECTOR)
    if selected is None:
        return None
    return normalize_gender(selected.get("data-gender"))


def _text_of(soup: BeautifulSoup, selector: str) -> str | None:
    el = soup.select_one(selector)
    return el.get_text(" ", strip=True) if el is not None else None


def parse_product(html: str, url: str) -> NamProduct:
    soup = _soup(html)
    attrs = parse_attributes(soup)
    selected = _selected_variant(soup)

    return NamProduct(
        url=url,
        name=_clean(_text_of(soup, "h1")),
        brand=attrs.get("Thương hiệu"),
        sku=attrs.get("Mã hàng"),
        gioi_tinh=parse_gender(soup),
        xuat_xu=attrs.get("Xuất xứ"),
        nong_do=parse_concentration(soup),
        nhom_huong=attrs.get("Nhóm hương"),
        phong_cach=attrs.get("Phong cách"),
        nam_phat_hanh=attrs.get("Năm phát hành"),
        standard_size=parse_standard_size(soup),
        price=_clean(selected.get("data-price")) if selected is not None else None,
        scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
