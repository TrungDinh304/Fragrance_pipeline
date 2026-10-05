"""Data lake: luật đồng bộ giữa spool local và MinIO.

Bộ này KHÔNG cần MinIO. Nó thay kho thật bằng một `LocalStore` nằm ở thư mục
khác, nên mọi luật trong `core/lake.py` đều kiểm được mà không có hạ tầng — và
đó là chủ ý: luật đồng bộ là chỗ dễ sai nhất, nên nó phải được kiểm ở mọi lần
`make test`, không phải chỉ khi ai đó nhớ bật Docker.

Điều khoản đáng giá nhất ở đây là `test_khong_ghi_de_khi_local_dai_hon`: đó là
thứ duy nhất đứng giữa một mẻ crawl dở và việc bị lake ghi đè mất.
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

from runner import run  # noqa: E402

from perfume_intel import config  # noqa: E402
from perfume_intel.core import bronze, lake  # noqa: E402
from perfume_intel.core.objects import (LocalStore, ObjectNotFound,
                                        StoreUnavailable)

BAN_GHI = '{"url": "https://f.com/perfume/a-1.html", "name": "A"}\n'


class _DemList:
    """Bọc một kho lại để đếm số lần `list` — dùng kiểm bộ nhớ đồng bộ."""

    def __init__(self, that) -> None:
        self._that = that
        self.so_lan_list = 0

    def list(self, prefix: str = ""):
        self.so_lan_list += 1
        return self._that.list(prefix)

    def __getattr__(self, name):
        return getattr(self._that, name)


class _KhoChet:
    """Kho luôn hỏng. Mất MinIO không được phép giết mẻ crawl."""

    def put(self, key, data):
        raise StoreUnavailable("minio chết")

    def get(self, key):
        raise StoreUnavailable("minio chết")

    def stat(self, key):
        raise StoreUnavailable("minio chết")

    def list(self, prefix=""):
        raise StoreUnavailable("minio chết")

    def delete(self, key):
        raise StoreUnavailable("minio chết")


@contextlib.contextmanager
def _lake(bat: bool = True, kho=None):
    """Dựng một lake giả: RAW_DIR/SILVER_DIR tạm + kho đứng thay MinIO.

    Trả về (raw_dir, kho_bronze, kho_silver).
    """
    raw_cu, silver_cu, store_cu = (config.RAW_DIR, config.SILVER_DIR,
                                   lake.store)
    kind_cu = config.lake_kind
    with tempfile.TemporaryDirectory() as td:
        goc = Path(td)
        config.RAW_DIR = goc / "raw"
        config.SILVER_DIR = goc / "silver"
        config.RAW_DIR.mkdir()
        config.SILVER_DIR.mkdir()
        config.lake_kind = lambda: ("s3" if bat else "off")
        khos = {lake.BRONZE: kho or LocalStore(goc / "lake-bronze"),
                lake.SILVER: kho or LocalStore(goc / "lake-silver")}
        lake.store = lambda layer=lake.BRONZE: khos[layer]
        lake.forget_sync()
        try:
            yield config.RAW_DIR, khos[lake.BRONZE], khos[lake.SILVER]
        finally:
            (config.RAW_DIR, config.SILVER_DIR, lake.store,
             config.lake_kind) = raw_cu, silver_cu, store_cu, kind_cu
            lake.forget_sync()


def _ghi(path: Path, noi_dung: str) -> Path:
    """Ghi bằng BYTE, không bằng `write_text`.

    `write_text` trên Windows đổi `\\n` thành `\\r\\n`, nên số byte và nội dung
    không còn đúng như hằng số trong test — mà cả bộ này so sánh chính hai thứ
    đó. Kho đối tượng giữ byte nguyên văn (đúng như nó phải làm), nên chỗ cần
    tất định là phía test.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(noi_dung.encode("utf-8"))
    return path


# --------------------------------------------------------- chốt giữ offline
def test_lake_tat_thi_khong_lam_gi():
    """Mặc định TẮT: niêm và kéo về đều là hàm rỗng.

    Nếu không, mọi test và mọi lần chạy tay đều đòi một MinIO đang sống.
    """
    with _lake(bat=False) as (raw, kho_bronze, _):
        path = _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        assert lake.seal(path) is None
        assert lake.ensure_local(raw / "fragrantica") == 0
        assert lake.seal_dir(raw) == (0, 0)
        assert kho_bronze.list() == []


def test_duong_dan_ngoai_vung_khong_bao_gio_cham_lake():
    """Đây là cái chốt giữ cho ~240 test khác chạy trên thư mục tạm mà không
    bao giờ nói chuyện với mạng, kể cả khi lake đang bật."""
    with _lake() as (_raw, kho_bronze, _):
        with tempfile.TemporaryDirectory() as ngoai:
            path = _ghi(Path(ngoai) / "a.jsonl", BAN_GHI)
            assert lake.key_for(path) is None
            assert lake.seal(path) is None
            assert lake.ensure_local(Path(ngoai)) == 0
        assert kho_bronze.list() == []


# -------------------------------------------------------------------- niêm
def test_niem_dung_khoa_la_duong_dan_tuong_doi():
    with _lake() as (raw, kho_bronze, _):
        path = _ghi(raw / "fragrantica" / "perfumes" / "Chanel_f_051026.jsonl",
                    BAN_GHI)
        info = lake.seal(path)
        assert info is not None
        assert info.key == "fragrantica/perfumes/Chanel_f_051026.jsonl"
        assert kho_bronze.get(info.key).decode() == BAN_GHI


def test_niem_duoc_file_phang_kieu_cu():
    """Bố cục cũ (file nằm thẳng ở `data/raw/<site>/`) lên lake không cần luật
    riêng — chính đường dẫn tương đối của nó là khoá."""
    with _lake() as (raw, kho_bronze, _):
        path = _ghi(raw / "fragrantica" / "Chanel_fragrantica_210926.jsonl",
                    BAN_GHI)
        info = lake.seal(path)
        assert info is not None
        assert info.key == "fragrantica/Chanel_fragrantica_210926.jsonl"
        assert len(kho_bronze.list()) == 1


def test_khong_gui_lai_khi_lake_da_du():
    """Niêm lại y nguyên là lãng phí, và với S3 thật là trả tiền cho việc vô
    nghĩa. `seal` phải tự biết bỏ qua."""
    with _lake() as (raw, _kho, _):
        path = _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        assert lake.seal(path) is not None
        assert lake.seal(path) is None, "gửi lại file lake đã có đủ"


def test_niem_lai_khi_file_dai_them():
    """`--resume` crawl nốt vào đúng file cũ, nên file dài thêm và phải lên lại."""
    with _lake() as (raw, kho_bronze, _):
        path = _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        lake.seal(path)
        _ghi(path, BAN_GHI * 3)
        assert lake.seal(path) is not None, "file dài thêm mà không niêm lại"
        assert kho_bronze.get("fragrantica/perfumes/a.jsonl").decode() \
            == BAN_GHI * 3


def test_niem_ca_thu_muc_chi_gui_phan_khac():
    with _lake() as (raw, kho_bronze, _):
        _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        _ghi(raw / "fragrantica" / "perfumes" / "b.jsonl", BAN_GHI)
        assert lake.seal_dir(raw) == (2, 0)
        assert lake.seal_dir(raw) == (0, 2), "niêm lại cả thư mục lần hai"
        _ghi(raw / "fragrantica" / "perfumes" / "c.jsonl", BAN_GHI)
        assert lake.seal_dir(raw) == (1, 2)
        assert len(kho_bronze.list()) == 3


def test_niem_bo_qua_duoi_file_la():
    """Thư mục này cũng có file tạm và file người dùng bỏ vào; niêm tất là đưa
    rác lên kho chính thức."""
    with _lake() as (raw, kho_bronze, _):
        _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        _ghi(raw / "fragrantica" / "perfumes" / "ghi-chu.txt", "rac")
        _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl.__tmp1", "nua voi")
        assert lake.seal_dir(raw) == (1, 0)
        assert [i.key for i in kho_bronze.list()] == \
            ["fragrantica/perfumes/a.jsonl"]


def test_silver_niem_file_parquet_khong_phai_jsonl():
    """Hai tầng hai loại file. Dùng chung danh sách đuôi thì silver trông như
    rỗng trên lake dù local đã dựng xong."""
    with _lake() as (_raw, _kho_bronze, kho_silver):
        _ghi(config.SILVER_DIR / "perfumes.parquet", "PAR1")
        _ghi(config.SILVER_DIR / "ghi-chu.jsonl", BAN_GHI)
        assert lake.seal_dir(config.SILVER_DIR, lake.SILVER) == (1, 0)
        assert [i.key for i in kho_silver.list()] == ["perfumes.parquet"]


# ----------------------------------------------------------------- kéo về
def test_keo_ve_file_may_nay_chua_co():
    """Container mới dựng: spool rỗng, lake có đủ. Thiếu bước này thì nó crawl
    lại từ đầu dù dữ liệu đang nằm nguyên trên MinIO."""
    with _lake() as (raw, kho_bronze, _):
        kho_bronze.put("fragrantica/perfumes/a.jsonl", BAN_GHI.encode())
        assert lake.ensure_local(raw / "fragrantica") == 1
        assert (raw / "fragrantica" / "perfumes" / "a.jsonl").read_text(
            encoding="utf-8") == BAN_GHI


def test_khong_ghi_de_khi_local_dai_hon():
    """LUẬT QUAN TRỌNG NHẤT CỦA CẢ MODULE: file dài hơn thì thắng.

    Cảnh thật: đang crawl dở hãng Avon, spool đã có 900 chai, lake còn bản niêm
    hôm qua với 400 chai. Kéo về mà ghi đè là xoá 500 chai vừa crawl — và vì
    bronze append-only nên không có cách nào biết là đã mất.
    """
    with _lake() as (raw, kho_bronze, _):
        kho_bronze.put("fragrantica/perfumes/a.jsonl", (BAN_GHI * 2).encode())
        path = _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI * 5)
        assert lake.ensure_local(raw / "fragrantica") == 0
        assert path.read_text(encoding="utf-8") == BAN_GHI * 5, \
            "lake đã ghi đè mất phần đang crawl dở"


def test_ghi_de_khi_lake_dai_hon():
    """Chiều ngược lại: máy khác đã crawl thêm thì phải nhận bản dài hơn."""
    with _lake() as (raw, kho_bronze, _):
        kho_bronze.put("fragrantica/perfumes/a.jsonl", (BAN_GHI * 9).encode())
        path = _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        assert lake.ensure_local(raw / "fragrantica") == 1
        assert path.read_text(encoding="utf-8") == BAN_GHI * 9


def test_bang_nhau_thi_khong_tai_lai():
    with _lake() as (raw, kho_bronze, _):
        kho_bronze.put("fragrantica/perfumes/a.jsonl", BAN_GHI.encode())
        _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        assert lake.ensure_local(raw / "fragrantica") == 0


def test_chi_list_mot_lan_moi_tien_trinh():
    """`analyze` gọi `bronze.scan` ba lần cho ba loại bản ghi. Không có bộ nhớ
    này thì mỗi lần đọc là một vòng mạng cho cùng một câu hỏi."""
    with _lake() as (raw, _kho, _):
        dem = _DemList(lake.store(lake.BRONZE))
        lake.store = lambda layer=lake.BRONZE: dem
        for _ in range(3):
            lake.ensure_local(raw / "fragrantica")
        assert dem.so_lan_list == 1, f"list {dem.so_lan_list} lần thay vì 1"


def test_bo_nho_dong_bo_tach_theo_tang():
    """Prefix của gốc mỗi tầng đều là chuỗi rỗng. Thiếu tiền tố tầng trong khoá
    bộ nhớ thì đồng bộ bronze xong sẽ làm silver tưởng cũng đã xong."""
    with _lake() as (_raw, kho_bronze, kho_silver):
        kho_bronze.put("a.jsonl", BAN_GHI.encode())
        kho_silver.put("perfumes.parquet", b"PAR1")
        assert lake.ensure_local(config.RAW_DIR, lake.BRONZE) == 1
        assert lake.ensure_local(config.SILVER_DIR, lake.SILVER) == 1, \
            "tầng silver bị coi như đã đồng bộ vì bronze vừa đồng bộ xong"


# -------------------------------------------------------- mất MinIO giữa mẻ
def test_mat_minio_khong_giet_me_crawl():
    """Niêm thất bại phải là một dòng cảnh báo, không phải một exception.

    Hàm này được gọi ở cuối mỗi hãng, giữa một mẻ crawl nhiều ngày. Hạ tầng chết
    thì dữ liệu vẫn còn nguyên trong spool và niêm lại được sau; để nó ném lỗi
    ra ngoài là biến một sự cố MinIO thành mất cả lượt crawl.
    """
    with _lake(kho=_KhoChet()) as (raw, _kho, _):
        path = _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        assert lake.seal(path) is None
        assert lake.seal_dir(raw) == (0, 0)


def test_mat_minio_van_doc_duoc_cache():
    """`analyze` phải chạy được trên phần đang có trong cache khi mất lake."""
    with _lake(kho=_KhoChet()) as (raw, _kho, _):
        _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        assert lake.ensure_local(raw / "fragrantica") == 0
        got = bronze.scan(raw / "fragrantica", bronze.PERFUME)
        assert len(got.records) == 1, "mất lake là mất luôn dữ liệu cache"


# ------------------------------------------------------------- tích hợp
def test_bronze_scan_tu_keo_lake_ve():
    """`bronze.scan` là chỗ DUY NHẤT đọc kho thô, nên nó phải là chỗ kéo về.

    Đây là bài kiểm chứng luật trong `docs/ARCHITECTURE.md` thật sự đúng: không
    lệnh nào phải tự nhớ gọi `lake pull` trước khi đọc.
    """
    with _lake() as (raw, kho_bronze, _):
        kho_bronze.put("fragrantica/perfumes/a.jsonl", BAN_GHI.encode())
        got = bronze.scan(raw / "fragrantica", bronze.PERFUME)
        assert len(got.records) == 1, \
            "scan không kéo lake về — dữ liệu trên MinIO vô hình với analyze"
        assert got.records[0]["name"] == "A"


def test_available_hoi_ca_lake_khong_chi_o_dia():
    """`bronze.available` phải trả lời "có dữ liệu" khi lake có, dù đĩa trống.

    Lỗi đã dính thật khi thử trên một máy sạch: mọi lệnh đọc kiểm
    `community.exists()` TRƯỚC khi tới `scan()`, nên trên container vừa dựng nó
    báo "Chưa có dữ liệu" rồi thoát — trong khi 125 file đang nằm trên MinIO.
    """
    with _lake() as (raw, kho_bronze, _):
        kho_bronze.put("fragrantica/perfumes/a.jsonl", BAN_GHI.encode())
        thu_muc = raw / "fragrantica"
        assert not thu_muc.exists(), "fixture hỏng: thư mục phải chưa tồn tại"
        assert bronze.available(thu_muc) is True, \
            "có dữ liệu trên lake mà vẫn báo là chưa có"


def test_available_tra_ve_false_khi_that_su_khong_co_gi():
    """Mặt còn lại: thông báo "chưa có dữ liệu" vẫn phải đúng khi đúng là chưa có.

    Sửa `available` thành luôn True thì bài trên vẫn xanh, và người dùng nhận một
    traceback thay vì một câu tiếng Việt dễ hiểu.
    """
    with _lake() as (raw, _kho, _):
        assert bronze.available(raw / "fragrantica") is False


def test_available_hoi_duoc_ca_mot_file_le():
    """`make products` cần đúng MỘT file danh mục hãng, không phải cả thư mục.

    Lake làm việc theo prefix nên hỏi về một file phải quy về thư mục chứa nó.
    """
    with _lake() as (raw, kho_bronze, _):
        kho_bronze.put("fragrantica/brands/brands_f_021026.jsonl", BAN_GHI.encode())
        file_le = raw / "fragrantica" / "brands" / "brands_f_021026.jsonl"
        assert bronze.available(file_le) is True


def test_khong_lenh_nao_kiem_du_lieu_bang_exists():
    """Canh chính LUẬT, không chỉ canh một lần sửa.

    Bài trên chứng minh `available` làm đúng việc; bài này chứng minh không ai đi
    vòng qua nó. Thiếu nó thì lệnh thứ 12 thêm vào sau này lại dùng
    `Path.exists()` và lỗi "máy mới báo chưa có dữ liệu" quay lại y nguyên — mà
    nó không làm đỏ bất cứ test nào khác.

    Chỉ soi các biến TRỎ VÀO KHO THÔ. `data/inputs/` là file người dùng tự bỏ vào
    máy, không nằm trên lake, nên `exists()` ở đó là đúng.
    """
    import re
    from pathlib import Path as P
    goc = P(__file__).resolve().parent.parent / "perfume_intel" / "cli"
    bien_kho_tho = r"(community|folder|scan|seed|from_brands)"
    vi_pham = []
    for path in sorted(goc.glob("*.py")):
        for so, dong in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(rf"{bien_kho_tho}[a-z_]*\)?\.exists\(\)", dong):
                vi_pham.append(f"{path.name}:{so}: {dong.strip()}")
    assert not vi_pham, (
        "kiểm dữ liệu bằng Path.exists() — trên máy mới nó báo 'chưa có dữ "
        "liệu' trong khi lake có đủ. Dùng bronze.available():\n  "
        + "\n  ".join(vi_pham))


def test_status_bao_duoc_ca_hai_ben():
    with _lake() as (raw, kho_bronze, _):
        _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        kho_bronze.put("fragrantica/perfumes/b.jsonl", (BAN_GHI * 2).encode())
        st = lake.status(lake.BRONZE)
        assert st["enabled"] is True
        assert st["local_files"] == 1
        assert st["lake_files"] == 1
        assert st["error"] is None


def test_status_khong_vo_khi_mat_lake():
    with _lake(kho=_KhoChet()) as (raw, _kho, _):
        _ghi(raw / "fragrantica" / "perfumes" / "a.jsonl", BAN_GHI)
        st = lake.status(lake.BRONZE)
        assert st["error"], "mất lake mà status không nói gì"
        assert st["local_files"] == 1


def test_tang_la_thi_bao_loi():
    """Gõ sai tên tầng phải vỡ ngay, không được âm thầm dùng bronze."""
    for ham in (lake.store, lake.root_of):
        try:
            ham("golden")
        except ValueError:
            continue
        raise AssertionError(f"{ham.__name__} nhận tầng không có")


if __name__ == "__main__":
    raise SystemExit(run(globals()))
