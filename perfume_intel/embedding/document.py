"""Một chai nước hoa được viết thành CÂU như thế nào để đưa vào model.

Đây là chỗ quyết định chất lượng tìm kiếm ngữ nghĩa, nhiều hơn cả việc chọn model.
Vector chỉ tốt bằng câu đã đưa vào.

BA QUYẾT ĐỊNH, ĐỀU ĐO RỒI MỚI CHỐT
Đo trên 5 câu hỏi tiếng Việt, thước đo là thứ hạng của đáp án đúng:

  1. **Khung tiếng Việt + tên note GIỮ NGUYÊN tiếng Anh.** Thử ba cách viết tài
     liệu — thuần Anh, dịch sẵn sang Việt, và trộn — cả ba đều 5/5. Nghĩa là
     model đa ngôn ngữ tự bắc cầu `woody` <-> `mùi gỗ`, nên KHÔNG CẦN từ điển
     note tiếng Việt. Chọn cách trộn vì nó giữ dữ liệu nguyên văn: không có tầng
     dịch nào để phải bảo trì, và không có chỗ nào dịch sai.

  2. **GIỮ DẤU tiếng Việt trong khung.** Bỏ dấu ở cả hai phía làm kết quả tụt từ
     5/5 xuống 2/5. Phản trực giác nhưng rõ: tuyệt đối không "chuẩn hoá" bằng
     cách bỏ dấu. Câu hỏi không dấu vẫn được 4/5 miễn là TÀI LIỆU còn dấu.

  3. **Chỉ viết những gì cộng đồng thật sự bình chọn.** Không suy diễn ra "hẹn
     hò", "công sở" — Fragrantica không có mấy trục đó, và bịa thêm nhãn vào câu
     là dạy model một thứ dữ liệu không có.

Cảnh báo về bằng chứng: phép đo trên dùng 4 tài liệu. 5/5 ở quy mô đó là bằng
chứng YẾU. `tests/test_embedding.py` có bộ đo trên dữ liệu thật để kiểm lại ở quy
mô 799 chai.
"""

from __future__ import annotations

from ..analytics.dataset import Row

# Số accord/note nhiều nhất đưa vào câu. Cắt bớt có lý do: đuôi dài của note là
# những thứ chỉ vài người bình chọn, thêm vào chỉ làm loãng vector — mọi chai đều
# "hơi giống" nhau vì đều có musk.
MAX_ACCORDS = 6
MAX_NOTES = 10

# Vote hoàn cảnh phải đạt mức này mới được viết vào câu. Mọi chai đều có điểm cho
# mọi mùa; không có ngưỡng thì câu nào cũng "phù hợp mùa đông, mùa hè, mùa xuân,
# mùa thu" và trục hoàn cảnh mất hết tác dụng phân biệt.
MIN_OCCASION = 50.0

MUA = {"winter": "mùa đông", "spring": "mùa xuân",
       "summer": "mùa hè", "fall": "mùa thu"}
GIO = {"day": "ban ngày", "night": "buổi tối"}


def _manh_nhat(diem: dict[str, float], toi_da: int,
               nguong: float = 0.0) -> list[str]:
    cao = [(v, k) for k, v in (diem or {}).items() if v and v >= nguong]
    cao.sort(reverse=True)
    return [k for _v, k in cao[:toi_da]]


def _hoan_canh(row: Row) -> list[str]:
    ra = []
    for khoa, nhan in (*MUA.items(), *GIO.items()):
        diem = (row.seasons or {}).get(khoa) or (row.day_night or {}).get(khoa)
        if diem and diem >= MIN_OCCASION:
            ra.append((diem, nhan))
    ra.sort(reverse=True)
    return [n for _d, n in ra]


def document(row: Row) -> str:
    """Câu mô tả một chai, để đưa vào model.

    Trả về chuỗi rỗng nếu chai không có cả accord lẫn note — chai như vậy không
    mang tín hiệu mùi nào, và embedding của nó chỉ là tên hãng.
    """
    accords = _manh_nhat(row.accords, MAX_ACCORDS)
    notes = list(row.notes or [])[:MAX_NOTES]
    if not accords and not notes:
        return ""

    phan = []
    ten = row.name or "(không tên)"
    phan.append(f"{ten} của {row.brand}." if row.brand else f"{ten}.")

    # Tên accord/note để NGUYÊN tiếng Anh như trong dữ liệu — xem quyết định 1.
    if accords:
        phan.append("Mùi chủ đạo: " + ", ".join(accords) + ".")
    if notes:
        phan.append("Note: " + ", ".join(notes) + ".")

    hoan_canh = _hoan_canh(row)
    if hoan_canh:
        phan.append("Phù hợp " + ", ".join(hoan_canh) + ".")

    if row.gender:
        phan.append(f"{row.gender}.")

    return " ".join(phan)


def documents(rows: list[Row]) -> dict[str, str]:
    """{perfume_key: câu}. Bỏ những chai không có tín hiệu mùi."""
    from ..core.text import url_key
    ra: dict[str, str] = {}
    for row in rows:
        khoa = url_key(row.url)
        cau = document(row)
        if khoa and cau:
            ra[khoa] = cau
    return ra
