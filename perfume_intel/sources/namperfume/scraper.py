"""Scraper cho namperfume.net (nền tảng Haravan)."""

from __future__ import annotations

from ..base import SiteScraper
from .models import CSV_COLUMNS, NamProduct
from .parsers import parse_product

HOST = "namperfume.net"
SITE = "namperfume"
BASE_URL = "https://namperfume.net"
WAIT_SELECTOR = "li.product-variant-item"


class NamperfumeScraper(SiteScraper):
    site = SITE
    host = HOST
    base_url = BASE_URL
    record_cls = NamProduct
    csv_columns = CSV_COLUMNS
    wait_selector = WAIT_SELECTOR
    scroll = False     # dữ liệu có sẵn ngay trong HTML, không cần cuộn

    def parse(self, html: str, url: str) -> NamProduct:
        return parse_product(html, url)

    def summary(self, product: NamProduct) -> str:
        return (f"{(product.name or '?')[:40]:<40} | {product.nong_do} "
                f"| {product.xuat_xu} | {'; '.join(product.standard_size)}")
