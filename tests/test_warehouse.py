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
def test_silver_dung_du_cac_bang():
    if not HAS_DUCKDB and _skip("test_silver_dung_du_cac_bang: chưa cài duckdb"):
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


def test_silver_chai_co_brand_key_noi_thang_sang_brands():
    """`perfumes.brand_key` phải nối THẲNG được sang `brands`, không đi vòng
    qua mục lục. Ba trường hợp: có trong mục lục, chỉ khớp theo tên hãng, và
    không khớp gì (phải ra NULL chứ không bịa khoá)."""
    if not HAS_DUCKDB and _skip("test_silver_brand_key: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag = tmp / "fragrantica"
        chanel = dict(PERFUME, url="https://f.com/perfume/Chanel/No5-2.html",
                      name="No 5", brand="Chanel")
        la = dict(PERFUME, url="https://f.com/perfume/X/Y-3.html",
                  name="Y", brand="Hang La")
        write(bronze.dir_for(frag, bronze.PERFUME) / "p.jsonl",
              [PERFUME, chanel, la])
        write(bronze.dir_for(frag, bronze.BRAND) / "b.jsonl", [
            BRAND, {"brand_url": "https://f.com/designers/Chanel.html",
                    "brand_name": "Chanel", "alphabet": "C"}])
        # Chỉ Sauvage có trong mục lục; Chanel phải khớp được nhờ tên hãng.
        write(bronze.dir_for(frag, bronze.BRAND_PERFUME) / "c.jsonl", [PRODUCT])
        silver.build(frag, out_dir=tmp / "silver")
        con = silver.connect()
        silver.read_silver(con, tmp / "silver")
        got = dict(con.execute(
            "SELECT p.name, b.brand_name FROM perfumes p "
            "LEFT JOIN brands b USING (brand_key)").fetchall())
        assert got == {"Sauvage": "Dior", "No 5": "Chanel", "Y": None}, got


def test_silver_giu_moi_lan_crawl_cho_lich_su_rating():
    """`perfumes` chỉ giữ bản mới nhất; `perfume_rating_history` phải giữ CẢ
    HAI lần crawl — đó là nguồn duy nhất cho xu hướng rating theo thời gian.
    Cùng một lần crawl bị ghi ở hai file thì vẫn chỉ là một dòng."""
    if not HAS_DUCKDB and _skip("test_silver_lich_su_rating: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frag = tmp / "fragrantica"
        cu = dict(PERFUME, rating=3.9, rating_count=8000,
                  scraped_at="2026-08-01T00:00:00+00:00")
        write(bronze.dir_for(frag, bronze.PERFUME) / "a.jsonl", [cu, PERFUME])
        write(bronze.dir_for(frag, bronze.PERFUME) / "b.jsonl", [PERFUME])
        report = silver.build(frag, out_dir=tmp / "silver")
        assert report.counts["perfumes"] == 1
        con = silver.connect()
        silver.read_silver(con, tmp / "silver")
        got = con.execute(
            "SELECT rating, rating_count FROM perfume_rating_history "
            "ORDER BY scraped_at").fetchall()
        assert got == [(3.9, 8000), (4.1, 9000)], got


def test_silver_khong_rong_thi_khop_so_voi_loader_python():
    """Hai đường đọc độc lập (Python và silver) phải ra cùng số chai.

    Lệch nghĩa là một trong hai đang khử trùng sai.

    VÌ SAO CÓ PHÉP KIỂM "DỮ LIỆU CÓ ĐỨNG YÊN KHÔNG"
    Bài này đọc bronze HAI lần: `silver.build` đọc một lần, `dataset.load_raw`
    đọc lần nữa. Nếu một lượt crawl đang chạy thì nó chèn bản ghi vào giữa hai
    lần đọc, và hai con số lệch nhau dù cả hai phép khử trùng đều đúng.

    Đã đỏ thật hai lần đúng lúc lịch Airflow đang crawl (956 vs 960, rồi
    962 vs 962 khi đo lại sau đó). Để nguyên thì `make test` sẽ đỏ ngẫu nhiên
    mỗi khi trùng giờ crawl — đỏ vì DỮ LIỆU đi tiếp, không phải vì CODE sai, và
    loại đỏ đó dạy người ta bỏ qua màu đỏ. Cùng lý do với `_marts_stale()`.
    """
    if not HAS_DUCKDB and _skip("test_silver_khop_python: chưa cài duckdb"):
        return
    root = config.raw_dir("fragrantica")
    if not root.exists() and _skip("test_silver_khop_python: chưa có dữ liệu thật"):
        return

    truoc = len(bronze.read(root, bronze.PERFUME, quiet=True))
    with tempfile.TemporaryDirectory() as td:
        report = silver.build(root, out_dir=Path(td))
        python = len(dataset.load_raw(root))
    sau = len(bronze.read(root, bronze.PERFUME, quiet=True))

    if truoc != sau and _skip(
            f"test_silver_khop_python: bronze đổi giữa lúc đo "
            f"({truoc} -> {sau} bản ghi) — có lượt crawl đang chạy"):
        return
    assert report.counts["perfumes"] == python, (
        f"silver {report.counts['perfumes']} != python {python} "
        f"trên cùng {sau} bản ghi thô")


# ------------------------------------------------- chốt schema trước khi dbt chạy
def _doi_parquet(path: Path, cot: str) -> None:
    """Ghi lại một file Parquet với danh sách cột khác — giả lập file mà code
    phiên bản trước đã dựng và còn nằm lại trên ổ."""
    tmp = path.with_suffix(".moi")
    con = silver.connect()
    try:
        con.execute(f"COPY (SELECT {cot} FROM "
                    f"read_parquet('{path.as_posix()}')) "
                    f"TO '{tmp.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
    tmp.replace(path)


def _dung_silver(tmp: Path) -> Path:
    silver.build(workspace(tmp), out_dir=tmp / "silver")
    return tmp / "silver"


def test_check_xanh_ngay_sau_khi_dung():
    if not HAS_DUCKDB and _skip("test_check_xanh: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        assert silver.kiem_tra(_dung_silver(Path(td))) == []


def test_check_bat_cot_thieu():
    """Đúng lỗi đã gặp thật: `TABLES` thêm `brand_key`, parquet trên ổ còn là
    bản cũ chưa có cột đó. Trước khi có phép kiểm này, dbt mới là chỗ phát hiện
    — bằng `Binder Error` ở model đầu tiên, rồi 65 node sau chuyển sang SKIP."""
    if not HAS_DUCKDB and _skip("test_check_cot_thieu: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        out = _dung_silver(Path(td))
        _doi_parquet(out / "perfumes.parquet", "* EXCLUDE (brand_key)")

        lech = silver.kiem_tra(out)
        assert [x.table for x in lech] == ["perfumes"], lech
        assert lech[0].thieu == ("brand_key",), lech[0]
        assert not lech[0].thua and not lech[0].sai_kieu, lech[0]


def test_check_bat_cot_la_va_sai_kieu():
    """Hai chiều còn lại của cùng một chuyện: file có cột code đã bỏ, và cột
    cùng tên nhưng khác kiểu. Kiểu đổi là ca im lặng nhất — `year` thành VARCHAR
    thì không model nào vỡ, chỉ có mọi phép so sánh năm so theo bảng chữ cái."""
    if not HAS_DUCKDB and _skip("test_check_cot_la: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        out = _dung_silver(Path(td))
        _doi_parquet(out / "perfumes.parquet",
                     "* REPLACE (CAST(year AS VARCHAR) AS year), "
                     "'x' AS cot_da_bo")

        lech = silver.kiem_tra(out)
        assert len(lech) == 1, lech
        assert lech[0].thua == ("cot_da_bo",), lech[0]
        assert len(lech[0].sai_kieu) == 1, lech[0]
        assert "year" in lech[0].sai_kieu[0], lech[0]


def test_check_bat_thieu_file():
    """Thay cho guard `test -d data/silver` cũ: nó chỉ xem thư mục có tồn tại,
    nên một thư mục thiếu 6/7 bảng vẫn đi qua."""
    if not HAS_DUCKDB and _skip("test_check_thieu_file: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        out = _dung_silver(Path(td))
        (out / "brands.parquet").unlink()

        lech = silver.kiem_tra(out)
        assert [x.table for x in lech] == ["brands"], lech
        assert "không có file" in lech[0].loi


def test_check_bat_file_cut():
    """Parquet thiếu footer vẫn có kích thước và vẫn trông như xong — đúng loại
    file mà một lần ghi bị ngắt để lại. Phải bắt, không được ném ra ngoài."""
    if not HAS_DUCKDB and _skip("test_check_file_cut: chưa cài duckdb"):
        return
    with tempfile.TemporaryDirectory() as td:
        out = _dung_silver(Path(td))
        path = out / "perfume_notes.parquet"
        path.write_bytes(path.read_bytes()[:120])

        lech = silver.kiem_tra(out)
        assert [x.table for x in lech] == ["perfume_notes"], lech
        assert "không đọc được" in lech[0].loi


def test_mo_ta_lech_noi_ro_bang_cot_va_lenh():
    """Phát hiện được mà không nói phải làm gì thì chỉ đổi một câu khó hiểu
    thành một câu khó hiểu khác. Câu báo phải có: bảng nào, cột nào, lệnh nào."""
    if not HAS_DUCKDB and _skip("test_mo_ta_lech: chưa cài duckdb"):
        return
    noi_dung = silver.mo_ta_lech(
        [silver.Lech("perfumes", thieu=("brand_key",))], Path("data/silver"))
    assert "perfumes" in noi_dung
    assert "brand_key" in noi_dung
    assert "make silver" in noi_dung


def test_cli_check_tra_ma_loi_khi_lech():
    """Cả cái chốt trong Makefile dựa vào MÃ THOÁT của lệnh này.

    Phát hiện đúng mà vẫn `return 0` thì `make marts` đi tiếp sang dbt y như cũ
    — phép kiểm trở thành một dòng log trang trí.
    """
    if not HAS_DUCKDB and _skip("test_cli_check: chưa cài duckdb"):
        return
    from perfume_intel.cli import app

    with tempfile.TemporaryDirectory() as td:
        out = _dung_silver(Path(td))
        assert app.main(["silver", "--check", "--out", str(out)]) == 0

        _doi_parquet(out / "perfumes.parquet", "* EXCLUDE (brand_key)")
        assert app.main(["silver", "--check", "--out", str(out)]) == 1


def test_marts_sh_chot_schema_truoc_roi_moi_exec_dbt():
    """Wrapper cho hai đường chạy dbt trong container.

    LF-only là bắt buộc, không phải chuyện thẩm mỹ: `\\r` cuối dòng shebang làm
    kernel đi tìm `/bin/sh\\r` và container chết với `no such file or directory`
    — đã mất một lượt debug cho đúng lỗi này ở `scripts/airflow/start.sh`.
    """
    goc = Path(__file__).resolve().parent.parent
    raw = (goc / "scripts" / "marts.sh").read_bytes()
    assert b"\r\n" not in raw, "marts.sh có CRLF: container sẽ không exec được"

    noi_dung = raw.decode("utf-8")
    assert "set -e" in noi_dung, "thiếu `set -e`: chốt đỏ mà script vẫn đi tiếp"
    assert noi_dung.index("silver --check") < noi_dung.index("dbt.cli.main"), \
        "chốt phải chạy TRƯỚC dbt"


def test_moi_duong_chay_dbt_deu_qua_chot():
    """Bốn đường tới gold, tất cả phải qua cùng một phép kiểm.

    `make marts` và `dbt build` tay chạy trên host; service `marts` của compose
    và task `marts` trong DAG chạy trong container qua `scripts/marts.sh`. Hai
    đường trong container nguy hiểm NGANG host: `data/silver` là bind mount từ
    máy host còn `transform/` đến từ ảnh, nên hai bên lệch schema được.
    """
    goc = Path(__file__).resolve().parent.parent
    com = (goc / "docker-compose.yml").read_text(encoding="utf-8")
    khoi = com[com.index("\n  marts:"):]
    khoi = khoi[:khoi.index("\n  # ---", 1)]
    assert "scripts/marts.sh" in khoi, \
        f"service `marts` của compose gọi dbt mà không qua chốt:\n{khoi}"

    dag = (goc / "airflow" / "dags" / "refresh_marts.py").read_text(
        encoding="utf-8")
    assert "scripts/marts.sh" in dag, \
        "task `marts` trong DAG gọi dbt mà không qua chốt"
    assert 'entrypoint=["python", "-m", "dbt.cli.main"]' not in dag, \
        "DAG còn entrypoint gọi dbt trực tiếp"


def test_make_marts_chot_schema_truoc_khi_goi_dbt():
    """Chốt phải nằm trong chính target `marts` — đường chạy trên host.

    `make marts` và `dbt build` tay là hai đường mà parquet trên ổ có thể đã cũ
    hơn code mà không có bước nào dựng lại nó.
    """
    goc = Path(__file__).resolve().parent.parent
    dong_than: list[str] = []
    trong = False
    for dong in (goc / "Makefile").read_text(encoding="utf-8").splitlines():
        if dong.startswith("marts:"):
            trong = True
            continue
        if trong:
            # Thân của một target là các dòng bắt đầu bằng TAB; dòng trống vẫn
            # thuộc target, dòng khác là đã sang target kế tiếp.
            if dong[:1] not in ("\t", ""):
                break
            dong_than.append(dong)
    assert dong_than, "không tìm thấy target `marts` trong Makefile"
    than = "\n".join(dong_than)
    assert "silver --check" in than, (
        f"target `marts` không chốt schema trước khi gọi dbt:\n{than}")
    assert than.index("silver --check") < than.index("dbt.cli.main"), (
        "chốt phải chạy TRƯỚC dbt, không phải sau")
    assert "test -d data/silver" not in than, (
        "guard cũ còn đó: nó chỉ xem thư mục có tồn tại nên parquet cũ vẫn qua")


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
