"""Tầng embedding: hợp đồng, cách viết tài liệu, và CHẤT LƯỢNG trên dữ liệu thật.

Ba phần tách rời có chủ đích:

  1. **Hợp đồng** — chạy cho `HashingEmbedder` (luôn có) và cho `OnnxEmbedder`
     (chỉ khi nạp được model). Kiểm tính thay thế được, không kiểm chất lượng.
  2. **Cách viết tài liệu** — thuần Python, không cần model. Đây là chỗ quyết
     định chất lượng nhiều hơn cả việc chọn model, nên nó phải được canh chặt.
  3. **Chất lượng trên dữ liệu thật** — chỉ chạy khi có model + dữ liệu. Đây là
     bài trả lời câu "model này có thật sự hiểu tiếng Việt về nước hoa không",
     thay cho phép đo 4 tài liệu lúc chọn model (bằng chứng quá yếu).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from runner import run  # noqa: E402

import embedder_contract as contract  # noqa: E402
from perfume_intel import config  # noqa: E402
from perfume_intel.analytics.dataset import Row  # noqa: E402
from perfume_intel.embedding import (HashingEmbedder, document,  # noqa: E402
                                     documents)
from perfume_intel.embedding import store  # noqa: E402
from perfume_intel.embedding.ports import (Embedding,  # noqa: E402
                                           EmbeddingSet, ModelMismatch)


def _skip(ly_do: str) -> bool:
    print(f"SKIP  {ly_do}")
    return True


_MAC_DINH = object()


def _row(name="Oud Thẳng", brand="AlphaHouse", accords=_MAC_DINH,
         notes=_MAC_DINH, seasons=None, day_night=None, gender="Unisex",
         url=None) -> Row:
    """Chú ý `_MAC_DINH` chứ không phải `None`.

    Viết `dict(accords or {...})` thì `accords={}` — đúng cái cần thử — lại rơi
    về giá trị mặc định, vì `{}` là falsy. Ba bài test đã xanh/đỏ sai vì chuyện
    này: chai "không có mùi" thật ra vẫn có đủ accord.
    """
    if accords is _MAC_DINH:
        accords = {"woody": 100.0, "sweet": 60.0}
    if notes is _MAC_DINH:
        notes = ["Agarwood (Oud)", "Vanilla"]
    return Row(
        url=url or f"https://f.com/perfume/{brand}/{name}.html",
        name=name, brand=brand, gender=gender, rating=4.0, rating_count=100,
        accords=dict(accords), notes=list(notes),
        notes_by_layer={}, seasons=dict(seasons or {}),
        day_night=dict(day_night or {}))


# ------------------------------------------------------------------ hợp đồng
def test_hop_dong_hashing_embedder():
    contract.check_all(HashingEmbedder)


def test_hop_dong_onnx_embedder():
    """Chỉ chạy khi nạp được model thật. Trên Windows thường không — onnxruntime
    lỗi DLL — nên BỎ QUA kèm lý do, không báo đỏ."""
    from perfume_intel.embedding.onnx import OnnxEmbedder
    from perfume_intel.embedding.ports import EmbeddingUnavailable
    try:
        OnnxEmbedder().dimensions
    except EmbeddingUnavailable as exc:
        if _skip(f"test_hop_dong_onnx_embedder: {str(exc)[:70]}"):
            return
    contract.check_all(OnnxEmbedder)


# -------------------------------------------------------- cách viết tài liệu
def test_tai_lieu_giu_nguyen_ten_note_tieng_anh():
    """KHÔNG dịch note sang tiếng Việt.

    Đã đo: ba cách viết (thuần Anh / dịch sẵn / trộn) đều cho 5/5, nghĩa là model
    đa ngôn ngữ tự bắc cầu `woody` <-> `mùi gỗ`. Nên không cần từ điển — và một
    tầng dịch không cần thiết là một tầng có thể dịch sai.
    """
    cau = document(_row(accords={"woody": 90.0}, notes=["Agarwood (Oud)"]))
    assert "woody" in cau, f"accord bị dịch hoặc bị bỏ: {cau}"
    assert "Agarwood (Oud)" in cau, f"note không còn nguyên văn: {cau}"


def test_tai_lieu_co_khung_tieng_viet_con_dau():
    """Giữ dấu là quyết định đã đo: bỏ dấu cả hai phía làm 5/5 tụt xuống 2/5."""
    cau = document(_row(seasons={"winter": 90.0}))
    assert "Mùi chủ đạo" in cau, cau
    assert "mùa đông" in cau, cau


def test_tai_lieu_bo_hoan_canh_diem_thap():
    """Mọi chai đều có điểm cho mọi mùa. Không có ngưỡng thì câu nào cũng 'phù hợp
    cả bốn mùa' và trục hoàn cảnh mất sạch tác dụng phân biệt."""
    cau = document(_row(seasons={"winter": 95.0, "summer": 5.0}))
    assert "mùa đông" in cau
    assert "mùa hè" not in cau, f"hoàn cảnh 5 điểm vẫn được viết vào: {cau}"


def test_tai_lieu_cat_bot_duoi_dai_cua_note():
    """Đuôi dài của note là thứ chỉ vài người bình chọn; thêm hết vào thì mọi chai
    đều 'hơi giống' nhau vì đều có musk."""
    nhieu = [f"Note{i}" for i in range(40)]
    cau = document(_row(notes=nhieu))
    assert "Note0" in cau
    assert "Note39" not in cau, "không cắt bớt note"


def test_chai_khong_co_mui_thi_khong_co_tai_lieu():
    """Chai không có accord lẫn note thì embedding của nó chỉ là tên hãng — đưa
    vào kho chỉ làm nhiễu."""
    assert document(_row(accords={}, notes=[])) == ""


def test_documents_khoa_theo_perfume_key():
    """Khoá phải là `perfume_key` chuẩn hoá, giống hệt cổng Retriever — nếu không
    thì không nối được vector với kết quả tìm kiếm."""
    ra = documents([_row(url="https://F.com/perfume/A/x-1.html/"),
                    _row(name="B", accords={}, notes=[])])
    assert list(ra) == ["https://f.com/perfume/a/x-1.html"], list(ra)


# ------------------------------------------------------------------ lưu trữ
def test_ghi_roi_doc_lai_giu_nguyen_model_id():
    if not _co_duckdb():
        return
    with tempfile.TemporaryDirectory() as td:
        bo = EmbeddingSet(model_id="thu-nghiem-8", dimensions=3,
                          items=(Embedding("k1", (0.1, 0.2, 0.3)),),
                          documents={"k1": "câu thử"})
        store.write(bo, Path(td))
        lai = store.read(Path(td))
        assert lai.model_id == "thu-nghiem-8"
        assert lai.dimensions == 3
        assert lai.items[0].key == "k1"
        assert [round(x, 4) for x in lai.items[0].vector] == [0.1, 0.2, 0.3]
        assert lai.documents["k1"] == "câu thử"


def test_doc_sai_model_thi_bao_loi():
    """Trộn vector của hai model là lỗi KHÔNG BAO GIỜ tự lộ ra: hai mảng số vẫn
    cộng trừ được với nhau, chỉ là kết quả vô nghĩa. Nên phải chặn lúc nạp."""
    if not _co_duckdb():
        return
    with tempfile.TemporaryDirectory() as td:
        store.write(EmbeddingSet(model_id="model-a", dimensions=2,
                                 items=(Embedding("k", (1.0, 0.0)),)), Path(td))
        try:
            store.read(Path(td), expect_model="model-b")
        except ModelMismatch:
            return
        raise AssertionError("nạp vector của model khác mà không báo lỗi")


def test_build_sinh_dung_so_vector():
    e = HashingEmbedder()
    bo = store.build(e, [_row(name="A"), _row(name="B"),
                         _row(name="C", accords={}, notes=[])])
    assert len(bo) == 2, "chai không có mùi vẫn được sinh vector"
    assert bo.model_id == e.model_id
    assert bo.dimensions == e.dimensions
    assert all(len(i.vector) == e.dimensions for i in bo.items)


def _co_duckdb() -> bool:
    try:
        import duckdb  # noqa: F401
    except ImportError:
        return not _skip("cần duckdb")
    return True


# -------------------------------------------- CHẤT LƯỢNG trên dữ liệu thật
# Câu hỏi tiếng Việt + từ khoá PHẢI có trong câu mô tả của chai được trả về.
# Không ghim tên chai cụ thể: kho dữ liệu lớn dần theo mỗi lượt crawl, ghim tên
# là test sẽ đỏ vì DỮ LIỆU đi tiếp, không phải vì code sai.
TRUY_VAN_THAT = [
    ("nước hoa mùi gỗ trầm hương", ("oud", "agarwood", "woody", "wood")),
    ("mùi cam chanh tươi mát cho mùa hè", ("citrus", "lemon", "fresh",
                                           "bergamot", "orange")),
    ("mùi hoa hồng nhẹ nhàng", ("rose", "floral", "jasmine", "flower")),
    ("mùi vani ngọt ấm cho buổi tối", ("vanilla", "sweet", "warm", "tonka")),
]


def test_chat_luong_tren_du_lieu_that():
    """Model có thật sự hiểu tiếng Việt về nước hoa không — đo trên 700+ chai.

    Phép đo lúc chọn model chỉ có 4 tài liệu; 5/5 ở quy mô đó là bằng chứng YẾU.
    Bài này là bản thay thế có nghĩa: với mỗi câu hỏi tiếng Việt, chai xếp đầu
    phải chứa ít nhất một từ khoá liên quan trong câu mô tả của nó.

    Bỏ qua (không báo đỏ) khi thiếu model hoặc thiếu dữ liệu — `make test` phải
    xanh được trên máy không chạy nổi onnxruntime.
    """
    from perfume_intel.embedding.onnx import OnnxEmbedder
    from perfume_intel.embedding.ports import EmbeddingUnavailable
    from perfume_intel.analytics import dataset

    goc = config.raw_dir("fragrantica")
    if not goc.exists() and _skip("test_chat_luong: chưa có dữ liệu thật"):
        return
    try:
        e = OnnxEmbedder()
        e.dimensions
    except EmbeddingUnavailable as exc:
        if _skip(f"test_chat_luong: {str(exc)[:60]}"):
            return

    rows = dataset.build(goc)
    tai_lieu = documents(rows)
    if len(tai_lieu) < 50 and _skip(
            f"test_chat_luong: chỉ có {len(tai_lieu)} chai, quá ít để đo"):
        return

    khoa = sorted(tai_lieu)
    V = e.embed_documents([tai_lieu[k] for k in khoa])

    def cosin(a, b):
        tren = sum(x * y for x, y in zip(a, b))
        duoi = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
        return tren / duoi if duoi else 0.0

    hong = []
    for cau, tu_khoa in TRUY_VAN_THAT:
        q = e.embed_query(cau)
        diem = sorted(((cosin(q, v), k) for v, k in zip(V, khoa)), reverse=True)
        dau = tai_lieu[diem[0][1]].lower()
        if not any(t in dau for t in tu_khoa):
            hong.append(f"{cau!r} -> {diem[0][1]} ({diem[0][0]:.3f})\n"
                        f"        {tai_lieu[diem[0][1]][:100]}")
    assert not hong, (
        f"{len(hong)}/{len(TRUY_VAN_THAT)} câu hỏi ra chai không liên quan:\n  "
        + "\n  ".join(hong))


if __name__ == "__main__":
    raise SystemExit(run(globals()))
