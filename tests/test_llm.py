"""Tầng LLM: tách ý định, diễn đạt, và các lớp chặn bịa.

KHÔNG gọi model thật. Mọi bài ở đây dùng một `ChatModel` giả, nên chúng chạy không
cần khoá và không tốn token. Thứ được kiểm là phần KHÔNG phụ thuộc model: dữ liệu
đưa vào prompt, việc lọc đầu ra, và đường xuống cấp khi mất model.

Chất lượng câu văn thì không kiểm được bằng assert — nó cần người đọc. Nhưng ba
thứ quanh nó thì kiểm được, và đều là chỗ từng sai ở các hệ thống kiểu này:
  1. nhãn kỹ thuật rò vào câu tiếng Việt ("hợp dịp winter");
  2. prompt bị nới lỏng dần cho tới khi model được phép bịa;
  3. mất model là trang chết, thay vì xuống cấp.
"""

from __future__ import annotations

from runner import run  # noqa: E402

from perfume_intel.llm import advise as ad  # noqa: E402
from perfume_intel.llm import intent as it  # noqa: E402
from perfume_intel.llm.ports import (ChatModel, LLMUnavailable,  # noqa: E402
                                     Message, Reply)
from perfume_intel.retrieval.ports import (Match, Reason,  # noqa: E402
                                           SearchResult)


class _ModelGia:
    """Trả về chuỗi đặt trước, và GIỮ LẠI prompt đã nhận để kiểm."""

    def __init__(self, tra_loi: str = "ok") -> None:
        self._tra_loi = tra_loi
        self.da_nhan: list[Message] = []

    @property
    def model_id(self) -> str:
        return "gia"

    def complete(self, messages, max_tokens=512, temperature=0.0) -> Reply:
        self.da_nhan = list(messages)
        return Reply(text=self._tra_loi, model="gia")

    @property
    def prompt(self) -> str:
        return "\n".join(m.content for m in self.da_nhan)

    @property
    def boi_canh(self) -> str:
        """CHỈ phần user — tức là dữ liệu, không gồm system prompt.

        Phải phân biệt hai thứ này: system prompt có chứa chữ hướng dẫn ("ít lượt",
        "chưa chắc"), nên soi `prompt` để kiểm DỮ LIỆU sẽ luôn khớp và bài test
        thành vô dụng. Đã dính đúng bẫy đó khi viết bài này.
        """
        return "\n".join(m.content for m in self.da_nhan if m.role == "user")


class _ModelChet:
    @property
    def model_id(self) -> str:
        return "chet"

    def complete(self, messages, max_tokens=512, temperature=0.0) -> Reply:
        raise LLMUnavailable("hết hạn mức")


def _match(name="Al Qiam Gold", brand="Lattafa Perfumes", rating=4.3,
           votes=1200, why=(("note", "Oud"), ("occasion", "winter"))) -> Match:
    return Match(perfume_key=f"https://f.com/{name}", score=0.45, name=name,
                 brand=brand, url=f"https://f.com/{name}", rating=rating,
                 rating_count=votes, gender="Unisex",
                 why=tuple(Reason(block=b, label=l, weight=0.3)
                           for b, l in why))


def _res(*ms) -> SearchResult:
    return SearchResult(matches=tuple(ms or (_match(),)))


# ------------------------------------------------- nhãn kỹ thuật không được rò
def test_nhan_hoan_canh_duoc_dich_truoc_khi_vao_prompt():
    """Nhãn `occasion` lưu bằng tiếng Anh. Để nguyên thì model viết "hợp dịp
    winter" giữa một câu tiếng Việt — đúng kiểu câu lộ ngay là máy sinh."""
    m = _ModelGia()
    ad.advise("mùa đông", _res(_match(why=(("occasion", "winter"),))), m)
    assert "trời lạnh" in m.prompt or "mùa đông" in m.prompt, m.prompt[-300:]
    assert "dịp winter" not in m.prompt, "nhãn tiếng Anh rò vào prompt"


def test_nhan_do_manh_duoc_dich_thanh_nghia():
    """Khối `strength` có nhãn là TÊN CHIỀU (`longevity`), mức độ nằm ở trọng số.
    Nên "độ longevity" không nói gì với người đọc."""
    m = _ModelGia()
    ad.advise("lâu", _res(_match(why=(("strength", "longevity"),))), m)
    assert "lưu hương lâu" in m.prompt, m.prompt[-300:]
    assert "longevity" not in m.prompt, "tên chiều kỹ thuật rò vào prompt"


def test_moi_nhan_hoan_canh_deu_co_ban_dich():
    """Thiếu một nhãn là nó rò ra nguyên văn tiếng Anh, và không test nào khác đỏ."""
    from perfume_intel.retrieval import ports
    thieu = [a for a in ports.OCCASIONS if a not in ad.VI_NHAN]
    assert not thieu, f"thiếu bản dịch cho: {thieu}"


# ---------------------------------------------------- prompt không được nới ra
def test_prompt_cam_du_nhung_thu_khong_co_trong_du_lieu():
    """Canh chính phần cấm, vì giọng bán hàng là giọng DỄ BỊA NHẤT.

    Bản năng của giọng đó là nói "bán chạy nhất", "giá tốt", "còn hàng" — không
    thứ nào có trong dữ liệu. Nếu ai đó sửa prompt cho "tự nhiên hơn" rồi bỏ mấy
    dòng cấm này, sẽ không có test nào khác đỏ, và cái sai đi thẳng tới khách.
    """
    p = ad.HE_THONG.lower()
    phai_cam = ["giá", "còn hàng", "bán chạy", "năm ra mắt", "nhà pha chế",
                "da", "so sánh"]
    thieu = [t for t in phai_cam if t not in p]
    assert not thieu, f"prompt không còn cấm: {thieu}"


def test_prompt_buoc_chi_dung_danh_sach():
    p = ad.HE_THONG.lower()
    assert "chỉ được nói về" in p or "toàn bộ những gì bạn biết" in p
    assert "không nhắc tên chai nào khác" in p


def test_prompt_khong_cho_doc_lai_diem_so():
    """Điểm 0.452 là chi tiết cài đặt. Khách đọc nó không hiểu gì, mà nó còn làm
    câu trả lời nghe như log."""
    assert "0.452" in ad.HE_THONG or "0.45" in ad.HE_THONG


# --------------------------------------------------------- lớp soát tên bịa
def test_verify_bat_ten_chai_khong_co_trong_ket_qua():
    """Lớp chặn tự động DUY NHẤT. Tên bịa trông y hệt tên thật."""
    res = _res(_match(name="Al Qiam Gold", brand="Lattafa Perfumes"))
    nghi = ad.verify("Mình nghĩ Al Qiam Gold rất hợp, hoặc Creed Aventus "
                     "cũng đáng thử.", res)
    assert any("Aventus" in n for n in nghi), nghi


def test_verify_khong_bao_nham_ten_co_that():
    """Báo nhầm quá nhiều thì người ta sẽ tắt luôn cảnh báo — lúc đó lớp này
    thành vô dụng. Nên tên có thật phải đi qua sạch."""
    res = _res(_match(name="Al Qiam Gold", brand="Lattafa Perfumes"))
    nghi = ad.verify("Al Qiam Gold của Lattafa Perfumes là sát ý bạn nhất.", res)
    assert not nghi, f"báo nhầm tên có thật: {nghi}"


def test_advise_bao_lai_ten_dang_nghi():
    m = _ModelGia("Thử Chanel No 5 xem sao.")
    _chu, nghi = ad.advise("gì cũng được", _res(_match(name="Al Qiam Gold")), m)
    assert nghi, "model nhắc chai lạ mà không báo"


# ------------------------------------------------------------ xuống cấp
def test_mat_model_thi_roi_ve_ban_ghep_san():
    """Mất model không được làm trang chết — nó chỉ mất phần diễn đạt."""
    chu, nghi = ad.advise("mùi gỗ", _res(_match()), _ModelChet())
    assert chu and "Al Qiam Gold" in chu
    assert nghi == []


def test_summary_khong_phai_thong_bao_ky_thuat():
    """Với người chưa cấu hình khoá, ĐÂY là chatbot. Nên nó phải ra câu tư vấn,
    không phải một dòng trạng thái."""
    chu = ad.summary(_res(_match()))
    assert "Al Qiam Gold" in chu
    assert chu.rstrip().endswith("?"), f"không có câu hỏi cuối: {chu}"
    for xau in ("Lý do:", "score", "0.45", "winter", "longevity"):
        assert xau not in chu, f"rò chi tiết kỹ thuật {xau!r}: {chu}"


def test_summary_noi_that_khi_it_luot_danh_gia():
    """Điểm 4.8/5 với 3 lượt đánh giá không phải điểm 4.8/5. Im lặng ở đây là để
    người đọc tin vào một con số chưa vững."""
    chu = ad.summary(_res(_match(rating=4.8, votes=3)))
    assert "chưa chắc" in chu, chu
    nhieu = ad.summary(_res(_match(rating=4.3, votes=4770)))
    assert "4.770" in nhieu and "chưa chắc" not in nhieu, nhieu


def test_summary_rong_thi_hoi_lai_cho_ro():
    chu = ad.summary(SearchResult())
    assert "?" in chu
    assert "oud" in chu.lower() or "note" in chu.lower()


# -------------------------------------------------------------- tách ý định
def test_intent_bo_nhan_model_bia_ra():
    """Chốt chặn chính của `intent.py`: model đề xuất, TỪ VỰNG quyết định."""
    class _Kho:
        def vocabulary(self, block):
            return ["oud", "vanilla"] if block == "note" else ["woody"]

    m = _ModelGia('{"notes":["oud","hương thanh xuân"],"accords":[],'
                  '"occasions":["winter"],"gender":null,"text":"abc"}')
    q = it.extract("abc", m, _Kho())
    assert "oud" in q.notes
    assert not any("thanh xuân" in n for n in q.notes), q.notes


def test_intent_chi_nhan_sau_truc_hoan_canh_that():
    class _Kho:
        def vocabulary(self, block):
            return []

    m = _ModelGia('{"notes":[],"accords":[],'
                  '"occasions":["winter","cong so","hen ho"],'
                  '"gender":null,"text":"abc"}')
    q = it.extract("abc", m, _Kho())
    assert q.occasions == ("winter",), q.occasions


def test_intent_model_hong_thi_roi_ve_tu_khoa():
    class _Kho:
        def vocabulary(self, block):
            return []

    q = it.extract("tìm nước hoa cho mùa đông buổi tối", _ModelChet(), _Kho())
    assert set(q.occasions) == {"winter", "night"}, q.occasions


def test_boi_canh_canh_bao_chai_it_luot_danh_gia():
    """Cờ "[ít lượt, điểm chưa chắc]" phải tới được MODEL, không chỉ tới `summary`.

    Mutation test bắt được chỗ hở này: bỏ cờ khỏi bối cảnh thì mọi test vẫn xanh,
    vì bài kiểm cũ chỉ soi `summary()` — đường chạy KHI KHÔNG có model. Lúc có
    khoá LLM thì model nhận một chai 2.50/5 với 6 lượt mà không biết là chưa chắc,
    và nó sẽ giới thiệu con số đó như thật.
    """
    m = _ModelGia()
    ad.advise("gì cũng được", _res(_match(rating=2.5, votes=6)), m)
    assert "chưa chắc" in m.boi_canh, \
        f"bối cảnh không cảnh báo chai ít lượt:\n{m.boi_canh}"

    m2 = _ModelGia()
    ad.advise("gì cũng được", _res(_match(rating=4.3, votes=4770)), m2)
    assert "chưa chắc" not in m2.boi_canh, "cảnh báo sai cho chai đủ lượt"


def test_prompt_day_model_cach_doc_so_luot():
    """Có cờ trong dữ liệu mà prompt không nói phải làm gì với nó thì model tự
    đoán — và nó sẽ đoán theo hướng dễ bán hàng hơn."""
    p = ad.HE_THONG.lower()
    assert "ít lượt" in p and "chưa chắc" in p, "prompt không dạy cách đọc số lượt"


def test_ban_dich_khong_duoc_co_dau_phay_ben_trong():
    """Các lý do được nối với nhau bằng dấu phẩy.

    Nên một bản dịch kiểu "trời lạnh, mùa đông" cho ra
    "hợp trời lạnh, mùa đông, hợp buổi tối" — đọc không ra câu, và không test nào
    khác đỏ vì mọi chữ vẫn đúng. Đã dính thật, chỉ phát hiện khi đọc bằng mắt.
    """
    xau = {k: v for k, v in ad.VI_NHAN.items() if "," in v}
    assert not xau, f"bản dịch có dấu phẩy bên trong: {xau}"


def test_ly_do_phai_noi_ve_mui_khong_chi_ve_dip():
    """Câu tiếng Việt không chứa tên nhãn tiếng Anh, nên phần khớp được thường chỉ
    là trục hoàn cảnh. Lý do ra "hợp mùa lạnh, hợp buổi tối" là ĐÚNG nhưng vô
    dụng — nó không nói chai đó mùi gì, mà đó mới là thứ người bán cần.
    """
    from perfume_intel.analytics.dataset import Row
    from perfume_intel.retrieval import InMemoryRetriever
    from perfume_intel.retrieval.ports import Query

    rows = [Row(url=f"https://f.com/p/{i}.html", name=f"Chai {i}",
                brand="Hãng", gender="Unisex", rating=4.0, rating_count=900,
                accords={"woody": 90.0, "sweet": 50.0},
                notes=["Agarwood (Oud)", "Vanilla"], notes_by_layer={},
                seasons={"winter": 95.0}, day_night={"night": 90.0})
            for i in range(4)]
    r = InMemoryRetriever.from_rows(rows)
    res = r.search(Query(text="nước hoa cho mùa đông buổi tối", limit=2,
                         explain=True))
    assert res.matches, "fixture hỏng"
    khoi = {w.block for w in res.matches[0].why}
    assert khoi & {"accord", "note"},         f"lý do chỉ có {khoi} — không nói chai đó mùi gì"


def test_khong_bat_explain_thi_khong_bu_ly_do():
    """Phần bù mùi chỉ được chạy khi hỏi `explain` — nếu không thì mọi truy vấn
    đều tốn thêm một vòng lấy dữ liệu mà không ai dùng."""
    from perfume_intel.analytics.dataset import Row
    from perfume_intel.retrieval import InMemoryRetriever
    from perfume_intel.retrieval.ports import Query

    rows = [Row(url=f"https://f.com/p/{i}.html", name=f"Chai {i}", brand="H",
                gender="Unisex", rating=4.0, rating_count=900,
                accords={"woody": 90.0}, notes=["Vanilla"], notes_by_layer={},
                seasons={"winter": 95.0}, day_night={}) for i in range(3)]
    res = InMemoryRetriever.from_rows(rows).search(
        Query(text="mùa đông", limit=2, explain=False))
    assert all(m.why == () for m in res.matches)


if __name__ == "__main__":
    raise SystemExit(run(globals()))
