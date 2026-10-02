"""Biến một chai nước hoa thành vector để đo độ giống nhau.

Vector ở đây **tự tính từ dữ liệu có cấu trúc**, không qua model nào. Ba lý do:

  1. Tất định — cùng dữ liệu vào thì luôn ra cùng kết quả, test được.
  2. Giải thích được — mỗi chiều là một thứ có tên ("acc:woody", "note:Oud"),
     nên trả lời được câu "vì sao hai chai này giống nhau" chứ không chỉ đưa ra
     một con số.
  3. Miễn phí và chạy offline — không gọi API, không tải model.

Đây là LỚP A. Lớp B (embedding văn bản, hỏi bằng câu tự do như "gì đó ấm cho
buổi tối mùa đông") là việc khác và cần model đa ngữ. Lớp A còn là mốc đối chứng:
lớp B phải hơn được nó mới đáng thêm 2 GB phụ thuộc vào ảnh Docker.

VECTOR THƯA, KHÔNG PHẢI DÀY
Từ vựng đầy đủ là ~650 chiều (64 accord + 551 note + 31 family + 6 hoàn cảnh +
2 cường độ) nhưng mỗi chai chỉ chiếm khoảng 33 chiều khác 0. Vì vậy vector là
`dict[str, float]` và cosine chỉ chạy trên khoá chung. Ở mức 100k chai, cách này
vẫn nhanh bằng Python thuần, không cần numpy — thêm một phụ thuộc chỉ để làm
chậm hơn thì không đáng.

CHUẨN HOÁ THEO KHỐI, RỒI MỚI ĐÁNH TRỌNG SỐ
Đây là điểm dễ làm sai nhất. Nếu ghép thẳng rồi chuẩn hoá một lần, khối note
(551 chiều) sẽ nhấn chìm khối hoàn cảnh (6 chiều) — kết quả là "hoàn cảnh sử
dụng" gần như không ảnh hưởng gì tới thứ tự, dù nó là một trong ba trục mà
người dùng hỏi. Nên: chuẩn hoá L2 TỪNG khối trước, nhân trọng số khối, rồi mới
chuẩn hoá toàn bộ. Sau bước đó cosine = tích vô hướng.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Iterable, Mapping, Sequence

from ..analytics.dataset import DAY_NIGHT, SEASONS, Row

# Tiền tố khối. Nằm ngay trong tên chiều để một vector in ra là đọc được.
ACCORD, NOTE, OCCASION, STRENGTH, FAMILY = "acc", "note", "occ", "str", "fam"

# Thang thứ tự cho hai trường dạng chữ. Nhãn lấy đúng như Fragrantica trả về —
# đã đối chiếu trên 629 chai, không có giá trị nào ngoài danh sách này.
LONGEVITY_SCALE = {
    "very weak": 0.0,
    "weak": 0.25,
    "moderate": 0.5,
    "long lasting": 0.75,
    "eternal": 1.0,
}
SILLAGE_SCALE = {
    "intimate": 0.0,
    "moderate": 1 / 3,
    "strong": 2 / 3,
    "enormous": 1.0,
}

# Trọng số theo tầng tháp hương: base > middle > top. Base là thứ còn lại sau
# vài giờ, top bay trong mươi phút — trùng base mới là thật sự giống nhau.
NOTE_LAYER_WEIGHT = {"base": 1.0, "middle": 0.7, "general": 0.7, "top": 0.5}

# Trọng số giữa các khối. Đây là những con số CÓ THỂ TRANH LUẬN, nên để lộ ra
# chứ không giấu trong code: mùi (accord + note) là chính, hoàn cảnh là phụ trợ,
# cường độ và họ hương chỉ để phá thế hoà.
BLOCK_WEIGHTS = {
    ACCORD: 1.0,
    NOTE: 1.0,
    OCCASION: 0.6,
    STRENGTH: 0.3,
    FAMILY: 0.4,
}

# Khối dùng để lọc ứng viên trong index đảo ngược. Hoàn cảnh và cường độ có ở
# ~100% số chai nên không lọc được gì — đưa vào chỉ khiến tập ứng viên = toàn bộ.
SELECTIVE_BLOCKS = (ACCORD, NOTE, FAMILY)

# Khối mang thông tin MÙI. Dùng khi so hãng với hãng, và lý do rất cụ thể:
# trọng tâm của 120 chai làm loãng hết chi tiết note (mỗi note chỉ có ở vài chai
# nên trong trọng tâm nó gần như bằng 0), trong khi khối hoàn cảnh có ở MỌI chai
# nên sống sót qua phép lấy trung bình và hội tụ về đúng một giá trị chung.
# Kết quả nếu không tách: mọi hãng đều "giống nhau" ở fall/winter/night và điểm
# số bị nén vào dải 0,76-0,82 — đo thật đã thấy vậy. Tức là con số vẫn ra nhưng
# không còn mang tin gì.
SMELL_BLOCKS = (ACCORD, NOTE, FAMILY)

# Note chỉ xuất hiện đúng 1 lần thì bỏ. Trên 629 chai có 194 note như vậy; giữ
# lại thì IDF đẩy chúng lên cao nhất và hai chai tình cờ trùng một note độc nhất
# trông như rất giống nhau. Ngưỡng này sẽ tự nới ra khi kho dữ liệu lớn dần.
MIN_DF = 2

# Chặn trần IDF: với kho nhỏ, một note xuất hiện 2/629 lần cho IDF ~5,8. Không
# chặn thì vài note hiếm chi phối toàn bộ điểm số.
MAX_IDF = 4.0


def term(block: str, name: str) -> str:
    return f"{block}:{name.strip().lower()}"


def _l2(vec: Mapping[str, float]) -> float:
    return math.sqrt(sum(v * v for v in vec.values()))


def _unit(vec: dict[str, float]) -> dict[str, float]:
    norm = _l2(vec)
    if norm == 0:
        return {}
    return {k: v / norm for k, v in vec.items()}


# --------------------------------------------------------------------- IDF
def build_idf(rows: Iterable[Row], min_df: int = MIN_DF,
              max_idf: float = MAX_IDF) -> dict[str, float]:
    """IDF cho khối accord và note.

    Mục đích: "Musk" có ở phần lớn các chai nên trùng nó gần như không nói gì,
    còn trùng "Oud" thì nói nhiều. Không có IDF thì mọi kết quả đều trôi về
    những note phổ biến nhất và danh sách gợi ý trông giống nhau hết.

    Trả về map chỉ chứa những term VƯỢT `min_df`. Term không có trong map coi
    như bị loại — xem `_accord_block` / `_note_block`.
    """
    rows = list(rows)
    df: Counter[str] = Counter()
    for row in rows:
        seen = {term(ACCORD, n) for n in row.accords}
        seen |= {term(NOTE, n) for names in row.notes_by_layer.values()
                 for n in names}
        df.update(seen)

    total = len(rows) or 1
    idf: dict[str, float] = {}
    for key, count in df.items():
        if count < min_df:
            continue
        idf[key] = min(math.log(total / count) + 1.0, max_idf)
    return idf


# ------------------------------------------------------------------ khối
def _accord_block(row: Row, idf: Mapping[str, float]) -> dict[str, float]:
    """Accord kèm độ mạnh.

    `width` là độ dài thanh bar trên trang (đo thực tế: 33-100), tức là độ mạnh
    tương đối của accord trong chai đó. Dùng nguyên rồi để bước chuẩn hoá L2 lo
    phần so sánh giữa các chai.
    """
    out: dict[str, float] = {}
    for name, width in row.accords.items():
        key = term(ACCORD, name)
        weight = idf.get(key)
        if weight is None:
            continue
        out[key] = (width or 0.0) / 100.0 * weight
    return out


def _note_block(row: Row, idf: Mapping[str, float]) -> dict[str, float]:
    """Note, đánh trọng số theo tầng rồi nhân IDF.

    Một note nằm ở nhiều tầng thì lấy tầng nặng nhất, không cộng dồn: xuất hiện
    ở cả top và base không làm nó quan trọng gấp đôi.
    """
    out: dict[str, float] = {}
    for layer, names in row.notes_by_layer.items():
        layer_weight = NOTE_LAYER_WEIGHT.get(layer, 0.7)
        for name in names:
            key = term(NOTE, name)
            weight = idf.get(key)
            if weight is None:
                continue
            out[key] = max(out.get(key, 0.0), layer_weight * weight)
    return out


def _occasion_block(row: Row) -> dict[str, float]:
    """Sáu trục hoàn cảnh: 4 mùa + ngày/đêm, theo % vote.

    CHỈ CÓ VẬY. Fragrantica không có "công sở", "hẹn hò", "phòng gym" — mấy nhãn
    đó phải suy diễn từ cường độ (xem `derive_occasion`), và phải nói rõ là suy
    diễn chứ không phải người dùng vote.
    """
    out: dict[str, float] = {}
    for axis in SEASONS:
        if axis in row.seasons:
            out[term(OCCASION, axis)] = (row.seasons[axis] or 0.0) / 100.0
    for axis in DAY_NIGHT:
        if axis in row.day_night:
            out[term(OCCASION, axis)] = (row.day_night[axis] or 0.0) / 100.0
    return out


def _strength_block(row: Row) -> dict[str, float]:
    out: dict[str, float] = {}
    lon = LONGEVITY_SCALE.get((row.longevity or "").strip().lower())
    sil = SILLAGE_SCALE.get((row.sillage or "").strip().lower())
    if lon is not None:
        out[term(STRENGTH, "longevity")] = lon
    if sil is not None:
        out[term(STRENGTH, "sillage")] = sil
    return out


def _family_block(row: Row) -> dict[str, float]:
    if not row.fragrance_family:
        return {}
    return {term(FAMILY, row.fragrance_family): 1.0}


# ----------------------------------------------------------------- vector
def blocks(row: Row, idf: Mapping[str, float]) -> dict[str, dict[str, float]]:
    return {
        ACCORD: _accord_block(row, idf),
        NOTE: _note_block(row, idf),
        OCCASION: _occasion_block(row),
        STRENGTH: _strength_block(row),
        FAMILY: _family_block(row),
    }


def combine(parts: Mapping[str, Mapping[str, float]],
            weights: Mapping[str, float] = BLOCK_WEIGHTS) -> dict[str, float]:
    """Chuẩn hoá L2 từng khối -> nhân trọng số khối -> chuẩn hoá toàn bộ.

    Thứ tự này quan trọng; xem phần đầu file.
    """
    out: dict[str, float] = {}
    for block, part in parts.items():
        weight = weights.get(block, 0.0)
        if not part or weight == 0:
            continue
        for key, value in _unit(dict(part)).items():
            out[key] = value * weight
    return _unit(out)


def vector(row: Row, idf: Mapping[str, float],
           weights: Mapping[str, float] = BLOCK_WEIGHTS) -> dict[str, float]:
    return combine(blocks(row, idf), weights)


def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Cosine của hai vector ĐÃ chuẩn hoá = tích vô hướng.

    Chạy vòng trên vector ngắn hơn: chi phí tỉ lệ với min(len(a), len(b)) chứ
    không phải với số chiều của từ vựng.
    """
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b[key] for key, value in a.items() if key in b)


def contributions(a: Mapping[str, float], b: Mapping[str, float],
                  limit: int = 5) -> list[tuple[str, float]]:
    """Những chiều góp nhiều nhất vào điểm giống nhau — phần "vì sao"."""
    shared = [(key, value * b[key]) for key, value in a.items() if key in b]
    shared.sort(key=lambda kv: kv[1], reverse=True)
    return shared[:limit]


def keep_blocks(vec: Mapping[str, float],
                blocks_: Sequence[str]) -> dict[str, float]:
    """Giữ lại vài khối rồi chuẩn hoá lại. Xem `SMELL_BLOCKS`."""
    kept = {k: v for k, v in vec.items() if k.split(":", 1)[0] in blocks_}
    return _unit(kept)


def centroid(vectors: Iterable[Mapping[str, float]]) -> dict[str, float]:
    """Trọng tâm của một nhóm vector, dùng làm chân dung mùi của một hãng."""
    total: dict[str, float] = {}
    count = 0
    for vec in vectors:
        count += 1
        for key, value in vec.items():
            total[key] = total.get(key, 0.0) + value
    if not count:
        return {}
    return _unit({k: v / count for k, v in total.items()})


# ------------------------------------------------------ truy vấn thủ công
def query_from_terms(terms: Iterable[str], occasion: Iterable[str] = (),
                     idf: Mapping[str, float] | None = None,
                     weights: Mapping[str, float] = BLOCK_WEIGHTS,
                     ) -> dict[str, float]:
    """Vector truy vấn từ các term ĐÃ khớp sẵn (đã có tiền tố khối).

    Dùng sau `index.resolve`, để tên người dùng gõ ("oud") được đưa về term thật
    ("agarwood (oud)") trước khi vào đây.
    """
    idf = idf or {}
    parts: dict[str, dict[str, float]] = {}
    for key in terms:
        block = key.split(":", 1)[0]
        parts.setdefault(block, {})[key] = idf.get(key, 1.0)

    occ = {term(OCCASION, name): 1.0 for name in occasion
           if name.strip().lower() in (*SEASONS, *DAY_NIGHT)}
    if occ:
        parts[OCCASION] = occ
    return combine(parts, weights)


def query_vector(notes: Iterable[str] = (), accords: Iterable[str] = (),
                 occasion: Iterable[str] = (),
                 idf: Mapping[str, float] | None = None,
                 weights: Mapping[str, float] = BLOCK_WEIGHTS,
                 ) -> dict[str, float]:
    """Vector cho một truy vấn người dùng gõ tay, không phải từ một chai có sẵn.

    Term không có trong `idf` (note quá hiếm, hoặc gõ sai) bị bỏ — phía gọi nên
    báo lại cho người dùng biết term nào không dùng được thay vì im lặng trả về
    kết quả vô nghĩa; xem `index.unknown_terms`.
    """
    idf = idf or {}
    parts: dict[str, dict[str, float]] = {}

    acc = {}
    for name in accords:
        key = term(ACCORD, name)
        if key in idf:
            acc[key] = idf[key]
    if acc:
        parts[ACCORD] = acc

    note = {}
    for name in notes:
        key = term(NOTE, name)
        if key in idf:
            note[key] = idf[key]
    if note:
        parts[NOTE] = note

    occ = {term(OCCASION, name): 1.0 for name in occasion
           if name.strip().lower() in (*SEASONS, *DAY_NIGHT)}
    if occ:
        parts[OCCASION] = occ

    return combine(parts, weights)


# --------------------------------------------------- hoàn cảnh suy diễn ra
# Fragrantica chỉ vote mùa và ngày/đêm. Những nhãn dưới đây là SUY DIỄN từ cường
# độ và mùa, KHÔNG phải dữ liệu người dùng bình chọn. Để riêng ra đây, không
# nhét vào vector, để không lẫn thứ đo được với thứ tự suy ra.
def derive_occasion(row: Row) -> list[str]:
    """Nhãn hoàn cảnh suy diễn. Chỉ để gợi ý, đừng coi là dữ liệu gốc."""
    labels: list[str] = []
    sil = SILLAGE_SCALE.get((row.sillage or "").strip().lower())
    lon = LONGEVITY_SCALE.get((row.longevity or "").strip().lower())

    # Công sở: toả vừa hoặc nhẹ (không xâm phạm người bên cạnh) nhưng bám đủ lâu.
    if sil is not None and lon is not None and sil <= 1 / 3 and lon >= 0.5:
        labels.append("công sở")
    # Tối/tiệc: toả mạnh và lâu.
    if sil is not None and lon is not None and sil >= 2 / 3 and lon >= 0.75:
        labels.append("tiệc tối")
    # Ngày nóng: mùa hè thắng rõ.
    if row.seasons.get("summer", 0) >= 80:
        labels.append("ngày nóng")
    if row.seasons.get("winter", 0) >= 80:
        labels.append("ngày lạnh")
    return labels
