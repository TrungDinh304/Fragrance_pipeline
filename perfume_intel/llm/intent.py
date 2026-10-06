"""Câu tiếng Việt -> `Query`. Model đề xuất, TỪ VỰNG quyết định.

CÁCH LÀM, VÀ VÌ SAO KHÔNG ĐỂ MODEL TỰ DO
Model được giao đúng một việc: đọc câu người dùng rồi đề xuất note / accord /
hoàn cảnh / giới tính. Sau đó mọi nhãn nó đề xuất đều bị **đối chiếu với
`retriever.vocabulary()`**, và nhãn nào không có thật thì bị bỏ.

Không có bước đối chiếu thì model bịa ra "hương thanh xuân" và hệ thống đi tìm một
thứ không tồn tại — trả về rỗng, hoặc tệ hơn là khớp mờ vào một nhãn chẳng liên
quan. Nhãn bịa trông y hệt nhãn thật, nên không ai phát hiện được bằng mắt.

MODEL CŨNG LÀM MỘT VIỆC PHỤ RẤT CÓ GIÁ TRỊ: THÊM LẠI DẤU
Đo được: câu hỏi không dấu làm chất lượng tìm kiếm ngữ nghĩa tụt từ 5/5 xuống 4/5
(và nếu bỏ dấu cả hai phía thì xuống 2/5). Người Việt gõ không dấu rất nhiều. Model
viết lại câu có dấu, nên phần embedding ở sau được hưởng miễn phí.

KHI KHÔNG CÓ MODEL
`fallback()` tách ý định bằng từ khoá thuần Python. Kém hơn, nhưng nó giữ cho trang
chatbot dùng được khi chưa cấu hình khoá — thay vì một trang báo lỗi.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ..retrieval import ports
from ..retrieval.ports import Query
from .ports import ChatModel, LLMUnavailable, Message

log = logging.getLogger(__name__)

# Ba việc mà một lượt nói tiếp có thể là. Phân biệt được ba cái này là cả phần khó
# của hội thoại nhiều lượt.
MOI = "moi"              # câu hỏi mới, bỏ hết ngữ cảnh cũ
LOC_THEM = "loc_them"    # vẫn chủ đề cũ, thêm/sửa điều kiện ("nhẹ hơn", "hãng khác")
VE_CHAI = "ve_chai"      # hỏi về một chai ĐÃ HIỆN ("chai thứ 2 thì sao")
VIEC = (MOI, LOC_THEM, VE_CHAI)

# Câu tự do dài quá thì cắt. Khi lọc thêm, text được cộng dồn qua các lượt nên
# không có trần là nó phình mãi và vector mất hết trọng tâm.
MAX_TEXT = 300


@dataclass(frozen=True)
class Intent:
    """Ý định của MỘT lượt, đã lọc qua từ vựng thật."""

    action: str
    query: Query
    # Khi `action == VE_CHAI`: khoá của chai người dùng đang trỏ tới. None nghĩa là
    # không giải được — phía trên phải hỏi lại cho rõ, KHÔNG được đoán.
    target: str | None = None

GIOI_TINH = ("Nam", "Nữ", "Unisex")

# Số nhãn tối đa gợi cho model xem. Đưa cả 500+ note vào prompt là tốn token vô
# ích và làm model bỏ qua phần cuối danh sách.
GOI_Y_NOTE = 60
GOI_Y_ACCORD = 40

HE_THONG = """Bạn là bộ tách ý định cho một hệ thống tra cứu nước hoa.
Đọc câu của người dùng rồi trả về DUY NHẤT một object JSON, không kèm giải thích.

Khuôn JSON:
{
  "notes":     [tên note hương, tiếng Anh, lấy từ danh sách gợi ý nếu khớp],
  "accords":   [tên accord, tiếng Anh, lấy từ danh sách gợi ý nếu khớp],
  "occasions": [chỉ được dùng: winter, spring, summer, fall, day, night],
  "gender":    "Nam" | "Nữ" | "Unisex" | null,
  "text":      "câu của người dùng, viết lại ĐẦY ĐỦ DẤU tiếng Việt"
}

Quy tắc:
- Chỉ dùng tên có trong danh sách gợi ý. Không tự nghĩ ra tên mới.
- Không suy diễn hoàn cảnh ngoài 6 giá trị trên (không có "công sở", "hẹn hò").
- "text" luôn phải có, kể cả khi không tách được note nào.
- Nếu câu nói về nam/nữ thì điền gender, còn không thì để null."""


def _lay_json(chu: str) -> dict:
    """Bóc JSON khỏi câu trả lời. Model hay bọc nó trong ```json ... ```."""
    chu = (chu or "").strip()
    chu = re.sub(r"^```(?:json)?|```$", "", chu, flags=re.M).strip()
    mo = chu.find("{")
    dong = chu.rfind("}")
    if mo < 0 or dong <= mo:
        raise ValueError(f"không tìm thấy JSON trong: {chu[:160]}")
    return json.loads(chu[mo:dong + 1])


def _loc_theo_tu_vung(ten: object, tu_vung: set[str]) -> list[str]:
    """Giữ lại nhãn CÓ THẬT. Khớp đúng trước, rồi khớp một phần.

    Đây là chốt chặn chính của cả module: model đề xuất, từ vựng quyết định.
    """
    if not isinstance(ten, (list, tuple)):
        return []
    ra: list[str] = []
    for t in ten:
        if not isinstance(t, str):
            continue
        goc = t.strip().lower()
        if not goc:
            continue
        if goc in tu_vung:
            ra.append(goc)
            continue
        phan = [v for v in tu_vung if goc in v or v in goc]
        if phan:
            # Nhãn ngắn nhất: "oud" nên ra "agarwood (oud)" chứ không ra một nhãn
            # dài ngẫu nhiên nào đó cũng chứa chữ đó.
            ra.append(min(phan, key=len))
        else:
            log.info("bỏ nhãn model bịa: %r", t)
    return list(dict.fromkeys(ra))


def extract(cau: str, model: ChatModel, retriever) -> Query:
    """Tách ý định bằng model, rồi lọc mọi thứ qua từ vựng thật.

    Model hỏng thì rơi về `fallback()` — mất chất lượng, không mất chức năng.
    """
    note_vv = {t.lower() for t in retriever.vocabulary(ports.NOTE)}
    accord_vv = {t.lower() for t in retriever.vocabulary(ports.ACCORD)}

    goi_y = (
        f"Note có thật (một phần): {', '.join(sorted(note_vv)[:GOI_Y_NOTE])}\n"
        f"Accord có thật: {', '.join(sorted(accord_vv)[:GOI_Y_ACCORD])}")
    try:
        tra_loi = model.complete([
            Message("system", HE_THONG),
            Message("user", f"{goi_y}\n\nCâu của người dùng: {cau}"),
        ], max_tokens=400, temperature=0.0)
        d = _lay_json(tra_loi.text)
    except (LLMUnavailable, ValueError, json.JSONDecodeError) as exc:
        log.warning("Tách ý định bằng model không được (%s) — dùng bản từ khoá.",
                    str(exc)[:120])
        return fallback(cau)

    gt = d.get("gender")
    occ = [a for a in (d.get("occasions") or [])
           if isinstance(a, str) and a.strip().lower() in ports.OCCASIONS]

    return Query(
        notes=tuple(_loc_theo_tu_vung(d.get("notes"), note_vv)),
        accords=tuple(_loc_theo_tu_vung(d.get("accords"), accord_vv)),
        occasions=tuple(dict.fromkeys(a.strip().lower() for a in occ)),
        gender=gt if gt in GIOI_TINH else None,
        # Câu đã được model thêm lại dấu. Giữ câu gốc nếu model không trả về.
        text=(d.get("text") or cau) if isinstance(d.get("text"), str) else cau,
        explain=True,
    )


# ------------------------------------------------------------------ không LLM
_HOAN_CANH_VI = {
    "mùa đông": "winter", "mùa xuân": "spring", "mùa hè": "summer",
    "mùa thu": "fall", "ban ngày": "day", "buổi tối": "night",
    "ban đêm": "night", "buổi sáng": "day", "mua dong": "winter",
    "mua he": "summer", "buoi toi": "night", "ban ngay": "day",
}
_GIOI_TINH_VI = {"nam": "Nam", "nữ": "Nữ", "nu": "Nữ", "unisex": "Unisex"}


def fallback(cau: str) -> Query:
    """Tách ý định không cần model: hoàn cảnh + giới tính bằng từ khoá.

    Note/accord thì để nguyên câu cho tầng truy xuất tự lo (`Query.text`) — đoán
    bừa tên note ở đây sẽ sai nhiều hơn là giúp.
    """
    thap = (cau or "").lower()
    occ = [v for k, v in _HOAN_CANH_VI.items() if k in thap]
    gt = next((v for k, v in _GIOI_TINH_VI.items()
               if re.search(rf"\b{k}\b", thap)), None)
    return Query(occasions=tuple(dict.fromkeys(occ)), gender=gt,
                 text=cau, explain=True)
