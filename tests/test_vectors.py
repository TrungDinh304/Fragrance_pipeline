"""Test tầng vector: dựng vector, đo độ giống, tra cứu chai/hãng.

    python tests/test_vectors.py

Mỗi test dưới đây canh một tính chất CÓ THỂ HỎNG ÂM THẦM. Vector hoá là loại code
mà lỗi không làm chương trình chết — nó chỉ làm thứ tự kết quả sai đi, và không
có gì báo. Vì vậy phần lớn test ở đây là so sánh thứ tự, không phải so khớp số.
"""

from runner import run  # noqa: E402  (đặt sys.path)

from perfume_intel.analytics import dataset  # noqa: E402
from perfume_intel.analytics.dataset import Row  # noqa: E402
from perfume_intel.vectors import features  # noqa: E402
from perfume_intel.vectors.index import VectorIndex  # noqa: E402

# Nhiều note để khối note thật sự áp đảo về số chiều — xem
# `test_khoi_nho_khong_bi_khoi_lon_nhan_chim`.
MANY_NOTES = [f"Note{i}" for i in range(40)]


def row(url, name="X", brand="B", accords=(), top=(), middle=(), base=(),
        seasons=(), day_night=(), **extra) -> Row:
    layers = {"top": list(top), "middle": list(middle), "base": list(base)}
    return Row(
        url=url, name=name, brand=brand,
        accords=dict(accords),
        notes_by_layer={k: v for k, v in layers.items() if v},
        seasons=dict(seasons), day_night=dict(day_night),
        **extra,
    )


def idf_for(rows):
    return features.build_idf(rows)


# ------------------------------------------------- chuẩn hoá theo khối
def test_khoi_nho_khong_bi_khoi_lon_nhan_chim():
    """Khối hoàn cảnh (6 chiều) phải còn tiếng nói cạnh khối note (15+ chiều).

    Đây là test quan trọng nhất của file. Nếu `combine` chuẩn hoá MỘT LẦN ở cuối
    thay vì chuẩn hoá từng khối trước, thì 40 chiều note sẽ áp đảo 2 chiều hoàn
    cảnh và hai chai trái ngược hẳn về mùa vẫn ra ~0,98 giống nhau — tức là trục
    "hoàn cảnh sử dụng" biến mất khỏi kết quả mà không có dấu hiệu gì.

    Số đo trên đúng fixture này: chuẩn hoá theo khối cho 0,857; chuẩn hoá một
    lần ở cuối cho 0,982. Ngưỡng 0,90 nằm giữa hai giá trị đó, nên test này
    THẬT SỰ phân biệt được hai cách làm (đã kiểm bằng mutation test).

    Chú ý: với cách đúng, 0,857 KHÔNG phụ thuộc vào việc có 15 hay 40 note —
    chuẩn hoá từng khối làm số chiều của khối không còn quyết định trọng số nữa.
    Đó chính là tính chất cần bảo vệ.
    """
    a = row("a", base=MANY_NOTES, seasons={"winter": 100.0},
            day_night={"night": 100.0})
    b = row("b", base=MANY_NOTES, seasons={"summer": 100.0},
            day_night={"day": 100.0})
    idf = idf_for([a, b])
    score = features.cosine(features.vector(a, idf), features.vector(b, idf))
    assert score < 0.90, (
        f"đổi ngược hoàn cảnh mà vẫn giống {score:.3f} — khối hoàn cảnh đang bị "
        "khối note nhấn chìm, kiểm tra thứ tự chuẩn hoá trong combine()")


def test_vector_la_vector_don_vi():
    a = row("a", accords={"woody": 100.0}, base=["Oud"], seasons={"winter": 90.0})
    idf = idf_for([a, row("b", accords={"woody": 50.0}, base=["Oud"])])
    vec = features.vector(a, idf)
    assert abs(features.cosine(vec, vec) - 1.0) < 1e-9


# --------------------------------------------------------- tầng note
def test_trung_note_base_giong_hon_trung_note_top():
    """Base quyết định dư hương sau vài giờ, top bay trong mươi phút."""
    target = row("t", base=["Oud", "Amber"], top=["Lemon", "Mint"])
    same_base = row("b", base=["Oud", "Amber"], top=["Pepper", "Basil"])
    same_top = row("o", base=["Rose", "Musk"], top=["Lemon", "Mint"])
    idf = idf_for([target, same_base, same_top])

    v = features.vector(target, idf)
    assert (features.cosine(v, features.vector(same_base, idf))
            > features.cosine(v, features.vector(same_top, idf)))


def test_note_o_nhieu_tang_khong_cong_don():
    """Một note ở cả top và base thì lấy tầng nặng nhất, không cộng hai lần.

    Phải có ÍT NHẤT HAI note thì test mới có tác dụng: nếu khối note chỉ có một
    chiều, chuẩn hoá L2 trong khối luôn đưa nó về 1,0 nên cộng dồn hay lấy max
    đều cho ra cùng một vector — test sẽ xanh cả khi code sai.
    """
    both = row("x", top=["Oud"], base=["Oud", "Amber"])
    only_base = row("y", base=["Oud", "Amber"])
    idf = idf_for([both, only_base, row("z", base=["Oud", "Amber"])])
    assert features.vector(both, idf) == features.vector(only_base, idf)


# --------------------------------------------------------------- IDF
def test_note_hiem_noi_nhieu_hon_note_pho_bien():
    """Trùng "Musk" (chai nào cũng có) phải nói ít hơn trùng một note hiếm."""
    corpus = [row(f"c{i}", base=["Musk", f"Filler{i}"]) for i in range(20)]
    corpus += [row("r1", base=["Musk", "Ambergris"]),
               row("r2", base=["Musk", "Ambergris"])]
    idf = idf_for(corpus)
    assert idf["note:ambergris"] > idf["note:musk"]


def test_note_xuat_hien_dung_mot_lan_bi_bo():
    """194/551 note trên kho hiện tại chỉ xuất hiện 1 lần. Giữ lại thì IDF đẩy
    chúng lên cao nhất và hai chai tình cờ trùng một note độc nhất trông như rất
    giống nhau."""
    corpus = [row("a", base=["Musk", "Hapax"]), row("b", base=["Musk"])]
    idf = idf_for(corpus)
    assert "note:hapax" not in idf
    assert "note:musk" in idf


def test_accord_cung_duoc_nhan_idf():
    """Không chỉ note — accord cũng phải qua IDF.

    Hai accord cùng `width` 100 thì nếu bỏ IDF chúng sẽ nặng bằng nhau, và
    "woody" (gần như chai nào cũng có) sẽ nói to ngang "leather". Test note ở
    trên không canh được chỗ này vì nó chỉ đọc bảng IDF, không kiểm việc
    `_accord_block` có thật sự dùng bảng đó hay không.
    """
    corpus = [row(f"c{i}", accords={"woody": 100.0}) for i in range(20)]
    rare = row("r1", accords={"woody": 100.0, "leather": 100.0})
    corpus += [rare, row("r2", accords={"woody": 100.0, "leather": 100.0})]
    block = features._accord_block(rare, idf_for(corpus))
    assert block["acc:leather"] > block["acc:woody"], \
        "accord hiếm không nặng hơn accord phổ biến — _accord_block bỏ qua IDF"


def test_idf_bi_chan_tran():
    corpus = [row(f"c{i}", base=["Common"]) for i in range(5000)]
    corpus += [row("r1", base=["Rare"]), row("r2", base=["Rare"])]
    idf = idf_for(corpus)
    assert idf["note:rare"] <= features.MAX_IDF


# ------------------------------------------------------- thang thứ tự
def test_thang_do_luu_huong_va_toa_huong():
    a = row("a", base=["Oud"], longevity="eternal", sillage="enormous")
    b = row("b", base=["Oud"], longevity="very weak", sillage="intimate")
    idf = idf_for([a, b])
    va, vb = features.blocks(a, idf), features.blocks(b, idf)
    assert va[features.STRENGTH]["str:longevity"] == 1.0
    assert vb[features.STRENGTH]["str:longevity"] == 0.0


def test_nhan_do_luu_huong_la_thi_khong_co_chieu_do():
    """Nhãn lạ phải VẮNG MẶT, không phải bằng 0 — 0 là một giá trị có nghĩa
    ("very weak"), còn vắng mặt nghĩa là không biết."""
    r = row("a", base=["Oud"], longevity="chua biet", sillage=None)
    parts = features.blocks(r, idf_for([r, row("b", base=["Oud"])]))
    assert parts[features.STRENGTH] == {}


# ------------------------------------------------- chân dung của hãng
def test_chan_dung_hang_chi_dung_khoi_mui():
    """Trọng tâm của nhiều chai làm loãng hết note, còn khối hoàn cảnh thì sống
    sót qua phép trung bình và hội tụ về một giá trị chung — kết quả là mọi hãng
    đều "giống nhau" ở fall/winter/night. Vì vậy chân dung hãng chỉ giữ khối mùi.
    """
    rows = [
        row("a1", brand="A", accords={"woody": 100.0}, base=["Oud"],
            seasons={"winter": 100.0}, longevity="eternal"),
        row("a2", brand="A", accords={"woody": 90.0}, base=["Oud"],
            seasons={"winter": 100.0}, longevity="eternal"),
        row("b1", brand="B", accords={"citrus": 100.0}, base=["Lemon"],
            seasons={"winter": 100.0}, longevity="eternal"),
        row("b2", brand="B", accords={"citrus": 90.0}, base=["Lemon"],
            seasons={"winter": 100.0}, longevity="eternal"),
    ]
    profiles = VectorIndex(rows).brand_profiles()
    dims = set(profiles["A"].vector)
    assert dims, "chân dung hãng rỗng"
    assert not [d for d in dims if d.startswith(("occ:", "str:"))], \
        f"chân dung hãng còn chiều hoàn cảnh/cường độ: {sorted(dims)}"


def test_hai_hang_khac_mui_khong_bi_cham_giong_nhau():
    """Hai hãng chỉ dùng chung đúng một note tầm thường ("Musk") thì điểm phải
    thấp. Cho chúng dùng chung một note, không phải không dùng chung gì: trùng
    đúng 0 chiều thì cosine bằng 0 và hãng đó bị loại khỏi kết quả, nên phép so
    sánh sẽ chẳng kiểm tra được gì.
    """
    rows = [
        row("a1", brand="A", accords={"woody": 100.0},
            base=["Oud", "Amber", "Musk"]),
        row("a2", brand="A", accords={"woody": 90.0},
            base=["Oud", "Amber", "Musk"]),
        row("b1", brand="B", accords={"citrus": 100.0},
            base=["Lemon", "Neroli", "Musk"]),
        row("b2", brand="B", accords={"citrus": 90.0},
            base=["Lemon", "Neroli", "Musk"]),
    ]
    index = VectorIndex(rows)
    _, hits = index.similar_brands("A", min_perfumes=1)
    assert hits, "không có hãng nào được trả về"
    assert hits[0].row.name == "B"
    assert hits[0].score < 0.4, \
        f"hai hãng khác mùi hẳn mà giống {hits[0].score:.3f}"


def test_min_perfumes_loc_hang_qua_it_chai():
    rows = [
        row("a1", brand="A", accords={"woody": 100.0}, base=["Oud"]),
        row("a2", brand="A", accords={"woody": 90.0}, base=["Oud"]),
        row("b1", brand="B", accords={"woody": 95.0}, base=["Oud"]),
    ]
    index = VectorIndex(rows)
    _, hits = index.similar_brands("A", min_perfumes=2)
    assert hits == [], "hãng chỉ có 1 chai vẫn lọt qua --min-perfumes 2"


# ------------------------------------------------------------ tra cứu
def test_resolve_khop_mot_phan():
    """Gõ "oud" phải khớp "Agarwood (Oud)" — tên trên Fragrantica không phải tên
    người ta hay gọi, bắt người dùng đoán đúng chính tả là vô lý."""
    rows = [row("a", base=["Agarwood (Oud)"]), row("b", base=["Agarwood (Oud)"])]
    index = VectorIndex(rows)
    terms, missing, renamed = index.resolve(features.NOTE, ["oud"])
    assert terms == ["note:agarwood (oud)"]
    assert missing == []
    assert renamed == {"oud": "agarwood (oud)"}


def test_resolve_bao_lai_term_khong_co():
    """Term không khớp phải được BÁO LẠI, không im lặng bỏ — im lặng thì người
    dùng nhận một danh sách trông hợp lý nhưng trả lời câu khác."""
    rows = [row("a", base=["Musk"]), row("b", base=["Musk"])]
    index = VectorIndex(rows)
    terms, missing, _ = index.resolve(features.NOTE, ["khongcotren doi"])
    assert terms == []
    assert missing == ["khongcotren doi"]


def test_resolve_nhieu_ket_qua_lay_ten_pho_bien_nhat():
    rows = [row(f"c{i}", base=["Vanilla"]) for i in range(10)]
    rows += [row("r1", base=["Vanilla Absolute"]),
             row("r2", base=["Vanilla Absolute"])]
    index = VectorIndex(rows)
    terms, _, _ = index.resolve(features.NOTE, ["vanilla"])
    assert terms == ["note:vanilla"]


def test_tim_theo_ten_lay_chai_nhieu_vote_nhat():
    rows = [
        row("a", name="Sauvage", base=["Oud"], rating_count=10),
        row("b", name="Sauvage Elixir", base=["Oud"], rating_count=9000),
    ]
    index = VectorIndex(rows)
    assert index.rows[index.find("sauvage")].url == "b"


# ---------------------------------------------------------- xếp hạng
def test_loc_ung_vien_khong_lam_sot_ket_qua():
    """Index đảo ngược chỉ là tối ưu tốc độ. Nếu nó bỏ sót thì kết quả sai mà
    không ai biết, nên ở đây so thẳng với cách chấm điểm toàn bộ kho.

    So sánh TẬP kết quả, không so thứ tự: trên fixture nhỏ rất nhiều chai có
    điểm bằng nhau, nên so thứ tự chỉ là so cách phá thế hoà chứ không kiểm tra
    được điều cần kiểm tra. Điều cần kiểm tra là: mọi chai có điểm > 0 đều phải
    xuất hiện.
    """
    pool = [f"N{i}" for i in range(6)]
    rows = [row(f"p{i}",
                base=[pool[i % 6], pool[(i + 2) % 6]],
                top=[pool[(i + 3) % 6]],
                accords={"woody": 100.0} if i % 3 == 0 else {"citrus": 80.0})
            for i in range(30)]
    index = VectorIndex(rows)
    vec = index.vectors[0]

    got = {h.row.url for h in index.query(vec, limit=len(index.rows),
                                          exclude={0})}
    want = {index.rows[i].url for i in range(1, len(index.rows))
            if features.cosine(vec, index.vectors[i]) > 0}
    assert want, "fixture không có chai nào trùng chiều nào"
    assert got == want, f"lọc ứng viên làm sót: {sorted(want - got)}"


def test_truy_van_chi_hoan_canh_van_ra_ket_qua():
    """Khối hoàn cảnh không nằm trong index đảo ngược (có ở ~100% số chai nên
    không lọc được gì), nên truy vấn chỉ có hoàn cảnh phải đi nhánh chấm cả kho.
    """
    rows = [row("a", base=["Oud"], seasons={"winter": 100.0}),
            row("b", base=["Lemon"], seasons={"summer": 100.0}),
            row("c", base=["Oud"], seasons={"winter": 90.0})]
    index = VectorIndex(rows)
    vec = features.query_from_terms([], occasion=["winter"], idf=index.idf)
    hits = index.query(vec, limit=3)
    assert [h.row.url for h in hits][:2] == ["a", "c"]


def test_khong_tu_tra_ve_chinh_no():
    rows = [row("a", base=["Oud"]), row("b", base=["Oud"])]
    index = VectorIndex(rows)
    assert "a" not in [h.row.url for h in index.similar(0, limit=5)]


def test_loai_chai_cung_hang():
    rows = [
        row("a", brand="Same", base=["Oud", "Amber"]),
        row("b", brand="Same", base=["Oud", "Amber"]),
        row("c", brand="Other", base=["Oud"]),
    ]
    index = VectorIndex(rows)
    urls = [h.row.url for h in index.similar(0, limit=5, same_brand=False)]
    assert urls == ["c"], urls


def test_loc_theo_gender_va_so_vote():
    rows = [
        row("a", base=["Oud"], gender="Nam", rating_count=5000),
        row("b", base=["Oud"], gender="Nữ", rating_count=5000),
        row("c", base=["Oud"], gender="Nam", rating_count=3),
    ]
    index = VectorIndex(rows)
    hits = index.similar(0, limit=5, gender="Nam", min_votes=100)
    assert [h.row.url for h in hits] == []
    hits = index.similar(1, limit=5, gender="Nam", min_votes=100)
    assert [h.row.url for h in hits] == ["a"]


def test_bo_chai_khong_co_accord_lan_note():
    """Vector rỗng thì chấm điểm là vô nghĩa; để lại thì nó trả về 0 cho mọi truy
    vấn và "0" trông giống một kết quả thật."""
    rows = [row("a", base=["Oud"]), row("b", base=["Oud"]),
            row("trong", accords={}, top=(), middle=(), base=())]
    index = VectorIndex(rows)
    assert [r.url for r in index.rows] == ["a", "b"]


def test_giai_thich_chi_ra_chieu_dong_gop():
    rows = [row("a", base=["Oud", "Amber"]), row("b", base=["Oud", "Amber"])]
    index = VectorIndex(rows)
    hits = index.similar(0, limit=1, explain=True)
    assert hits[0].why, "bật --explain mà không có chiều nào"
    assert all(":" in dim for dim, _ in hits[0].why)


# ------------------------------------------- phần nối với loader dữ liệu
def test_loader_giu_tang_note():
    """`dataset._to_row` phải giữ tầng; mất tầng thì trọng số base/middle/top ở
    trên thành vô nghĩa mà không có lỗi nào."""
    raw = {
        "url": "https://x/1",
        "top_notes": ["Lemon"],
        "middle_notes": ["Rose"],
        "base_notes": ["Oud"],
        "accords": [{"name": "woody", "width": 100.0}],
    }
    r = dataset._to_row(raw)
    assert r.notes_by_layer == {"top": ["Lemon"], "middle": ["Rose"],
                               "base": ["Oud"]}
    assert r.notes == ["Lemon", "Rose", "Oud"]


def test_loader_bo_tang_rong():
    r = dataset._to_row({"url": "https://x/2", "base_notes": ["Oud"]})
    assert r.notes_by_layer == {"base": ["Oud"]}


def test_hoan_canh_suy_dien_chi_tu_cuong_do():
    office = row("a", base=["Oud"], sillage="intimate", longevity="long lasting")
    party = row("b", base=["Oud"], sillage="enormous", longevity="eternal")
    assert "công sở" in features.derive_occasion(office)
    assert "tiệc tối" in features.derive_occasion(party)
    assert "công sở" not in features.derive_occasion(party)


if __name__ == "__main__":
    raise SystemExit(run(dict(globals())))
