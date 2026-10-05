"""Adapter `Retriever` thứ hai: Postgres + pgvector, qua ĐÚNG bộ hợp đồng cũ.

Đây là bài kiểm chứng rằng cổng `Retriever` thật sự là một cổng. Nếu
`PgVectorRetriever` qua được cả 26 điều khoản mà không ai phía trên phải sửa một
dòng nào, thì ranh giới đó có giá trị thật. Nếu phải sửa, nó chỉ là chữ.

Cần Postgres + pgvector đang chạy. Không có thì BỎ QUA kèm lý do:

    docker compose up -d postgres
    PG_DSN=postgresql://perfume:perfume-dev-only@localhost:5433/perfume \
      python tests/test_pgvector.py

Dùng `HashingEmbedder` chứ không phải model thật — có chủ đích. Hợp đồng kiểm
HÀNH VI (khoá, thứ tự, bộ lọc, báo lại), không kiểm chất lượng ngữ nghĩa; và bản
hashing chạy được trên Windows, nơi onnxruntime không nạp nổi DLL.
"""

from __future__ import annotations

import os

from runner import run  # noqa: E402

import retrieval_contract as contract  # noqa: E402
from perfume_intel.embedding import HashingEmbedder  # noqa: E402
from perfume_intel.retrieval.ports import Query  # noqa: E402
from perfume_intel.retrieval.pgvector_store import (PgUnavailable,  # noqa: E402
                                                    PgVectorRetriever, connect)

DSN = os.environ.get("PG_DSN") or \
    "postgresql://perfume:perfume-dev-only@localhost:5433/perfume"


# Bảng RIÊNG cho test. Thiếu tiền tố này thì bộ test TRUNCATE đúng bảng mà
# production đang dùng — đã xảy ra thật: test tạo `perfume_vectors` dạng
# vector(64) của embedder hashing, rồi lần nạp model thật (384 chiều) đổ ngay với
# `expected 64 dimensions, not 384`.
PREFIX = "t_contract_"


def _skip(ly_do: str) -> bool:
    print(f"SKIP  {ly_do}")
    return True


def _con():
    """Kết nối, hoặc None nếu không có Postgres."""
    try:
        return connect(DSN)
    except PgUnavailable as exc:
        print(f"      ({str(exc)[:90]})")
        return None


def test_hop_dong_pgvector():
    con = _con()
    if con is None and _skip("test_hop_dong_pgvector: chưa có Postgres+pgvector"):
        return
    try:
        contract.check_all(
            lambda rows: PgVectorRetriever.load(
                rows, HashingEmbedder(), con=con, prefix=PREFIX))
    finally:
        con.close()


def test_tinh_toan_nam_trong_sql_khong_nap_vao_ram():
    """Nếu adapter âm thầm giữ dữ liệu trong RAM thì kho vector vô nghĩa.

    Cách kiểm: nạp xong, XOÁ SẠCH bảng bằng một kết nối khác. Nếu retriever vẫn
    trả về kết quả thì nó đang đọc bộ nhớ chứ không đọc Postgres — tức là tiến
    trình vẫn phải chứa toàn bộ dữ liệu, đúng cái giới hạn mà người ta dựng kho
    vector để thoát khỏi.
    """
    con = _con()
    if con is None and _skip("test_tinh_toan_nam_trong_sql: chưa có Postgres"):
        return
    try:
        r = PgVectorRetriever.load(contract.rows(), HashingEmbedder(),
                                   con=con, prefix=PREFIX)
        assert r.search(Query(notes=("oud",), limit=3)).matches, "fixture hỏng"
        with connect(DSN) as khac:
            with khac.cursor() as cur:
                cur.execute(f"TRUNCATE {PREFIX}perfume_vectors")
                cur.execute(f"TRUNCATE {PREFIX}perfume_terms")
        assert not r.search(Query(notes=("oud",), limit=3)).matches, \
            "bảng đã rỗng mà vẫn trả kết quả — dữ liệu đang nằm trong RAM"
        assert len(r) == 0
    finally:
        con.close()


def test_mo_hinh_sai_thi_bao_loi():
    """Trộn vector hai model không gây lỗi — nó chỉ làm kết quả sai lặng lẽ."""
    from perfume_intel.embedding.ports import ModelMismatch
    con = _con()
    if con is None and _skip("test_mo_hinh_sai: chưa có Postgres"):
        return
    try:
        PgVectorRetriever.load(contract.rows(), HashingEmbedder(), con=con,
                               prefix=PREFIX)
        # Cùng bảng, nhưng hỏi bằng tên model khác.
        khac = PgVectorRetriever(con, embedder=HashingEmbedder(),
                                 model_id="model-khac", prefix=PREFIX)
        try:
            khac.search(Query(notes=("oud",)))
        except ModelMismatch:
            return
        raise AssertionError("hỏi bằng model khác mà không báo lỗi")
    finally:
        con.close()


def test_bo_test_khong_duoc_cham_bang_that():
    """Canh chính cái bẫy vừa dính, chứ không chỉ sửa một lần.

    Mọi lệnh SQL trong file này phải đi qua `PREFIX`. Nếu ai đó thêm một bài test
    mới gọi thẳng `perfume_vectors`, nó sẽ TRUNCATE kho đang phục vụ — và không
    một test nào khác đỏ để báo.
    """
    import re
    from pathlib import Path as P
    src = P(__file__).read_text(encoding="utf-8")
    src = src.split("def test_bo_test_khong_duoc_cham_bang_that")[0]
    xau = [d.strip() for d in src.splitlines()
           if re.search(r"(TRUNCATE|DROP|INSERT INTO|DELETE FROM)\s+perfume_", d)]
    assert not xau, "SQL cham thang bang that, phai dung PREFIX: " + "; ".join(xau)


if __name__ == "__main__":
    raise SystemExit(run(globals()))
