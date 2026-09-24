"""Bóc tách dữ liệu từ HTML của Fragrantica.

Các selector dưới đây bám theo cấu trúc thật của trang perfume:
  - accords : <h6>main accords</h6> + các thanh <div style="... width: N%">
  - notes   : <pyramid-level-new notes="top|middle|base"> > a.pyramid-note-link
  - rating  : microdata itemprop=ratingValue / ratingCount
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from ...core.text import normalize_gender

from ... import config
from .models import Accord, Brand, BrandPerfume, Perfume, WearVote

PERFUME_URL_RE = re.compile(r"/perfume/[^/]+/[^/]+-(\d+)\.html")
GENDER_RE = re.compile(
    r"\bfor (women and men|men and women|women|men)\b", re.I)
# Không bắt nhóm, để findall() trả về cả năm chứ không phải riêng '19'/'20'.
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
LAUNCH_RE = re.compile(
    r"\b(?:launched|released|introduced)\b[^.]*?\b((?:19|20)\d{2})\b", re.I)
NOTE_URL_RE = re.compile(r"/notes/(.+?)-\d+\.html")
TIER_RE = re.compile(r"\b(top|middle|heart|base)\s+notes\b", re.I)

# Nhóm hương (fragrance family) nằm trong description:
#   "Sauvage by Dior is a Aromatic Fougere fragrance for men."
FRAGRANCE_FAMILIES = (
    "Aromatic", "Aromatic Aquatic", "Aromatic Fougere", "Aromatic Fruity",
    "Aromatic Green", "Aromatic Spicy",
    "Chypre", "Chypre Floral", "Chypre Fruity",
    "Citrus", "Citrus Aromatic", "Citrus Gourmand",
    "Floral", "Floral Aldehyde", "Floral Aquatic", "Floral Fruity",
    "Floral Fruity Gourmand", "Floral Green", "Floral Woody Musk",
    "Leather",
    "Oriental", "Oriental Floral", "Oriental Fougere", "Oriental Spicy",
    "Oriental Vanilla", "Oriental Woody",
    "Woody", "Woody Aquatic", "Woody Aromatic", "Woody Chypre",
    "Woody Floral Musk", "Woody Spicy",
)
# Xếp tên dài trước: re chọn nhánh khớp ĐẦU TIÊN, nếu để "Floral" trước thì
# "Floral Fruity Gourmand" sẽ bị cắt còn "Floral".
_FAMILIES_LONGEST_FIRST = sorted(FRAGRANCE_FAMILIES, key=len, reverse=True)
_FAMILY_CANONICAL = {f.lower(): f for f in FRAGRANCE_FAMILIES}

KNOWN_FAMILY_RE = re.compile(
    r"\bis\s+an?\s+(" + "|".join(re.escape(f) for f in _FAMILIES_LONGEST_FIRST)
    + r")\s+fragrance\b", re.I)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _text(el) -> str | None:
    if el is None:
        return None
    value = el.get("content") or el.get_text(" ", strip=True)
    return value.strip() or None


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


# --------------------------------------------------------------------- accords
# Khối chứa các thanh accord, dùng làm phương án dự phòng khi không thấy tiêu đề.
ACCORD_CONTAINER_SELECTOR = 'div[class*="max-w-[280px]"]'

_RGB_RE = re.compile(r"rgba?\(\s*(\d+)\D+(\d+)\D+(\d+)")


def _normalize_color(value: str | None) -> str | None:
    """Đưa 'rgb(173, 119, 39)' và '#AD7727' về cùng dạng hex thường."""
    if not value:
        return None
    value = value.strip().rstrip(";").strip()
    m = _RGB_RE.match(value)
    if m:
        return "#{:02x}{:02x}{:02x}".format(*(int(g) for g in m.groups()))
    return value.lower() if value.startswith("#") else value


def _css_ratio(style: str, prop: str) -> float | None:
    """Đọc một thuộc tính CSS dạng tỉ lệ, trả về thang 0-100.

    Chấp nhận cả '87.2151%' (HTML gốc) lẫn '0.890415' / '1' (bản browser render).
    """
    m = re.search(rf"{prop}:\s*([\d.]+)\s*(%?)", style)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    if not m.group(2) and value <= 1:      # dạng tỉ lệ 0-1 -> đổi sang %
        value *= 100
    return round(value, 2)


def _accord_bars(container) -> list[Accord]:
    accords: list[Accord] = []
    for bar in container.select("div[style*='width']"):
        name = bar.get_text(" ", strip=True)
        if not name:
            continue
        style = bar.get("style") or ""
        color = re.search(r"background(?:-color)?:\s*([^;]+)", style)
        accords.append(Accord(
            name=name,
            width=_css_ratio(style, "width"),
            opacity=_css_ratio(style, "opacity"),
            color=_normalize_color(color.group(1)) if color else None,
        ))
    return accords


def _parse_accords(soup: BeautifulSoup) -> list[Accord]:
    """Lấy 'main accords' + chỉ số độ mạnh (width) và độ đậm (opacity)."""
    heading = soup.find(
        lambda t: t.name in ("h6", "h5", "h4")
        and "main accords" in t.get_text(strip=True).lower()
    )
    if heading is not None:
        container = heading.find_next_sibling("div") or heading.parent
        accords = _accord_bars(container)
        if accords:
            return accords

    # Dự phòng: trang đổi tiêu đề nhưng vẫn giữ khối bar accord.
    for container in soup.select(ACCORD_CONTAINER_SELECTOR):
        accords = _accord_bars(container)
        if accords:
            return accords
    return []


# ----------------------------------------------------------------------- notes
def _note_names(scope) -> list[str]:
    names: list[str] = []
    for a in scope.select("a.pyramid-note-link, a[href*='/notes/']"):
        href = a.get("href") or ""
        m = NOTE_URL_RE.search(href)
        if not m:                  # link mục lục /notes/ chứ không phải 1 note
            continue
        name = a.get_text(" ", strip=True)
        if not name:               # dự phòng: lấy từ slug trong href
            name = unquote(m.group(1)).replace("-", " ")
        name = name.strip()
        if name and name not in names:
            names.append(name)
    return names


def _tier_from_heading(container) -> str | None:
    """Suy ra tầng hương từ tiêu đề <h4>Top/Middle/Base Notes</h4> đứng trước."""
    heading = container.find_previous(
        lambda t: t.name in ("h3", "h4", "h5", "h6")
        and TIER_RE.search(t.get_text(" ", strip=True))
    )
    if heading is None:
        return None
    word = TIER_RE.search(heading.get_text(" ", strip=True)).group(1).lower()
    return {"heart": "middle"}.get(word, word)


def _parse_notes(soup: BeautifulSoup) -> dict[str, list[str]]:
    """Bóc tháp hương.

    HTML gốc dùng thẻ <pyramid-level-new notes="top">; sau khi browser render
    thẻ này biến mất, chỉ còn <h4>Top Notes</h4> + div.pyramid-level-container.
    """
    out: dict[str, list[str]] = {"top": [], "middle": [], "base": [], "general": []}

    levels = soup.find_all("pyramid-level-new")
    if levels:
        for level in levels:
            kind = (level.get("notes") or "").lower()
            kind = kind if kind in out else "general"
            out[kind].extend(n for n in _note_names(level) if n not in out[kind])
        return out

    containers = soup.select("div.pyramid-level-container")
    if containers:
        for container in containers:
            kind = _tier_from_heading(container) or "general"
            out[kind].extend(
                n for n in _note_names(container) if n not in out[kind])
        return out

    # Nước hoa không có tháp hương -> gom toàn bộ note trong khối pyramid.
    wrapper = soup.find("pyramid-notes-new") or soup
    out["general"] = _note_names(wrapper)
    return out


# --------------------------------------------------------------- when to wear
# Nhãn hợp lệ của widget; dùng để không nhặt nhầm widget khác cũng có [index].
WEAR_LABELS = {"winter", "spring", "summer", "fall", "autumn", "day", "night"}


def _parse_vote_count(label: str | None) -> int | None:
    """'6.3k' -> 6300, '499' -> 499, '1.2M' -> 1200000."""
    if not label:
        return None
    m = re.fullmatch(r"([\d.,]+)\s*([kKmM]?)", label.strip())
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return int(value * {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1))


def _parse_when_to_wear(soup: BeautifulSoup) -> list[WearVote]:
    """Bóc biểu đồ 'when to wear' (chỉ có trong HTML đã render bằng browser)."""
    votes: list[WearVote] = []
    seen: set[str] = set()

    for item in soup.select("div[index]"):
        label_el = item.find("span")
        if label_el is None:
            continue
        name = label_el.get_text(strip=True).lower()
        if name not in WEAR_LABELS or name in seen:
            continue
        seen.add(name)

        bar = item.select_one("div[style*='width']")
        count_el = item.select_one("span.tabular-nums") or item.select("span")[-1]
        count_label = count_el.get_text(strip=True) if count_el is not None else None

        votes.append(WearVote(
            name=name,
            percent=_css_ratio(bar.get("style") or "", "width") if bar else None,
            votes=_parse_vote_count(count_label),
            votes_label=count_label,
        ))
    return votes


# ------------------------------------------------- độ lưu hương / độ toả hương
# Nhãn của từng thang; 'moderate' có ở cả hai nên bắt buộc phải tách theo card.
LONGEVITY_LABELS = ("very weak", "weak", "moderate", "long lasting", "eternal")
SILLAGE_LABELS = ("intimate", "moderate", "strong", "enormous")


def _performance_card(soup: BeautifulSoup, title: str):
    """Tìm thẻ card chứa tiêu đề LONGEVITY / SILLAGE."""
    node = soup.find(string=lambda s: s and s.strip().lower() == title)
    if node is None:
        return None
    # Đi ngược lên tới div gần nhất có chứa các thanh bar.
    for parent in node.parents:
        if parent.name == "div" and parent.select_one("div[style*='width']"):
            return parent
    return None


def _performance_bars(card, labels: tuple[str, ...]) -> list[WearVote]:
    bars: list[WearVote] = []
    seen: set[str] = set()

    for span in card.select("span"):
        name = span.get_text(" ", strip=True).lower()
        if name not in labels or name in seen:
            continue
        row = span.find_parent("div", class_="flex")
        if row is None:
            continue
        seen.add(name)

        spans = row.find_all("span")
        count_label = spans[1].get_text(strip=True) if len(spans) > 1 else None
        bar = row.select_one("div[style*='width']")
        bars.append(WearVote(
            name=name,
            percent=_css_ratio(bar.get("style") or "", "width") if bar else None,
            votes=_parse_vote_count(count_label),
            votes_label=count_label,
        ))
    return bars


def _top_label(bars: list[WearVote]) -> str | None:
    """Nhãn được vote nhiều nhất; hoà thì lấy nhãn xuất hiện trước."""
    rated = [b for b in bars if b.votes is not None]
    return max(rated, key=lambda b: b.votes).name if rated else None


def _parse_performance(soup: BeautifulSoup) -> dict[str, str | None]:
    """Title được vote nhiều nhất của độ lưu hương & độ toả hương.

    Chỉ có trong HTML đã render VÀ đã cuộn tới (khối này nằm trong
    `lazy-section-new` section-id="performance").
    """
    out: dict[str, str | None] = {"longevity": None, "sillage": None}
    for key, labels in (("longevity", LONGEVITY_LABELS),
                        ("sillage", SILLAGE_LABELS)):
        card = _performance_card(soup, key)
        if card is not None:
            out[key] = _top_label(_performance_bars(card, labels))
    return out


# ------------------------------------------------------------------- perfumers
def _parse_perfumers(soup: BeautifulSoup) -> list[str]:
    names: list[str] = []
    for a in soup.select("a[href*='/noses/']"):
        href = a.get("href") or ""
        if href.rstrip("/").endswith("/noses"):   # link tới trang index
            continue
        name = a.get_text(" ", strip=True)
        if name and name.lower() != "perfumers" and name not in names:
            names.append(name)
    return names


# ----------------------------------------------------------------- nhóm hương
def _parse_family(description: str | None) -> str | None:
    """Lấy nhóm hương từ description ("... is a Aromatic Fougere fragrance ...").

    Chỉ chấp nhận nhóm nằm trong `FRAGRANCE_FAMILIES`; không khớp được thì để
    trống (None) chứ không đoán, tránh lẫn giá trị rác vào dữ liệu.
    """
    if not description:
        return None

    m = KNOWN_FAMILY_RE.search(description)
    if not m:
        return None
    return _FAMILY_CANONICAL[re.sub(r"\s+", " ", m.group(1)).strip().lower()]


# ------------------------------------------------------------------------ year
def _parse_year(soup: BeautifulSoup, description: str | None) -> int | None:
    """Tìm năm phát hành.

    Không dùng năm ĐẦU TIÊN bắt gặp: tên nước hoa có thể chứa số 4 chữ số
    ('1969 Parfum de Revolte' ra mắt 2001, 'Chris 1947' ra mắt 2003) và tên
    luôn đứng trước năm thật trong <title>.
    """
    # 1. Câu chuẩn hoá "<tên> was launched in YYYY" trong description.
    m = LAUNCH_RE.search(description or "")
    if m:
        return int(m.group(1))

    # 2. <title> có dạng "<tên> <hãng> ... for <gender> <năm>" -> lấy năm CUỐI.
    title = soup.title.get_text(strip=True) if soup.title else ""
    for source in (title, description or ""):
        years = YEAR_RE.findall(source)
        if years:
            return int(years[-1])

    # 3. Dự phòng cuối: link tới trang theo năm (chưa thấy trang nào dùng).
    for a in soup.select("a[href*='/year/'], a[href*='-year-']"):
        m = YEAR_RE.search(a.get_text(strip=True))
        if m:
            return int(m.group(0))
    return None


# ------------------------------------------------------------------- perfume
def parse_perfume(html: str, url: str) -> Perfume:
    """Bóc tách một trang chi tiết nước hoa thành object `Perfume`."""
    soup = _soup(html)

    h1 = _text(soup.find("h1")) or ""
    brand = _text(soup.select_one("[itemprop='brand']"))
    description = _text(soup.select_one("[itemprop='description']"))
    if not description:
        description = _text(soup.select_one("meta[property='og:description']"))

    # Tên: bỏ phần brand và "for men/women" khỏi h1.
    name = h1
    gender = None
    gm = GENDER_RE.search(h1)
    if gm:
        gender = gm.group(1).lower().replace("men and women", "women and men")
        name = h1[: gm.start()].strip()
    if brand and name.endswith(brand):
        name = name[: -len(brand)].strip()
    name = name or _text(soup.select_one("meta[property='og:title']")) or h1

    notes = _parse_notes(soup)
    perf = _parse_performance(soup)
    pid = PERFUME_URL_RE.search(url)

    image = _text(soup.select_one("meta[property='og:image']"))
    if not image:
        img = soup.select_one("[itemprop='image']")
        if img is not None:
            image = img.get("src") or img.get("content")

    return Perfume(
        url=url,
        perfume_id=pid.group(1) if pid else None,
        name=name or None,
        brand=brand,
        gender=normalize_gender(gender),
        year=_parse_year(soup, description),
        fragrance_family=_parse_family(description),
        rating=_to_float(_text(soup.select_one("[itemprop='ratingValue']"))),
        rating_count=_to_int(_text(soup.select_one("[itemprop='ratingCount']"))),
        accords=_parse_accords(soup),
        top_notes=notes["top"],
        middle_notes=notes["middle"],
        base_notes=notes["base"],
        general_notes=notes["general"],
        perfumers=_parse_perfumers(soup),
        when_to_wear=_parse_when_to_wear(soup),
        longevity=perf["longevity"],
        sillage=perf["sillage"],
        description=description,
        image=image,
        scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# ------------------------------------------------------------------ discovery
def extract_perfume_links(html: str, base_url: str = config.BASE_URL) -> list[str]:
    """Lấy toàn bộ link trang nước hoa từ một trang bất kỳ (designer, note, ...).

    Chỉ giữ link cùng tên miền với `base_url`; trang Fragrantica có sẵn link sang
    bản dịch (fragrantica.es, .it, .ru, ...) mà ta không muốn crawl trùng.
    """
    soup = _soup(html)
    host = urlparse(base_url).netloc.lower()
    links: list[str] = []
    seen: set[str] = set()

    for a in soup.select("a[href]"):
        href = a["href"]
        if not PERFUME_URL_RE.search(href):
            continue
        full = urljoin(base_url, href).split("#")[0]
        if urlparse(full).netloc.lower() != host:
            continue
        if full not in seen:
            seen.add(full)
            links.append(full)
    return links


# ---------------------------------------------------------------- danh mục hãng
# Trang /designers/ liệt kê HÃNG (không phải từng chai). Hai nguồn:
#   - khối "Most Popular Brands" ở footer;
#   - mục lục A-Z, mỗi chữ cái là một link .alphabet-link.
DESIGNER_URL_RE = re.compile(r"/designers/([^/?#]+)\.html", re.I)

# Selector chính xác của khối "Most Popular Brands" (vị trí thật trong footer).
POPULAR_BRANDS_SELECTOR = "#app > footer > div > div:nth-child(4)"
# Nhãn đứng ngay trước khối đó; dùng để dò lại khi footer đổi thứ tự cột.
POPULAR_BRANDS_LABEL = "most popular brands"

ALPHABET_LINK_SELECTOR = ".alphabet-link"


def _brand_links(scope, base_url: str) -> list[tuple[str, str]]:
    """Mọi link hãng trong một vùng DOM -> [(tên hãng, url đầy đủ)].

    Giữ nguyên thứ tự xuất hiện và bỏ link trùng ngay trong vùng đó.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for a in scope.select("a[href]"):
        href = a.get("href") or ""
        if not DESIGNER_URL_RE.search(href):
            continue
        full = urljoin(base_url, href).split("#")[0]
        if full in seen:
            continue
        seen.add(full)

        name = a.get_text(" ", strip=True)
        if not name:
            # Link không có chữ (ảnh logo) -> lấy tạm slug trong URL.
            name = unquote(DESIGNER_URL_RE.search(href).group(1)).replace("-", " ")
        out.append((name, full))
    return out


def _popular_brands_box(soup: BeautifulSoup):
    """Khối chứa danh sách hãng phổ biến, hoặc None nếu không tìm thấy.

    Ưu tiên selector theo vị trí; nếu footer đổi thứ tự cột thì dò theo nhãn
    "Most Popular Brands" rồi lấy khối ngay sau nó.
    """
    box = soup.select_one(POPULAR_BRANDS_SELECTOR)
    if box is not None and _brand_links(box, config.BASE_URL):
        return box

    for div in soup.select("#app footer div"):
        text = div.get_text(" ", strip=True).lower()
        if text != POPULAR_BRANDS_LABEL:
            continue
        for sibling in div.find_next_siblings("div"):
            if _brand_links(sibling, config.BASE_URL):
                return sibling
    return box


def parse_popular_brands(html: str, base_url: str = config.BASE_URL) -> list[Brand]:
    """Khối "Most Popular Brands" ở footer -> danh sách Brand kèm thứ hạng."""
    box = _popular_brands_box(_soup(html))
    if box is None:
        return []

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [Brand(brand_url=url, brand_name=name, popular_rank=rank,
                  scraped_at=now)
            for rank, (name, url) in enumerate(_brand_links(box, base_url), 1)]


def parse_alphabet_links(html: str, base_url: str = config.BASE_URL,
                         ) -> list[tuple[str, str]]:
    """Mục lục A-Z -> [(chữ cái, url trang chứa chữ cái đó)].

    Lấy href thật trong DOM chứ không tự dựng URL: nhiều chữ cái dùng chung một
    trang (`/designers-5/#F`, `#G`, `#H`) và cách gom đó có thể đổi bất cứ lúc
    nào. Fragment `#F` được giữ lại vì đó chính là id của section cần cắt.
    """
    soup = _soup(html)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for a in soup.select(ALPHABET_LINK_SELECTOR):
        letter = a.get_text(" ", strip=True).upper()
        href = a.get("href")
        if not letter or not href or letter in seen:
            continue
        seen.add(letter)
        out.append((letter, urljoin(base_url, href)))
    return out


def parse_brands_for_letter(html: str, letter: str,
                            base_url: str = config.BASE_URL) -> list[Brand]:
    """Các hãng thuộc MỘT chữ cái trên trang mục lục.

    Một trang chứa nhiều chữ cái, mỗi chữ là `<span id="F">F</span>` rồi tới
    khối lưới các hãng. Vì vậy phải cắt theo anchor — lấy cả trang sẽ gộp nhầm
    hãng của chữ cái khác.
    """
    soup = _soup(html)
    anchor = soup.find(id=letter)
    if anchor is None:
        return []

    section = anchor.parent
    if section is None:
        return []

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [Brand(brand_url=url, brand_name=name, alphabet=letter.upper(),
                  scraped_at=now)
            for name, url in _brand_links(section, base_url)]


def _letter_sections(soup: BeautifulSoup) -> dict[str, object]:
    """Mọi section chữ cái trên trang -> {id section: thẻ bao section}.

    Section là `<span id="F">F</span>` nằm cạnh lưới các hãng. Nhận diện theo
    "span có id ngắn, toàn chữ, và khối cha có chứa link hãng" thay vì theo class
    Tailwind — class đổi liên tục, cấu trúc này thì không.
    """
    sections: dict[str, object] = {}
    for span in soup.select("span[id]"):
        key = (span.get("id") or "").strip()
        if not key or len(key) > 3 or not key.isalpha():
            continue
        box = span.parent
        if box is None or key in sections:
            continue
        if any(DESIGNER_URL_RE.search(a.get("href") or "")
               for a in box.select("a[href]")):
            sections[key] = box
    return sections


def parse_extra_sections(html: str, skip: set[str],
                         base_url: str = config.BASE_URL) -> dict[str, list[Brand]]:
    """Các section KHÔNG nằm trong mục lục A-Z -> {chữ cái: danh sách Brand}.

    Mục lục `.alphabet-link` chỉ có A-Z, nhưng trang còn những section chữ có
    dấu (`À`, `É`, `Ô`, `Œ`, `æ`...) chứa vài chục hãng thật. Trang đã tải rồi
    nên quét thêm không tốn request nào.
    """
    out: dict[str, list[Brand]] = {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for key, box in _letter_sections(_soup(html)).items():
        if key.upper() in skip:
            continue
        brands = [Brand(brand_url=url, brand_name=name, alphabet=key,
                        scraped_at=now)
                  for name, url in _brand_links(box, base_url)]
        if brands:
            out[key] = brands
    return out


# ------------------------------------------------- sản phẩm của một hãng (#brands)
# Trang hãng liệt kê chai theo từng collection. Khối này Vue render, nhưng bản
# HTML tĩnh đã chứa sẵn markup trong <template> nên KHÔNG cần --render.
SECTION_HEADER_SELECTOR = ".tw-gridlist-section-header"
LISTVIEW_ITEM_SELECTOR = "a.tw-listview-item"
GENDER_CLASS_PREFIX = "tw-listview-item-"
# Năm chưa rõ được trang ghi là '0000' chứ không bỏ trống.
UNKNOWN_YEAR = "0000"


def _tpl_text(el) -> str | None:
    """Như `_text` nhưng đọc được cả chữ nằm trong <template>.

    Bản tĩnh của trang hãng đặt danh sách chai trong <template> của Vue;
    BeautifulSoup gắn nhãn `TemplateString` cho chữ bên trong và `get_text()`
    mặc định BỎ QUA chúng — `types=None` mới lấy đủ. Thuộc tính (href, title,
    class) thì không bị ảnh hưởng.
    """
    if el is None:
        return None
    return el.get_text(" ", strip=True, types=None).strip() or None


def _item_gender(item) -> str | None:
    """Giới tính đọc từ class `tw-listview-item-unisex/female/male`."""
    for cls in item.get("class") or []:
        if cls.startswith(GENDER_CLASS_PREFIX):
            return normalize_gender(cls[len(GENDER_CLASS_PREFIX):])
    return None


def _item_comments(item) -> int | None:
    """Số bình luận hiện trên thẻ (không có khi bằng 0)."""
    for div in item.select("div.relative.z-10"):
        value = _tpl_text(div)
        if value and value.replace(",", "").isdigit():
            return int(value.replace(",", ""))
    return None


def _item_year(item) -> int | None:
    value = _tpl_text(item.select_one("span.tw-year-badge"))
    if not value or value == UNKNOWN_YEAR or not value.isdigit():
        return None
    return int(value)


def parse_brand_perfumes(html: str, brand_url: str | None = None,
                         base_url: str = config.BASE_URL) -> list[BrandPerfume]:
    """Trang hãng -> danh sách chai, kèm collection của từng chai.

    Đi theo đúng thứ tự tài liệu: gặp header thì đổi collection hiện tại, gặp
    thẻ chai thì gán collection đó. Bản tĩnh chứa hai view (lưới + danh sách)
    nên header xuất hiện hai lần, nhưng chỉ view danh sách mới có
    `a.tw-listview-item` — header của view kia không có chai nào đi kèm nên
    không ảnh hưởng.

    Chai nằm dưới "All Fragrances" là chai KHÔNG thuộc collection nào; ở đó
    `collection` để None thay vì ghi "All Fragrances" cho khỏi hiểu nhầm là một
    dòng sản phẩm thật.
    """
    soup = _soup(html)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    collection: str | None = None
    anchor: str | None = None
    out: list[BrandPerfume] = []
    seen: set[tuple[str, str | None]] = set()

    for el in soup.select(f"{SECTION_HEADER_SELECTOR}, {LISTVIEW_ITEM_SELECTOR}"):
        classes = el.get("class") or []
        if "tw-gridlist-section-header" in classes:
            holder = el.select_one("[id]")
            anchor = holder.get("id") if holder else None
            collection = _tpl_text(el.select_one("h2"))
            continue

        href = el.get("href") or ""
        match = PERFUME_URL_RE.search(href)
        if not match:
            continue
        url = urljoin(base_url, href).split("#")[0]

        named = anchor not in (None, "all-fragrances")
        key = (url, anchor)
        if key in seen:
            continue
        seen.add(key)

        out.append(BrandPerfume(
            perfume_url=url,
            perfume_id=match.group(1),
            perfume_name=_tpl_text(el.select_one("h3.tw-perfume-title")),
            brand_name=_tpl_text(el.select_one("p.tw-perfume-designer")),
            brand_url=brand_url,
            collection=collection if named else None,
            collection_anchor=anchor if named else None,
            year=_item_year(el),
            gender=_item_gender(el),
            comments=_item_comments(el),
            scraped_at=now,
        ))
    return out


def parse_brand_collections(html: str) -> list[tuple[str, str]]:
    """Danh sách collection của hãng -> [(tên, anchor)], bỏ 'All Fragrances'."""
    soup = _soup(html)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for header in soup.select(SECTION_HEADER_SELECTOR):
        holder = header.select_one("[id]")
        anchor = holder.get("id") if holder else None
        name = _tpl_text(header.select_one("h2"))
        if not anchor or anchor == "all-fragrances" or anchor in seen:
            continue
        seen.add(anchor)
        out.append((name or anchor, anchor))
    return out
