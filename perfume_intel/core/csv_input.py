"""Đọc danh sách URL cần crawl từ file CSV/text của người dùng.

File đầu vào do người khác xuất ra nên không chuẩn hoá được: dấu phân cách có
thể là ',' hay ';', có thể không có dòng tiêu đề, link cần crawl có khi nằm lẫn
trong ô văn bản. Mọi xử lý "đoán" đó gom hết vào đây, tách khỏi phần crawl.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

log = logging.getLogger(__name__)

# Một ô "trông như URL": link tuyệt đối bất kỳ, hoặc đường dẫn tương đối kiểu
# Fragrantica (file đầu vào cũ hay chỉ chép '/perfume/...'). URL tương đối chỉ
# ghép được thành link đầy đủ khi caller truyền `base_url`.
URL_CELL_RE = re.compile(r"(?:https?://|/)\S*fragrantica|^/perfume/|^https?://", re.I)
# Bắt URL nằm lẫn trong ô có chữ khác (vd ô chứa cả câu tìm kiếm lẫn link).
URL_IN_TEXT_RE = re.compile(r"https?://[^\s,\"'<>]+")
# Cột chứa URL nguồn để crawl.
URL_COLUMN_NAMES = ("url", "source_url", "source", "link", "href",
                    "url_nguon", "link_nguon", "duong_dan", "đường dẫn", "địa chỉ")
# Cột chứa URL đích, chỉ gắn kèm vào kết quả chứ không crawl.
DEST_COLUMN_NAMES = ("des_url", "dest_url", "destination_url", "des", "dest",
                     "target_url", "product_url", "url_dich", "link_dich")


def read_urls_file(path: Path) -> list[str]:
    """File text: mỗi dòng 1 URL, bỏ qua dòng trống và dòng bắt đầu bằng '#'."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def iter_csv_files(path: Path) -> list[Path]:
    """1 file -> [file]; 1 thư mục -> mọi .csv bên trong (kể cả thư mục con)."""
    if path.is_dir():
        return sorted(p for p in path.rglob("*.csv") if p.is_file())
    return [path]


def _sniff_delimiter(sample: str) -> str:
    """Excel tiếng Việt hay xuất CSV bằng ';' thay vì ','."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample.splitlines() else ""
        return max(",;\t|", key=first.count)


def _looks_like_url(value: str) -> bool:
    return bool(value) and bool(URL_CELL_RE.search(value.strip()))


def _find_column(header: list[str], names) -> int | None:
    for i, name in enumerate(header):
        if name.strip().lower() in names:
            return i
    return None


def _pick_url_column(rows: list[list[str]], header: list[str] | None,
                     column: str | None, exclude: int | None = None) -> int:
    """Chọn cột chứa URL: theo tên người dùng chỉ định -> theo header -> theo nội dung."""
    if column and header:
        index = _find_column(header, {column.strip().lower()})
        if index is None:
            raise ValueError(
                f"Không thấy cột {column!r}. Các cột có trong file: "
                f"{', '.join(header)}")
        return index
    if column and not header:
        raise ValueError("File CSV không có dòng tiêu đề nên không dùng được "
                         "--url-column.")

    if header:
        index = _find_column(header, URL_COLUMN_NAMES)
        if index is not None:
            return index

    # Không có header phù hợp -> lấy cột có nhiều ô giống URL nhất,
    # bỏ qua cột đích để không crawl nhầm link sản phẩm.
    width = max((len(r) for r in rows), default=0)
    counts = [0 if i == exclude else
              sum(1 for r in rows if i < len(r) and _looks_like_url(r[i]))
              for i in range(width)]
    if not counts or max(counts) == 0:
        raise ValueError("Không tìm thấy cột nào chứa URL Fragrantica.")
    return counts.index(max(counts))


def _load_csv_rows(path: Path) -> tuple[list[str] | None, list[list[str]], str]:
    """Đọc file CSV thô -> (header hoặc None, các dòng dữ liệu, dấu phân cách)."""
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return None, [], ","

    delimiter = _sniff_delimiter(text[:4096])
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)
            if any(cell.strip() for cell in r)]
    if not rows:
        return None, [], delimiter

    # Dòng đầu là tiêu đề nếu bản thân nó không chứa URL.
    header = None if any(_looks_like_url(c) for c in rows[0]) else rows[0]
    return header, (rows[1:] if header else rows), delimiter


def _row_urls(row: list[str]) -> list[str]:
    """Mọi URL trong một dòng, kể cả khi nằm lẫn trong ô có chữ khác."""
    urls: list[str] = []
    for cell in row:
        urls.extend(URL_IN_TEXT_RE.findall(cell))
    return urls


def _pairs_by_host(data: list[list[str]], host: str, unique: bool = True,
                   ) -> list[tuple[str, str | None]]:
    """Chọn URL theo tên miền thay vì theo cột.

    File đầu vào không phải lúc nào cũng đặt link cần crawl vào cột source_url
    — có file để link Fragrantica lẫn trong ô khác. Vì vậy quét cả dòng: URL
    thuộc `host` là link để crawl, URL khác host là link đích.
    """
    pairs: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    skipped = 0

    for row in data:
        urls = _row_urls(row)
        source = next(
            (u for u in urls if host in urlparse(u).netloc.lower()), None)
        if source is None:
            skipped += 1
            continue
        source = source.split("#")[0]
        if unique and source in seen:
            continue
        seen.add(source)
        dest = next(
            (u for u in urls if host not in urlparse(u).netloc.lower()), None)
        pairs.append((source, dest))

    if skipped:
        log.warning("Bỏ qua %d dòng không có URL của %s.", skipped, host)
    return pairs


def read_url_pairs(path: Path, column: str | None = None,
                   dest_column: str | None = None,
                   base_url: str = "",
                   prefer_host: str | None = None,
                   unique: bool = True,
                   ) -> list[tuple[str, str | None]]:
    """Đọc CSV -> danh sách (url_nguon, url_dich).

    `url_nguon` là trang sẽ crawl; `url_dich` (nếu có) chỉ được gắn kèm vào kết
    quả, không truy cập. Tự nhận dấu phân cách và dòng tiêu đề.

    `unique=False` giữ nguyên mọi dòng: một trang nguồn có thể ứng với nhiều URL
    đích (vd cùng một chai full nhưng có hai bản mini khác nhau).
    """
    header, data, delimiter = _load_csv_rows(path)
    if not data:
        return []

    # Không chỉ định cột cụ thể -> ưu tiên bám theo tên miền cần crawl.
    if prefer_host and not column:
        pairs = _pairs_by_host(data, prefer_host.lower(), unique)
        log.info("CSV %s: %d URL của %s (dấu phân cách %r).",
                 path.name, len(pairs), prefer_host, delimiter)
        return pairs

    dest_index: int | None = None
    if dest_column:
        if not header:
            raise ValueError("File CSV không có dòng tiêu đề nên không dùng "
                             "được --dest-column.")
        dest_index = _find_column(header, {dest_column.strip().lower()})
        if dest_index is None:
            raise ValueError(
                f"Không thấy cột {dest_column!r}. Các cột có trong file: "
                f"{', '.join(header)}")
    elif header:
        dest_index = _find_column(header, DEST_COLUMN_NAMES)

    index = _pick_url_column(data, header, column, exclude=dest_index)
    log.info("CSV: dấu phân cách %r, cột nguồn %s, cột đích %s",
             delimiter,
             f"{header[index]!r}" if header else f"#{index + 1}",
             f"{header[dest_index]!r}" if (header and dest_index is not None)
             else "(không có)")

    pairs: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for row in data:
        if index >= len(row):
            continue
        value = row[index].strip().strip('"')
        if not _looks_like_url(value):
            continue
        full = urljoin(base_url, value).split("#")[0]
        if unique and full in seen:
            continue
        seen.add(full)

        dest = None
        if dest_index is not None and dest_index < len(row):
            dest = row[dest_index].strip().strip('"') or None
        pairs.append((full, dest))

    log.info("CSV: đọc được %d URL từ %d dòng dữ liệu (%d dòng có URL đích).",
             len(pairs), len(data), sum(1 for _, d in pairs if d))
    return pairs


def read_urls(path: Path, column: str | None = None, base_url: str = "",
              ) -> list[str]:
    """Như `read_url_pairs` nhưng chỉ lấy danh sách URL nguồn."""
    return [url for url, _ in read_url_pairs(path, column, base_url=base_url)]
