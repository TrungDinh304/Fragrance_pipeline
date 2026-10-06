"""Phiên hội thoại: nhớ các lượt trước để giải được câu nói tiếp.

VẤN ĐỀ CẦN GIẢI
Một lượt độc lập thì "mùi gỗ ấm cho mùa đông" đủ nghĩa. Nhưng câu nói tiếp thì
không: "nhẹ hơn chút", "chai thứ 2 thì sao", "của hãng khác đi" — không câu nào tự
nó nói được người dùng muốn gì. Phải có lượt trước mới hiểu.

PHIÊN LÀ THỨ TẠM, VÀ ĐƯỢC THỪA NHẬN LÀ TẠM
Giữ trong bộ nhớ tiến trình, có trần và có hạn. Hệ quả nói thẳng:

  - restart API là mất hết hội thoại đang dở;
  - chạy nhiều tiến trình API thì mỗi tiến trình có một bộ phiên riêng.

Không đưa vào Postgres, vì hội thoại không phải dữ liệu cần giữ: nó không dựng lại
được từ bronze, nhưng cũng không ai cần nó sau khi đóng tab. Theo đúng nguyên tắc
gốc của project, thứ đáng lưu bền là thứ không dựng lại được VÀ còn giá trị sau đó
— hội thoại chỉ có nửa đầu.

HAI THỨ PHẢI CÓ TRẦN, NẾU KHÔNG LÀ RÒ BỘ NHỚ
  1. số phiên (`MAX_PHIEN`) — mỗi tab mới là một phiên; không dọn thì tiến trình
     chạy vài tuần sẽ phình mãi;
  2. số lượt trong một phiên (`MAX_LUOT`) — một người ngồi hỏi cả buổi cũng không
     được làm prompt dài vô hạn.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from ..retrieval.ports import Query

log = logging.getLogger(__name__)

MAX_PHIEN = 200
MAX_LUOT = 40              # 20 cặp hỏi-đáp
TTL = 6 * 3600             # phiên im lặng quá lâu thì bỏ

# Số lượt gần nhất đưa vào prompt. Đưa cả phiên là tốn token mà không giúp gì:
# người ta hiếm khi nói tiếp về thứ đã nói từ 10 lượt trước.
LUOT_VAO_PROMPT = 6


@dataclass(frozen=True)
class Turn:
    """Một lượt. Lượt của trợ lý giữ thêm NHỮNG GÌ ĐÃ HIỆN RA.

    `shown` là chốt để giải "chai thứ 2": không có nó thì số thứ tự người dùng nói
    chẳng trỏ vào đâu, và model sẽ phải đoán — nó sẽ đoán ra một chai nào đó trông
    hợp lý, tức là bịa.
    """

    role: str                                  # "user" | "assistant"
    text: str
    shown: tuple[tuple[str, str], ...] = ()    # (perfume_key, tên)
    query: Query | None = None
    at: float = field(default_factory=time.time)


@dataclass
class Conversation:
    id: str
    turns: list[Turn] = field(default_factory=list)
    touched: float = field(default_factory=time.time)

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)
        # Cắt từ ĐẦU: lượt mới có giá trị hơn lượt cũ.
        if len(self.turns) > MAX_LUOT:
            del self.turns[:len(self.turns) - MAX_LUOT]
        self.touched = time.time()

    @property
    def last_query(self) -> Query | None:
        """Query của lượt trợ lý gần nhất, kể cả lượt hỏi về một chai."""
        for t in reversed(self.turns):
            if t.role == "assistant" and t.query is not None:
                return t.query
        return None

    @property
    def last_search_query(self) -> Query | None:
        """Query TÌM KIẾM gần nhất — bỏ qua các lượt hỏi về một chai cụ thể.

        Hỏi "chai thứ 2 thì sao" là một NHÁNH RẼ, không phải đổi chủ đề tìm kiếm.
        Nếu lấy luôn query đó làm nền cho lượt sau thì "còn gì nữa không" sẽ đi gộp
        với một query `like_perfume` — mà phép gộp không mang `like_perfume` theo,
        nên kết quả ra rỗng và bot trả lời "chưa tìm được chai nào".

        Đã thấy đúng chuỗi đó khi thử 5 lượt liền: lượt 3 hỏi về một chai, lượt 4
        thành ra mất hết ngữ cảnh.
        """
        for t in reversed(self.turns):
            if (t.role == "assistant" and t.query is not None
                    and not t.query.like_perfume):
                return t.query
        return None

    @property
    def last_shown(self) -> tuple[tuple[str, str], ...]:
        for t in reversed(self.turns):
            if t.role == "assistant" and t.shown:
                return t.shown
        return ()

    def history(self, n: int = LUOT_VAO_PROMPT) -> list[Turn]:
        return self.turns[-n:] if n > 0 else []

    def history_text(self, n: int = LUOT_VAO_PROMPT) -> str:
        """Lịch sử dạng chữ, để đưa vào prompt.

        Kèm luôn danh sách đã hiện ở lượt trợ lý gần nhất, vì đó là thứ người dùng
        trỏ tới khi nói "chai thứ 2" hay "cái đầu tiên".
        """
        dong = []
        for t in self.history(n):
            ai = "Khách" if t.role == "user" else "Bạn"
            dong.append(f"{ai}: {t.text}")
            if t.role == "assistant" and t.shown:
                ds = "; ".join(f"{i}. {ten}"
                               for i, (_k, ten) in enumerate(t.shown, 1))
                dong.append(f"  (đã hiện cho khách: {ds})")
        return "\n".join(dong)


class SessionStore:
    """Kho phiên trong bộ nhớ. An toàn khi nhiều thread gọi."""

    def __init__(self, max_phien: int = MAX_PHIEN, ttl: float = TTL) -> None:
        self._phien: dict[str, Conversation] = {}
        self._lock = threading.Lock()
        self._max = max_phien
        self._ttl = ttl

    def __len__(self) -> int:
        return len(self._phien)

    def get(self, sid: str | None) -> Conversation:
        """Phiên theo id, tạo mới nếu chưa có hoặc id không còn.

        KHÔNG ném lỗi khi id lạ: phiên hết hạn là chuyện bình thường (người dùng
        mở lại tab cũ), và bắt họ nhận lỗi 404 vì chuyện đó là vô nghĩa — cho họ
        một phiên mới là đúng hành vi.
        """
        with self._lock:
            if sid and sid in self._phien:
                hoi = self._phien[sid]
                hoi.touched = time.time()
                self._don()
                return hoi
            moi = Conversation(id=sid or uuid.uuid4().hex[:16])
            self._phien[moi.id] = moi
            # Dọn SAU KHI thêm, không phải trước.
            #
            # Dọn trước thì trần bị lệch một: với trần 3, lần tạo thứ 5 sẽ dọn khi
            # đang có 4 (còn 3) rồi mới thêm -> 4. Phiên vừa tạo cũng là phiên mới
            # nhất nên nó không bao giờ bị cắt oan.
            self._don()
            return moi

    def drop(self, sid: str) -> bool:
        with self._lock:
            return self._phien.pop(sid, None) is not None

    def _don(self) -> None:
        """Bỏ phiên hết hạn, rồi bỏ phiên cũ nhất nếu vẫn quá trần.

        Gọi trong `get` chứ không dùng thread riêng: dọn khi có người tới là đủ,
        và một thread nền chỉ để xoá dict là thứ sẽ sống mãi mà không ai nhớ.
        """
        gio = time.time()
        het = [k for k, v in self._phien.items() if gio - v.touched > self._ttl]
        for k in het:
            del self._phien[k]
        if len(self._phien) > self._max:
            theo_tuoi = sorted(self._phien.items(), key=lambda kv: kv[1].touched)
            for k, _v in theo_tuoi[:len(self._phien) - self._max]:
                del self._phien[k]
        if het:
            log.info("Dọn %d phiên hết hạn, còn %d.", len(het), len(self._phien))
