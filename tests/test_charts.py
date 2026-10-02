"""Test tầng biểu đồ: dựng hình, ghép trang HTML.

    python tests/test_charts.py

Biểu đồ hỏng thì KHÔNG có lỗi nào được ném ra — nó chỉ vẽ ra một hình nói sai,
hoặc một file mở lên là trang trắng. Nên test ở đây canh ba thứ:

  - hợp đồng với người đọc: hình nào cũng có bảng số, có chú giải khi cần, và
    khi thiếu dữ liệu thì để TRỐNG kèm lý do chứ không vẽ bừa;
  - hợp đồng kỹ thuật: file tự chứa (không mạng, không JS), XML hợp lệ kể cả khi
    tên hãng có dấu `&`;
  - mấy phép tính dễ sai lặng lẽ: mốc trục log, thang lưỡng hướng, đảo thang màu
    cho nền tối.
"""

from runner import run  # noqa: E402  (đặt sys.path)

import re  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

from perfume_intel.analytics import charts, html_report, svg  # noqa: E402
from perfume_intel.analytics.dataset import Row  # noqa: E402


def row(url="https://x/1", name="X", brand="B", accords=None, notes=None,
        seasons=None, day_night=None, **extra) -> Row:
    notes = notes or []
    return Row(url=url, name=name, brand=brand,
               accords=dict(accords or {}),
               notes=list(notes),
               notes_by_layer={"base": list(notes)} if notes else {},
               seasons=dict(seasons or {}), day_night=dict(day_night or {}),
               **extra)


def sample(n=12) -> list[Row]:
    out = []
    for i in range(n):
        out.append(row(
            url=f"https://x/{i}", name=f"Chai {i}",
            brand=f"Hãng {i % 3}",
            accords={"woody": 100.0 - i, "sweet": 60.0 + i},
            notes=["Musk", "Oud" if i % 2 else "Vanilla"],
            seasons={"winter": 80.0 - i, "summer": 20.0 + i,
                     "spring": 50.0, "fall": 40.0},
            day_night={"day": 40.0 + i, "night": 60.0 - i},
            gender=["Nam", "Nữ", "Unisex"][i % 3],
            year=2010 + i % 10,
            rating=4.0 + (i % 5) / 10,
            rating_count=100 * (i + 1),
            longevity=["moderate", "long lasting", "weak"][i % 3],
            sillage="moderate",
            fragrance_family="Woody",
        ))
    return out


# ------------------------------------------------------- hợp đồng với người đọc
def test_moi_hinh_ve_duoc_deu_co_bang_so():
    """Thang màu liên tục không bao giờ được là cách DUY NHẤT đọc một giá trị."""
    for figure in charts.build_all(sample()):
        if figure.empty:
            continue
        assert figure.rows, f"{figure.key}: vẽ hình nhưng không có bảng số"
        assert figure.columns, f"{figure.key}: bảng số không có tiêu đề cột"
        assert all(len(r) == len(figure.columns) for r in figure.rows), \
            f"{figure.key}: số ô không khớp số cột"


def test_hinh_nhieu_nhom_phai_co_chu_giai():
    """Từ 2 nhóm trở lên thì danh tính không được chỉ dựa vào màu."""
    figures = {f.key: f for f in charts.build_all(sample())}
    for key in ("by_year", "longevity", "accord_season"):
        assert len(figures[key].legend) >= 2, f"{key}: thiếu chú giải"


def test_cac_nhom_trong_mot_hinh_phai_khac_mau_nhau():
    """Chú giải có đủ 3 dòng nhưng cả 3 cùng một màu thì vẫn vô dụng — cột xếp
    chồng thành một khối đặc, không đọc ra nhóm nào là nhóm nào."""
    for figure in charts.build_all(sample()):
        if len(figure.legend) < 2:
            continue
        tokens = [token for _, token in figure.legend]
        assert len(set(tokens)) == len(tokens), \
            f"{figure.key}: có nhóm dùng trùng màu — {tokens}"


def test_mau_gan_theo_nhom_chu_khong_theo_thu_hang():
    """Lọc bớt một nhóm thì các nhóm còn lại phải GIỮ NGUYÊN màu. Gán màu theo
    thứ hạng thì người đã nhớ "Nam màu xanh" bị dẫn sai ngay khi dữ liệu đổi."""
    full = charts.by_year(sample(), since=2000)
    fewer = charts.by_year([r for r in sample() if r.gender != "Nữ"], since=2000)
    kept = dict(fewer.legend)
    for label, token in full.legend:
        if label in kept:
            assert kept[label] == token, \
                f"{label} đổi màu khi tập dữ liệu đổi: {token} -> {kept[label]}"


def test_moi_hinh_deu_noi_cach_doc():
    for figure in charts.build_all(sample()):
        assert figure.how or figure.empty, f"{figure.key}: không có câu cách đọc"


def test_thieu_du_lieu_thi_de_trong_kem_ly_do_chu_khong_ve_bua():
    """Không ghép được namperfume thì KHÔNG được xếp hạng chai nhiều vote rồi
    gọi đó là "khoảng trống thị trường" — đó là một câu khác hẳn."""
    figure = charts.market_gap_chart(sample())
    assert figure.empty, "listed=0 mà vẫn vẽ khoảng trống thị trường"
    assert "namperfume" in figure.empty
    assert not figure.svg


def test_co_du_lieu_thi_trường_thi_ve_that():
    rows = sample()
    rows[0].listed = True
    figure = charts.market_gap_chart(rows)
    assert not figure.empty
    assert figure.svg


def test_khong_co_du_lieu_thi_moi_hinh_deu_de_trong_chu_khong_no():
    for figure in charts.build_all([]):
        assert figure.empty, f"{figure.key}: rỗng dữ liệu mà vẫn vẽ"


def test_thieu_when_to_wear_thi_bao_ro():
    bare = [row(url=f"https://x/{i}", accords={"woody": 90.0}) for i in range(3)]
    figure = charts.accord_season(bare)
    assert figure.empty
    assert "--render" in figure.empty


# -------------------------------------------------------- hợp đồng kỹ thuật
def test_svg_van_hop_le_khi_ten_hang_co_ky_tu_xml():
    """`Viktor & Rolf`, `Victoria's Secret` là tên hãng CÓ THẬT trong dữ liệu.
    Không thoát ký tự thì một dấu `&` làm hỏng XML và browser bỏ hiển thị từ
    chỗ đó trở đi — trang vẫn mở, chỉ là mất nửa hình."""
    rows = sample()
    for r in rows:
        r.brand = "Viktor & Rolf <script>"
        r.name = "L'Eau \"Bleue\" & Co"
    for figure in charts.build_all(rows):
        if not figure.svg:
            continue
        ET.fromstring(figure.svg)          # ném ParseError nếu XML hỏng


def test_trang_html_khong_can_mang_va_khong_co_script():
    """Tự chứa theo nghĩa chặt: mở được khi máy không có mạng, nửa năm sau."""
    page = html_report.render(charts.build_all(sample()),
                              charts.headline(sample()))
    assert "<script" not in page.lower(), "trang có <script> — đã hứa là không JS"
    for pattern in ("src=\"http", "href=\"http", "@import", "url(http"):
        assert pattern not in page, f"trang tham chiếu tài nguyên ngoài: {pattern}"


def test_nen_toi_khai_o_ca_hai_noi():
    """Một nơi cho thiết lập hệ điều hành, một nơi cho lựa chọn ghim trên thẻ
    <html>. Thiếu nơi thứ hai thì nút đổi theme không có tác dụng."""
    page = html_report.render(charts.build_all(sample()),
                              charts.headline(sample()))
    assert "@media (prefers-color-scheme: dark)" in page
    assert ':root[data-theme="dark"]' in page


def test_ten_hang_co_dau_va_duoc_thoat_trong_bang():
    figure = charts.Figure("k", "T", "h", svg="", columns=["Hãng"],
                           rows=[["Viktor & Rolf"]])
    page = html_report.render([figure], [])
    assert "Viktor &amp; Rolf" in page
    assert "Viktor & Rolf" not in page


def test_bang_mau_khai_du_moi_bien_svg_dung_den():
    """SVG trỏ tới biến CSS; thiếu một biến thì mark đó tô đen hoặc trong suốt
    mà không có lỗi nào."""
    page = html_report.render(charts.build_all(sample()),
                              charts.headline(sample()))
    used = set(re.findall(r"var\(--([a-z0-9-]+)\)", page))
    declared = set(re.findall(r"^\s*--([a-z0-9-]+):", page, re.M))
    assert used <= declared, f"dùng biến chưa khai: {sorted(used - declared)}"


# ------------------------------------------------- phép tính dễ sai lặng lẽ
def test_moc_truc_log_khong_bi_rong_khi_dai_hep():
    """Dải vote thật là 24.000-250.000, chưa tới hai bậc. Nếu chỉ lấy luỹ thừa
    10 thì chỉ còn đúng một mốc trong dải và trục thành vô dụng — đã gặp."""
    ticks = [t for t in svg.log_ticks(23982, 249771) if 23982 <= t <= 249771]
    assert len(ticks) >= 2, f"trục log chỉ có {len(ticks)} mốc: {ticks}"


def test_thang_luong_huong_dung_dau():
    mean, sigma = svg.mean_sigma([10.0, 20.0, 30.0, 40.0, 50.0])
    assert svg.div_token(mean, mean, sigma) == svg.DIVERGING[2]
    assert svg.div_token(mean + 3 * sigma, mean, sigma) == svg.DIVERGING[4]
    assert svg.div_token(mean - 3 * sigma, mean, sigma) == svg.DIVERGING[0]


def test_moi_gia_tri_bang_nhau_thi_khong_mau_nao_noi_troi():
    mean, sigma = svg.mean_sigma([7.0, 7.0, 7.0])
    assert sigma == 0
    assert svg.div_token(7.0, mean, sigma) == svg.DIVERGING[2]


def test_thang_mot_sac_bi_dao_tren_nen_toi():
    """Bậc ứng với "gần 0" phải lùi về phía mặt nền. Nền sáng thì bậc nhạt lùi
    về nền; nền tối thì bậc ĐẬM mới lùi. Không đảo thì ô gần 0 sáng rực."""
    assert svg.SEQ_DARK[0] == svg.SEQ_LIGHT[-1]
    assert svg.SEQ_DARK[-1] == svg.SEQ_LIGHT[0]
    assert len(svg.SEQ_DARK) == len(svg.SEQ_LIGHT) == svg.SEQ_STEPS


def test_heatmap_so_theo_tung_cot_chu_khong_theo_gia_tri_tho():
    """"Mùa" và "ngày/đêm" là hai khối vote riêng, mỗi khối tự quy về 100% của
    nó. Tô theo giá trị thô thì cột có mẫu số lớn hơn đậm đều từ trên xuống —
    trông như phát hiện, thực ra chỉ là mẫu số khác.

    Ở đây cột `day` cao đều (90-92) còn `winter` thấp đều (10-12). So theo cột
    thì CẢ HAI phải ra gần như trung tính; so theo giá trị thô thì một cột sẽ
    đậm hết còn cột kia nhạt hết.
    """
    rows = []
    for i in range(6):
        rows.append(row(
            url=f"https://x/{i}", accords={f"acc{i}": 100.0},
            seasons={"winter": 10.0 + i * 0.4},
            day_night={"day": 90.0 + i * 0.4}))
    figure = charts.accord_season(rows)
    tokens = re.findall(r'fill="var\(--(div-[a-z0-9-]+)\)"', figure.svg)
    assert tokens, "không tô ô nào"
    extreme = [t for t in tokens if t in ("div-neg-2", "div-pos-2")]
    assert not extreme, (
        "có ô bị tô ở mức cực trị dù trong cột gần như không chênh nhau — "
        "nhiều khả năng đang tô theo giá trị thô thay vì theo lệch so với cột")


def test_so_dong_bang_khop_so_nhom():
    rows = sample()
    figure = charts.by_year(rows, since=2010)
    years = {r.year for r in rows if r.year and r.year >= 2010}
    assert len(figure.rows) == len(years)


def test_ghi_chu_dem_ca_chai_bi_loai_vi_qua_cu():
    rows = sample()
    rows[0].year = 1990
    figure = charts.by_year(rows, since=2010)
    assert "trước 2010" in figure.note


def test_thong_tin_dau_trang_khong_bia_so_thi_truong():
    rows = sample()
    kpis = dict((k, v) for k, v, _ in charts.headline(rows))
    assert kpis["Có bán ở VN"] == "0"


if __name__ == "__main__":
    raise SystemExit(run(dict(globals())))
