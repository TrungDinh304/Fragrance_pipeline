"""Kho đối tượng: hợp đồng chạy cho CẢ hai adapter, cộng vài bài riêng.

Bài quan trọng nhất ở đây không phải một `assert` nào cả, mà là việc cùng một bộ
`objectstore_contract` chạy cho `LocalStore` và `S3Store`. Hai adapter lệch nhau
là loại lỗi không bao giờ hiện ra trên máy dev — nó chờ tới sau khi đã chuyển
bronze lên MinIO.

Phần S3 cần một MinIO đang chạy. Không có thì BỎ QUA kèm lý do, chứ không báo
lỗi: `make test` phải xanh được khi không có hạ tầng, nếu không người ta sẽ học
cách phớt lờ màu đỏ.

    docker compose up -d minio
    LAKE=s3 S3_ENDPOINT=http://localhost:9000 \
      S3_ACCESS_KEY=minioadmin S3_SECRET_KEY=minioadmin python tests/test_objects.py
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from runner import run  # noqa: E402

import objectstore_contract as contract  # noqa: E402
from perfume_intel.core.objects import (LocalStore, ObjectInfo, ObjectNotFound,
                                        S3Store, check_key, copy_tree,
                                        total_size)


def _skip(ly_do: str) -> bool:
    print(f"SKIP  {ly_do}")
    return True


# --------------------------------------------------------------- hợp đồng
def test_hop_dong_local_store():
    with tempfile.TemporaryDirectory() as td:
        dem = [0]

        def make():
            dem[0] += 1
            goc = Path(td) / f"kho{dem[0]}"
            goc.mkdir()
            return LocalStore(goc)

        contract.check_all(make)


def _minio_san_sang() -> S3Store | None:
    """Một S3Store trỏ vào bucket tạm, hoặc None nếu không có MinIO."""
    if not os.environ.get("S3_ENDPOINT"):
        return None
    kho = S3Store(bucket=f"test-{uuid.uuid4().hex[:12]}",
                  endpoint=os.environ["S3_ENDPOINT"],
                  access_key=os.environ.get("S3_ACCESS_KEY"),
                  secret_key=os.environ.get("S3_SECRET_KEY"))
    try:
        kho.ensure_bucket()
    except Exception as exc:                       # noqa: BLE001
        print(f"      (không nối được MinIO: {exc})")
        return None
    return kho


def test_hop_dong_s3_store():
    mau = _minio_san_sang()
    if mau is None and _skip("test_hop_dong_s3_store: chưa có MinIO "
                             "(đặt S3_ENDPOINT để bật)"):
        return

    tao: list[S3Store] = []

    def make():
        kho = S3Store(bucket=f"test-{uuid.uuid4().hex[:12]}",
                      endpoint=mau.endpoint, access_key=mau._access_key,
                      secret_key=mau._secret_key)
        kho.ensure_bucket()
        tao.append(kho)
        return kho

    try:
        contract.check_all(make)
    finally:
        for kho in [mau, *tao]:
            for info in kho.list():
                kho.delete(info.key)


# ------------------------------------------------------------ bài riêng lẻ
def test_khoa_sai_dang_bi_tu_choi_truoc_khi_cham_kho():
    """`check_key` phải chặn ĐỘC LẬP với kho, vì nó là luật chung của cổng.

    Nếu mỗi adapter tự kiểm thì sớm muộn có một adapter kiểm lỏng hơn, và cùng
    một khoá được nhận ở kho này nhưng bị từ chối ở kho kia.
    """
    for xau in ("/a", "a//b", "../a", "a/./b", "a/..", ""):
        try:
            check_key(xau)
        except ValueError:
            continue
        raise AssertionError(f"check_key nhận khoá sai dạng: {xau!r}")
    assert check_key("a\\b\\c.jsonl") == "a/b/c.jsonl"


def test_khoa_qua_dai_bi_tu_choi():
    """Giới hạn của S3 là 1024 byte. Phải chặn ở cổng, không chờ S3 chặn.

    Chặn muộn nghĩa là `LocalStore` nhận (NTFS cho phép đường dẫn dài hơn) rồi
    `S3Store` từ chối — tức là hai adapter hết tương đương, đúng thứ hợp đồng
    sinh ra để ngăn.
    """
    try:
        check_key("x" * 1025)
    except ValueError:
        return
    raise AssertionError("nhận khoá dài hơn 1024 byte")


def test_ghi_do_dang_khong_de_lai_file_nua_voi():
    """LocalStore ghi tạm rồi đổi tên, nên `list` không bao giờ thấy file tạm.

    Nếu `list` thấy file tạm thì phía đọc sẽ parse một JSONL bị cắt giữa dòng —
    và `read_jsonl` bỏ qua dòng hỏng trong im lặng, nên mất dữ liệu mà không có
    một dòng log nào.
    """
    with tempfile.TemporaryDirectory() as td:
        kho = LocalStore(Path(td))
        kho.put("a.jsonl", b"x" * 10)
        (Path(td) / "a.jsonl.__tmp999").write_bytes(b"nua voi")
        assert [i.key for i in kho.list()] == ["a.jsonl"]


def test_sao_ca_kho_sang_kho_khac():
    """Phép di trú: local -> MinIO dùng đúng hàm này, nên nó phải giữ đủ khoá."""
    with tempfile.TemporaryDirectory() as td:
        nguon, dich = LocalStore(Path(td) / "a"), LocalStore(Path(td) / "b")
        nguon.put("fragrantica/perfumes/x.jsonl", b"1")
        nguon.put("fragrantica/brands/y.jsonl", b"22")
        assert copy_tree(nguon, dich) == 2
        assert [i.key for i in dich.list()] == [i.key for i in nguon.list()]
        assert total_size(dich.list()) == 3


def test_doc_thu_muc_khong_phai_object_thi_bao_khong_co():
    """`get` vào một thư mục phải ra ObjectNotFound, không phải IsADirectoryError.

    Trên S3 thì `fragrantica/` không tồn tại như một object; bản local phải nói
    cùng một câu, nếu không phía gọi phải bắt hai loại lỗi khác nhau tuỳ kho.
    """
    with tempfile.TemporaryDirectory() as td:
        kho = LocalStore(Path(td))
        kho.put("fragrantica/a.jsonl", b"1")
        assert kho.stat("fragrantica") is None
        try:
            kho.get("fragrantica")
        except ObjectNotFound:
            return
        raise AssertionError("đọc một thư mục mà không báo ObjectNotFound")


def test_object_info_la_bat_bien():
    """Bất biến để phía trên không sửa được số liệu rồi tưởng là của kho."""
    info = ObjectInfo(key="a", size=1, modified=2.0)
    try:
        info.size = 999                            # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ObjectInfo sửa được")


if __name__ == "__main__":
    raise SystemExit(run(globals()))
