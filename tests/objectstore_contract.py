"""Hợp đồng của `ObjectStore` — ĐỊNH NGHĨA THẬT của tầng kho đối tượng.

Phần chữ trong `core/objects.py` chỉ là giải thích; file này mới là thứ ràng buộc.

VÌ SAO BỘ NÀY QUAN TRỌNG HƠN HỢP ĐỒNG CỦA RETRIEVER
`Retriever` có hai bản cài đặt cùng chạy trong một tiến trình, lệch nhau thì thấy
ngay. Kho đối tượng thì không: bản local chạy trong mọi test, còn bản S3 chỉ chạy
khi có MinIO. Một chỗ lệch giữa hai bên (vd `prefix` hiểu theo thư mục thay vì
theo chuỗi) sẽ xanh suốt trên máy dev và chỉ vỡ sau khi đã chuyển kho — lúc đó
bronze đã nằm trên MinIO và không còn đường lùi dễ dàng.

CÁCH DÙNG KHI THÊM MỘT BACKEND MỚI
    def test_hop_dong_r2():
        contract.check_all(lambda: R2Store(...))     # phải trả về kho RỖNG

`make` là hàm không tham số, trả về một kho RỖNG đã sẵn sàng. Backend nào cần
dựng bucket thì tự lo trong hàm đó.
"""

from __future__ import annotations

from typing import Callable

from perfume_intel.core.objects import ObjectNotFound, ObjectStore

Make = Callable[[], ObjectStore]

NOI_DUNG = b'{"url": "https://f.com/perfume/x-1.html", "name": "Oud"}\n'


# ------------------------------------------------------------- các điều khoản
def check_ghi_roi_doc_lai_dung_nguyen_van(make: Make) -> None:
    store = make()
    store.put("fragrantica/perfumes/a.jsonl", NOI_DUNG)
    assert store.get("fragrantica/perfumes/a.jsonl") == NOI_DUNG


def check_ghi_de_khoa_cu(make: Make) -> None:
    """Niêm lại một file đã dài thêm là việc BÌNH THƯỜNG, không phải lỗi.

    `--resume` crawl nốt phần còn thiếu vào đúng file cũ rồi niêm lại. Kho nào
    từ chối ghi đè là kho không dùng được cho việc này.
    """
    store = make()
    store.put("a.jsonl", b"dong 1\n")
    store.put("a.jsonl", b"dong 1\ndong 2\n")
    assert store.get("a.jsonl") == b"dong 1\ndong 2\n"
    assert len(store.list("a.jsonl")) == 1, "ghi đè mà thành hai object"


def check_doc_khoa_khong_co_thi_bao_loi(make: Make) -> None:
    try:
        make().get("khong/co/dau.jsonl")
    except ObjectNotFound:
        return
    raise AssertionError("đọc khoá không có mà không báo lỗi")


def check_stat_khoa_khong_co_tra_ve_none(make: Make) -> None:
    """Thiếu một object là chuyện bình thường -> None, KHÔNG ném lỗi.

    Phân biệt với `get`: `stat` là câu hỏi "có không", nên "không" là một câu
    trả lời. Nếu nó cũng ném lỗi thì mọi chỗ kiểm tra tồn tại đều phải bọc
    try/except, và sớm muộn có chỗ bọc rộng quá rồi nuốt luôn lỗi mất kho.
    """
    assert make().stat("khong/co.jsonl") is None


def check_stat_bao_dung_so_byte(make: Make) -> None:
    store = make()
    store.put("a.jsonl", NOI_DUNG)
    info = store.stat("a.jsonl")
    assert info is not None
    assert info.size == len(NOI_DUNG), f"{info.size} != {len(NOI_DUNG)}"
    assert info.key == "a.jsonl"
    assert info.modified > 0, "không có mốc thời gian"


def check_put_tra_ve_dung_thu_stat_tra_ve(make: Make) -> None:
    store = make()
    ghi = store.put("a/b.jsonl", NOI_DUNG)
    doc = store.stat("a/b.jsonl")
    assert doc is not None
    assert (ghi.key, ghi.size) == (doc.key, doc.size)


def check_kho_rong_liet_ke_ra_rong(make: Make) -> None:
    """Kho chưa có gì phải ra danh sách rỗng, KHÔNG ném lỗi.

    Lần chạy đầu tiên luôn gặp cảnh này (bucket vừa tạo, thư mục chưa có). Ném
    lỗi ở đây nghĩa là không ai cài được project lần đầu.
    """
    assert make().list() == []


def check_liet_ke_sap_theo_khoa(make: Make) -> None:
    store = make()
    for key in ("c.jsonl", "a.jsonl", "b.jsonl"):
        store.put(key, NOI_DUNG)
    assert [i.key for i in store.list()] == ["a.jsonl", "b.jsonl", "c.jsonl"]


def check_prefix_khop_theo_chuoi_khong_theo_thu_muc(make: Make) -> None:
    """ĐIỀU KHOẢN DỄ LỆCH NHẤT GIỮA HAI BACKEND.

    S3 không có thư mục, `prefix` của nó là so khớp chuỗi thuần. Bản local rất
    dễ cài thành "liệt kê thư mục này", và lúc đó `list("fragrantica/per")` trả
    về rỗng trên local nhưng ra đủ file trên S3. Chỗ lệch đó không gây lỗi —
    nó chỉ làm dữ liệu biến mất.
    """
    store = make()
    store.put("fragrantica/perfumes/a.jsonl", NOI_DUNG)
    store.put("fragrantica/products/b.jsonl", NOI_DUNG)
    assert len(store.list("fragrantica/")) == 2
    assert [i.key for i in store.list("fragrantica/per")] == \
        ["fragrantica/perfumes/a.jsonl"]
    assert store.list("fragrantica/z") == []


def check_liet_ke_duoc_khoa_nhieu_cap(make: Make) -> None:
    store = make()
    store.put("a/b/c/d/e.jsonl", NOI_DUNG)
    assert [i.key for i in store.list()] == ["a/b/c/d/e.jsonl"]


def check_xoa_khoa_co_that_tra_ve_true(make: Make) -> None:
    store = make()
    store.put("a.jsonl", NOI_DUNG)
    assert store.delete("a.jsonl") is True
    assert store.stat("a.jsonl") is None


def check_xoa_khoa_khong_co_tra_ve_false(make: Make) -> None:
    """False, không phải ném lỗi: xoá thứ vốn không có là một kết quả, không
    phải một sự cố. Nhờ vậy dọn dẹp viết được mà không cần try/except."""
    assert make().delete("khong/co.jsonl") is False


def check_giu_nguyen_byte_nhi_phan(make: Make) -> None:
    """Kho này cũng dùng cho Parquet, không riêng JSONL. Encode/decode lén ở
    giữa sẽ làm hỏng file nhị phân mà JSONL vẫn trông bình thường."""
    store = make()
    data = bytes(range(256))
    store.put("a.parquet", data)
    assert store.get("a.parquet") == data


def check_giu_nguyen_tieng_viet_trong_noi_dung(make: Make) -> None:
    store = make()
    data = '{"brand": "Hãng Nước Hoa", "note": "gỗ đàn hương"}\n'.encode()
    store.put("a.jsonl", data)
    assert store.get("a.jsonl").decode() == data.decode()


def check_khoa_co_dau_tieng_viet_dung_duoc(make: Make) -> None:
    """Tên hãng đi THẲNG vào tên file: `Hermès`, `L'Artisan Parfumeur`...

    Không phải chuyện giả định — `data/inputs/fragrantica/` đã có `Viktor & Rolf`
    và `Victoria's Secret`. Kho nào không chịu được khoá như vậy là kho làm mất
    đúng những hãng đó.
    """
    store = make()
    key = "fragrantica/perfumes/Hermès & Cie_fragrantica_051026.jsonl"
    store.put(key, NOI_DUNG)
    assert store.get(key) == NOI_DUNG
    assert [i.key for i in store.list("fragrantica/")] == [key]


def check_object_rong_van_la_mot_object(make: Make) -> None:
    """0 byte khác hẳn "không có". Một hãng crawl ra 0 chai vẫn là kết quả đã
    biết; lẫn nó với "chưa crawl" là crawl lại vô ích mỗi ngày."""
    store = make()
    store.put("rong.jsonl", b"")
    info = store.stat("rong.jsonl")
    assert info is not None and info.size == 0
    assert store.get("rong.jsonl") == b""
    assert [i.key for i in store.list()] == ["rong.jsonl"]


def check_dau_gach_nguoc_duoc_chuan_hoa(make: Make) -> None:
    """Windows là môi trường chạy chính. Nếu `a\\b` và `a/b` thành hai object
    khác nhau thì cùng một file có hai tên, và phía đọc chỉ thấy một nửa."""
    store = make()
    store.put("fragrantica\\perfumes\\a.jsonl", NOI_DUNG)
    assert store.get("fragrantica/perfumes/a.jsonl") == NOI_DUNG
    assert [i.key for i in store.list()] == ["fragrantica/perfumes/a.jsonl"]


def check_tu_choi_khoa_sai_dang(make: Make) -> None:
    """Từ chối ngay lúc ghi. Khoá sai dạng không gây lỗi — nó tạo ra một object
    thứ hai nằm cạnh object thật, và phía đọc chỉ thấy thiếu dữ liệu."""
    store = make()
    for xau in ("/a.jsonl", "a//b.jsonl", "../a.jsonl", "a/./b.jsonl",
                "a/", "", "  a.jsonl  "):
        try:
            store.put(xau, NOI_DUNG)
        except ValueError:
            continue
        raise AssertionError(f"nhận khoá sai dạng: {xau!r}")


def check_la_mot_object_store(make: Make) -> None:
    assert isinstance(make(), ObjectStore)


CHECKS = [v for k, v in sorted(globals().items()) if k.startswith("check_")]


def check_all(make: Make) -> None:
    """Chạy toàn bộ hợp đồng. Hỏng điều khoản nào thì báo rõ điều khoản đó."""
    for fn in CHECKS:
        try:
            fn(make)
        except AssertionError as exc:
            raise AssertionError(f"[{fn.__name__}] {exc}") from None
