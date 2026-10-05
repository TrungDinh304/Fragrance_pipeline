"""Embedder tất định bằng hashing. KHÔNG dùng để tìm kiếm thật.

VÌ SAO NÓ TỒN TẠI, VÀ VÌ SAO KHÔNG PHẢI LÀ "CODE TEST BỊ BỎ TRONG PRODUCTION"
`onnxruntime` không nạp được DLL trên máy Windows đang dùng để phát triển (lỗi
`DLL load failed`, thiếu VC++ redistributable). Model thật chỉ chạy trong container
Linux. Nếu không có bản này thì:

  - không test được `PgVectorRetriever`, tầng API hay chatbot trên host;
  - mỗi lần sửa một dòng ở tầng trên phải dựng lại cả container mới biết đúng sai;
  - và bộ hợp đồng của `Embedder` sẽ chỉ có MỘT bản cài đặt, tức là nó không
    chứng minh được điều gì về tính thay thế được.

Nó cho vector TẤT ĐỊNH (cùng chữ ra cùng vector) và có tính chất duy nhất mà tầng
trên cần: văn bản giống nhau thì vector giống nhau. Nó KHÔNG có ngữ nghĩa — "mùi
gỗ" và "woody" ra hai vector không liên quan. Nên nó hợp cho việc kiểm ĐƯỜNG ĐI
của dữ liệu, và vô dụng cho việc kiểm CHẤT LƯỢNG tìm kiếm.

`model_id` của nó cố ý bắt đầu bằng `hashing-` để không bao giờ bị tưởng là model
thật khi đọc file Parquet hay bảng pgvector.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

DIMENSIONS = 64
MODEL_ID = f"hashing-{DIMENSIONS}"

_TU = re.compile(r"[0-9a-zA-ZÀ-ỹ]+", re.UNICODE)


class HashingEmbedder:
    """Túi từ -> vector cố định, bằng hash. Tất định, không cần tải gì."""

    def __init__(self, dimensions: int = DIMENSIONS) -> None:
        if dimensions < 8:
            raise ValueError("Cần ít nhất 8 chiều mới phân biệt được gì.")
        self._dim = dimensions

    @property
    def model_id(self) -> str:
        return f"hashing-{self._dim}"

    @property
    def dimensions(self) -> int:
        return self._dim

    # ----------------------------------------------------------------- nội bộ
    def _vector(self, text: str) -> tuple[float, ...]:
        v = [0.0] * self._dim
        for tu in _TU.findall((text or "").lower()):
            h = hashlib.blake2b(tu.encode("utf-8"), digest_size=8).digest()
            chi_so = int.from_bytes(h[:4], "big") % self._dim
            # Dấu lấy từ byte khác, để hai từ cùng rơi vào một chiều không luôn
            # cộng dồn thành một con số to vô nghĩa.
            dau = 1.0 if h[4] % 2 == 0 else -1.0
            v[chi_so] += dau
        chuan = math.sqrt(sum(x * x for x in v))
        if chuan == 0:
            # Văn bản rỗng hoặc không có từ nào: trả vector 0 đã chuẩn hoá được.
            # Không ném lỗi — câu rỗng là chuyện bình thường ở tầng trên.
            return tuple(v)
        return tuple(x / chuan for x in v)

    # ------------------------------------------------------------------ cổng
    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> tuple[float, ...]:
        # Cùng phép biến đổi cho hai phía: model này không có tiền tố nào.
        return self._vector(text)
