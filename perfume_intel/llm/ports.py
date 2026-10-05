"""Cổng gọi model ngôn ngữ. Ranh giới, không phải nơi xử lý.

VÌ SAO LÀ MỘT CỔNG, DÙ 9ROUTER ĐÃ LÀ MỘT CỔNG
9router đã gộp 40+ nhà cung cấp sau một endpoint kiểu OpenAI, nên nhìn thì cổng
này có vẻ thừa. Nó không thừa vì nó giải một việc khác: 9router gộp *nhà cung cấp*,
còn cổng này gộp *giao thức*. Đổi sang Anthropic API trực tiếp, sang Ollama, hay
sang một model chạy trong máy đều không phải sửa `intent.py` hay `advise.py` —
nơi chứa toàn bộ phần khó là prompt và việc kiểm đầu ra.

LUẬT TUYỆT ĐỐI CỦA TẦNG NÀY: KHÔNG TIN ĐẦU RA CỦA MODEL
Model trả về chữ, và chữ đó là **dữ liệu**, không phải lệnh. Cụ thể:

  - `intent.py` chỉ nhận những nhãn CÓ THẬT trong `retriever.vocabulary()`.
    Model bịa ra note "hương thanh xuân" thì nhãn đó bị bỏ, không phải bị tra.
  - `advise.py` chỉ được nhắc những chai mà retriever ĐÃ trả về. Không có đường
    nào cho model tự thêm một chai.

Lý do không phải là lo model "nói dối": một chai bịa ra trông y hệt một chai thật,
nên không ai kiểm được bằng mắt, và cái sai đó đi thẳng tới người dùng cuối.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable


class LLMUnavailable(RuntimeError):
    """Không gọi được model: thiếu khoá, sai endpoint, hết hạn mức, hoặc mạng.

    Tầng trên PHẢI xử lý được lỗi này mà vẫn trả lời được người dùng — tìm kiếm
    không có LLM vẫn chạy (nó chỉ mất phần diễn đạt), nên mất LLM không được làm
    sập cả trang.
    """


@dataclass(frozen=True)
class Message:
    role: str            # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class Reply:
    text: str
    model: str = ""
    # Số token, nếu nhà cung cấp báo. Có để biết một lượt chat tốn bao nhiêu —
    # với hạn mức miễn phí thì đó là con số đáng theo dõi.
    tokens_in: int = 0
    tokens_out: int = 0


@runtime_checkable
class ChatModel(Protocol):
    """Mọi bản cài đặt phải nói đúng ngần này."""

    @property
    def model_id(self) -> str:
        ...

    def complete(self, messages: Sequence[Message], max_tokens: int = 512,
                 temperature: float = 0.0) -> Reply:
        """Một lượt hỏi-đáp. Không trả về stream — tầng trên không cần.

        `temperature=0.0` là mặc định có chủ đích: việc chính của model ở đây là
        TÁCH Ý ĐỊNH, và việc đó cần tất định. Phần diễn đạt tự nhiên mới cần nhiệt
        độ cao hơn, và nó tự truyền vào.
        """
        ...
