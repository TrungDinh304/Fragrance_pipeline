"""Hợp đồng của `Retriever` — ĐỊNH NGHĨA THẬT của tầng truy xuất.

Phần chữ trong `ports.py` chỉ là giải thích; file này mới là thứ ràng buộc.

CÁCH DÙNG KHI THÊM MỘT BACKEND MỚI
Viết `DuckDBRetriever` / `PgVectorRetriever`, rồi thêm vào `tests/test_retrieval.py`:

    def test_hop_dong_duckdb():
        contract.check_all(lambda rows: DuckDBRetriever.from_rows(rows))

Qua hết là backend đó thay thế được cho bản cũ, và KHÔNG ai phía trên phải sửa.
Không qua là nó chưa thay thế được — dù chạy đúng tới đâu trong các thử nghiệm
riêng lẻ.

`make` là một hàm nhận `list[Row]` và trả về một `Retriever` đã sẵn sàng. Backend
nào cần nạp qua file (Parquet, bảng SQL) thì tự lo trong hàm đó.
"""

from __future__ import annotations

from typing import Callable

from perfume_intel.analytics.dataset import Row
from perfume_intel.retrieval import ports
from perfume_intel.retrieval.ports import (Query, Retriever, UnknownBrand,
                                           UnknownPerfume)

Make = Callable[[list[Row]], Retriever]


# ------------------------------------------------------------------ dữ liệu
def rows() -> list[Row]:
    """Tập nhỏ nhưng đủ phân biệt: hai nhóm mùi, hai hãng, một chai ít vote."""
    def row(key, name, brand, accords, notes, seasons=None, gender="Nam",
            votes=1000, rating=4.0) -> Row:
        return Row(
            url=f"https://f.com/perfume/{brand}/{key}.html",
            name=name, brand=brand, gender=gender, rating=rating,
            rating_count=votes,
            accords=dict(accords),
            notes=list(notes),
            notes_by_layer={"base": list(notes)},
            seasons=dict(seasons or {}),
            day_night={},
        )

    return [
        # nhóm ấm
        row("a1", "Oud Thẳng", "AlphaHouse", {"woody": 100.0, "sweet": 60.0},
            ["Agarwood (Oud)", "Vanilla", "Musk"], {"winter": 90.0}),
        row("a2", "Oud Dịu", "AlphaHouse", {"woody": 90.0, "sweet": 70.0},
            ["Agarwood (Oud)", "Vanilla", "Musk"], {"winter": 80.0}),
        row("a3", "Oud Ít Ai Biết", "AlphaHouse", {"woody": 85.0},
            ["Agarwood (Oud)", "Musk"], {"winter": 70.0}, votes=3),
        # nhóm tươi
        row("b1", "Chanh Biển", "BetaWorks", {"citrus": 100.0, "fresh": 80.0},
            ["Lemon", "Neroli", "Musk"], {"summer": 95.0}, gender="Nữ"),
        row("b2", "Chanh Nhẹ", "BetaWorks", {"citrus": 95.0, "fresh": 70.0},
            ["Lemon", "Neroli", "Musk"], {"summer": 90.0}, gender="Nữ"),
    ]


def _keys(result) -> list[str]:
    return [m.perfume_key for m in result.matches]


# ------------------------------------------------------------- các điều khoản
def check_tra_ve_khoa_co_nghia(make: Make) -> None:
    """Khoá phải là thứ có nghĩa ở MỌI backend, không phải vị trí trong list."""
    r = make(rows())
    res = r.search(Query(notes=("oud",), limit=5))
    assert res.matches, "không tìm được gì với note có thật"
    for m in res.matches:
        assert m.perfume_key, "khoá rỗng"
        assert not m.perfume_key.isdigit(), \
            f"khoá là số thứ tự ({m.perfume_key!r}) — vô nghĩa ngoài bộ nhớ"
        assert m.perfume_key.startswith("http"), \
            f"khoá không phải URL chuẩn hoá: {m.perfume_key!r}"


def check_khoa_on_dinh_giua_hai_lan_hoi(make: Make) -> None:
    r = make(rows())
    a = _keys(r.search(Query(notes=("oud",), limit=3)))
    b = _keys(r.search(Query(notes=("oud",), limit=3)))
    assert a == b, "cùng câu hỏi ra khoá khác nhau"


def check_cau_hoi_rong_khong_no(make: Make) -> None:
    """Câu hỏi rỗng là chuyện bình thường (người dùng chưa gõ gì), không phải lỗi."""
    res = make(rows()).search(Query())
    assert res.matches == ()


def check_bao_lai_term_khong_co(make: Make) -> None:
    """Im lặng bỏ một từ gõ sai = trả lời câu khác mà người hỏi không biết."""
    res = make(rows()).search(Query(notes=("khongtontai",), limit=5))
    assert "khongtontai" in res.unknown
    assert res.matches == ()


def check_bao_lai_ten_da_duoc_khop(make: Make) -> None:
    """Gõ "oud" mà hệ thống tìm "agarwood (oud)" thì phải nói ra."""
    res = make(rows()).search(Query(notes=("oud",), limit=3))
    assert res.resolved, "khớp một phần mà không báo lại"
    assert any("agarwood" in v for v in res.resolved.values()), res.resolved


def check_tim_bang_cau_tu_do(make: Make) -> None:
    """Hỏi bằng một câu tiếng Việt, không gõ đúng tên note.

    Đây là cách chatbot hỏi. Mỗi backend làm theo cách riêng (khớp từ khoá, hay
    vector ngữ nghĩa) và chất lượng khác nhau — hợp đồng chỉ đòi: có trả về kết
    quả, và nói ra đã hiểu câu đó thành gì.
    """
    r = make(rows())
    res = r.search(Query(text="nước hoa mùi oud gỗ ấm", limit=3))
    assert res.matches, "hỏi bằng câu tự do mà không ra gì"
    assert res.resolved or res.unknown, \
        "không báo lại đã hiểu câu tự do thành gì — người hỏi không biết hệ " \
        "thống đã trả lời câu nào"


def check_cau_tu_do_khong_khop_gi_thi_bao_lai(make: Make) -> None:
    """Câu không liên quan gì tới nước hoa phải được báo, không im lặng trả bừa."""
    res = make(rows()).search(Query(text="xyzzy qwerty khongcotu", limit=3))
    assert res.unknown or not res.matches, \
        "câu không khớp gì mà vẫn trả về kết quả và không báo gì"


def check_cau_tu_do_va_hoan_canh_dung_ca_hai(make: Make) -> None:
    """Có CẢ câu tự do lẫn hoàn cảnh thì phải dùng cả hai, không bỏ một bên.

    Hai adapter đã lệch nhau đúng chỗ này hai lần: điều kiện định tuyến viết là
    "có hoàn cảnh -> nhánh theo term", nên chỉ cần khách nói một chữ về mùa là
    phần mùi trong câu bị bỏ sạch. Không gây lỗi, không làm đỏ test nào — chỉ làm
    câu trả lời nói về mùa thay vì nói về mùi.

    Kiểm bằng LÝ DO: nếu phần mùi được dùng thì lý do phải có ít nhất một khối mùi
    (accord hoặc note), không chỉ toàn `occasion`.
    """
    r = make(rows())
    res = r.search(Query(text="nước hoa mùi oud gỗ ấm cho mùa đông",
                         occasions=("winter",), limit=3, explain=True))
    assert res.matches, "có cả câu tự do lẫn hoàn cảnh mà không ra gì"
    khoi = {w.block for m in res.matches for w in m.why}
    assert khoi & {ports.ACCORD, ports.NOTE}, (
        f"lý do chỉ có {khoi or 'rỗng'} — phần mùi trong câu đã bị bỏ, "
        f"chỉ còn hoàn cảnh")


def check_hoi_theo_hoan_canh_van_co_ly_do_ve_mui(make: Make) -> None:
    """Hỏi theo hoàn cảnh THUẦN cũng phải nói được chai đó mùi gì.

    Lý do chỉ có `occasion` là đúng nhưng vô dụng: "hợp mùa lạnh, hợp buổi tối"
    không giúp người bán nói được câu nào về mùi.
    """
    res = make(rows()).search(Query(occasions=("winter",), limit=3,
                                    explain=True))
    assert res.matches, "hỏi theo hoàn cảnh mà không ra gì"
    khoi = {w.block for m in res.matches for w in m.why}
    assert khoi & {ports.ACCORD, ports.NOTE},         f"lý do chỉ có {khoi or 'rỗng'} — không nói chai đó mùi gì"


def check_hoan_canh_khai_tuong_minh_thang_chu_trong_cau(make: Make) -> None:
    """`occasions` khai tường minh phải GHI ĐÈ mấy trục đọc ra được từ `text`.

    Trong hội thoại, `text` cộng dồn qua các lượt. Khách hỏi mùa đông rồi sau đó
    nói "mùa hè thì sao": ý định đã đổi sang summer, nhưng câu cộng dồn vẫn còn
    chữ "mùa đông". Trộn cả hai thì kết quả vừa hợp mùa nóng vừa hợp mùa lạnh —
    tức là lượt đó không đổi gì cả, dù khách vừa nói rõ muốn đổi.
    """
    r = make(rows())
    res = r.search(Query(occasions=("summer",),
                         text="mùa đông lạnh ... mùa hè thì sao",
                         limit=5, explain=True))
    assert res.matches, "không ra gì"
    nhan = {w.label for m in res.matches for w in m.why
            if w.block == ports.OCCASION}
    assert "winter" not in nhan, (
        f"'winter' đọc ra từ câu vẫn được dùng dù đã khai occasions=summer: "
        f"{nhan}")


def check_chai_goc_cung_co_ly_do(make: Make) -> None:
    """Chai gốc (`seed`) cũng phải có lý do về mùi của chính nó.

    Thiếu thì câu trả lời về đúng chai đó phải nói "chưa có đủ dữ liệu về mùi"
    trong khi dữ liệu có đủ — đã thấy thật khi thử hội thoại.
    """
    res = make(rows()).search(Query(like_perfume="Oud Thẳng", limit=2,
                                    explain=True))
    assert res.seed is not None
    assert res.seed.why, "chai gốc không có lý do nào"


def check_cau_tu_do_van_ton_trong_bo_loc(make: Make) -> None:
    """Nhánh câu tự do là nhánh THỨ BA, rất dễ quên áp bộ lọc — y như nhánh
    `like_perfume` từng quên lọc giới tính."""
    res = make(rows()).search(Query(text="musk oud", gender="Nữ", limit=9))
    assert all(m.gender == "Nữ" for m in res.matches), \
        f"lọt chai khác giới tính: {[(m.name, m.gender) for m in res.matches]}"
    res = make(rows()).search(Query(text="musk oud", min_votes=100, limit=9))
    assert all((m.rating_count or 0) >= 100 for m in res.matches), \
        "lọt chai dưới ngưỡng vote ở nhánh câu tự do"


def check_hoan_canh_sai_bi_bao(make: Make) -> None:
    res = make(rows()).search(Query(occasions=("thu-ba",), limit=3))
    assert "thu-ba" in res.unknown


def check_tim_theo_chai_goc(make: Make) -> None:
    r = make(rows())
    res = r.search(Query(like_perfume="Oud Thẳng", limit=5))
    assert res.seed is not None and res.seed.name == "Oud Thẳng"
    assert res.seed.perfume_key not in _keys(res), "tự trả về chính nó"
    assert res.matches, "không tìm được chai nào giống"


def check_chai_goc_khong_co_thi_bao_loi(make: Make) -> None:
    try:
        make(rows()).search(Query(like_perfume="Khong Ton Tai Dau"))
    except UnknownPerfume:
        return
    raise AssertionError("chai gốc không có mà vẫn trả kết quả")


def check_loai_cung_hang(make: Make) -> None:
    r = make(rows())
    res = r.search(Query(like_perfume="Oud Thẳng", include_same_brand=False,
                         limit=5))
    assert res.matches, "loại cùng hãng xong không còn gì (fixture hỏng)"
    for m in res.matches:
        assert m.brand != "AlphaHouse", f"vẫn còn chai cùng hãng: {m.name}"


def check_loc_theo_gioi_tinh(make: Make) -> None:
    res = make(rows()).search(Query(notes=("musk",), gender="Nữ", limit=9))
    assert res.matches
    assert all(m.gender == "Nữ" for m in res.matches)


def check_loc_theo_gioi_tinh_ca_khi_hoi_bang_chai_goc(make: Make) -> None:
    """Bộ lọc phải áp dụng cho MỌI nhánh hỏi, không riêng nhánh theo note.

    Hai nhánh (`like_perfume` và theo term) rất dễ cài rời nhau rồi quên một
    bên — và quên kiểu đó không gây lỗi, chỉ lặng lẽ trả thêm thứ người hỏi đã
    bảo là không muốn.
    """
    r = make(rows())
    res = r.search(Query(like_perfume="Chanh Biển", gender="Nam", limit=9))
    assert all(m.gender == "Nam" for m in res.matches),         f"lọt chai khác giới tính: {[(m.name, m.gender) for m in res.matches]}"


def check_loc_theo_so_vote_ca_khi_hoi_bang_chai_goc(make: Make) -> None:
    r = make(rows())
    res = r.search(Query(like_perfume="Oud Thẳng", min_votes=100, limit=9))
    assert all((m.rating_count or 0) >= 100 for m in res.matches),         "lọt chai dưới ngưỡng vote khi hỏi bằng chai gốc"


def check_loc_theo_so_vote(make: Make) -> None:
    res = make(rows()).search(Query(notes=("oud",), min_votes=100, limit=9))
    assert res.matches
    assert all((m.rating_count or 0) >= 100 for m in res.matches), \
        "lọt chai dưới ngưỡng vote"


def check_ton_trong_limit(make: Make) -> None:
    res = make(rows()).search(Query(notes=("musk",), limit=2))
    assert len(res.matches) <= 2


def check_diem_giam_dan(make: Make) -> None:
    res = make(rows()).search(Query(notes=("musk",), limit=9))
    scores = [m.score for m in res.matches]
    assert scores == sorted(scores, reverse=True), f"không sắp xếp: {scores}"


def check_khong_bat_explain_thi_khong_co_ly_do(make: Make) -> None:
    res = make(rows()).search(Query(notes=("oud",), limit=3, explain=False))
    assert all(m.why == () for m in res.matches)


def check_ly_do_dung_ten_khoi_nghiep_vu(make: Make) -> None:
    """Lý do phải nói "note"/"accord", KHÔNG phải tiền tố kỹ thuật bên trong.

    Rò tiền tố ra đây nghĩa là ranh giới đã thủng: phía trên sẽ bắt đầu phụ
    thuộc vào cách mã hoá của một backend cụ thể.
    """
    res = make(rows()).search(Query(notes=("oud",), limit=3, explain=True))
    reasons = [w for m in res.matches for w in m.why]
    assert reasons, "bật explain mà không có lý do nào"
    for w in reasons:
        assert w.block in ports.BLOCKS, \
            f"khối lạ {w.block!r} — chỉ được dùng {ports.BLOCKS}"
        assert ":" not in w.label, f"nhãn còn dính tiền tố: {w.label!r}"


def check_hang_giong_hang(make: Make) -> None:
    r = make(rows())
    out = r.similar_brands("AlphaHouse", min_perfumes=1, limit=5)
    assert out.target is not None and out.target.brand == "AlphaHouse"
    assert all(m.brand != "AlphaHouse" for m in out.matches)


def check_hang_khong_co_thi_bao_loi(make: Make) -> None:
    try:
        make(rows()).similar_brands("Hang Khong Ton Tai")
    except UnknownBrand:
        return
    raise AssertionError("hãng không có mà vẫn trả kết quả")


def check_tu_vung(make: Make) -> None:
    r = make(rows())
    notes = list(r.vocabulary(ports.NOTE))
    assert notes and notes == sorted(notes), "từ vựng không sắp xếp"
    assert all(":" not in n for n in notes), "từ vựng còn dính tiền tố"
    assert list(r.vocabulary(ports.OCCASION)) == list(ports.OCCASIONS)


def check_tu_vung_khoi_la_thi_bao_loi(make: Make) -> None:
    try:
        make(rows()).vocabulary("khong-co-khoi-nay")
    except ValueError:
        return
    raise AssertionError("khối lạ mà vẫn trả về từ vựng")


def check_dem_duoc_so_chai(make: Make) -> None:
    assert len(make(rows())) == len(rows())


def check_la_mot_retriever(make: Make) -> None:
    assert isinstance(make(rows()), Retriever)


CHECKS = [v for k, v in sorted(globals().items()) if k.startswith("check_")]


def check_all(make: Make) -> None:
    """Chạy toàn bộ hợp đồng. Hỏng điều khoản nào thì báo rõ điều khoản đó."""
    for fn in CHECKS:
        try:
            fn(make)
        except AssertionError as exc:
            raise AssertionError(f"[{fn.__name__}] {exc}") from None
