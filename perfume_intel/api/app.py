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
import threading
from dataclasses import replace
from contextlib import asynccontextmanager
from pathlib import Path

from .. import config
from ..chat.session import SessionStore
from ..retrieval.ports import Query, Retriever, SearchResult, UnknownPerfume

log = logging.getLogger(__name__)

TINH = Path(__file__).resolve().parent / "static"

# Trạng thái dùng chung, dựng một lần lúc bật. Nạp index tốn vài giây; làm lại mỗi
# request thì trang không dùng được.
_trang_thai: dict = {}

# HAI LOCK, HAI VIỆC KHÁC NHAU — ĐÃ TREO THẬT VÌ THIẾU CHÚNG.
#
# `_LOCK_DUNG`: FastAPI chạy handler đồng bộ trong threadpool, nên healthcheck và
# request của người dùng có thể vào `state()` CÙNG LÚC khi chưa dựng xong. Mỗi bên
# nạp một bản model ONNX và mở một kết nối Postgres riêng; triệu chứng là API
# listening nhưng mọi request treo, kể cả gọi từ trong container.
#
# `_LOCK_HOI`: một kết nối psycopg KHÔNG dùng được từ nhiều thread cùng lúc. Khoá
# cả phép tra cứu là cách đơn giản và đúng cho một trang test một người dùng. Khi
# nào API phục vụ nhiều người thật thì thay bằng `psycopg_pool` — lúc đó mới đau,
# chưa đau thì chưa làm.
_LOCK_DUNG = threading.Lock()
_LOCK_HOI = threading.Lock()

# Kho hội thoại. Trong bộ nhớ, có trần và có hạn — xem `chat/session.py`.
SESSIONS = SessionStore()


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
    """Trạng thái đã dựng. An toàn khi nhiều thread gọi cùng lúc."""
    if _trang_thai:
        return _trang_thai
    with _LOCK_DUNG:
        # Kiểm lại trong lock: thread thứ hai chờ ở đây xong thì việc đã xong rồi.
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


def _da_hien(hoi) -> set[str]:
    """Mọi chai đã hiện ra trong cả phiên."""
    return {k for t in hoi.turns for k, _ten in t.shown}


def _bo_chai_da_hien(retriever, q, res, hoi, bo_qua: bool = False):
    """Khi khách nói tiếp, bỏ những chai ĐÃ hiện. Trả về (kết quả, hết_chai_mới).

    VÌ SAO CẦN: "nhẹ hơn chút" và "còn gì nữa không" thường không mang theo điều
    kiện nào tách được, nên câu truy vấn gần như không đổi và kết quả ra Y HỆT lượt
    trước. Lặp lại nguyên văn câu trả lời là dấu hiệu "bot hỏng" rõ nhất mà một
    cuộc hội thoại có thể phát ra. Đã thấy thật khi thử 5 lượt liền.

    Chỉ áp cho lượt LỌC THÊM: câu hỏi mới thì khách có quyền thấy lại chai cũ, và
    lượt hỏi về một chai cụ thể thì chính chai đó là thứ cần hiện.

    `het_moi = True` nghĩa là đã cạn chai mới — phía trên PHẢI nói ra điều đó thay
    vì im lặng trả về rỗng.
    """
    if bo_qua or not res.matches:
        return res, False
    cu = _da_hien(hoi)
    if not cu:
        return res, False

    con = tuple(m for m in res.matches if m.perfume_key not in cu)
    if len(con) >= q.limit:
        return replace(res, matches=con[:q.limit]), False

    # Còn ít hơn `limit`: xin thêm rồi lọc lại, chứ KHÔNG trả về phần ít ỏi vừa
    # lọc được. Trả về sớm là lý do lượt "nhẹ hơn chút" chỉ ra đúng một chai dù
    # trong kho còn nhiều — nhìn vào tưởng hết hàng, thật ra chỉ là lọc xong sớm.
    #
    # Xin gấp đôi cộng số đã hiện, vì phần đã hiện chắc chắn bị loại hết.
    rong = replace(q, limit=min(q.limit * 2 + len(cu), 50))
    them = retriever.search(rong)
    rong_hon = tuple(m for m in them.matches if m.perfume_key not in cu)
    if len(rong_hon) > len(con):
        con = rong_hon[:q.limit]
        res = them
    if con:
        return replace(res, matches=con), False
    # Thật sự cạn: giữ kết quả cũ và báo lại, để câu trả lời nói đúng tình hình.
    return res, True


def create_app():
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel
    except ImportError as exc:                          # pragma: no cover
        raise RuntimeError(
            'Cần FastAPI. Cài bằng: pip install -e ".[api]"') from exc

    @asynccontextmanager
    async def lifespan(_app):
        # Dựng SẴN lúc bật, không chờ request đầu tiên. Hai lý do: healthcheck
        # của Docker mới phản ánh đúng "đã sẵn sàng", và người mở trang không
        # phải chờ vài giây nạp model ngay ở câu hỏi đầu.
        import anyio
        try:
            await anyio.to_thread.run_sync(state)
        except Exception as exc:                        # noqa: BLE001
            # Không chặn bật server: /health phải trả lời được để nói ra là
            # đang thiếu gì. Một API không bật được thì không chẩn đoán được gì.
            log.error("Dựng trạng thái lúc bật không xong: %s", exc)
        yield

    app = FastAPI(title="perfume-intel", version="0.3.0",
                  lifespan=lifespan,
                  description="Tra cứu nước hoa theo tín hiệu cộng đồng.")

    class ChatIn(BaseModel):
        message: str
        limit: int = 5
        # Không truyền thì tạo phiên mới. Id lạ hoặc đã hết hạn cũng cho phiên
        # mới chứ không trả 404: mở lại tab cũ là chuyện bình thường.
        session_id: str | None = None

    class ResetIn(BaseModel):
        session_id: str

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
            "sessions": len(SESSIONS),
        }

    @app.post("/search")
    def search(q: SearchIn) -> dict:
        st = state()
        try:
            with _LOCK_HOI:
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
        """Một lượt trong hội thoại: tách ý định -> tra cứu -> diễn đạt.

        Hội thoại được giữ theo `session_id`. Không truyền thì tạo phiên mới và
        trả về id để lượt sau gửi lại — xem `chat/session.py` cho giới hạn.
        """
        from ..llm import advise as ad
        from ..llm import turn as tn
        from ..chat.session import Turn

        st = state()
        model = st["chat"]
        cau = (inp.message or "").strip()
        if not cau:
            raise HTTPException(400, "message rỗng")

        hoi = SESSIONS.get(inp.session_id)
        with _LOCK_HOI:
            y = tn.extract(cau, hoi, model, st["retriever"])

        if y.action == tn.VE_CHAI:
            # Khách trỏ vào đúng một chai. Query đã là `like_perfume` nên không
            # áp luật chọn nhánh ở dưới — ở đây không còn gì phải chọn.
            q = Query(like_perfume=y.query.like_perfume,
                      limit=min(inp.limit, 20), explain=True)
        else:
            # CHỌN NHÁNH: có note/accord cụ thể thì đi nhánh term (chính xác và
            # giải thích được). Chỉ có hoàn cảnh thì đi nhánh ngữ nghĩa, vì hoàn
            # cảnh một mình là tín hiệu yếu — mọi chai đều có điểm cho mọi mùa —
            # còn câu tự do thì chứa phần mùi thật sự quan trọng.
            #
            # Thiếu luật này thì "mùi gỗ trầm ấm cho buổi tối mùa đông" đi nhánh
            # term chỉ với winter+night và bỏ hẳn "gỗ trầm".
            co_mui = bool(y.query.notes or y.query.accords)
            q = Query(notes=y.query.notes if co_mui else (),
                      accords=y.query.accords if co_mui else (),
                      occasions=y.query.occasions,
                      text=None if co_mui else y.query.text,
                      gender=y.query.gender, limit=min(inp.limit, 20),
                      explain=True)

        lich_su = hoi.history_text()
        try:
            with _LOCK_HOI:
                res = st["retriever"].search(q)
                res, het_moi = _bo_chai_da_hien(
                    st["retriever"], q, res, hoi,
                    bo_qua=(y.action != tn.LOC_THEM))
        except UnknownPerfume as exc:
            raise HTTPException(404, f"Không còn chai nào khớp {exc}") from exc

        ve_chai = y.action == tn.VE_CHAI
        if model:
            tra_loi, nghi = ad.advise(cau, res, model, lich_su=lich_su,
                                      ve_chai=ve_chai, het_moi=het_moi)
        else:
            tra_loi, nghi = ad.summary(res, ve_chai=ve_chai,
                                       het_moi=het_moi), []

        # Ghi lại lượt SAU KHI đã có kết quả. `shown` là chốt để lượt sau giải
        # được "chai thứ 2" — thiếu nó thì số thứ tự chẳng trỏ vào đâu.
        hien = tuple((m.perfume_key, m.name or "") for m in res.matches)
        if ve_chai and res.seed is not None:
            hien = ((res.seed.perfume_key, res.seed.name or ""), *hien)
        hoi.add(Turn("user", cau))
        hoi.add(Turn("assistant", tra_loi, shown=hien, query=q))

        return {
            "session_id": hoi.id,
            "action": y.action,
            "turn": len([t for t in hoi.turns if t.role == "user"]),
            "answer": tra_loi,
            # Trả về CẢ hai: câu diễn đạt và dữ liệu gốc. Giao diện hiện cạnh
            # nhau để người đọc đối chiếu được, không phải tin lời model.
            "matches": [_match_json(m) for m in res.matches],
            "seed": _match_json(res.seed) if res.seed else None,
            "intent": {"notes": list(q.notes), "accords": list(q.accords),
                       "occasions": list(q.occasions), "gender": q.gender,
                       "text": q.text, "like_perfume": q.like_perfume},
            "resolved": dict(res.resolved), "unknown": list(res.unknown),
            "suspicious": nghi,
            "exhausted": het_moi,
            "llm": bool(model),
        }

    @app.post("/chat/reset")
    def chat_reset(inp: ResetIn) -> dict:
        """Xoá một phiên. Không có phiên đó cũng trả về ok — xoá thứ vốn không
        còn là một kết quả, không phải một sự cố."""
        return {"ok": True, "dropped": SESSIONS.drop(inp.session_id),
                "sessions": len(SESSIONS)}

    @app.get("/vocabulary/{block}")
    def vocabulary(block: str) -> dict:
        try:
            with _LOCK_HOI:
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
