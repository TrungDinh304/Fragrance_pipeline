"""Cấu hình chung cho crawler."""

import os
from pathlib import Path

BASE_URL = "https://www.fragrantica.com"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Bố cục dữ liệu: nguồn vào -> dữ liệu thô đã crawl -> kết quả phân tích.
#   data/inputs/<site>/*.csv     danh sách URL do người dùng cung cấp
#   data/raw/<site>/*.jsonl      bản ghi crawl được, mỗi hãng một file theo ngày
#   data/processed/              đầu ra của pipeline phân tích
#
# KHI LAKE BẬT (xem `core/lake.py`), `data/raw/` KHÔNG còn là bản gốc: nó là
# spool ghi trước + cache đọc, còn bản chính thức nằm trên MinIO. Xoá nó sau khi
# đã niêm là an toàn. `data/state/` thì vẫn luôn là bản gốc và chỉ của máy này —
# SQLite không chạy được trên S3.
DATA_DIR = PROJECT_ROOT / "data"
INPUTS_DIR = DATA_DIR / "inputs"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
# Sổ theo dõi tiến độ crawl (SQLite). Không phải dữ liệu thu được, mà là trạng
# thái của công việc: hãng nào xong, chai nào còn nợ, lần chạy nào hỏng.
STATE_DIR = DATA_DIR / "state"
STATE_DB = STATE_DIR / "crawl_state.db"

# Tầng silver: cùng dữ liệu bronze nhưng đã khử trùng, có kiểu và trải phẳng
# (Parquet). Tầng gold/marts do dbt sinh ra trong một file DuckDB.
SILVER_DIR = DATA_DIR / "silver"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
WAREHOUSE_DB = WAREHOUSE_DIR / "perfume.duckdb"

# Cache HTML thô: rất nặng, luôn nằm ngoài data/ và không commit.
CACHE_DIR = PROJECT_ROOT / ".cache" / "html"


# --- Data lake (MinIO / S3) -------------------------------------------------
# Khi bật, MinIO là NƠI LƯU CHÍNH THỨC của bronze và silver; `data/raw/` tụt
# xuống thành spool ghi trước + cache đọc. Xem `core/lake.py`.
#
# MẶC ĐỊNH TẮT, có chủ đích: bật mặc định nghĩa là mọi test và mọi lần chạy tay
# đều đòi một MinIO đang sống. `docker-compose.yml` bật nó lên.
#
# Đọc qua HÀM chứ không phải hằng số module, vì hai lý do:
#   - đổi biến môi trường có hiệu lực ngay, không phải reload module;
#   - test đặt được env rồi gọi hàm, không phải vá thuộc tính module.
LAKE_DEFAULT_BUCKET = {"bronze": "bronze", "silver": "silver"}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def lake_kind() -> str:
    """'off' (mặc định) hoặc 's3'. Giá trị lạ bị coi là 'off' và báo một dòng."""
    kind = _env("LAKE", "off").lower()
    if kind in ("", "off", "0", "false", "local", "file"):
        return "off"
    if kind in ("s3", "minio", "1", "true", "on"):
        return "s3"
    import logging
    logging.getLogger(__name__).warning(
        "LAKE=%r không hiểu được — coi như tắt. Chỉ nhận 'off' hoặc 's3'.", kind)
    return "off"


def lake_endpoint() -> str | None:
    """None = S3 thật của AWS. Với MinIO thì bắt buộc có, vd http://minio:9000."""
    return _env("S3_ENDPOINT") or None


def lake_access_key() -> str | None:
    return _env("S3_ACCESS_KEY") or None


def lake_secret_key() -> str | None:
    return _env("S3_SECRET_KEY") or None


def lake_region() -> str:
    return _env("S3_REGION", "us-east-1")


def lake_bucket(layer: str) -> str:
    """Hai bucket riêng, không phải hai prefix trong một bucket.

    Vì hai tầng có GIÁ TRỊ khác nhau, nên phải đặt được chính sách khác nhau:
    bronze là thứ duy nhất không dựng lại được (phải crawl lại nhiều ngày) nên
    bật versioning và không bao giờ hết hạn; silver xoá đi dựng lại trong vài
    giây. Chung một bucket thì không tách được hai chính sách đó.
    """
    if layer not in LAKE_DEFAULT_BUCKET:
        raise ValueError(f"Tầng không có: {layer!r}")
    return _env(f"{layer.upper()}_BUCKET", LAKE_DEFAULT_BUCKET[layer])

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

# --- Ngân sách mỗi lần chạy theo lịch ---------------------------------------
# Fragrantica chặn thiết bị truy cập quá dày. Cách sống chung là nhỏ giọt: mỗi
# ngày chỉ đụng vài hãng và một số request có hạn, phần còn lại để mai.
#
# `DAILY_BRANDS` là số hãng tối đa mỗi lần chạy; `DAILY_BUDGET` là TỔNG số
# request tối đa (tính cả request lấy mục lục — site đếm mọi request). Hãng lớn (Avon 1.379 chai) sẽ tự tràn sang các ngày sau
# — tiến độ được ghi lại ở mức từng chai nên hôm sau đi tiếp đúng chỗ dừng.
DAILY_BRANDS = 2
DAILY_BUDGET = 150

# Bỏ qua chai có ít hơn ngần này bình luận.
#
# Đo trên 7.938 chai đang trong sổ: ngưỡng 5 giữ lại 31% số chai nhưng mang theo
# 96,3% TOÀN BỘ lượng bình luận. 2.987 chai (37,6%) có đúng 0 bình luận — crawl
# chúng là tiêu request để lấy về số không.
#
#   ngưỡng   số chai   % bình luận giữ được   ngày @150/ngày
#        0      7938                  100%               53
#        5      2442                 96,3%               16
#       20       982                 85,9%                7
#
# Đặt 0 để crawl tất cả. Ngưỡng này KHÔNG đổi thứ tự ưu tiên (vốn đã xếp theo
# `comments` giảm dần) — nó cho phép DỪNG SỚM thay vì cào nốt phần đuôi dài mà
# không ai bàn tới.
DAILY_MIN_COMMENTS = 5

# Hãng lỗi thì nghỉ bao lâu trước khi thử lại (giờ), theo số lần lỗi liên tiếp.
BRAND_COOLDOWN_HOURS = (6, 24, 72)


def raw_dir(site: str, kind: str | None = None) -> Path:
    """Nơi chứa .jsonl đã crawl của một site.

        raw_dir("fragrantica")              -> data/raw/fragrantica
        raw_dir("fragrantica", "perfumes")  -> data/raw/fragrantica/perfumes

    `kind` là một trong `core.bronze.KINDS`. Mỗi loại bản ghi một thư mục, vì ba
    loại (chi tiết chai / danh mục hãng / mục lục chai) có khoá chính khác nhau;
    để chung một chỗ thì phía đọc phải lọc, và đã có lúc lọc im lặng mất 97% số
    dòng. Xem `core/bronze.py`.
    """
    base = RAW_DIR / site
    return base / kind if kind else base


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
