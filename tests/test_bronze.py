"""Test bố cục kho thô: phân loại bản ghi, quét theo loại, danh mục, độ phủ.

    python tests/test_bronze.py

Cả file này canh đúng MỘT kiểu hỏng: bỏ sót dữ liệu trong im lặng. Đó là lỗi đã
xảy ra thật — 24.676/25.465 dòng (97%) bị loader vứt mà không một dòng log —
và nó không bao giờ tự lộ ra, vì phần còn lại vẫn chạy ngon lành.
"""

from runner import run  # noqa: E402  (đặt sys.path)

import json  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from perfume_intel.analytics import dataset, metrics  # noqa: E402
from perfume_intel.core import bronze  # noqa: E402

PERFUME = {"url": "https://f.com/perfume/Dior/Sauvage-1.html", "name": "Sauvage",
           "brand": "Dior", "rating": 4.1, "rating_count": 9000}
BRAND = {"brand_url": "https://f.com/designers/Dior.html", "brand_name": "Dior",
         "alphabet": "D"}
PRODUCT = {"perfume_url": "https://f.com/perfume/Dior/Sauvage-1.html",
           "brand_url": "https://f.com/designers/Dior.html",
           "brand_name": "Dior", "perfume_name": "Sauvage", "comments": 120}


def write(path: Path, records) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


# ------------------------------------------------------------- phân loại
def test_muc_luc_co_ca_hai_khoa_phai_ra_muc_luc():
    """Bản ghi mục lục mang CẢ `perfume_url` lẫn `brand_url`. Hỏi `brand_url`
    trước thì nó bị xếp nhầm thành danh mục hãng, và toàn bộ 8.212 dòng mục lục
    biến thành 8.212 "hãng" ma — thứ tự kiểm tra trong `classify` là có lý do."""
    assert bronze.classify(PRODUCT) == bronze.BRAND_PERFUME


def test_phan_loai_ba_hinh_dang_con_lai():
    assert bronze.classify(PERFUME) == bronze.PERFUME
    assert bronze.classify(BRAND) == bronze.BRAND
    assert bronze.classify({"name": "khong co khoa nao"}) is None


def test_loai_khong_co_thi_bao_loi_ngay():
    try:
        bronze.dir_for(Path("x"), "khong-co-loai-nay")
    except ValueError as exc:
        assert "khong-co-loai-nay" in str(exc)
    else:
        raise AssertionError("loại sai mà vẫn trả về đường dẫn")


# ------------------------------------------------------------------ quét
def test_doc_mot_loai_khong_nhin_thay_loai_khac():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(bronze.dir_for(root, bronze.PERFUME) / "a.jsonl", [PERFUME])
        write(bronze.dir_for(root, bronze.BRAND) / "b.jsonl", [BRAND] * 3)
        write(bronze.dir_for(root, bronze.BRAND_PERFUME) / "c.jsonl", [PRODUCT] * 2)

        got = bronze.scan(root, bronze.PERFUME)
        assert len(got.records) == 1
        # Đã xếp đúng thư mục thì không phải "bỏ qua" gì cả.
        assert got.skipped == 0, f"quét lan sang thư mục khác: {got.other}"


def test_file_phang_kieu_cu_van_doc_duoc():
    """Dọn kho là việc TUỲ CHỌN. Không chạy script dọn thì vẫn phải chạy đúng,
    nếu không thì bản cập nhật này tự làm hỏng kho của người đang dùng."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "lan_lon.jsonl", [PERFUME, BRAND, PRODUCT])
        got = bronze.scan(root, bronze.PERFUME)
        assert len(got.records) == 1
        assert got.other[bronze.BRAND] == 1
        assert got.other[bronze.BRAND_PERFUME] == 1


def test_phan_bi_bo_duoc_dem_chu_khong_im_lang():
    """Đây là cả lý do module này tồn tại."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "x.jsonl", [PERFUME] + [BRAND] * 50 + [PRODUCT] * 20)
        got = bronze.scan(root, bronze.PERFUME)
        assert got.skipped == 70
        assert got.other[bronze.BRAND] == 50
        assert got.other[bronze.BRAND_PERFUME] == 20


def test_dong_hong_duoc_dem_rieng():
    """Dòng không có khoá chính nào là dữ liệu hỏng, không phải "loại thứ tư" —
    gộp chung vào `other` thì nó trốn mất giữa đám bỏ qua hợp lệ."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "x.jsonl", [PERFUME, {"name": "hong"}, {"name": "hong2"}])
        got = bronze.scan(root, bronze.PERFUME)
        assert got.unknown == 2
        assert not got.other


def test_doc_duoc_ca_hai_bo_cuc_cung_luc():
    """Dọn dở chừng (một nửa đã chuyển, một nửa chưa) vẫn phải ra đủ."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "cu.jsonl", [PERFUME])
        other = dict(PERFUME, url="https://f.com/perfume/Dior/Homme-2.html")
        write(bronze.dir_for(root, bronze.PERFUME) / "moi.jsonl", [other])
        got = bronze.scan(root, bronze.PERFUME)
        assert len(got.records) == 2


def test_thu_muc_luu_tru_tu_dat_ten_khong_bi_bo_quen():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "luu_tru_2025" / "cu.jsonl", [PERFUME])
        assert len(bronze.scan(root, bronze.PERFUME).records) == 1


# ----------------------------------------------------- loader của analytics
def test_load_raw_chi_tra_ve_chi_tiet_chai():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "x.jsonl", [PERFUME] + [BRAND] * 5 + [PRODUCT] * 5)
        index = dataset.load_raw(root)
        assert len(index) == 1
        assert all(r.get("url") for r in index.values())


def test_load_raw_giu_ban_crawl_moi_nhat():
    """Bản MỚI cố ý đặt ở file có tên đứng TRƯỚC theo bảng chữ cái.

    Nếu để bản mới ở file đọc sau cùng thì test vẫn xanh ngay cả khi code bỏ
    hẳn việc so `scraped_at` — "bản đọc sau thắng" tình cờ cho ra đúng kết quả.
    Đảo thứ tự lại thì chỉ phép so ngày mới cứu được.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        moi = dict(PERFUME, rating=4.5, scraped_at="2026-09-01T00:00:00+00:00")
        cu = dict(PERFUME, rating=3.0, scraped_at="2026-01-01T00:00:00+00:00")
        write(root / "a_moi.jsonl", [moi])
        write(root / "z_cu.jsonl", [cu])
        index = dataset.load_raw(root)
        assert len(index) == 1
        assert next(iter(index.values()))["rating"] == 4.5, \
            "giữ nhầm bản cũ — đang lấy bản đọc sau cùng thay vì bản mới nhất"


def test_load_catalog_ghep_muc_luc_theo_hang():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(bronze.dir_for(root, bronze.BRAND) / "b.jsonl", [BRAND])
        items = [dict(PRODUCT, perfume_url=f"https://f.com/perfume/Dior/{i}.html")
                 for i in range(7)]
        write(bronze.dir_for(root, bronze.BRAND_PERFUME) / "p.jsonl", items)

        catalog = dataset.load_catalog(root)
        assert len(catalog.brands) == 1
        assert catalog.url_of("Dior")
        assert catalog.total_for(catalog.url_of("Dior")) == 7
        assert not catalog.empty


def test_catalog_trong_thi_bao_la_trong():
    with tempfile.TemporaryDirectory() as td:
        assert dataset.load_catalog(Path(td)).empty


def test_catalog_khong_dem_trung_chai():
    """Mục lục của một hãng thường được crawl lại nhiều lần; đếm cả bản trùng
    thì "hãng có 391 chai" thành 782 và mọi tỉ lệ phủ đều sai."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        folder = bronze.dir_for(root, bronze.BRAND_PERFUME)
        write(folder / "lan1.jsonl", [PRODUCT])
        write(folder / "lan2.jsonl", [PRODUCT])
        catalog = dataset.load_catalog(root)
        assert catalog.total_for(catalog.url_of("Dior")) == 1


# ------------------------------------------------------------------ độ phủ
def _rows(n: int, brand: str = "Dior") -> list[dataset.Row]:
    return [dataset.Row(url=f"https://f.com/perfume/{brand}/{i}.html",
                        name=f"P{i}", brand=brand, rating_count=100)
            for i in range(n)]


def test_do_phu_tinh_dung_ti_le():
    catalog = dataset.Catalog(
        brands={"k": BRAND},
        products={"https://f.com/designers/dior.html":
                  [dict(PRODUCT, perfume_url=f"u{i}") for i in range(10)]})
    out = {r["brand"]: r for r in metrics.coverage(_rows(3), catalog)}
    assert out["Dior"]["catalog_perfumes"] == 10
    assert out["Dior"]["detailed"] == 3
    assert out["Dior"]["coverage_pct"] == 30.0


def test_chua_biet_tong_khac_voi_phu_khong_phan_tram():
    """Hãng chưa crawl mục lục phải ra None, KHÔNG phải 0. "Chưa biết tổng" và
    "biết tổng, mới phủ 0%" là hai câu khác hẳn; trộn lại thì bảng độ phủ nói
    sai về đúng những hãng chưa đụng tới."""
    catalog = dataset.Catalog(brands={}, products={})
    out = {r["brand"]: r for r in metrics.coverage(_rows(2, "Chanel"), catalog)}
    assert out["Chanel"]["catalog_perfumes"] is None
    assert out["Chanel"]["coverage_pct"] is None
    assert out["Chanel"]["detailed"] == 2


def test_do_phu_ke_ca_hang_chua_crawl_chai_nao():
    """Hãng có mục lục 300 chai mà chưa crawl chi tiết chai nào vẫn phải xuất
    hiện — đó chính là hãng cần biết nhất."""
    catalog = dataset.Catalog(
        brands={},
        products={"https://f.com/designers/dior.html":
                  [dict(PRODUCT, perfume_url=f"u{i}") for i in range(300)]})
    out = {r["brand"]: r for r in metrics.coverage([], catalog)}
    assert out["Dior"]["catalog_perfumes"] == 300
    assert out["Dior"]["detailed"] == 0
    assert out["Dior"]["coverage_pct"] == 0.0


# ------------------------------------------------------------- dọn kho
def test_script_don_kho_bo_qua_file_lan_nhieu_loai():
    """Chẻ một file lẫn nhiều loại là SỬA dữ liệu, không còn là dọn chỗ. Thà bỏ
    qua và báo ra."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migrate_bronze",
        Path(__file__).resolve().parent.parent / "scripts" / "migrate_bronze.py")
    migrate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrate)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "sach.jsonl", [PERFUME])
        write(root / "lan.jsonl", [PERFUME, BRAND])
        moves, mixed = migrate.plan(root)
        assert [s.name for s, _ in moves] == ["sach.jsonl"]
        assert [p.name for p, _ in mixed] == ["lan.jsonl"]


def test_don_kho_khong_lam_doi_ket_qua_doc():
    """Bất biến quan trọng nhất của việc dọn: đọc trước và sau phải y hệt."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migrate_bronze",
        Path(__file__).resolve().parent.parent / "scripts" / "migrate_bronze.py")
    migrate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrate)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "chai.jsonl", [PERFUME])
        write(root / "hang.jsonl", [BRAND])
        write(root / "mucluc.jsonl", [PRODUCT])
        truoc = {k: len(bronze.scan(root, k).records) for k in bronze.KINDS}

        moves, _ = migrate.plan(root)
        migrate.apply(moves)
        sau = {k: len(bronze.scan(root, k).records) for k in bronze.KINDS}
        assert truoc == sau, f"dọn xong đọc ra khác: {truoc} -> {sau}"
        assert not list(root.glob("*.jsonl")), "còn file phẳng ở gốc"


if __name__ == "__main__":
    raise SystemExit(run(dict(globals())))
