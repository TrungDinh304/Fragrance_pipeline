"""Hợp đồng của `Embedder` — ĐỊNH NGHĨA THẬT của tầng embedding.

Phần chữ trong `embedding/ports.py` chỉ là giải thích; file này mới ràng buộc.

BỘ NÀY KHÔNG ĐO CHẤT LƯỢNG, VÀ ĐÓ LÀ CHỦ Ý
Chất lượng ngữ nghĩa phụ thuộc model, nên không thể là điều khoản của hợp đồng:
`HashingEmbedder` sẽ không bao giờ qua được một bài kiểm "mùi gỗ phải gần woody".
Hợp đồng này kiểm những thứ MỌI bản cài đặt phải đúng — tất định, đúng số chiều,
đúng thứ tự, không vỡ vì tiếng Việt. Chất lượng đo riêng, trên dữ liệu thật, trong
`tests/test_embedding.py`.

CÁCH DÙNG KHI THÊM MỘT BACKEND MỚI
    def test_hop_dong_openai():
        contract.check_all(lambda: OpenAIEmbedder(...))
"""

from __future__ import annotations

from typing import Callable

from perfume_intel.embedding.ports import Embedder

Make = Callable[[], Embedder]

CAU = ["Vintage Radio của Lattafa Perfumes. Mùi chủ đạo: woody, sweet.",
       "Chanh Biển của BetaWorks. Mùi chủ đạo: citrus, fresh."]


def check_tra_ve_dung_so_luong_va_thu_tu(make: Make) -> None:
    """Thứ tự là thứ duy nhất nối vector với chai của nó.

    Nếu một adapter trả về theo thứ tự khác (vd sắp lại để chia lô cho nhanh) thì
    mọi vector bị gán cho sai chai — và không có cách nào phát hiện từ con số.
    """
    e = make()
    ra = e.embed_documents(CAU)
    assert len(ra) == len(CAU), f"{len(ra)} vector cho {len(CAU)} câu"
    # Đưa vào thứ tự đảo, kết quả phải đảo theo.
    dao = e.embed_documents(list(reversed(CAU)))
    assert list(dao[0]) == list(ra[1]) and list(dao[1]) == list(ra[0]), \
        "đổi thứ tự đầu vào mà vector không đổi theo — vector bị gán sai chai"


def check_dung_so_chieu(make: Make) -> None:
    e = make()
    for v in e.embed_documents(CAU):
        assert len(v) == e.dimensions, f"{len(v)} != {e.dimensions}"
    assert len(e.embed_query("mùi gỗ")) == e.dimensions


def check_tat_dinh(make: Make) -> None:
    """Cùng chữ phải ra cùng vector.

    Không tất định thì không cache được, không so sánh được hai lần sinh, và một
    chai crawl lại sẽ nhảy chỗ trong kho vector dù dữ liệu không đổi.
    """
    e = make()
    assert list(e.embed_documents([CAU[0]])[0]) == \
        list(e.embed_documents([CAU[0]])[0])
    assert list(e.embed_query("oud")) == list(e.embed_query("oud"))


def check_chu_khac_nhau_thi_vector_khac_nhau(make: Make) -> None:
    e = make()
    a, b = e.embed_documents(CAU)
    assert list(a) != list(b), "hai câu khác hẳn nhau mà ra cùng một vector"


def check_cau_rong_khong_no(make: Make) -> None:
    """Câu rỗng là chuyện bình thường (chai thiếu dữ liệu), không phải lỗi."""
    e = make()
    v = e.embed_documents([""])
    assert len(v) == 1 and len(v[0]) == e.dimensions
    assert len(e.embed_query("")) == e.dimensions


def check_danh_sach_rong_tra_ve_rong(make: Make) -> None:
    assert make().embed_documents([]) == []


def check_tieng_viet_co_dau_khong_no(make: Make) -> None:
    """Dữ liệu và câu hỏi đều có dấu, và việc GIỮ dấu là một quyết định đã đo."""
    e = make()
    v = e.embed_query("nước hoa mùi gỗ đàn hương cho buổi tối mùa đông")
    assert len(v) == e.dimensions


def check_cau_rat_dai_khong_no(make: Make) -> None:
    """Chai có 10 note + 6 accord ra câu khá dài; model nào cũng phải tự cắt."""
    e = make()
    v = e.embed_documents([" ".join(CAU) * 200])
    assert len(v[0]) == e.dimensions


def check_model_id_on_dinh_va_khong_rong(make: Make) -> None:
    """`model_id` đi kèm mọi vector. Rỗng hoặc đổi giữa hai lần gọi thì mất luôn
    khả năng phát hiện trộn vector của hai model."""
    e = make()
    assert e.model_id and isinstance(e.model_id, str)
    assert e.model_id == make().model_id


def check_so_chieu_on_dinh_va_duong(make: Make) -> None:
    e = make()
    assert e.dimensions > 0
    assert e.dimensions == e.dimensions


def check_la_mot_embedder(make: Make) -> None:
    assert isinstance(make(), Embedder)


CHECKS = [v for k, v in sorted(globals().items()) if k.startswith("check_")]


def check_all(make: Make) -> None:
    for fn in CHECKS:
        try:
            fn(make)
        except AssertionError as exc:
            raise AssertionError(f"[{fn.__name__}] {exc}") from None
