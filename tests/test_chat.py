"""Hội thoại nhiều lượt: phiên, phân loại lượt, và việc giải "chai thứ 2".

KHÔNG cần model, không cần FastAPI, không cần Postgres. Toàn bộ phần khó của hội
thoại là Python thuần, nên nó phải được kiểm ở mọi lần `make test`.

Thứ đáng kiểm nhất ở đây KHÔNG phải câu văn, mà là ba chỗ dễ sai và sai âm thầm:
  1. phân loại lượt (mới / lọc thêm / về một chai) — sai là mỗi lượt thành một câu
     hỏi mới, và "nhẹ hơn chút" đi tìm note tên "nhẹ";
  2. gộp điều kiện — gộp sai là khách thêm một ý mà mất ý cũ;
  3. giải số thứ tự — đoán bừa ở đây cho ra một chai CÓ THẬT, nên không ai phát
     hiện được là nó trả lời về chai khác.
"""

from __future__ import annotations

from runner import run  # noqa: E402

from perfume_intel.chat.session import (MAX_LUOT, Conversation,  # noqa: E402
                                        SessionStore, Turn)
from perfume_intel.llm import turn as tn  # noqa: E402
from perfume_intel.retrieval.ports import Query  # noqa: E402


def _hoi(*luot) -> Conversation:
    h = Conversation(id="thu")
    for t in luot:
        h.add(t)
    return h


def _sau_mot_luot(q: Query | None = None, shown=(("k1", "Al Qiam Gold"),
                                                 ("k2", "Al Maqaam"),
                                                 ("k3", "Hypnotic Poison"))):
    q = q or Query(occasions=("winter",), text="mùi gỗ ấm cho mùa đông",
                   explain=True)
    return _hoi(Turn("user", "mùi gỗ ấm cho mùa đông"),
                Turn("assistant", "...", shown=shown, query=q))


# --------------------------------------------------------------- phân loại
def test_luot_dau_luon_la_moi():
    """Chưa có gì phía trước thì không thể là lọc thêm — kể cả khi câu có chữ
    "hơn". Nếu không, lượt đầu sẽ đi gộp với một Query không tồn tại."""
    y = tn.fallback_turn("tìm chai nào nhẹ hơn", None)
    assert y.action == tn.MOI
    y2 = tn.fallback_turn("tìm chai nào nhẹ hơn", Conversation(id="x"))
    assert y2.action == tn.MOI


def test_cau_so_sanh_la_loc_them():
    for cau in ["nhẹ hơn chút", "ngọt hơn được không", "của hãng khác đi",
                "còn gì nữa không", "mùa hè thì sao", "đổi sang cho nữ"]:
        y = tn.fallback_turn(cau, _sau_mot_luot())
        assert y.action == tn.LOC_THEM, f"{cau!r} -> {y.action}"


def test_cau_hoi_mot_chai_la_ve_chai():
    h = _sau_mot_luot()
    for cau, mong in [("chai thứ 2 thì sao", "k2"), ("chai số 3", "k3"),
                      ("cái đầu tiên", "k1"), ("chai đó có gì", "k1"),
                      ("chai vừa rồi", "k1")]:
        y = tn.fallback_turn(cau, h)
        assert y.action == tn.VE_CHAI, f"{cau!r} -> {y.action}"
        assert y.target == mong, f"{cau!r} -> {y.target}, mong {mong}"
        assert y.query.like_perfume == mong


def test_cau_hoi_chu_de_khac_la_moi():
    y = tn.fallback_turn("tìm nước hoa cho mùa hè đi biển", _sau_mot_luot())
    assert y.action == tn.MOI
    assert "summer" in y.query.occasions


def test_khong_co_danh_sach_thi_khong_the_ve_chai():
    """Hỏi "chai thứ 2" khi lượt trước chẳng hiện gì thì không có gì để trỏ vào.

    Phải hạ xuống loại khác, KHÔNG được bịa một khoá.
    """
    h = _sau_mot_luot(shown=())
    y = tn.fallback_turn("chai thứ 2 thì sao", h)
    assert y.action != tn.VE_CHAI
    assert y.target is None


def test_so_thu_tu_ngoai_pham_vi_khong_tro_bua():
    """Hỏi chai thứ 9 khi chỉ hiện 3 chai -> KHÔNG được trỏ vào chai nào.

    Bản test đầu của bài này xanh vì lý do sai: nó chỉ đòi `target` nằm trong ba
    khoá đã hiện, nên phép `(so-1) % len(shown)` vẫn qua — mà đó đúng là cách sai
    cần chặn. Mutation test bắt được chỗ đó.

    Lấy modulo hay kẹp về cuối danh sách đều cho ra một chai CÓ THẬT, nên không ai
    phát hiện được; chỉ thấy câu trả lời nói về chai khác thứ mình hỏi.
    """
    h = _sau_mot_luot()
    y = tn.fallback_turn("chai thứ 9 thì sao", h)
    assert y.action != tn.VE_CHAI, f"trỏ vào {y.target} cho 'chai thứ 9'"
    assert y.target is None


def test_chai_cuoi_tro_vao_chai_cuoi():
    """"chai cuối" và "chai đầu" là hai vị trí khác nhau.

    Trả về chai đầu cho cả hai không gây lỗi — nó chỉ trả lời về chai khác.
    """
    h = _sau_mot_luot()
    assert tn.fallback_turn("chai cuối thì sao", h).target == "k3"
    assert tn.fallback_turn("chai đầu thì sao", h).target == "k1"


def test_chua_co_luot_tim_kiem_nao_thi_khong_ve_chai():
    """Có danh sách đã hiện nhưng chưa có truy vấn tìm kiếm nào thì không có mạch
    nào để nói tiếp — `ve_chai` lúc đó là trỏ vào hư không."""
    h = _hoi(Turn("assistant", "...", shown=(("k1", "A"), ("k2", "B")),
                  query=None))
    y = tn.fallback_turn("chai thứ 2 thì sao", h)
    assert y.action != tn.VE_CHAI, f"-> {y.action}/{y.target}"


def test_luot_ve_chai_khong_lam_mat_mach_tim_kiem():
    """Hỏi về một chai là NHÁNH RẼ. Lượt sau phải quay lại truy vấn tìm kiếm cũ.

    Thiếu luật này thì "còn gì nữa không" sau một lượt `ve_chai` sẽ đi gộp với một
    Query `like_perfume` — mà phép gộp không mang `like_perfume` theo, nên ra rỗng
    và bot trả lời "chưa tìm được chai nào". Đã thấy đúng chuỗi đó khi thử 5 lượt.
    """
    tim = Query(occasions=("winter",), text="mùi gỗ ấm mùa đông", explain=True)
    h = _hoi(Turn("user", "mùi gỗ ấm mùa đông"),
             Turn("assistant", "a", shown=(("k1", "A"), ("k2", "B")), query=tim),
             Turn("user", "chai thứ 2 thì sao"),
             Turn("assistant", "b", shown=(("k2", "B"),),
                  query=Query(like_perfume="k2", explain=True)))
    assert h.last_search_query is tim, "lấy nhầm query của lượt hỏi-một-chai"
    y = tn.fallback_turn("còn gì nữa không", h)
    assert y.action == tn.LOC_THEM
    assert y.query.occasions == ("winter",), \
        f"mất ngữ cảnh tìm kiếm sau nhánh rẽ: {y.query}"
    assert not y.query.like_perfume, "mang theo like_perfume vào lượt lọc thêm"


# ------------------------------------------------------------------- gộp
def test_gop_cong_them_note_giu_hoan_canh():
    cu = Query(notes=("oud",), occasions=("winter",), text="gỗ ấm mùa đông")
    moi = Query(notes=("vanilla",), text="thêm vani")
    ra = tn.gop(cu, moi)
    assert set(ra.notes) == {"oud", "vanilla"}, ra.notes
    assert ra.occasions == ("winter",), "mất hoàn cảnh cũ khi khách thêm note"
    assert "gỗ ấm mùa đông" in (ra.text or "") and "vani" in (ra.text or "")


def test_gop_ghi_de_hoan_canh_khi_luot_moi_co_noi():
    """"mùa hè thì sao" là ĐỔI mùa, không phải muốn cả đông lẫn hè."""
    cu = Query(notes=("oud",), occasions=("winter",))
    ra = tn.gop(cu, Query(occasions=("summer",)))
    assert ra.occasions == ("summer",), ra.occasions
    assert ra.notes == ("oud",), "đổi mùa mà mất note đang tìm"


def test_gop_ghi_de_gioi_tinh_khi_co_noi():
    cu = Query(notes=("oud",), gender="Nam")
    assert tn.gop(cu, Query(gender="Nữ")).gender == "Nữ"
    assert tn.gop(cu, Query()).gender == "Nam", "mất giới tính cũ"


def test_gop_cat_text_khong_cho_phinh_mai():
    """Lọc thêm nhiều lượt thì `text` cộng dồn. Không có trần là vector mất hết
    trọng tâm sau chục lượt, và prompt phình theo."""
    q = Query(text="x" * 50)
    for _ in range(20):
        q = tn.gop(q, Query(text="thêm một ý nữa"))
    assert len(q.text or "") <= tn.MAX_TEXT, len(q.text or "")


def test_gop_voi_khong_co_gi_truoc_do():
    moi = Query(notes=("oud",))
    assert tn.gop(None, moi) is moi


# ----------------------------------------------------------------- phiên
def test_phien_moi_khi_id_la():
    s = SessionStore()
    a = s.get(None)
    b = s.get("khong-ton-tai-dau")
    assert a.id != b.id
    assert len(s) == 2


def test_phien_cu_duoc_dung_lai():
    s = SessionStore()
    a = s.get(None)
    a.add(Turn("user", "xin chào"))
    assert len(s.get(a.id).turns) == 1


def test_cat_bot_luot_cu_khi_qua_tran():
    """Người ngồi hỏi cả buổi không được làm prompt dài vô hạn. Cắt từ ĐẦU vì
    lượt mới có giá trị hơn lượt cũ."""
    h = Conversation(id="x")
    for i in range(MAX_LUOT + 10):
        h.add(Turn("user", f"câu {i}"))
    assert len(h.turns) == MAX_LUOT
    assert h.turns[-1].text == f"câu {MAX_LUOT + 9}", "cắt sai đầu"


def test_bo_phien_cu_nhat_khi_qua_tran():
    s = SessionStore(max_phien=3)
    ids = [s.get(None).id for _ in range(5)]
    assert len(s) <= 3
    # Phiên mới nhất phải còn.
    assert s.get(ids[-1]).turns == []


def test_phien_het_han_bi_don():
    s = SessionStore(max_phien=10, ttl=-1)     # mọi phiên đều coi như hết hạn
    cu = s.get(None)
    cu.add(Turn("user", "xin chào"))
    moi = s.get(None)
    assert moi.id != cu.id or not moi.turns


def test_drop_phien_khong_co_khong_phai_loi():
    s = SessionStore()
    assert s.drop("khong-co") is False


# ------------------------------------------------------- lịch sử vào prompt
def test_lich_su_kem_danh_sach_da_hien():
    """Thiếu danh sách đã hiện thì model không biết "chai thứ 2" trỏ vào đâu, và
    nó sẽ đoán — ra một chai trông hợp lý, tức là bịa."""
    h = _sau_mot_luot()
    lich = h.history_text()
    assert "Khách:" in lich and "Bạn:" in lich
    assert "1. Al Qiam Gold" in lich, lich
    assert "2. Al Maqaam" in lich, lich


def test_lich_su_chi_lay_may_luot_gan_nhat():
    h = Conversation(id="x")
    for i in range(30):
        h.add(Turn("user", f"câu {i}"))
    lich = h.history_text(n=4)
    assert "câu 29" in lich and "câu 0" not in lich


def test_last_query_va_last_shown_lay_luot_tro_ly_gan_nhat():
    q1 = Query(notes=("oud",))
    q2 = Query(notes=("rose",))
    h = _hoi(Turn("assistant", "a", shown=(("k1", "A"),), query=q1),
             Turn("user", "tiếp"),
             Turn("assistant", "b", shown=(("k2", "B"),), query=q2))
    assert h.last_query is q2
    assert h.last_shown == (("k2", "B"),)


if __name__ == "__main__":
    raise SystemExit(run(globals()))
