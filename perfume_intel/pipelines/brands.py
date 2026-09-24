"""Crawl toàn bộ danh mục hãng từ /designers/ — chỉ hãng, không chai nào.

Luồng:
    1. tải /designers/ một lần;
    2. lấy khối "Most Popular Brands" ở footer;
    3. lấy mục lục A-Z từ `.alphabet-link` — href đọc thẳng từ DOM;
    4. tải từng trang mục lục, cắt đúng section của mỗi chữ cái;
    5. gộp lại, khử trùng theo brand_url.

Rẻ bất ngờ: 26 chữ cái chỉ nằm trên 11 trang (`/designers-5/` chứa cả F, G, H)
nên cả job chỉ tốn ~12 request. Vì vậy các trang được gom theo đường dẫn và mỗi
trang chỉ tải đúng một lần.

KHÔNG cần `--render` / cuộn trang. Đã đo bằng cách mở chính các trang này bằng
browser thật rồi cuộn hết xuống đáy và so số hãng với bản `requests`:

    designers-1   A=859  (+7 section chữ có dấu)  -> render: y hệt
    designers-5   F=300 G=240 H=253               -> render: y hệt
    designers-8   M=760                           -> render: y hệt

Thứ duy nhất lazy-load trên trang là ảnh logo (`<img loading="lazy">`); còn các
thẻ `<a>` tới từng hãng thì server đã render sẵn đủ. Cũng không có phân trang
trong một chữ cái (không có `?page=`, không có nút "load more").
"""

from __future__ import annotations

import logging
import string
import time
from dataclasses import dataclass, field
from urllib.parse import urldefrag

from .. import config
from ..core.http import Fetcher
from ..core.text import url_key
from ..sources.fragrantica.models import Brand
from ..sources.fragrantica.parsers import (parse_alphabet_links,
                                           parse_brands_for_letter,
                                           parse_extra_sections,
                                           parse_popular_brands)

log = logging.getLogger(__name__)

ALPHABET = tuple(string.ascii_uppercase)

# `Fetcher.get` đã tự retry lỗi mạng/5xx/429 bên trong. Lớp này retry thêm một
# vòng nữa cho trường hợp nó chịu thua hẳn (trả None) — danh mục chỉ có 12 trang
# nên mất một trang là mất cả trăm hãng, đáng để thử lại.
PAGE_RETRIES = 3
PAGE_RETRY_WAIT = 10.0


@dataclass
class BrandCrawlReport:
    """Kết quả một lần crawl danh mục, kèm những gì đã hỏng."""

    brands: list[Brand] = field(default_factory=list)
    popular: int = 0
    by_letter: dict[str, int] = field(default_factory=dict)
    failed_pages: list[str] = field(default_factory=list)
    missing_letters: list[str] = field(default_factory=list)
    # Section chữ có dấu (À, É, Ô, Œ...) không có trong mục lục A-Z.
    extra_sections: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.brands) and not self.missing_letters


def _get_with_retry(fetcher: Fetcher, url: str,
                    attempts: int = PAGE_RETRIES) -> str | None:
    """Tải một trang, thử lại vài lần trước khi chịu thua."""
    for attempt in range(1, attempts + 1):
        html = fetcher.get(url)
        if html:
            return html
        if attempt < attempts:
            log.warning("Không tải được %s (lần %d/%d) — chờ %.0fs rồi thử lại.",
                        url, attempt, attempts, PAGE_RETRY_WAIT)
            time.sleep(PAGE_RETRY_WAIT)
    log.error("Bỏ cuộc sau %d lần thử: %s", attempts, url)
    return None


def _pages_by_letter(pairs: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """[(chữ cái, url)] -> {url trang (bỏ fragment): [(chữ cái, url đầy đủ)]}.

    Gom theo trang để mỗi trang chỉ tải một lần dù chứa nhiều chữ cái.
    """
    pages: dict[str, list[tuple[str, str]]] = {}
    for letter, url in pairs:
        page, _ = urldefrag(url)
        pages.setdefault(page, []).append((letter, url))
    return pages


def merge(groups: list[list[Brand]]) -> list[Brand]:
    """Gộp nhiều danh sách Brand, khử trùng theo `brand_url`.

    Trùng thì hợp nhất thông tin chứ không bỏ bản sau: một hãng vừa nằm trong
    "Most Popular Brands" (có `popular_rank`) vừa nằm trong mục lục (có
    `alphabet`) phải giữ được cả hai.
    """
    merged: dict[str, Brand] = {}
    for group in groups:
        for brand in group:
            key = url_key(brand.brand_url)
            if not key:
                continue
            current = merged.get(key)
            if current is None:
                merged[key] = brand
                continue
            current.brand_name = current.brand_name or brand.brand_name
            current.alphabet = current.alphabet or brand.alphabet
            if current.popular_rank is None:
                current.popular_rank = brand.popular_rank
            current.scraped_at = current.scraped_at or brand.scraped_at

    # Thứ tự cuối: theo chữ cái rồi theo tên; hãng chưa rõ chữ cái xếp xuống cuối.
    return sorted(merged.values(),
                  key=lambda b: (b.alphabet is None, b.alphabet or "",
                                 (b.brand_name or "").lower()))


def crawl(fetcher: Fetcher, base_url: str = config.BASE_URL,
          letters: str | None = None,
          only_az: bool = False) -> BrandCrawlReport:
    """Crawl cả danh mục hãng.

    `letters` giới hạn chữ cái (vd "ABC") để thử nhanh. `only_az` bỏ qua các
    section chữ có dấu — bám sát đúng mục lục A-Z, đổi lại thiếu vài chục hãng.
    """
    report = BrandCrawlReport()
    index_url = f"{base_url.rstrip('/')}/designers/"

    html = _get_with_retry(fetcher, index_url)
    if html is None:
        report.failed_pages.append(index_url)
        report.missing_letters = list(ALPHABET)
        log.error("Không tải được trang danh mục %s — không có gì để crawl.",
                  index_url)
        return report

    popular = parse_popular_brands(html, base_url)
    report.popular = len(popular)
    log.info("Most Popular Brands: %d hãng.", len(popular))
    if not popular:
        log.warning("Không đọc được khối 'Most Popular Brands' — footer có thể "
                    "đã đổi cấu trúc, kiểm tra POPULAR_BRANDS_SELECTOR.")

    pairs = parse_alphabet_links(html, base_url)
    log.info("Mục lục A-Z: %d chữ cái -> %d trang.",
             len(pairs), len(_pages_by_letter(pairs)))

    found = {letter for letter, _ in pairs}
    absent = [c for c in ALPHABET if c not in found]
    if absent:
        log.warning("Mục lục thiếu link cho: %s", ", ".join(absent))

    if letters:
        wanted = {c.upper() for c in letters if c.strip()}
        pairs = [(le, url) for le, url in pairs if le in wanted]
        log.info("Chỉ crawl %d chữ cái theo yêu cầu: %s",
                 len(pairs), ", ".join(sorted(wanted)))

    groups: list[list[Brand]] = [popular]
    pages = _pages_by_letter(pairs)

    for i, (page, entries) in enumerate(pages.items(), 1):
        letters_here = ", ".join(letter for letter, _ in entries)
        log.info("[%d/%d] %s (chữ cái: %s)", i, len(pages), page, letters_here)

        page_html = _get_with_retry(fetcher, page)
        if page_html is None:
            report.failed_pages.append(page)
            report.missing_letters += [letter for letter, _ in entries]
            continue

        if not only_az:
            # Trang đã tải rồi -> quét thêm section chữ có dấu, 0 request.
            for key, brands in parse_extra_sections(
                    page_html, set(ALPHABET), base_url).items():
                groups.append(brands)
                report.extra_sections[key] = len(brands)

        for letter, _ in entries:
            brands = parse_brands_for_letter(page_html, letter, base_url)
            report.by_letter[letter] = len(brands)
            if brands:
                groups.append(brands)
                log.info("   %s: %d hãng", letter, len(brands))
            else:
                # Trang tải được nhưng không cắt ra hãng nào -> anchor đổi tên.
                report.missing_letters.append(letter)
                log.error("   %s: không thấy hãng nào (anchor #%s có còn không?)",
                          letter, letter)

    report.brands = merge(groups)

    expected = sorted(letter for letter, _ in pairs)
    done = sorted(letter for letter in expected
                  if letter not in report.missing_letters)
    log.info("Xong: %d hãng sau khi khử trùng (%d chữ cái xử lý được / %d).",
             len(report.brands), len(done), len(expected))
    if report.missing_letters:
        log.warning("Chữ cái chưa lấy được: %s",
                    ", ".join(sorted(set(report.missing_letters))))
    if report.extra_sections:
        log.info("Thêm %d hãng từ %d section chữ có dấu ngoài A-Z (%s).",
                 sum(report.extra_sections.values()), len(report.extra_sections),
                 ", ".join(sorted(report.extra_sections)))
    if report.failed_pages:
        log.warning("Trang tải hỏng: %s", ", ".join(report.failed_pages))
    return report
