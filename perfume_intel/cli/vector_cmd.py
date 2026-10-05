"""Hai lệnh cho tầng vector: `embed` (sinh vector) và `vectordb` (nạp/hỏi kho).

Tách thành hai lệnh vì chúng là hai việc khác hẳn nhau về chi phí và tần suất:

    embed      đọc bronze -> ghi data/silver/perfume_embeddings.parquet
               tốn CPU, chạy lại khi dữ liệu mới về (hoặc khi đổi model)
    vectordb   nạp Parquet đó vào Postgres, hoặc hỏi thử
               rẻ, chạy lại bất cứ lúc nào, và kho dựng lại được trong vài giây

Thứ tự đó cũng là thứ tự của nguyên tắc gốc: Parquet là artifact dẫn xuất ở tầng
silver (niêm lên lake), còn Postgres chỉ là kho PHỤC VỤ nạp từ Parquet. Mất kho thì
nạp lại; mất Parquet thì sinh lại từ bronze.
"""

from __future__ import annotations

import argparse
import logging

from .. import config
from ..core import bronze

log = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("embed",
                       help="Sinh embedding cho các chai đã crawl -> Parquet")
    p.add_argument("--model", metavar="TÊN",
                   help="Model embedding (mặc định: xem embedding/onnx.py)")
    p.add_argument("--hashing", action="store_true",
                   help="Dùng embedder hashing thay vì model thật — CHỈ để thử "
                        "đường đi, không có ngữ nghĩa")
    p.add_argument("--community", help="Thư mục bronze (mặc định: "
                                       "data/raw/fragrantica)")
    p.add_argument("--out", help=f"Nơi ghi (mặc định: {config.SILVER_DIR})")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run_embed)

    q = sub.add_parser("vectordb",
                       help="Nạp/hỏi kho vector (Postgres + pgvector)")
    q.add_argument("action", choices=["status", "load", "query"],
                   nargs="?", default="status")
    q.add_argument("text", nargs="?", help="Câu hỏi, dùng với `query`")
    q.add_argument("--limit", type=int, default=5)
    q.add_argument("--hashing", action="store_true",
                   help="Dùng embedder hashing (phải khớp với lúc `embed`)")
    q.add_argument("--dsn", help="Chuỗi kết nối Postgres (mặc định: PG_DSN)")
    q.add_argument("--community", help="Thư mục bronze khi nạp")
    q.add_argument("-v", "--verbose", action="store_true")
    q.set_defaults(func=run_vectordb)


def _embedder(args):
    if getattr(args, "hashing", False):
        from ..embedding import HashingEmbedder
        log.warning("Dùng embedder HASHING: vector không có ngữ nghĩa, chỉ để "
                    "kiểm đường đi của dữ liệu.")
        return HashingEmbedder()
    from ..embedding.onnx import OnnxEmbedder
    return OnnxEmbedder(getattr(args, "model", None))


def _rows(args):
    from pathlib import Path

    from ..analytics import dataset
    goc = Path(args.community) if args.community else config.raw_dir("fragrantica")
    if not bronze.available(goc):
        log.error("Chưa có dữ liệu bronze: %s", goc)
        return None
    return dataset.build(goc)


# ----------------------------------------------------------------- embed
def run_embed(args: argparse.Namespace) -> int:
    from pathlib import Path

    from ..embedding import store
    from ..embedding.ports import EmbeddingUnavailable

    rows = _rows(args)
    if rows is None:
        return 1
    try:
        emb = _embedder(args)
        bo = store.build(emb, rows)
    except EmbeddingUnavailable as exc:
        log.error("%s", exc)
        return 1
    if not len(bo):
        log.error("Không chai nào có đủ dữ liệu để sinh vector.")
        return 1
    dich = store.write(bo, Path(args.out) if args.out else None)
    print(f"{len(bo)} vector · {bo.dimensions} chiều · {bo.model_id}")
    print(f"-> {dich}")
    return 0


# -------------------------------------------------------------- vectordb
def run_vectordb(args: argparse.Namespace) -> int:
    from ..retrieval.pgvector_store import PgUnavailable, PgVectorRetriever, dsn
    return {"status": _vdb_status, "load": _vdb_load,
            "query": _vdb_query}[args.action](args, PgVectorRetriever,
                                              PgUnavailable, dsn)


def _vdb_status(args, Retriever, PgUnavailable, dsn) -> int:
    from ..retrieval.pgvector_store import connect
    print(f"DSN: {args.dsn or dsn()}")
    try:
        con = connect(args.dsn)
    except PgUnavailable as exc:
        log.error("%s", exc)
        print("\nBật kho bằng: docker compose up -d postgres")
        return 1
    try:
        with con.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension "
                        "WHERE extname='vector'")
            ver = cur.fetchone()
            print(f"pgvector: {ver[0] if ver else 'CHƯA BẬT extension'}")
            cur.execute("SELECT to_regclass('perfume_vectors')")
            if cur.fetchone()[0] is None:
                print("Bảng: chưa có. Nạp bằng `perfume-intel vectordb load`.")
                return 0
            cur.execute("SELECT model_id, count(*) FROM perfume_vectors "
                        "GROUP BY model_id")
            for model, n in cur.fetchall():
                print(f"  {n} chai  ·  model {model}")
            cur.execute("SELECT count(*) FROM perfume_terms")
            print(f"  {cur.fetchone()[0]} dòng term (dùng cho phần 'vì sao')")
    finally:
        con.close()
    return 0


def _vdb_load(args, Retriever, PgUnavailable, dsn) -> int:
    from ..embedding.ports import EmbeddingUnavailable
    rows = _rows(args)
    if rows is None:
        return 1
    try:
        r = Retriever.load(rows, _embedder(args), dsn_=args.dsn)
    except (PgUnavailable, EmbeddingUnavailable) as exc:
        log.error("%s", exc)
        return 1
    print(f"Đã nạp {len(r)} chai vào kho vector.")
    return 0


def _vdb_query(args, Retriever, PgUnavailable, dsn) -> int:
    from ..embedding.ports import EmbeddingUnavailable
    from ..retrieval.pgvector_store import connect
    from ..retrieval.ports import Query
    if not args.text:
        log.error('Cần câu hỏi: vectordb query "mùi gỗ ấm cho mùa đông"')
        return 1
    try:
        emb = _embedder(args)
        r = Retriever(connect(args.dsn), embedder=emb, model_id=emb.model_id)
        res = r.search(Query(text=args.text, limit=args.limit, explain=True))
    except (PgUnavailable, EmbeddingUnavailable) as exc:
        log.error("%s", exc)
        return 1
    if res.unknown:
        print(f"Không hiểu: {', '.join(res.unknown)}")
    if not res.matches:
        print("Không có kết quả.")
        return 1
    for m in res.matches:
        print(f"  {m.score:6.3f}  {(m.name or '?')[:40]:<40} {m.brand or ''}")
        if m.why:
            print(f"  {'':6}  └─ " + ", ".join(
                f"{w.block}:{w.label}" for w in m.why))
    return 0
