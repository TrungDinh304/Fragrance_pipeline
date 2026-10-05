"""API + trang test chatbot. Tầng này KHÔNG biết gì về vector hay SQL.

Nó chỉ nhìn thấy ba cổng:

    retrieval.ports.Retriever    tra cứu  (pgvector, hoặc in-memory)
    llm.ports.ChatModel          model    (9router, hoặc gì khác)
    embedding.ports.Embedder     embedding (ONNX local)

Đổi backend nào cũng không phải sửa file này — `tests/test_api.py` có một bài canh
đúng điều đó bằng cách chạy toàn bộ API trên hai retriever khác nhau.

LUỒNG MỘT LƯỢT CHAT, VÀ CHỖ NÀO CHỐNG BỊA

    câu tiếng Việt
       │  llm/intent.py   model đề xuất, TỪ VỰNG quyết định (bỏ nhãn bịa)
       ▼
    Query (notes/accords/occasions/gender/text có dấu)
       │  retrieval        pgvector: vector đặc + khối thưa cho lý do
       ▼
    SearchResult (kèm `why`)
       │  llm/advise.py   model CHỈ được dùng danh sách này; verify() soát lại
       ▼
    câu tư vấn + danh sách gốc hiện cạnh nhau

TỰ XUỐNG CẤP, KHÔNG TỰ CHẾT
Thiếu khoá LLM -> vẫn tìm được, chỉ mất phần diễn đạt. Thiếu Postgres -> rơi về
in-memory. Trang vẫn dùng được và NÓI RÕ đang thiếu gì, vì một trang test báo lỗi
trắng thì không test được gì.
"""

#
# KHÔNG dùng `from __future__ import annotations` ở file này, dù cả project dùng.
# Lý do cụ thể: future import biến mọi annotation thành CHUỖI, và FastAPI phân
# giải chuỗi đó trong namespace của MODULE. Các model Pydantic ở đây khai bên
# trong `create_app()` nên không có trong namespace module -> FastAPI không nhận
# ra chúng là body, và hiểu `inp` thành query param. Triệu chứng là
# `422 {"loc":["query","inp"],"msg":"Field required"}` — không hề nhắc tới
# annotation, nên rất dễ đi tìm sai chỗ.
#
import logging
import os
from pathlib import Path

from .. import config
from ..retrieval.ports import Query, Retriever, SearchResult, UnknownPerfume

log = logging.getLogger(__name__)

TINH = Path(__file__).resolve().parent / "static"

# Trạng thái dùng chung, dựng một lần lúc bật. Nạp index là việc tốn vài chục ms
# tới vài giây; làm lại mỗi request thì trang không dùng được.
_trang_thai: dict = {}


# --------------------------------------------------------------- dựng backend
def _retriever() -> tuple[Retriever, str]:
    """pgvector nếu nối được, nếu không thì in-memory. Trả về (retriever, mô tả)."""
    if os.environ.get("RETRIEVER", "").lower() == "memory":
        return _in_memory(), "in-memory (RETRIEVER=memory)"
    try:
        from ..embedding.onnx import OnnxEmbedder
        from ..retrieval.pgvector_store import PgVectorRetriever, connect
        emb = OnnxEmbedder()
        emb.dimensions                      # ép nạp model để lỗi hiện ra ở đây
        con = connect()
        r = PgVectorRetriever(con, embedder=emb)
        if len(r) == 0:
            raise RuntimeError(
                "bảng pgvector rỗng — nạp bằng `cli vectordb load`")
        return r, f"pgvector ({len(r)} chai, {emb.model_id})"
    except Exception as exc:                            # noqa: BLE001
        log.warning("Không dùng được pgvector (%s) — rơi về in-memory.",
                    str(exc)[:160])
        return _in_memory(), f"in-memory (pgvector lỗi: {str(exc)[:90]})"


def _in_memory() -> Retriever:
    from ..retrieval import InMemoryRetriever
    return InMemoryRetriever.from_bronze(config.raw_dir("fragrantica"))


def _chat_model():
    from ..llm.router import RouterChatModel, configured
    return RouterChatModel() if configured() else None


def state() -> dict:
    if not _trang_thai:
        r, mo_ta = _retriever()
        _trang_thai.update(retriever=r, retriever_info=mo_ta,
                           chat=_chat_model())
    return _trang_thai


# ------------------------------------------------------------------ chuyển đổi
def _match_json(m) -> dict:
    return {"perfume_key": m.perfume_key, "score": round(m.score, 4),
            "name": m.name, "brand": m.brand, "url": m.url,
            "rating": m.rating, "rating_count": m.rating_count,
            "gender": m.gender,
            "why": [{"block": w.block, "label": w.label, "weight": w.weight}
                    for w in m.why]}


def _result_json(res: SearchResult) -> dict:
    return {"matches": [_match_json(m) for m in res.matches],
            "resolved": dict(res.resolved), "unknown": list(res.unknown),
            "seed": _match_json(res.seed) if res.seed else None}


def create_app():
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel
    except ImportError as exc:                          # pragma: no cover
        raise RuntimeError(
            'Cần FastAPI. Cài bằng: pip install -e ".[api]"') from exc

    app = FastAPI(title="perfume-intel", version="0.3.0",
                  description="Tra cứu nước hoa theo tín hiệu cộng đồng.")

    class ChatIn(BaseModel):
        message: str
        limit: int = 5

    class SearchIn(BaseModel):
        text: str | None = None
        notes: list[str] = []
        accords: list[str] = []
        occasions: list[str] = []
        like_perfume: str | None = None
        gender: str | None = None
        min_votes: int = 0
        limit: int = 10
        explain: bool = True

    @app.get("/health")
    def health() -> dict:
        st = state()
        from ..core import lake
        from ..llm.router import base_url, configured, model_name
        return {
            "ok": True,
            "retriever": st["retriever_info"],
            "perfumes": len(st["retriever"]),
            "lake": lake.describe(),
            "llm": {"configured": configured(), "model": model_name(),
                    "base_url": base_url()},
        }

    @app.post("/search")
    def search(q: SearchIn) -> dict:
        st = state()
        try:
            res = st["retriever"].search(Query(
                like_perfume=q.like_perfume, notes=tuple(q.notes),
                accords=tuple(q.accords), occasions=tuple(q.occasions),
                text=q.text, gender=q.gender, min_votes=q.min_votes,
                limit=min(q.limit, 50), explain=q.explain))
        except UnknownPerfume as exc:
            raise HTTPException(404, f"Không có chai nào khớp {exc}") from exc
        return _result_json(res)

    @app.post("/chat")
    def chat(inp: ChatIn) -> dict:
        """Một lượt: tách ý định -> tra cứu -> diễn đạt. Xem docstring module."""
        from ..llm import advise as ad
        from ..llm import intent as it

        st = state()
        model = st["chat"]
        cau = (inp.message or "").strip()
        if not cau:
            raise HTTPException(400, "message rỗng")

        q = it.extract(cau, model, st["retriever"]) if model else it.fallback(cau)

        # CHỌN NHÁNH: có note/accord cụ thể thì đi nhánh term (chính xác và giải
        # thích được). Chỉ có hoàn cảnh thì đi nhánh ngữ nghĩa, vì hoàn cảnh một
        # mình là tín hiệu yếu — mọi chai đều có điểm cho mọi mùa — còn câu tự do
        # thì chứa phần mùi thật sự quan trọng.
        #
        # Không có luật này thì "mùi gỗ trầm ấm cho buổi tối mùa đông" đi nhánh
        # term chỉ với winter+night và bỏ hẳn "gỗ trầm".
        co_mui = bool(q.notes or q.accords)
        q = Query(notes=q.notes
                  if co_mui else (), accords=q.accords if co_mui else (),
                  occasions=q.occasions, text=None if co_mui else q.text,
                  gender=q.gender, limit=min(inp.limit, 20), explain=True)
        res = st["retriever"].search(q)

        if model:
            tra_loi, nghi = ad.advise(cau, res, model)
        else:
            tra_loi, nghi = ad.summary(res), []

        return {
            "answer": tra_loi,
            # Trả về CẢ hai: câu diễn đạt và dữ liệu gốc. Giao diện hiện cạnh
            # nhau để người đọc đối chiếu được, không phải tin lời model.
            "matches": [_match_json(m) for m in res.matches],
            "intent": {"notes": list(q.notes), "accords": list(q.accords),
                       "occasions": list(q.occasions), "gender": q.gender,
                       "text": q.text},
            "resolved": dict(res.resolved), "unknown": list(res.unknown),
            "suspicious": nghi,
            "llm": bool(model),
        }

    @app.get("/vocabulary/{block}")
    def vocabulary(block: str) -> dict:
        try:
            tu = list(state()["retriever"].vocabulary(block))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"block": block, "count": len(tu), "items": tu[:500]}

    if TINH.is_dir():
        app.mount("/static", StaticFiles(directory=str(TINH)), name="static")

        @app.get("/")
        def trang_chu():
            return FileResponse(str(TINH / "index.html"))

    return app


app = None


def main() -> int:                                      # pragma: no cover
    import uvicorn
    uvicorn.run("perfume_intel.api.app:create_app", factory=True,
                host=os.environ.get("API_HOST", "0.0.0.0"),
                port=int(os.environ.get("API_PORT", "8000")))
    return 0
