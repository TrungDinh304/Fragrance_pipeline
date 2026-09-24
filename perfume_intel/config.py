"""Cấu hình chung cho crawler."""

from pathlib import Path

BASE_URL = "https://www.fragrantica.com"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Bố cục dữ liệu: nguồn vào -> dữ liệu thô đã crawl -> kết quả phân tích.
#   data/inputs/<site>/*.csv     danh sách URL do người dùng cung cấp
#   data/raw/<site>/*.jsonl      bản ghi crawl được, mỗi hãng một file theo ngày
#   data/processed/              đầu ra của pipeline phân tích
DATA_DIR = PROJECT_ROOT / "data"
INPUTS_DIR = DATA_DIR / "inputs"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# Cache HTML thô: rất nặng, luôn nằm ngoài data/ và không commit.
CACHE_DIR = PROJECT_ROOT / ".cache" / "html"


def raw_dir(site: str) -> Path:
    """Nơi chứa .jsonl đã crawl của một site, vd raw_dir("fragrantica")."""
    return RAW_DIR / site


def inputs_dir(site: str) -> Path:
    """Nơi chứa .csv đầu vào của một site."""
    return INPUTS_DIR / site

# Lịch sự với server: nghỉ ngẫu nhiên giữa 2 request (giây).
# Fragrantica là site nhỏ và có rate limit thật (429). Crawl dày sẽ bị chặn cả
# IP; chạy chậm mà đều còn nhanh hơn là bị chặn rồi phải chờ hàng giờ.
DELAY_MIN = 5.0
DELAY_MAX = 10.0

# Retry khi lỗi mạng / bị chặn tạm thời.
MAX_RETRIES = 3
BACKOFF_FACTOR = 2.0      # 2s, 4s, 8s ...
TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

def _accept_encoding() -> str:
    """Chỉ khai báo những kiểu nén mà requests thật sự giải mã được.

    Nếu khai 'br' mà thiếu package brotli, server trả về body nén và requests
    giao lại chuỗi nhị phân -> parser không tìm thấy gì.
    """
    encodings = ["gzip", "deflate"]
    for module, name in (("brotli", "br"), ("zstandard", "zstd")):
        try:
            __import__(module)
        except ImportError:
            continue
        encodings.append(name)
    return ", ".join(encodings)


DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": _accept_encoding(),
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
