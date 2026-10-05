"""Cổng embedding. Ranh giới, không phải nơi xử lý.

VÌ SAO LÀ MỘT CỔNG RIÊNG, KHÔNG GỘP VÀO `retrieval/`
Model embedding và kho vector thay theo hai nhịp khác nhau: model mới ra vài
tháng một lần, kho vector thay vài năm một lần. Ghép hai thứ lại là tự buộc hai
nhịp đó vào nhau — đổi model thì phải đụng vào kho, và ngược lại.

LUẬT QUAN TRỌNG NHẤT: `model_id` ĐI KÈM VECTOR
Vector của hai model khác nhau KHÔNG so sánh được với nhau, nhưng chúng là những
mảng số trông y hệt nhau. Trộn lẫn không gây lỗi — nó chỉ làm kết quả tìm kiếm
sai một cách im lặng, và không có cách nào phát hiện từ chính con số. Nên:

  - mỗi vector phải mang `model_id` của model đã sinh ra nó;
  - đổi model = sinh lại TOÀN BỘ, không bao giờ trộn;
  - `EmbeddingSet` giữ `model_id` ở cấp tập hợp và kiểm khi nạp.

Embedding là ARTIFACT DẪN XUẤT, không phải nguồn sự thật: xoá đi sinh lại được từ
bronze. Vì vậy nó ghi ra `data/silver/perfume_embeddings.parquet` và niêm lên lake
như mọi thứ khác ở tầng silver — kho vector chỉ NẠP từ đó, không bao giờ là bản
gốc. Xem `docs/ARCHITECTURE.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable


class EmbeddingUnavailable(RuntimeError):
    """Không nạp được model (chưa cài, chưa tải, hoặc môi trường không chạy được).

    Tách riêng khỏi lỗi dữ liệu: thiếu model là chuyện hạ tầng, và trên Windows
    thì `onnxruntime` còn không nạp nổi DLL. Phía gọi cần phân biệt được "model
    hỏng" với "câu hỏi sai".
    """


class ModelMismatch(ValueError):
    """Vector được sinh bởi một model khác với model đang hỏi.

    Đây là lỗi mà nếu không ném ra thì sẽ KHÔNG BAO GIỜ bị phát hiện: hai vector
    của hai model vẫn cộng trừ được với nhau, chỉ là kết quả vô nghĩa.
    """


@dataclass(frozen=True)
class Embedding:
    """Một vector kèm khoá của thứ nó mô tả."""

    key: str                      # perfume_key — URL đã chuẩn hoá
    vector: tuple[float, ...]

    def __len__(self) -> int:
        return len(self.vector)


@dataclass(frozen=True)
class EmbeddingSet:
    """Cả tập vector của một lần sinh, kèm thông tin model đã sinh ra chúng."""

    model_id: str
    dimensions: int
    items: tuple[Embedding, ...] = ()
    # Văn bản đã đưa vào model, giữ lại để soi được vì sao một chai khớp hay
    # không khớp. Không có nó thì mọi phép gỡ lỗi đều là đoán.
    documents: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.items)

    def check(self, model_id: str) -> None:
        if model_id != self.model_id:
            raise ModelMismatch(
                f"Tập vector sinh bởi {self.model_id!r} nhưng đang hỏi bằng "
                f"{model_id!r}. Sinh lại toàn bộ bằng `perfume-intel embed`, "
                f"KHÔNG trộn hai model.")


@runtime_checkable
class Embedder(Protocol):
    """Mọi bản cài đặt phải nói đúng ngần này.

    Bản nào cũng phải qua `tests/embedder_contract.py` — bộ test đó là định nghĩa
    thật của hợp đồng này.
    """

    @property
    def model_id(self) -> str:
        """Tên model, đủ để phân biệt. Đi kèm mọi vector sinh ra."""
        ...

    @property
    def dimensions(self) -> int:
        """Số chiều. Cố định cho một model."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """Vector cho văn bản được LƯU. Theo đúng thứ tự đầu vào."""
        ...

    def embed_query(self, text: str) -> tuple[float, ...]:
        """Vector cho câu HỎI.

        Tách khỏi `embed_documents` không phải cho đẹp: nhiều model (e5, bge) đòi
        tiền tố khác nhau cho hai phía, và dùng sai thì chất lượng sụt mà không
        có lỗi nào. Có hai hàm thì chỗ khác biệt đó nằm trong adapter, nơi duy
        nhất biết model cần gì.
        """
        ...
