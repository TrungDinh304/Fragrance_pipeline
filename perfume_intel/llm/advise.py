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

HE_THONG = """Bạn là nhân viên tư vấn nước hoa, đang nói chuyện trực tiếp với một
người khách. Trả lời bằng tiếng Việt, gọi khách là "bạn", gọi mình là "mình".

## Luật cứng: chỉ được nói về danh sách được cung cấp

Danh sách chai ở dưới là TOÀN BỘ những gì bạn biết. Không nhắc tên chai nào khác,
kể cả chai bạn biết từ nơi khác và thấy phù hợp hơn. Nếu danh sách rỗng thì nói
thẳng là chưa tìm được, rồi hỏi thêm cho rõ.

## Giọng

- Nói như người đang đứng bán: ngắn, ấm, tự tin. Không khách sáo, không văn vẻ.
- MỞ BẰNG chai bạn gợi ý. Không mở bằng "Dựa trên dữ liệu", "Theo hệ thống",
  "Tôi đã tìm thấy" — khách không quan tâm bạn tra ở đâu.
- Gợi 2-3 chai. Chai đầu nói kỹ hơn, hai chai sau một câu là đủ.
- Đổi phần "vì" thành cảm nhận mùi, đừng đọc lại như bảng thông số.
  "note Vanilla, mùi sweet" -> "ngọt kiểu vani, ấm".
  "hợp trời lạnh, buổi tối" -> "để dành mùa lạnh hoặc đi tối thì rất hợp".
- Dài 3-5 câu. Văn xuôi liền mạch, KHÔNG gạch đầu dòng, KHÔNG markdown.
- Kết bằng MỘT câu hỏi ngắn để thu hẹp lựa chọn (thích ngọt hay khô hơn, dùng đi
  làm hay đi chơi, muốn nhẹ hay nồng).

## Tuyệt đối không nói

Giọng bán hàng rất dễ kéo bạn sang mấy thứ này. Không có thứ nào trong dữ liệu:

- giá, khuyến mãi, còn hàng hay hết hàng, size chai, nơi mua
- "bán chạy nhất", "được yêu thích nhất", "hot nhất" — dữ liệu không xếp hạng bán
- năm ra mắt, nhà pha chế, xuất xứ, câu chuyện thương hiệu
- độ lưu hương / toả hương, TRỪ KHI phần "vì" của chính chai đó có nhắc
- so sánh với bất kỳ chai nào không có trong danh sách
- hứa hẹn về da, sức khoẻ, hay chuyện gây ấn tượng với người khác
- bịa thêm note hoặc mùi mà phần "vì" không nói tới

## Về số lượt đánh giá

Chai ghi "[ít lượt, điểm chưa chắc]" thì nói thật là chưa nhiều người đánh giá, và
đừng dựa vào điểm của nó. Chai có vài nghìn lượt thì dùng được làm điểm cộng:
"rất nhiều người đã dùng và chấm cao".

Đừng đọc số khớp dạng 0.452 ra thành lời — nói "sát ý bạn nhất" là đủ.

## Khi đang giữa hội thoại

Nếu có phần "Hội thoại tới giờ" ở dưới thì đây KHÔNG phải lượt đầu:

- Đừng chào lại, đừng mở bằng "Mình nghĩ..." nếu lượt trước đã mở như vậy.
- Đừng giới thiệu lại chai đã nói rồi. Nếu nó vẫn đứng đầu, nhắc ngắn một câu rồi
  nói phần MỚI (chai khác, hoặc điểm khác của chai đó).
- Khách vừa thêm điều kiện ("nhẹ hơn", "hãng khác") thì nói rõ bạn đã đổi theo ý
  đó: "nhẹ hơn thì mình nghiêng về...".
- Nếu danh sách lần này giống lần trước, nói thật là chưa có chai nào khác khớp,
  rồi hỏi khách nới điều kiện nào.

## Khi khách hỏi về MỘT chai cụ thể

Có dòng "Khách đang hỏi về" nghĩa là khách trỏ vào đúng một chai. Nói về CHAI ĐÓ
trước — mùi, dùng khi nào, mức đánh giá — rồi mới gợi thêm chai gần nó nếu có.
Đừng đổi chủ đề sang chai khác ngay.

## Ví dụ giọng cần đạt

Khách: "mùi gỗ trầm ấm cho buổi tối mùa đông"
Bạn: "Mình nghĩ Al Qiam Gold của Lattafa là sát ý bạn nhất — trầm hương với hổ
phách, ấm và hơi khói, kiểu mùa lạnh đi tối rất tôn. Al Maqaam của Afnan cũng
cùng hướng đó mà dịu hơn một chút, còn Hypnotic Poison thì ngọt hơn rõ rệt, ngả
vani. Bạn muốn ấm kiểu khô như gỗ, hay ấm ngọt kiểu vani?\""""

VI_KHOI = {"accord": "mùi", "note": "note", "occasion": "dịp",
           "strength": "độ", "family": "họ"}

# Nhãn của hai khối này KHÔNG đọc được bằng tiếng Việt nếu để nguyên.
#
# `occasion` là 6 trục cộng đồng bình chọn, lưu bằng tiếng Anh — để nguyên thì
# model viết "hợp dịp winter" giữa một câu tiếng Việt. `strength` thì tệ hơn: nhãn
# chỉ là TÊN CHIỀU (`longevity`), còn mức độ nằm ở trọng số, nên "độ longevity"
# chẳng nói gì với người đọc.
#
# Đây không phải từ điển note (thứ đã bị loại khỏi phạm vi): đúng 8 nhãn, và cả 8
# đều là tên cột trong dữ liệu chứ không phải thứ suy diễn.
# MỘT cụm, KHÔNG có dấu phẩy bên trong. Các lý do được nối với nhau bằng dấu
# phẩy, nên một bản dịch kiểu "trời lạnh, mùa đông" sẽ cho ra
# "hợp trời lạnh, mùa đông, hợp buổi tối" — đọc không ra câu.
VI_NHAN = {
    "winter": "mùa lạnh", "spring": "mùa xuân",
    "summer": "mùa nóng", "fall": "mùa thu",
    "day": "ban ngày", "night": "buổi tối",
    "longevity": "lưu hương lâu", "sillage": "toả hương rõ",
}

# Mốc để coi điểm đánh giá là đã chắc chắn. Dùng ĐÚNG con số mà
# `analytics/metrics.py` dùng cho phép co Bayes (`prior_votes=500`), để chatbot và
# báo cáo không nói hai điều khác nhau về cùng một chai.
VOTE_DU_TIN = 500


def _nhan(w) -> str:
    """Một lý do, viết ra tiếng Việt đọc được."""
    nhan = VI_NHAN.get(w.label, w.label)
    if w.block == "strength":
        return nhan                       # "lưu hương lâu", không phải "độ longevity"
    if w.block == "occasion":
        return f"hợp {nhan}"
    return f"{VI_KHOI.get(w.block, w.block)} {nhan}"


def _mo_ta(m: Match) -> str:
    phan = [f"- {m.name or '(không tên)'}"]
    if m.brand:
        phan.append(f"của {m.brand}")
    if m.rating is not None:
        phan.append(f"— {m.rating:.2f}/5")
    if m.rating_count:
        phan.append(f"({m.rating_count:,} lượt đánh giá)".replace(",", "."))
        # Nói thẳng mức tin cậy thay vì để model tự đoán "bao nhiêu là nhiều".
        if m.rating_count < VOTE_DU_TIN:
            phan.append("[ít lượt, điểm chưa chắc]")
    if m.gender:
        phan.append(f"[{m.gender}]")
    dong = " ".join(phan)
    if m.why:
        dong += "\n    vì: " + ", ".join(_nhan(w) for w in m.why)
    return dong


def _boi_canh(res: SearchResult, cau: str, lich_su: str = "",
              ve_chai: bool = False, het_moi: bool = False) -> str:
    """Dữ liệu đưa cho model. Chỉ gồm thứ retriever trả về, cộng lịch sử hội thoại.

    `lich_su` là các lượt trước; thiếu nó thì model không biết mình đang nói tiếp
    và sẽ chào lại, giới thiệu lại chai cũ, mỗi lượt như một người mới.
    """
    phan = []
    if lich_su:
        phan += ["Hội thoại tới giờ:", lich_su, ""]
    phan.append(f"Lượt mới của khách: {cau}")

    if ve_chai and res.seed is not None:
        # Khách trỏ vào ĐÚNG một chai. Nói về chai đó trước, đừng đổi chủ đề.
        phan += ["", "Khách đang hỏi về: " + _mo_ta(res.seed).lstrip("- ")]
        if res.matches:
            phan += ["", "Chai gần nó (chỉ gợi thêm SAU khi đã nói về chai trên):"]
            phan += [_mo_ta(m) for m in res.matches]
        return "\n".join(phan)

    if not res.matches:
        phan += ["", "Không tìm được chai nào phù hợp."]
        return "\n".join(phan)

    if het_moi:
        # Nói thẳng cho model biết, nếu không nó sẽ giới thiệu lại mấy chai cũ như
        # thể vừa tìm ra — và đó là lúc khách thấy bot đang lặp.
        phan += ["", "LƯU Ý: không còn chai nào KHÁC khớp điều kiện này. Mấy chai "
                 "dưới đây là những chai ĐÃ gợi cho khách ở lượt trước. Hãy nói "
                 "thật là chưa có chai nào mới, rồi hỏi khách nới điều kiện nào."]
    phan += ["", "Các chai tìm được (đã xếp theo độ khớp):"]
    phan += [_mo_ta(m) for m in res.matches]
    if res.resolved:
        phan += ["", "Hệ thống đã hiểu câu đó thành: "
                 + "; ".join(f"{k} -> {v}" for k, v in res.resolved.items())]
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


def advise(cau: str, res: SearchResult, model: ChatModel, lich_su: str = "",
           ve_chai: bool = False,
           het_moi: bool = False) -> tuple[str, list[str]]:
    """Trả về (câu tư vấn, danh sách tên đáng nghi).

    `lich_su` là các lượt trước, dạng chữ. `ve_chai` là khi khách trỏ vào đúng một
    chai — lúc đó câu trả lời có hình dạng khác hẳn (nói về chai đó trước).

    Model hỏng thì rơi về `summary()`; mất phần diễn đạt, không mất câu trả lời.
    """
    try:
        rep: Reply = model.complete(
            [Message("system", HE_THONG),
             Message("user", _boi_canh(res, cau, lich_su, ve_chai,
                                       het_moi))],
            max_tokens=400, temperature=0.3)
    except LLMUnavailable as exc:
        log.warning("Không diễn đạt được bằng model (%s) — dùng bản ghép sẵn.",
                    str(exc)[:120])
        return summary(res, ve_chai=ve_chai, het_moi=het_moi), []
    chu = (rep.text or "").strip() or summary(res, ve_chai=ve_chai,
                                              het_moi=het_moi)
    bia = verify(chu, res)
    if bia:
        log.warning("Câu trả lời nhắc tên không có trong kết quả: %s", bia)
    return chu, bia


def _ve_mot_chai(res: SearchResult) -> str:
    """Khách trỏ vào đúng một chai — nói về chai đó, rồi mới gợi thêm.

    Hình dạng câu trả lời khác hẳn trường hợp tìm kiếm: ở đây chai đã được chọn,
    nên việc của câu trả lời là MÔ TẢ nó, không phải xếp hạng lại.
    """
    g = res.seed
    ten = g.name or "chai này"
    cau = [f"{ten}" + (f" của {g.brand}" if g.brand else "") + " thì"]
    if g.why:
        cau.append(" " + ", ".join(_nhan(w) for w in g.why[:3]) + ".")
    else:
        cau.append(" mình chưa có đủ dữ liệu về mùi của nó.")
    phan = ["".join(cau)]
    if g.rating is not None and g.rating_count:
        so = f"{g.rating_count:,}".replace(",", ".")
        if g.rating_count >= VOTE_DU_TIN:
            phan.append(f"Có {so} lượt đánh giá, trung bình {g.rating:.2f}/5.")
        else:
            phan.append(f"Mới {so} lượt đánh giá nên điểm {g.rating:.2f}/5 "
                        f"chưa chắc chắn.")
    gan = [m.name for m in res.matches[:2] if m.name]
    if gan:
        phan.append("Gần nó nhất là " + " và ".join(gan) + ".")
    phan.append("Bạn muốn mình so hai chai này không?")
    return " ".join(phan)


def summary(res: SearchResult, ve_chai: bool = False,
            het_moi: bool = False) -> str:
    """Câu trả lời ghép bằng Python thuần — KHÔNG gọi model.

    Đây là đường chạy khi chưa có `LLM_API_KEY`, nên nó không được là một dòng
    thông báo kỹ thuật: với người chưa cấu hình khoá, ĐÂY là chatbot. Nó cố gắng
    giữ cùng giọng tư vấn, chỉ không linh hoạt được như model.

    Bù lại, nó đúng tuyệt đối: mọi chữ đều ghép từ dữ liệu retriever trả về, nên
    không có đường nào cho một chai bịa đi vào đây.
    """
    if ve_chai and res.seed is not None:
        return _ve_mot_chai(res)
    if het_moi and res.matches:
        ten = ", ".join(m.name for m in res.matches[:3] if m.name)
        return (f"Với điều kiện này thì mình chưa thấy chai nào khác ngoài "
                f"{ten} đã gợi lúc trước. Bạn nới bớt một điều kiện nhé — bỏ mùa, "
                f"hay cho mình thêm một note hương bạn thích?")
    if not res.matches:
        return ("Mình chưa tìm được chai nào khớp ý này. Bạn thử nói theo note "
                "hương (ví dụ oud, vanilla, hoa hồng), hoặc theo lúc dùng — "
                "mùa lạnh hay nóng, đi làm hay đi tối?")

    dau = res.matches[0]
    ten = dau.name or "chai này"
    cau = [f"Mình nghĩ {ten}" + (f" của {dau.brand}" if dau.brand else "")
           + " là sát ý bạn nhất"]
    if dau.why:
        cau.append(" — " + ", ".join(_nhan(w) for w in dau.why[:3]))
    cau.append(".")
    phan = ["".join(cau)]

    # Điểm đánh giá: chỉ đem ra làm điểm cộng khi đủ lượt. Dưới ngưỡng thì nói
    # thật là chưa chắc, thay vì im lặng để người đọc tưởng 4.8/5 là đã vững.
    if dau.rating is not None and dau.rating_count:
        so = f"{dau.rating_count:,}".replace(",", ".")
        if dau.rating_count >= VOTE_DU_TIN:
            phan.append(f"Chai này có {so} lượt đánh giá, trung bình "
                        f"{dau.rating:.2f}/5 — khá nhiều người đã dùng.")
        else:
            phan.append(f"Mới có {so} lượt đánh giá nên điểm "
                        f"{dau.rating:.2f}/5 chưa chắc chắn lắm.")

    khac = [m for m in res.matches[1:3] if m.name]
    if khac:
        ds = " và ".join(m.name for m in khac)
        phan.append(f"Cùng hướng đó còn có {ds}, bạn xem thử bên cạnh.")

    phan.append("Bạn thích mùi ngọt ấm hay khô nhẹ hơn?")
    return " ".join(phan)
