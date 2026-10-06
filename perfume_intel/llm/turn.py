"""Tách ý định của MỘT LƯỢT trong hội thoại — phần khó nhất của chatbot nhiều lượt.

Một lượt độc lập thì câu hỏi tự đủ nghĩa. Lượt nói tiếp thì không: "nhẹ hơn chút",
"chai thứ 2 thì sao", "của hãng khác đi" — không câu nào tự nó nói được muốn gì.

BA VIỆC, VÀ VÌ SAO PHẢI PHÂN BIỆT
    moi       khách hỏi chuyện khác hẳn      -> bỏ hết điều kiện cũ
    loc_them  vẫn chuyện cũ, thêm điều kiện  -> GỘP với điều kiện cũ
    ve_chai   hỏi về một chai đã hiện        -> trỏ vào đúng chai đó

Không phân biệt được thì mỗi lượt là một câu hỏi mới, và "nhẹ hơn chút" sẽ đi tìm
nước hoa có note "nhẹ" — ra rỗng, hoặc ra một danh sách chẳng liên quan.

LUẬT TUYỆT ĐỐI: KHÔNG ĐOÁN CHAI
Khi `ve_chai` mà không giải được người dùng đang trỏ vào chai nào, hạ xuống
`loc_them` chứ KHÔNG chọn bừa một chai. Đoán ở đây cho ra một chai có thật, nên
không ai thấy là sai — chỉ thấy câu trả lời nói về chai khác thứ mình hỏi. Đó là
kiểu sai tệ nhất: không có cách nào phát hiện từ phía người đọc.
"""

from __future__ import annotations

import json
import logging
import re

from ..retrieval import ports
from ..retrieval.ports import Query
from .intent import (GIOI_TINH, GOI_Y_ACCORD, GOI_Y_NOTE, LOC_THEM, MAX_TEXT,
                     MOI, VE_CHAI, VIEC, Intent, _lay_json, _loc_theo_tu_vung,
                     fallback)
from .ports import ChatModel, LLMUnavailable, Message

log = logging.getLogger(__name__)

HE_THONG = """Bạn là bộ tách ý định cho một hệ thống tra cứu nước hoa, đang đọc MỘT
lượt trong một cuộc hội thoại. Trả về DUY NHẤT một object JSON, không giải thích.

Trước hết quyết định lượt này là việc gì:

- "moi": khách hỏi chuyện khác hẳn. Bỏ hết điều kiện cũ.
- "loc_them": khách vẫn muốn thứ vừa rồi nhưng thêm hoặc sửa điều kiện.
  Dấu hiệu: nhẹ hơn, ngọt hơn, hãng khác, còn gì nữa, cho nữ thì sao, mùa hè thì
  sao. Câu ngắn và phải dựa vào lượt trước mới hiểu thì gần như luôn là loại này.
- "ve_chai": khách hỏi về MỘT chai đã hiện ra ở lượt trước.
  Dấu hiệu: chai thứ 2, cái đầu tiên, chai đó, hoặc gọi đúng tên một chai trong
  danh sách đã hiện.

Khuôn JSON:
{
  "action":    "moi" hoặc "loc_them" hoặc "ve_chai",
  "target":    chỉ khi action la ve_chai - SỐ THỨ TỰ trong danh sách đã hiện
               (1, 2, 3...) hoặc đúng tên chai đó; không chắc thì để null,
  "notes":     [tên note, tiếng Anh, lấy từ danh sách gợi ý],
  "accords":   [tên accord, tiếng Anh, lấy từ danh sách gợi ý],
  "occasions": [chỉ được dùng: winter, spring, summer, fall, day, night],
  "gender":    "Nam" hoặc "Nữ" hoặc "Unisex" hoặc null,
  "text":      "ý khách muốn ở lượt NÀY, viết lại ĐẦY ĐỦ DẤU tiếng Việt"
}

Quy tắc:
- Chỉ dùng tên có trong danh sách gợi ý. Không tự nghĩ ra tên mới.
- Với loc_them, chỉ điền thứ khách VỪA nói thêm; đừng nhắc lại điều kiện cũ, hệ
  thống tự giữ.
- Với ve_chai, target phải trỏ vào danh sách đã hiện. Không chắc thì để null.
- Không suy diễn hoàn cảnh ngoài 6 giá trị trên."""


def _khoa_tu_target(target, shown) -> str | None:
    """`target` của model -> `perfume_key` trong danh sách đã hiện, hoặc None."""
    if target is None or not shown:
        return None
    so = None
    if isinstance(target, (int, float)):
        so = int(target)
    elif isinstance(target, str) and target.strip().isdigit():
        so = int(target.strip())
    if so is not None:
        i = so - 1
        return shown[i][0] if 0 <= i < len(shown) else None
    if not isinstance(target, str):
        return None
    goc = target.strip().lower()
    if not goc:
        return None
    for khoa, ten in shown:
        if (ten or "").lower() == goc:
            return khoa
    for khoa, ten in shown:
        t = (ten or "").lower()
        if t and (goc in t or t in goc):
            return khoa
    return None


def gop(cu: Query | None, moi: Query) -> Query:
    """Gộp điều kiện cũ với điều kiện vừa thêm.

    Luật, và lý do từng cái:
      - note/accord **cộng thêm**: khách đang kể thêm thứ mình muốn.
      - hoàn cảnh và giới tính **ghi đè** nếu lượt này có nói: "mùa hè thì sao" là
        ĐỔI mùa, không phải muốn cả đông lẫn hè.
      - `text` cộng dồn rồi cắt: vector ngữ nghĩa cần cả ý tích luỹ, nhưng không
        có trần thì nó phình mãi và mất trọng tâm.
    """
    if cu is None:
        return moi
    text = " ".join(x for x in ((cu.text or ""), (moi.text or "")) if x).strip()
    return Query(
        notes=tuple(dict.fromkeys((*cu.notes, *moi.notes))),
        accords=tuple(dict.fromkeys((*cu.accords, *moi.accords))),
        occasions=moi.occasions or cu.occasions,
        gender=moi.gender or cu.gender,
        text=(text[:MAX_TEXT] or None),
        min_votes=cu.min_votes,
        explain=True,
    )


def _cu(hoi) -> Query | None:
    """Query TÌM KIẾM gần nhất, bỏ qua các lượt hỏi về một chai cụ thể.

    Xem `Conversation.last_search_query` cho lý do: lượt hỏi về một chai là nhánh
    rẽ, lấy nó làm nền cho lượt sau là mất hết ngữ cảnh tìm kiếm.
    """
    if hoi is None:
        return None
    return getattr(hoi, "last_search_query", None) or hoi.last_query


def extract(cau: str, hoi, model: ChatModel | None, retriever) -> Intent:
    """Ý định của một lượt, có xét các lượt trước.

    `hoi` là `chat.session.Conversation` (hoặc None cho lượt đầu). Không có model
    thì rơi về `fallback_turn`.
    """
    shown = hoi.last_shown if hoi is not None else ()
    if model is None:
        return fallback_turn(cau, hoi)

    note_vv = {t.lower() for t in retriever.vocabulary(ports.NOTE)}
    accord_vv = {t.lower() for t in retriever.vocabulary(ports.ACCORD)}
    phan = [f"Note có thật (một phần): {', '.join(sorted(note_vv)[:GOI_Y_NOTE])}",
            f"Accord có thật: {', '.join(sorted(accord_vv)[:GOI_Y_ACCORD])}"]
    lich_su = hoi.history_text() if hoi is not None else ""
    if lich_su:
        phan += ["", "Hội thoại tới giờ:", lich_su]
    phan += ["", f"Lượt mới của khách: {cau}"]

    try:
        d = _lay_json(model.complete(
            [Message("system", HE_THONG), Message("user", "\n".join(phan))],
            max_tokens=400, temperature=0.0).text)
    except (LLMUnavailable, ValueError, json.JSONDecodeError) as exc:
        log.warning("Tách ý định nhiều lượt không được (%s) — dùng từ khoá.",
                    str(exc)[:120])
        return fallback_turn(cau, hoi)

    viec = d.get("action") if d.get("action") in VIEC else MOI
    gt = d.get("gender")
    occ = [a.strip().lower() for a in (d.get("occasions") or [])
           if isinstance(a, str) and a.strip().lower() in ports.OCCASIONS]
    q = Query(
        notes=tuple(_loc_theo_tu_vung(d.get("notes"), note_vv)),
        accords=tuple(_loc_theo_tu_vung(d.get("accords"), accord_vv)),
        occasions=tuple(dict.fromkeys(occ)),
        gender=gt if gt in GIOI_TINH else None,
        text=(d.get("text") or cau) if isinstance(d.get("text"), str) else cau,
        explain=True,
    )

    if viec == VE_CHAI:
        khoa = _khoa_tu_target(d.get("target"), shown)
        if khoa is None:
            # Hạ xuống lọc thêm, KHÔNG đoán chai. Xem docstring module.
            log.info("ve_chai nhưng target %r không trỏ vào danh sách đã hiện",
                     d.get("target"))
            return Intent(LOC_THEM, gop(_cu(hoi), q))
        return Intent(VE_CHAI, Query(like_perfume=khoa, explain=True),
                      target=khoa)
    if viec == LOC_THEM:
        return Intent(LOC_THEM, gop(_cu(hoi), q))
    return Intent(MOI, q)


# ----------------------------------------------------------- không có model
_DAU_LOC_THEM = ("hơn", "khác", "còn gì", "còn chai", "thêm", "nữa", "thì sao",
                 "vẫn", "nhưng", "đổi", "bớt")
_DAU_VE_CHAI = ("chai thứ", "chai số", "cái thứ", "cái số", "chai đầu",
                "cái đầu", "chai cuối", "chai đó", "cái đó", "chai này",
                "chai trên", "em đó", "chai vừa")
# Số thứ tự viết bằng chữ. PHẢI có từ dẫn ("thứ", "số", "cái", "chai") đứng trước,
# và phải khớp trọn từ.
#
# Khớp chuỗi con ở đây là một cái bẫy thật, đã dính: chữ "hai" nằm trong "chai",
# nên "chai đó có gì" bị hiểu thành "chai thứ hai". Tương tự "ba" nằm trong "bao
# nhiêu", "tư" nằm trong "từ". Lỗi này không gây exception — nó chỉ trả lời về
# đúng một chai có thật, nhưng không phải chai người ta hỏi.
_SO_CHU = {"đầu": 1, "nhất": 1, "hai": 2, "ba": 3, "tư": 4, "năm": 5}
_RE_SO_CHU = re.compile(
    r"(?:thứ|số|cái|chai)\s+(đầu|nhất|hai|ba|tư|năm)\b")
# "chai đầu tiên", "cái cuối" — vị trí chứ không phải số.
_RE_VI_TRI = re.compile(r"(?:chai|cái|em)\s+(đầu|cuối)\b")


def fallback_turn(cau: str, hoi) -> Intent:
    """Phân loại lượt bằng từ khoá. Kém hơn model, nhưng không đoán bừa.

    Đây là đường chạy khi chưa cấu hình `LLM_API_KEY`. Một trang hội thoại mà mất
    hẳn khả năng nói tiếp thì không còn là hội thoại, nên phần này phải làm tử tế
    thay vì trả về `moi` cho mọi lượt.
    """
    thap = (cau or "").strip().lower()
    cu = _cu(hoi)
    shown = hoi.last_shown if hoi is not None else ()

    if cu is not None and shown and any(d in thap for d in _DAU_VE_CHAI):
        khoa = _tro_vao_chai(thap, shown)
        if khoa is not None:
            return Intent(VE_CHAI, Query(like_perfume=khoa, explain=True),
                          target=khoa)

    moi = fallback(cau)
    if cu is not None and any(d in thap for d in _DAU_LOC_THEM):
        return Intent(LOC_THEM, gop(cu, moi))
    return Intent(MOI, moi)


def _tro_vao_chai(thap: str, shown) -> str | None:
    """Câu đã hạ chữ thường -> khoá chai trong danh sách đã hiện, hoặc None.

    Bốn cách người ta trỏ, thử theo thứ tự cụ thể dần:
      "chai thứ 2"      số viết bằng chữ số
      "chai đầu/cuối"   vị trí
      "chai thứ hai"    số viết bằng chữ
      "chai đó"         mặc định là chai đứng đầu lượt trước
    """
    so = re.search(r"(?:thứ|số)\s*(\d+)", thap)
    if so:
        khoa = _khoa_tu_target(so.group(1), shown)
        if khoa is not None:
            return khoa

    vi_tri = _RE_VI_TRI.search(thap)
    if vi_tri:
        return shown[0][0] if vi_tri.group(1) == "đầu" else shown[-1][0]

    chu = _RE_SO_CHU.search(thap)
    if chu:
        khoa = _khoa_tu_target(_SO_CHU[chu.group(1)], shown)
        if khoa is not None:
            return khoa

    if any(d in thap for d in ("chai đó", "cái đó", "chai này", "chai trên",
                               "chai vừa")):
        return shown[0][0]
    return None
