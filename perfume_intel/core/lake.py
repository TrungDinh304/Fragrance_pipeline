"""Data lake: MinIO/S3 là NƠI LƯU CHÍNH THỨC của bronze và silver.

VAI TRÒ MỚI CỦA `data/raw/` — ĐỌC KỸ ĐOẠN NÀY
Trước đây `data/raw/` là bản gốc. Từ giờ nó là HAI thứ khác, cùng nằm một chỗ:

  - **spool ghi trước** — crawler vẫn append từng dòng vào đây ngay khi có.
  - **cache đọc** — bản sao cục bộ của những gì đã có trên lake.

Bản chính thức nằm trên lake. Xoá `data/raw/` sau khi đã niêm là an toàn; xoá
bucket thì không.

VÌ SAO PHẢI CÓ SPOOL, KHÔNG GHI THẲNG LÊN S3
S3 không append. Ghi thẳng chỉ còn hai cách, cả hai đều tệ:

  - PUT lại NGUYÊN file sau mỗi chai. File của Avon (1.379 chai) tới cuối nặng
    ~4 MB, nên tổng lưu lượng cho một hãng là tổng cấp số cộng ≈ 2,8 GB. Với
    MinIO ở localhost thì vẫn chạy, nhưng đổi sang S3 thật là trả tiền cho đúng
    một việc vô nghĩa.
  - Mỗi chai một object. 150k object nhỏ, và `list` trở thành thứ đắt nhất trong
    pipeline — mà `list` chạy ở MỌI lần đọc.

Spool giải cả hai: append local (rẻ, an toàn khi mất điện), và **niêm** một lần
mỗi hãng -> ~8k object, mỗi object một lần PUT.

LUẬT ĐỒNG BỘ: FILE DÀI HƠN THẮNG
Bronze là append-only, nên số byte LÀ thước đo "bên nào có nhiều dữ liệu hơn".
  - local dài hơn remote  -> giữ local (đang crawl dở, chưa niêm), rồi niêm lên.
  - remote dài hơn local  -> tải về (máy khác đã crawl thêm).
  - bằng nhau             -> không làm gì.
Cùng tinh thần với `site_cooldown` ("khoá lâu hơn thắng"): khi hai bên không
đồng ý, chọn phía KHÔNG mất mát.

GIỚI HẠN CÒN LẠI, CỐ Ý KHÔNG GIẢI Ở ĐÂY
Sổ tiến độ `data/state/crawl_state.db` là SQLite, KHÔNG chạy được trên S3 (nó
cần lock theo byte-range trên một file tại chỗ). Nên lake làm cho dữ liệu thu
được dùng chung được, nhưng **hàng đợi thì vẫn của riêng từng máy**. Hệ quả: hai
máy crawl cùng lúc sẽ làm trùng việc và ghi đè file của nhau. Muốn nhiều máy
crawl song song thì phải chuyển state sang Postgres trước — xem
`docs/ARCHITECTURE.md`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .. import config
from .objects import (LocalStore, ObjectInfo, ObjectNotFound, ObjectStore,
                      S3Store, StoreUnavailable, check_key)

log = logging.getLogger(__name__)

# Đuôi file được coi là dữ liệu bronze. Cố ý KHÔNG niêm mọi thứ: thư mục này
# cũng có file tạm và file do người dùng bỏ vào.
DATA_SUFFIXES = (".jsonl", ".csv")

BRONZE = "bronze"
SILVER = "silver"
LAYERS = (BRONZE, SILVER)

# Đã đồng bộ prefix nào trong tiến trình này. Một lần chạy `analyze` gọi
# `bronze.scan` ba lần (ba loại bản ghi); không có bộ nhớ này thì mỗi lần là một
# cuộc `list` qua mạng cho cùng một thứ.
_da_dong_bo: set[str] = set()


# ------------------------------------------------------------------ bật/tắt
def enabled() -> bool:
    """Lake có đang bật không.

    MẶC ĐỊNH TẮT, có chủ đích. Bật mặc định nghĩa là mọi test và mọi lần chạy tay
    đều cần một MinIO đang sống; một project phân tích dữ liệu không nên hỏng vì
    thiếu hạ tầng mà nó chỉ cần khi chạy thật. `docker-compose.yml` bật nó lên.
    """
    return config.lake_kind() == "s3"


def root_of(layer: str) -> Path:
    """Thư mục local của một tầng: spool+cache khi lake bật, bản gốc khi tắt."""
    if layer not in LAYERS:
        raise ValueError(f"Tầng không có: {layer!r}. Chỉ có: {', '.join(LAYERS)}.")
    return config.RAW_DIR if layer == BRONZE else config.SILVER_DIR


def store(layer: str = BRONZE) -> ObjectStore:
    """Kho của một tầng. Lake tắt thì trả về kho local, cùng hợp đồng."""
    if not enabled():
        return LocalStore(root_of(layer))
    return S3Store(bucket=config.lake_bucket(layer),
                   endpoint=config.lake_endpoint(),
                   access_key=config.lake_access_key(),
                   secret_key=config.lake_secret_key(),
                   region=config.lake_region())


def describe() -> str:
    if not enabled():
        return f"tắt (bronze nằm ở {config.RAW_DIR})"
    return f"{store(BRONZE)} + {store(SILVER)}"


# ------------------------------------------------------------------ khoá
def key_for(path: Path | str, layer: str = BRONZE) -> str | None:
    """Khoá trên lake của một file, hoặc None nếu nó không thuộc tầng đó.

    Khoá = ĐÚNG đường dẫn tương đối dưới thư mục của tầng, viết kiểu posix. Cố ý
    không có bảng ánh xạ nào: round-trip chính xác, và bố cục phẳng kiểu cũ
    (`fragrantica/Chanel_fragrantica_210926.jsonl`) lên lake không cần luật
    riêng — chính nó là khoá.

    Trả về None cho đường dẫn NẰM NGOÀI thư mục của tầng. Đó là cái chốt giữ cho
    mọi test chạy trên thư mục tạm không bao giờ đụng tới mạng.
    """
    try:
        rel = Path(path).resolve().relative_to(root_of(layer).resolve())
    except (ValueError, OSError):
        return None
    return check_key(rel.as_posix()) if rel.parts else None


def local_path(key: str, layer: str = BRONZE) -> Path:
    return root_of(layer) / check_key(key)


def prefix_for(root: Path | str, layer: str = BRONZE) -> str | None:
    """Prefix cần đồng bộ cho một thư mục.

    `bronze.scan` nhận `data/raw/fragrantica` rồi tự quét cả thư mục con lẫn file
    phẳng ở gốc, nên prefix phải là cả site: `fragrantica/`. Với chính thư mục
    gốc của tầng (vd `data/silver`) thì prefix là chuỗi rỗng — tức cả tầng.
    """
    try:
        rel = Path(root).resolve().relative_to(root_of(layer).resolve())
    except (ValueError, OSError):
        return None
    if not rel.parts:
        return ""
    return f"{check_key(rel.as_posix())}/"


# ------------------------------------------------------------------ niêm
def seal(path: Path, layer: str = BRONZE, force: bool = False) -> ObjectInfo | None:
    """Đẩy một file bronze lên lake. Trả về None nếu không cần hoặc không thể.

    KHÔNG BAO GIỜ NÉM LỖI RA NGOÀI. Hàm này được gọi ở cuối mỗi hãng, giữa một mẻ
    crawl nhiều ngày. MinIO chết là chuyện của hạ tầng; nó không được phép giết
    mẻ crawl, vì dữ liệu vẫn còn nguyên trong spool và niêm lại được sau bằng
    `perfume-intel lake push`.
    """
    path = Path(path)
    if not enabled() or not path.is_file():
        return None
    key = key_for(path, layer)
    if key is None:
        return None

    try:
        kho = store(layer)
        if not force:
            tren_lake = kho.stat(key)
            if tren_lake is not None and tren_lake.size >= path.stat().st_size:
                return None             # lake đã có đủ, khỏi gửi lại
        info = kho.put(key, path.read_bytes())
    except (StoreUnavailable, ObjectNotFound, OSError) as exc:
        log.warning("Chưa niêm được %s lên lake (%s). Dữ liệu còn trong spool, "
                    "niêm lại bằng `perfume-intel lake push`.", path.name, exc)
        return None
    log.info("Đã niêm %s -> %s (%s)", path.name, info.key, _goi(info.size))
    return info


def seal_base(out_base: Path, layer: str = BRONZE) -> None:
    """Niêm cặp file kết quả của một hãng (.jsonl và .csv nếu có).

    `crawl_urls` làm việc theo `out_base` không đuôi, nên đây là cái móc đúng chỗ
    để gọi sau mỗi hãng.
    """
    for suffix in DATA_SUFFIXES:
        seal(Path(out_base).with_suffix(suffix), layer)


def seal_dir(root: Path, layer: str = BRONZE) -> tuple[int, int]:
    """Niêm mọi file dưới `root` chưa có (hoặc ngắn hơn) trên lake.

    Trả về (số file đã niêm, số file bỏ qua vì lake đã đủ). Chạy được nhiều lần,
    và đây là cách dọn những file còn sót vì lần trước tiến trình chết giữa hãng.

    Hỏi lake bằng MỘT lần `list`, không phải `stat` từng file: kho đang có ~8.000
    file, nên kiểu `stat` từng cái là 8.000 vòng HTTP cho một việc mà một request
    trả lời xong. Đó là khác biệt giữa "chạy được ở đầu mỗi lượt crawl" và "không
    ai dám gọi".
    """
    root = Path(root)
    if not enabled() or not root.is_dir():
        return 0, 0
    # Silver là Parquet, bronze là JSONL/CSV. Niêm sai đuôi thì tầng silver
    # trông như rỗng trên lake dù local đã dựng xong.
    duoi = DATA_SUFFIXES if layer == BRONZE else (".parquet",)
    prefix = prefix_for(root, layer)
    if prefix is None:
        return 0, 0
    try:
        kho = store(layer)
        da_co = {info.key: info.size for info in kho.list(prefix)}
    except (StoreUnavailable, ObjectNotFound) as exc:
        log.warning("Không đọc được lake để niêm (%s).", exc)
        return 0, 0

    niem = bo_qua = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in duoi:
            continue
        key = key_for(path, layer)
        if key is None:
            continue
        if da_co.get(key, -1) >= path.stat().st_size:
            bo_qua += 1
            continue
        try:
            kho.put(key, path.read_bytes())
        except (StoreUnavailable, ObjectNotFound, OSError) as exc:
            log.warning("Chưa niêm được %s (%s).", path.name, exc)
            continue
        niem += 1
    if niem:
        log.info("Đã niêm %d file lên %s (%d file lake đã có đủ).",
                 niem, kho, bo_qua)
    return niem, bo_qua


# ------------------------------------------------------------------ kéo về
def ensure_local(root: Path, layer: str = BRONZE, force: bool = False) -> int:
    """Kéo về những gì lake có mà máy này thiếu. Trả về số file đã tải.

    Gọi từ `bronze.scan`, nên nó phải RẺ khi không có gì mới: một lần `list` cho
    mỗi prefix trong mỗi tiến trình, nhờ `_da_dong_bo`.

    Cũng KHÔNG ném lỗi ra ngoài: mất MinIO thì `analyze` vẫn phải chạy được trên
    phần dữ liệu đang có trong cache, chỉ cần nói rõ là đang chạy trên bản cũ.
    """
    if not enabled():
        return 0
    prefix = prefix_for(root, layer)
    if prefix is None:
        return 0
    # Khoá bộ nhớ phải gồm cả tầng: prefix của gốc mỗi tầng đều là chuỗi rỗng,
    # nên thiếu tiền tố này thì đồng bộ bronze xong sẽ coi như silver cũng xong.
    memo = f"{layer}:{prefix}"
    if not force and memo in _da_dong_bo:
        return 0

    try:
        tren_lake = store(layer).list(prefix)
    except (StoreUnavailable, ObjectNotFound) as exc:
        log.warning("Không đọc được lake (%s). Dùng bản cache trong %s — có thể "
                    "thiếu dữ liệu máy khác vừa crawl.", exc, config.RAW_DIR)
        return 0

    _da_dong_bo.add(memo)
    kho = store(layer)
    tai = 0
    for info in tren_lake:
        dich = local_path(info.key, layer)
        o_day = dich.stat().st_size if dich.is_file() else -1
        # Bronze chỉ dài thêm, nên số byte là thước đo "bên nào nhiều hơn". Local
        # dài hơn = đang crawl dở, chưa niêm -> KHÔNG được ghi đè.
        if o_day >= info.size:
            continue
        try:
            data = kho.get(info.key)
        except (StoreUnavailable, ObjectNotFound) as exc:
            log.warning("Bỏ qua %s: %s", info.key, exc)
            continue
        dich.parent.mkdir(parents=True, exist_ok=True)
        tmp = dich.with_name(f"{dich.name}.__pull{os.getpid()}")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, dich)
        finally:
            tmp.unlink(missing_ok=True)
        tai += 1
    if tai:
        log.info("Lấy từ lake về %d file (%s).", tai, prefix)
    return tai


def forget_sync() -> None:
    """Quên bộ nhớ đã-đồng-bộ. Dùng trong test, và khi muốn ép đồng bộ lại."""
    _da_dong_bo.clear()


# ------------------------------------------------------------------ thống kê
def _goi(so_byte: int) -> str:
    for don_vi in ("B", "KB", "MB", "GB"):
        if so_byte < 1024 or don_vi == "GB":
            return f"{so_byte:,.0f} {don_vi}".replace(",", ".")
        so_byte /= 1024.0
    return f"{so_byte} B"


def status(layer: str = BRONZE) -> dict:
    """Số liệu hai bên để `lake status` in ra, không ném lỗi."""
    root = root_of(layer)
    local = [p for p in root.rglob("*")
             if p.is_file() and p.suffix.lower() in (*DATA_SUFFIXES, ".parquet")] \
        if root.is_dir() else []
    out = {"layer": layer, "enabled": enabled(), "where": describe(),
           "local_files": len(local),
           "local_bytes": sum(p.stat().st_size for p in local),
           "lake_files": None, "lake_bytes": None, "error": None}
    if not enabled():
        return out
    try:
        tren_lake = store(layer).list()
    except (StoreUnavailable, ObjectNotFound) as exc:
        out["error"] = str(exc)
        return out
    out["lake_files"] = len(tren_lake)
    out["lake_bytes"] = sum(i.size for i in tren_lake)
    return out
