"""Embedder thật: model đa ngôn ngữ chạy local qua ONNX.

VÌ SAO ONNX CHỨ KHÔNG PHẢI torch
`sentence-transformers` kéo theo torch — khoảng 2,5 GB vào ảnh Docker. `fastembed`
chạy cùng model đó qua `onnxruntime`, khoảng 250 MB. Cùng vector, cùng chất lượng,
ảnh nhỏ hơn 10 lần. Với một project mà bản lõi chỉ có requests + bs4 + lxml thì
khác biệt đó không nhỏ.

VÌ SAO LÀ MODEL NÀY
Đo trên 5 câu hỏi tiếng Việt, thước đo là thứ hạng của đáp án đúng:

    MiniLM-L12 (0,22 GB, 384 chiều)      5/5    MRR 1.000
    multilingual-e5-large (2,24 GB)      4/5    MRR 0.900

Model nhỏ hơn 10 lần lại thắng. Không phải nghịch lý: `e5` mạnh hơn trên các bộ đo
tiếng Anh dài, còn ở đây tài liệu ngắn và câu hỏi là tiếng Việt thường ngày.
Chọn theo số đo trên đúng việc mình làm, không chọn theo bảng xếp hạng chung.

(Mẫu 4 tài liệu là bằng chứng yếu — `tests/test_embedding.py` đo lại trên 799 chai
thật.)

CHỈ CHẠY TRONG CONTAINER LINUX
`onnxruntime` không nạp được DLL trên máy Windows đang dùng (`DLL load failed`,
thiếu VC++ redistributable). Lỗi được bắt lại và nói rõ phải làm gì, thay vì để
traceback của onnxruntime rơi ra ngoài. Trên host thì dùng `HashingEmbedder` —
xem `embedding/hashing.py`.
"""

from __future__ import annotations

import logging
import os
from typing import Sequence

from .ports import EmbeddingUnavailable

log = logging.getLogger(__name__)

# 384 chiều, 0,22 GB. Đổi được bằng biến môi trường, nhưng đổi là PHẢI sinh lại
# toàn bộ vector — xem luật `model_id` trong ports.py.
MODEL_MAC_DINH = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Model họ e5/bge đòi tiền tố khác nhau cho câu hỏi và tài liệu; dùng sai thì chất
# lượng sụt mà không có lỗi nào báo. Bảng này giữ chỗ khác biệt đó ở đúng một nơi.
TIEN_TO = {
    "intfloat/multilingual-e5-small": ("passage: ", "query: "),
    "intfloat/multilingual-e5-base": ("passage: ", "query: "),
    "intfloat/multilingual-e5-large": ("passage: ", "query: "),
}


def model_name() -> str:
    return (os.environ.get("EMBED_MODEL") or MODEL_MAC_DINH).strip()


class OnnxEmbedder:
    """Model đa ngôn ngữ qua fastembed/ONNX. Nạp một lần, dùng lại."""

    def __init__(self, model: str | None = None) -> None:
        self._name = model or model_name()
        self._emb = None
        self._dim: int | None = None
        self._tt_doc, self._tt_query = TIEN_TO.get(self._name, ("", ""))

    @property
    def model_id(self) -> str:
        return self._name

    @property
    def dimensions(self) -> int:
        if self._dim is None:
            self._nap()
        return int(self._dim or 0)

    # ----------------------------------------------------------------- nạp
    def _nap(self):
        if self._emb is not None:
            return self._emb
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            # `DLL load failed` của onnxruntime CŨNG là ImportError, nên không
            # được kết luận là "chưa cài". Phân biệt bằng importlib — nói sai
            # nguyên nhân ở đây là đẩy người đọc đi cài lại thứ đã cài rồi, rồi
            # ngồi tự hỏi vì sao không đỡ. Đã dính đúng cảnh này khi chạy test.
            import importlib.util
            if importlib.util.find_spec("fastembed") is not None:
                raise EmbeddingUnavailable(
                    f"fastembed đã cài nhưng không nạp được: {exc}\n"
                    "Trên Windows đây gần như luôn là onnxruntime thiếu Visual "
                    "C++ Redistributable. Cách chắc chắn chạy được:\n"
                    "    docker compose run --rm cli embed") from exc
            raise EmbeddingUnavailable(
                "Chưa cài fastembed. Cài bằng:\n"
                '    pip install -e ".[embed]"') from exc
        try:
            self._emb = TextEmbedding(model_name=self._name)
        except Exception as exc:                        # noqa: BLE001
            # Bắt rộng có chủ đích: onnxruntime ném ra nhiều loại lỗi rất khó đọc
            # (DLL load failed, external data path, Fail: [ONNXRuntimeError]).
            # Người đọc cần biết PHẢI LÀM GÌ, không cần biết tên lớp lỗi.
            raise EmbeddingUnavailable(
                f"Không nạp được model {self._name!r}: "
                f"{type(exc).__name__}: {str(exc)[:200]}\n"
                "Trên Windows, onnxruntime thường lỗi DLL vì thiếu Visual C++ "
                "Redistributable. Cách chắc chắn chạy được là dùng container:\n"
                "    docker compose run --rm cli embed") from exc

        mo_ta = [m for m in self._emb.list_supported_models()
                 if m.get("model") == self._name]
        self._dim = (mo_ta[0].get("dim") if mo_ta else None) or len(
            next(iter(self._emb.embed(["x"]))))
        log.info("Model embedding: %s (%d chiều)", self._name, self._dim)
        return self._emb

    # ------------------------------------------------------------------ cổng
    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        emb = self._nap()
        xin = [self._tt_doc + (t or "") for t in texts]
        return [tuple(float(x) for x in v) for v in emb.embed(xin)]

    def embed_query(self, text: str) -> tuple[float, ...]:
        emb = self._nap()
        v = next(iter(emb.embed([self._tt_query + (text or "")])))
        return tuple(float(x) for x in v)


def embedder(model: str | None = None):
    """Embedder thật nếu chạy được, nếu không thì nói rõ lý do.

    KHÔNG tự lặng lẽ rơi về `HashingEmbedder`: bản hashing không có ngữ nghĩa, nên
    rơi về nó mà không báo sẽ cho ra một hệ thống trông vẫn hoạt động và trả kết
    quả vô nghĩa. Muốn dùng bản hashing thì phải gọi tên nó.
    """
    return OnnxEmbedder(model)
