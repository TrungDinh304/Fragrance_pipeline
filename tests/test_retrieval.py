"""Test tầng truy xuất: hợp đồng `Retriever` + vài điểm riêng của bản in-memory.

    python tests/test_retrieval.py

Phần lõi nằm ở `retrieval_contract.py` — đó là định nghĩa của ranh giới. File
này chỉ nối bản cài đặt hiện có vào bộ hợp đồng đó, cộng vài test cho những thứ
chỉ bản in-memory mới có.
"""

from runner import run  # noqa: E402  (đặt sys.path)

import retrieval_contract as contract  # noqa: E402

from perfume_intel.retrieval import InMemoryRetriever, Query  # noqa: E402
from perfume_intel.retrieval import ports  # noqa: E402


def make(rows):
    return InMemoryRetriever.from_rows(rows)


def test_in_memory_qua_duoc_hop_dong():
    """Một test, nhưng nó là 20 điều khoản — xem retrieval_contract.CHECKS."""
    contract.check_all(make)


def test_hop_dong_co_du_dieu_khoan():
    """Chống việc hợp đồng bị rỗng ruột mà không ai nhận ra."""
    assert len(contract.CHECKS) >= 15, \
        f"hợp đồng chỉ còn {len(contract.CHECKS)} điều khoản"


def test_ten_mo_ho_thi_bao_cac_lua_chon_khac():
    r = make(contract.rows())
    res = r.search(Query(like_perfume="Oud", limit=3))
    assert res.ambiguous, "tên khớp nhiều chai mà không báo lựa chọn nào khác"


def test_tu_vung_accord_lay_tu_du_lieu_that():
    r = make(contract.rows())
    accords = list(r.vocabulary(ports.ACCORD))
    assert "woody" in accords and "citrus" in accords


def test_phia_tren_khong_import_thang_vao_vectors():
    """Ranh giới chỉ có giá trị khi không ai đi vòng qua nó."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "perfume_intel"
    pham = []
    for path in list((root / "cli").glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "vectors" in src and "retrieval" not in src:
            pham.append(path.name)
    assert not pham, f"các file này còn với thẳng vào vectors/: {pham}"


if __name__ == "__main__":
    raise SystemExit(run(globals()))
