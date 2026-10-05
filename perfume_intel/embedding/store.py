"""Đọc/ghi tập vector ra Parquet ở tầng silver.

Vector là ARTIFACT DẪN XUẤT, nên nó nằm đúng chỗ của mọi artifact dẫn xuất khác:
`data/silver/perfume_embeddings.parquet`, rồi niêm lên lake như các bảng silver.
Kho vector (pgvector) chỉ NẠP từ file này, không bao giờ là bản gốc — nên xoá bảng
trong Postgres là chuyện vặt, còn mất file thì sinh lại từ bronze.

`model_id` và số chiều được ghi THÀNH CỘT, không ghi vào tên file. Tên file thì
người ta đổi, còn cột thì đi theo dữ liệu. Nhờ vậy nạp vào pgvector là kiểm được
ngay có đúng model không, trước khi kịp trộn hai loại vector vào một bảng.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import config
from ..core import lake
from .ports import Embedding, EmbeddingSet, ModelMismatch

log = logging.getLogger(__name__)

TEN_FILE = "perfume_embeddings.parquet"


def duong_dan(out_dir: Path | None = None) -> Path:
    return Path(out_dir or config.SILVER_DIR) / TEN_FILE


def _connect():
    from ..warehouse.silver import connect
    return connect(":memory:")


def write(bo: EmbeddingSet, out_dir: Path | None = None) -> Path:
    """Ghi tập vector ra Parquet rồi niêm lên lake."""
    dich = duong_dan(out_dir)
    dich.parent.mkdir(parents=True, exist_ok=True)

    con = _connect()
    try:
        con.execute(
            'CREATE OR REPLACE TABLE emb ("perfume_key" VARCHAR, '
            '"model_id" VARCHAR, "dimensions" INTEGER, '
            '"vector" DOUBLE[], "document" VARCHAR)')
        con.executemany(
            "INSERT INTO emb VALUES (?, ?, ?, ?, ?)",
            [(e.key, bo.model_id, bo.dimensions, list(e.vector),
              bo.documents.get(e.key, "")) for e in bo.items])
        con.execute(f"COPY emb TO '{dich.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()

    log.info("%d vector (%s, %d chiều) -> %s",
             len(bo), bo.model_id, bo.dimensions, dich)
    lake.seal(dich, lake.SILVER)
    return dich


def read(out_dir: Path | None = None,
         expect_model: str | None = None) -> EmbeddingSet:
    """Nạp tập vector. Kéo từ lake về trước nếu máy này chưa có.

    `expect_model` có mặt để chỗ nạp vào kho vector không phải tự nhớ kiểm — trộn
    vector của hai model là lỗi không bao giờ tự lộ ra.
    """
    nguon = Path(out_dir or config.SILVER_DIR)
    lake.ensure_local(nguon, lake.SILVER)
    dich = duong_dan(nguon)
    if not dich.is_file():
        raise FileNotFoundError(
            f"Chưa có {dich}. Sinh bằng `perfume-intel embed` "
            f"(hoặc `docker compose run --rm cli embed`).")

    con = _connect()
    try:
        hang = con.execute(
            "SELECT perfume_key, model_id, dimensions, vector, document "
            f"FROM read_parquet('{dich.as_posix()}')").fetchall()
    finally:
        con.close()

    if not hang:
        raise ValueError(f"{dich} rỗng — sinh lại bằng `perfume-intel embed`.")

    models = {r[1] for r in hang}
    if len(models) > 1:
        raise ModelMismatch(
            f"{dich} chứa vector của nhiều model: {sorted(models)}. "
            f"Sinh lại toàn bộ, KHÔNG trộn.")
    model_id = hang[0][1]
    if expect_model and model_id != expect_model:
        raise ModelMismatch(
            f"{dich} sinh bởi {model_id!r} nhưng đang cần {expect_model!r}. "
            f"Sinh lại bằng `perfume-intel embed`.")

    return EmbeddingSet(
        model_id=model_id, dimensions=int(hang[0][2]),
        items=tuple(Embedding(key=r[0], vector=tuple(r[3])) for r in hang),
        documents={r[0]: (r[4] or "") for r in hang})


def build(embedder, rows, batch: int = 128) -> EmbeddingSet:
    """Sinh vector cho một danh sách Row.

    Chia lô vì model nạp cả lô một lượt nhanh hơn từng câu, nhưng lô quá lớn thì
    ngốn RAM mà không nhanh thêm.
    """
    from .document import documents
    tai_lieu = documents(list(rows))
    khoa = sorted(tai_lieu)
    items: list[Embedding] = []
    for i in range(0, len(khoa), batch):
        phan = khoa[i:i + batch]
        for k, v in zip(phan, embedder.embed_documents(
                [tai_lieu[k] for k in phan])):
            items.append(Embedding(key=k, vector=tuple(v)))
        log.info("  embed %d/%d", min(i + batch, len(khoa)), len(khoa))
    return EmbeddingSet(model_id=embedder.model_id,
                        dimensions=embedder.dimensions,
                        items=tuple(items), documents=tai_lieu)
