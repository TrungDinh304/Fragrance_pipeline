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

# --- Vòng đời cache -------------------------------------------------------
# Hai loại dữ liệu, hai tuổi thọ khác nhau:
#   - Trang khám phá (danh mục hãng, trang hãng): đây CHÍNH LÀ tín hiệu "có chai
#     mới". Cache vĩnh viễn nghĩa là planner không bao giờ thấy chai mới, trong
#     khi pipeline trông vẫn khoẻ mạnh.
#   - Chi tiết chai đã render: accord/notes của một chai không đổi, mà render
#     lại tốn ~7 giây, nên giữ lâu.
CACHE_TTL_DISCOVERY = 7 * 24 * 3600
CACHE_TTL_RENDERED = 90 * 24 * 3600

# Trần dung lượng cache. Đo thực tế: trang chi tiết đã render ~678 KB/chai, nên
# 150k chai ≈ 101 GB — vượt chỗ trống của ổ đĩa. Quá trần thì xoá dần theo
# mtime cũ nhất.
CACHE_MAX_BYTES = 40 * 1024 ** 3          # 40 GB
CACHE_SWEEP_EVERY = 200                   # số lần ghi giữa 2 lần kiểm trần

# Chặn sớm khi bị rate limit: RATE_LIMIT_BACKOFF đi hết thang mất 21 phút cho
# MỖI url. Nếu đã dính 429 nhiều lần trong một cửa sổ ngắn thì site đang chặn
# thật, dừng ngay thay vì ngủ tiếp.
RATE_LIMIT_TRIP_COUNT = 4
RATE_LIMIT_TRIP_WINDOW = 900              # giây


def raw_dir(site: str) -> Path:
    """Nơi chứa .jsonl đã crawl của một site, vd raw_dir("fragrantica")."""
    return RAW_DIR / site


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
