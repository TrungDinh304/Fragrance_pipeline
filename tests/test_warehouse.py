"""Test tầng silver (Parquet) và đối chiếu marts với bản Python.

    python tests/test_warehouse.py

Cần `duckdb` (pip install -e ".[warehouse]"). Thiếu thì các test silver tự bỏ
qua thay vì đỏ — máy chỉ dùng để crawl không nhất thiết phải cài kho phân tích.

Phần đối chiếu marts cần thêm dbt VÀ một bản marts đã dựng sẵn; thiếu cũng bỏ
qua. Đây là loại test không chạy được ở mọi nơi, nên nó phải NÓI RÕ là đã bỏ
qua — một test "xanh" vì không chạy gì cả là thứ tệ nhất trong một bộ test.
"""

from runner import run  # noqa: E402  (đặt sys.path)

import json  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from perfume_intel import config  # noqa: E402
from perfume_intel.analytics import dataset, metrics  # noqa: E402
from perfume_intel.core import bronze  # noqa: E402

try:
    import duckdb  # noqa: F401
    from perfume_intel.warehouse import silver
    HAS_DUCKDB = True
except ImportError:                                    # pragma: no cover
    HAS_DUCKDB = False

SKIPPED: list[str] = []


def _skip(reason: str) -> bool:
    SKIPPED.append(reason)
    return True


PERFUME = {
    "url": "https://f.com/perfume/Dior/Sauvage-1.html", "name": "Sauvage",
    "brand": "Dior", "gender": "Nam", "year": "2015", "rating": 4.1,
    "rating_count": 9000, "fragrance_family": "Aromatic",
    "longevity": "long lasting", "sillage": "strong",
    "accords": [{"name": "woody", "width": 100.0, "opacity": 90.0},
                {"name": "citrus", "width": 70.0, "opacity": 60.0}],
    "top_notes": ["Bergamot"], "middle_notes": ["Pepper"],
    "base_notes": ["Ambroxan", "Cedar"],
    "when_to_wear": [{"name": "winter", "percent": 80.0, "votes": 120},
                     {"name": "night", "percent": 60.0, "votes": 90}],
    "des_url": "https://namperfume.net/products/dior-sauvage",
    "scraped_at": "2026-09-01T00:00:00+00:00",
}
BRAND = {"brand_url": "https://f.com/designers/Dior.html",
         "brand_name": "Dior", "alphabet": "D", "popular_rank": 3}
PRODUCT = {"perfume_url": "https://f.com/perfume/Dior/Sauvage-1.html",
           "brand_url": "https://f.com/designers/Dior.html",
           "brand_name": "Dior", "perfume_name": "Sauvage", "comments": 120}


def write(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def workspace(tmp: Path) -> Path:
    frag = tmp / "fragrantica"
    write(bronze.dir_for(frag, bronze.PERFUME) / "p.jsonl", [PERFUME])
    write(bronze.dir_for(frag, bronze.BRAND) / "b.jsonl", [BRAND])
    write(bronze.dir_for(frag, bronze.BRAND_PERFUME) / "c.jsonl", [PRODUCT])
    return frag


# ----------------------------------------------------------------- tầng silver
def test_silver_dung_du_bay_bang():
    if not HAS_DUCKDB and _skip("test_silver_dung_du_bay_bang: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag = workspace(tmp)
        report = silver.build(frag, out_dir=tmp / "silver")
        assert set(report.counts) == set(silver.TABLES)
        for name in silver.TABLES:
            assert (tmp / "silver" / f"{name}.parquet").exists(), name


def test_silver_trai_phang_cai_long_nhau():
    """Accord/note/vote vốn là mảng lồng trong bản ghi. Ở dạng bảng dài thì câu
    "accord nào phổ biến theo năm" mới là một câu GROUP BY."""
    if not HAS_DUCKDB and _skip("test_silver_trai_phang: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag = workspace(tmp)
        report = silver.build(frag, out_dir=tmp / "silver")
        assert report.counts["perfumes"] == 1
        assert report.counts["perfume_accords"] == 2
        assert report.counts["perfume_notes"] == 4      # 1 top, 1 middle, 2 base
        assert report.counts["perfume_wear"] == 2


def test_silver_dat_kieu_that_chu_khong_de_chuoi():
    """`year` vào là chuỗi "2015", ra phải là số. Để nguyên chuỗi thì mọi phép
    so sánh năm đều so theo bảng chữ cái và "999" > "2015"."""
    if not HAS_DUCKDB and _skip("test_silver_dat_kieu: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag = workspace(tmp)
        silver.build(frag, out_dir=tmp / "silver")
        con = silver.connect()
        silver.read_silver(con, tmp / "silver")
        kinds = dict(con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'perfumes'").fetchall())
        assert kinds["year"] in ("INTEGER", "INT32"), kinds["year"]
        assert kinds["rating"] in ("DOUBLE", "FLOAT8"), kinds["rating"]
        assert con.execute("SELECT year FROM perfumes").fetchone()[0] == 2015


def test_silver_khu_trung_giu_ban_moi_nhat():
    if not HAS_DUCKDB and _skip("test_silver_khu_trung: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag = tmp / "fragrantica"
        moi = dict(PERFUME, rating=4.9, scraped_at="2026-09-09T00:00:00+00:00")
        cu = dict(PERFUME, rating=3.0, scraped_at="2026-01-01T00:00:00+00:00")
        # Bản mới ở file đứng TRƯỚC theo bảng chữ cái, để "bản đọc sau thắng"
        # không tình cờ cho ra đúng kết quả.
        write(bronze.dir_for(frag, bronze.PERFUME) / "a_moi.jsonl", [moi])
        write(bronze.dir_for(frag, bronze.PERFUME) / "z_cu.jsonl", [cu])
        report = silver.build(frag, out_dir=tmp / "silver")
        assert report.counts["perfumes"] == 1
        con = silver.connect()
        silver.read_silver(con, tmp / "silver")
        assert con.execute("SELECT rating FROM perfumes").fetchone()[0] == 4.9


def test_silver_noi_duoc_muc_luc_voi_chi_tiet():
    """Khoá `perfume_key` phải nối được `brand_perfumes` với `perfumes` — đó
    chính là phép nối cho ra con số độ phủ."""
    if not HAS_DUCKDB and _skip("test_silver_noi_muc_luc: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag = workspace(tmp)
        silver.build(frag, out_dir=tmp / "silver")
        con = silver.connect()
        silver.read_silver(con, tmp / "silver")
        got = con.execute(
            "SELECT COUNT(*), COUNT(p.perfume_key) FROM brand_perfumes b "
            "LEFT JOIN perfumes p USING (perfume_key)").fetchone()
        assert got == (1, 1), got


def test_silver_giu_do_manh_cua_accord():
    """`width` là độ mạnh tương đối của accord trong chai đó. Mất nó thì bảng
    accord vẫn đủ dòng, chỉ là `avg_strength` toàn NULL — hỏng im lặng."""
    if not HAS_DUCKDB and _skip("test_silver_do_manh_accord: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag = workspace(tmp)
        silver.build(frag, out_dir=tmp / "silver")
        con = silver.connect()
        silver.read_silver(con, tmp / "silver")
        got = con.execute("SELECT accord, width, opacity FROM perfume_accords "
                          "ORDER BY rank").fetchall()
        assert got == [("woody", 100.0, 90.0), ("citrus", 70.0, 60.0)], got


def test_silver_noi_duoc_muc_luc_voi_danh_muc_hang():
    """`brand_perfumes.brand_key` phải chuẩn hoá y như `brands.brand_key`.

    Không chuẩn hoá thì phép nối trượt sạch — và nó trượt ÂM THẦM: LEFT JOIN
    vẫn trả đủ dòng, chỉ là cột bên phải toàn NULL.
    """
    if not HAS_DUCKDB and _skip("test_silver_noi_danh_muc: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag = workspace(tmp)
        silver.build(frag, out_dir=tmp / "silver")
        con = silver.connect()
        silver.read_silver(con, tmp / "silver")
        got = con.execute(
            "SELECT b.brand_name FROM brand_perfumes bp "
            "JOIN brands b USING (brand_key)").fetchall()
        assert got == [("Dior",)], f"nối mục lục với danh mục hãng trượt: {got}"


def test_silver_khong_rong_thi_khop_so_voi_loader_python():
    """Hai đường đọc độc lập (Python và silver) phải ra cùng số chai. Lệch nghĩa
    là một trong hai đang khử trùng sai."""
    if not HAS_DUCKDB and _skip("test_silver_khop_python: chưa cài duckdb"):
        return
    root = config.raw_dir("fragrantica")
    if not root.exists() and _skip("test_silver_khop_python: chưa có dữ liệu thật"):
        return
    with tempfile.TemporaryDirectory() as td:
        report = silver.build(root, out_dir=Path(td))
        assert report.counts["perfumes"] == len(dataset.load_raw(root))


# ---------------------------------------------------- đối chiếu marts <-> Python
def _marts_stale() -> str | None:
    """Marts có cũ hơn dữ liệu bronze không?

    Bộ marts được dựng một lần rồi nằm đó; mỗi lượt crawl lại làm nó lệch thêm.
    Nếu để test đỏ vì chuyện đó thì `make test` sẽ đỏ mỗi lần crawl xong — đỏ vì
    DỮ LIỆU đi tiếp, không phải vì CODE sai, và loại đỏ đó dạy người ta bỏ qua
    màu đỏ. Nên: marts cũ hơn bronze thì BỎ QUA kèm lý do, chứ không báo lỗi.
    Phép đối chiếu chỉ có nghĩa khi hai bên nhìn cùng một mẻ dữ liệu.
    """
    db = config.WAREHOUSE_DB
    newest = 0.0
    for folder in (config.raw_dir("fragrantica"), config.raw_dir("namperfume")):
        if folder.exists():
            for path in folder.rglob("*.jsonl"):
                newest = max(newest, path.stat().st_mtime)
    if newest and db.stat().st_mtime < newest:
        return ("marts cũ hơn dữ liệu bronze — chạy `make silver && make marts` "
                "rồi test lại")
    return None


def _marts_con():
    """Kết nối tới kho marts đã dựng, hoặc None nếu chưa có / đã cũ."""
    if not HAS_DUCKDB or not config.WAREHOUSE_DB.exists():
        return None
    cu = _marts_stale()
    if cu:
        SKIPPED.append(cu)
        return None
    con = silver.connect(str(config.WAREHOUSE_DB))
    have = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "mart_brand" not in have:
        con.close()
        return None
    return con


def _compare(label, py: dict, sql: dict, fields: list[str], tol=0.0011):
    assert set(py) == set(sql), \
        f"{label}: lệch danh sách khoá ({len(py)} Python vs {len(sql)} dbt)"
    for key, want in py.items():
        got = sql[key]
        for i, name in enumerate(fields):
            a, b = want[name], got[i + 1]
            if a is None and b is None:
                continue
            assert a is not None and b is not None, \
                f"{label}[{key}].{name}: Python={a!r} dbt={b!r}"
            assert abs(float(a) - float(b)) <= tol, \
                f"{label}[{key}].{name}: Python={a!r} dbt={b!r}"


def test_mart_brand_khop_bang_python():
    """dbt phải cho ra ĐÚNG con số mà `metrics.by_brand` cho ra.

    Đây là lý do giữ nguyên `analytics/metrics.py`: nó là bộ đối chứng. Công
    thức hiệu chỉnh Bayes viết lại bằng SQL rất dễ lệch ở chỗ mẫu số hoặc chỗ
    lọc `rating > 0`, và lệch kiểu đó không làm hỏng gì — chỉ làm bảng xếp hạng
    sai một cách rất thuyết phục.

    GIỚI HẠN: test này đọc file marts ĐÃ DỰNG SẴN, không tự dựng lại. Nó bắt
    được SQL viết sai, nhưng KHÔNG bắt được thay đổi trong `warehouse/silver.py`
    cho tới khi marts được dựng lại (`make marts`). Phần đúng đắn của chính tầng
    silver do nhóm test `test_silver_*` ở trên canh.
    """
    con = _marts_con()
    if con is None and _skip("test_mart_brand: marts chưa dựng hoặc đã cũ"):
        return
    try:
        rows = dataset.build(config.raw_dir("fragrantica"))
        py = {r["brand"]: r for r in metrics.by_brand(rows)}
        sql = {r[0]: r for r in con.execute(
            "SELECT brand, perfumes, rating_votes, attention_share_pct, "
            "rating_avg, rating_weighted FROM mart_brand").fetchall()}
        _compare("mart_brand", py, sql,
                 ["perfumes", "rating_votes", "attention_share_pct",
                  "rating_avg", "rating_weighted"])
    finally:
        con.close()


def test_mart_accord_khop_bang_python():
    con = _marts_con()
    if con is None and _skip("test_mart_accord: marts chưa dựng hoặc đã cũ"):
        return
    try:
        rows = dataset.build(config.raw_dir("fragrantica"))
        py = {r["accord"]: r for r in metrics.by_accord(rows)}
        sql = {r[0]: r for r in con.execute(
            "SELECT accord, perfumes, coverage_pct, avg_strength, "
            "rating_weighted, rating_votes FROM mart_accord").fetchall()}
        _compare("mart_accord", py, sql,
                 ["perfumes", "coverage_pct", "avg_strength",
                  "rating_weighted", "rating_votes"])
    finally:
        con.close()


def test_mart_coverage_khop_bang_python():
    con = _marts_con()
    if con is None and _skip("test_mart_coverage: marts chưa dựng hoặc đã cũ"):
        return
    try:
        root = config.raw_dir("fragrantica")
        rows = dataset.build(root)
        py = {r["brand"]: r for r in metrics.coverage(rows,
                                                      dataset.load_catalog(root))}
        sql = {r[0]: r for r in con.execute(
            "SELECT brand, catalog_perfumes, detailed, coverage_pct, "
            "rating_votes FROM mart_coverage").fetchall()}
        _compare("mart_coverage", py, sql,
                 ["catalog_perfumes", "detailed", "coverage_pct",
                  "rating_votes"])
    finally:
        con.close()


if __name__ == "__main__":
    code = run(dict(globals()))
    if SKIPPED:
        print(f"\nBỎ QUA {len(SKIPPED)} test (thiếu phụ thuộc hoặc dữ liệu):")
        for reason in SKIPPED:
            print(f"  - {reason}")
    raise SystemExit(code)
