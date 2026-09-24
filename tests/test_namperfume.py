"""Test parser namperfume.net trên HTML thật đã lưu (chạy offline).

    python tests\\test_namperfume.py
hoặc  python -m pytest tests/ -v
"""

import csv
import sys
import tempfile
from pathlib import Path

from runner import run  # noqa: E402  (dat sys.path)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from perfume_intel.core import storage  # noqa: E402
from perfume_intel.sources.namperfume.models import (  # noqa: E402
    CSV_COLUMNS, NamProduct)
from perfume_intel.sources.namperfume.parsers import (  # noqa: E402
    parse_attributes, parse_concentration, parse_gender, parse_product,
    parse_standard_size, split_variant_title, _soup)

FIXTURE = Path(__file__).parent / "fixtures" / "namperfume_libre.html"
URL = "https://namperfume.net/products/yves-saint-laurent-libre-eau-de-parfum"


def load():
    return parse_product(FIXTURE.read_text(encoding="utf-8"), URL)


def test_xuat_xu():
    assert load().xuat_xu == "Pháp"


def test_nong_do():
    """Nồng độ phải bỏ phần dung tích: 'Eau de Parfum 90ml' -> 'Eau de Parfum'."""
    assert load().nong_do == "Eau de Parfum"


def test_nhom_huong():
    assert load().nhom_huong == "Hoa cam, Hoa oải hương, Hương vanila"


def test_phong_cach():
    assert load().phong_cach == "Sang trọng, Nữ tính, Tươi mới"


def test_standard_size():
    assert load().standard_size == ["90ml", "30ml", "50ml"]


def test_variant_title_order_khong_co_dinh():
    """Hai sản phẩm khác nhau ghi ngược thứ tự -> vẫn phải tách đúng."""
    assert split_variant_title("90ml / Eau de Parfum") == ("90ml", "Eau de Parfum")
    assert split_variant_title("Eau de Parfum/105ml") == ("105ml", "Eau de Parfum")
    assert split_variant_title("Extrait de Parfum/10ml") == ("10ml", "Extrait de Parfum")
    assert split_variant_title("1.5ml / Parfum") == ("1.5ml", "Parfum")
    assert split_variant_title(None) == (None, None)
    assert split_variant_title("Gift Set") == (None, "Gift Set")


def test_attributes_khong_bi_lap():
    """Trang lặp mỗi thuộc tính 2 lần (desktop + mobile) -> dict chỉ giữ 1."""
    attrs = parse_attributes(_soup(FIXTURE.read_text(encoding="utf-8")))
    assert attrs["Xuất xứ"] == "Pháp"
    assert attrs["Thương hiệu"] == "Yves Saint Laurent"
    assert attrs["Năm phát hành"] == "2019"
    assert len(attrs) == 6


def test_cac_truong_con_lai():
    p = load()
    assert p.name == "Yves Saint Laurent Libre Eau de Parfum"
    assert p.brand == "Yves Saint Laurent"
    assert p.sku == "110100201545"
    assert p.nam_phat_hanh == "2019"
    assert p.price == "5,310,000₫"
    assert p.url == URL and p.scraped_at


def test_xuat_file():
    p = load()
    p.des_url = "https://yupi.vn/products/ysl-libre"
    flat = p.to_flat_dict()
    assert flat["standard_size"] == "90ml; 30ml; 50ml"

    out = Path(tempfile.gettempdir()) / "nam_test.csv"
    storage.save_csv([p], out, columns=CSV_COLUMNS)
    row = next(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert row["xuat_xu"] == "Pháp"
    assert row["nong_do"] == "Eau de Parfum"
    assert row["standard_size"] == "90ml; 30ml; 50ml"
    assert row["des_url"] == "https://yupi.vn/products/ysl-libre"
    assert list(row.keys()) == CSV_COLUMNS


def test_gioi_tinh():
    """Lấy từ data-gender, cùng bộ giá trị với bên Fragrantica."""
    assert load().gioi_tinh == "Nữ"
    assert parse_gender(_soup(
        "<html><li class='product-variant-item' data-gender='Unisex'></li></html>"
    )) == "Unisex"
    assert parse_gender(_soup(
        "<html><li class='product-variant-item' data-gender='Nam'></li></html>"
    )) == "Nam"
    assert parse_gender(_soup("<html></html>")) is None


def test_des_url_mac_dinh_trong():
    """Không có cột des_url trong CSV đầu vào thì để trống."""
    assert load().des_url is None


def test_doc_lai_tu_jsonl():
    """load_records dùng khi --resume: đọc JSONL cũ để dựng lại CSV đầy đủ."""
    p = load()
    p.des_url = "https://yupi.vn/products/x"
    out = Path(tempfile.gettempdir()) / "nam_records.jsonl"
    storage.save_jsonl([p, p], out)

    lai = storage.load_records(out, NamProduct)
    assert len(lai) == 2
    assert lai[0].standard_size == ["90ml", "30ml", "50ml"]   # list giữ nguyên
    assert lai[0].gioi_tinh == "Nữ"
    assert lai[0].des_url == "https://yupi.vn/products/x"
    assert storage.load_records(Path(tempfile.gettempdir()) / "khong_co.jsonl",
                                NamProduct) == []


def test_trang_khong_co_du_lieu():
    """HTML rỗng thì trả về bản ghi trống chứ không văng lỗi."""
    p = parse_product("<html><body></body></html>", URL)
    assert p.xuat_xu is None and p.nong_do is None
    assert p.standard_size == []
    assert parse_standard_size(_soup("<html></html>")) == []
    assert parse_concentration(_soup("<html></html>")) is None


if __name__ == "__main__":
    raise SystemExit(run(globals()))
