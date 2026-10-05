"""Diễn đạt kết quả thành câu tư vấn. Model CHỈ được dùng dữ liệu đưa vào.

ĐÂY LÀ RAG ĐÚNG NGHĨA, KHÔNG PHẢI "HỎI MODEL VỀ NƯỚC HOA"
Model không biết gì về kho dữ liệu này và không được phép tự nghĩ ra chai nào. Nó
nhận một danh sách chai đã tìm được — kèm điểm và kèm LÝ DO — rồi viết lại thành
câu người đọc được.

BA LỚP CHẶN BỊA, CỐ Ý LÀM CẢ BA
  1. **Prompt nói rõ chỉ được dùng danh sách.** Lớp yếu nhất, nhưng rẻ.
  2. **`verify()` kiểm lại câu trả lời**: tên chai nào xuất hiện trong câu mà
     không có trong danh sách thì bị báo. Model bịa một cái tên trông y hệt tên
     thật, nên không ai soát được bằng mắt.
  3. **Giao diện luôn hiện danh sách gốc bên cạnh câu trả lời.** Người đọc đối
     chiếu được, không phải tin lời model.

Lớp 2 là lớp duy nhất tự động, nên nó quan trọng nhất. Nó không chặn được mọi kiểu
bịa (model vẫn có thể nói sai về một chai CÓ trong danh sách), nhưng nó chặn được
kiểu tệ nhất: giới thiệu một chai không tồn tại.

KHÔNG CÓ MODEL THÌ VẪN PHẢI DÙNG ĐƯỢC
`summary()` ghép câu bằng Python thuần từ đúng dữ liệu đó. Khô hơn, nhưng đúng
tuyệt đối và không tốn token.
"""

from __future__ import annotations

import logging
import re

from ..retrieval.ports import Match, SearchResult
from .ports import ChatModel, LLMUnavailable, Message, Reply

log = logging.getLogger(__name__)

HE_THONG = """Bạn là người tư vấn nước hoa, trả lời bằng tiếng Việt.

Bạn CHỈ được nói về những chai trong danh sách được cung cấp. Tuyệt đối không nhắc
tên chai nào khác, không nhắc chai bạn biết từ nơi khác. Nếu danh sách rỗng, hãy
nói thẳng là chưa tìm được và gợi ý người dùng mô tả rõ hơn.

Cách viết:
- 2 đến 4 câu, không gạch đầu dòng, không markdown.
- Nêu 2-3 chai đầu và nói VÌ SAO chúng phù hợp, dựa vào phần "vì" đã cho.
- Nhắc số vote khi nó đáng chú ý (rất nhiều hoặc rất ít người đánh giá).
- Không bịa giá, không bịa nơi bán, không bịa năm ra mắt.
- Đừng nhắc lại điểm số dạng 0.348 — nói "gần nhất", "khớp nhiều nhất"."""

VI_KHOI = {"accord": "mùi", "note": "note", "occasion": "dịp",
           "strength": "độ", "family": "họ"}


def _mo_ta(m: Match) -> str:
    phan = [f"- {m.name or '(không tên)'}"]
    if m.brand:
        phan.append(f"của {m.brand}")
    if m.rating is not None:
        phan.append(f"— {m.rating:.2f}/5")
    if m.rating_count:
        phan.append(f"({m.rating_count:,} vote)".replace(",", "."))
    if m.gender:
        phan.append(f"[{m.gender}]")
    dong = " ".join(phan)
    if m.why:
        vi = ", ".join(f"{VI_KHOI.get(w.block, w.block)} {w.label}"
                       for w in m.why)
        dong += f"\n    vì: {vi}"
    return dong


def _boi_canh(res: SearchResult, cau: str) -> str:
    if not res.matches:
        return f"Câu hỏi: {cau}\n\nKhông tìm được chai nào phù hợp."
    phan = [f"Câu hỏi: {cau}", "", "Các chai tìm được (đã xếp theo độ khớp):"]
    phan += [_mo_ta(m) for m in res.matches]
    if res.resolved:
        phan.append("")
        phan.append("Hệ thống đã hiểu câu đó thành: "
                    + "; ".join(f"{k} -> {v}" for k, v in res.resolved.items()))
    return "\n".join(phan)


def verify(tra_loi: str, res: SearchResult) -> list[str]:
    """Tên chai nào trong câu trả lời mà KHÔNG có trong danh sách.

    Cách làm: lấy các chuỗi trông như tên riêng (cụm từ viết hoa) trong câu trả
    lời, bỏ những cụm khớp tên chai hoặc tên hãng đã đưa vào, phần còn lại là
    nghi vấn. Cố ý thà báo nhầm hơn là bỏ sót — đây là lớp chặn tự động duy nhất.
    """
    cho_phep = set()
    for m in res.matches:
        for ten in (m.name, m.brand):
            if ten:
                cho_phep.add(ten.lower())
                cho_phep.update(t for t in re.split(r"[\s/&-]+", ten.lower())
                                if len(t) > 2)
    # Từ tiếng Việt viết hoa đầu câu rất nhiều, nên chỉ xét cụm >= 2 từ hoa liền
    # nhau — đó mới là dạng của tên nước hoa ("Vintage Radio", "Bond No 9").
    nghi = []
    for cum in re.findall(r"\b([A-Z][\w']*(?:\s+[A-Z0-9][\w']*)+)", tra_loi):
        thap = cum.lower()
        if thap in cho_phep:
            continue
        tu = [t for t in re.split(r"\s+", thap) if len(t) > 2]
        if tu and all(t in cho_phep for t in tu):
            continue
        nghi.append(cum)
    return list(dict.fromkeys(nghi))


def advise(cau: str, res: SearchResult, model: ChatModel) -> tuple[str, list[str]]:
    """Trả về (câu tư vấn, danh sách tên đáng nghi).

    Model hỏng thì rơi về `summary()`; mất phần diễn đạt, không mất câu trả lời.
    """
    try:
        rep: Reply = model.complete(
            [Message("system", HE_THONG), Message("user", _boi_canh(res, cau))],
            max_tokens=400, temperature=0.3)
    except LLMUnavailable as exc:
        log.warning("Không diễn đạt được bằng model (%s) — dùng bản ghép sẵn.",
                    str(exc)[:120])
        return summary(res), []
    chu = (rep.text or "").strip() or summary(res)
    bia = verify(chu, res)
    if bia:
        log.warning("Câu trả lời nhắc tên không có trong kết quả: %s", bia)
    return chu, bia


def summary(res: SearchResult) -> str:
    """Câu trả lời ghép bằng Python thuần. Khô, nhưng đúng tuyệt đối."""
    if not res.matches:
        return ("Chưa tìm được chai nào khớp. Thử mô tả theo note hương "
                "(ví dụ: oud, vanilla) hoặc theo mùa và thời điểm dùng.")
    dau = res.matches[0]
    phan = [f"Khớp nhất là {dau.name or '(không tên)'}"
            + (f" của {dau.brand}" if dau.brand else "") + "."]
    if dau.why:
        phan.append("Lý do: " + ", ".join(
            f"{VI_KHOI.get(w.block, w.block)} {w.label}" for w in dau.why) + ".")
    if len(res.matches) > 1:
        ten = ", ".join(m.name or "?" for m in res.matches[1:3])
        phan.append(f"Gần đó còn có {ten}.")
    return " ".join(phan)
