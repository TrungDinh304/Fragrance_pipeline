"""Test ghép dữ liệu đã crawl với danh sách bản mini (chạy offline).

    python tests\\test_mini.py
hoặc  python -m pytest tests/ -v
"""

import json
import sys
import tempfile
from pathlib import Path

from runner import run  # noqa: E402  (dat sys.path)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from perfume_intel.core import storage  # noqa: E402
from perfume_intel.pipelines import mini  # noqa: E402
from perfume_intel.sources.fragrantica.models import (  # noqa: E402
    CSV_COLUMNS, Perfume)
from perfume_intel.core.csv_input import read_url_pairs  # noqa: E402
from perfume_intel.core.text import url_key  # noqa: E402

FIERCE = "https://www.fragrantica.com/perfume/Abercrombie-Fitch/Fierce-3508.html"
GUILTY = "https://www.fragrantica.com/perfume/Gucci/Gucci-Guilty-9677.html"
CHUA_CRAWL = "https://www.fragrantica.com/perfume/Versace/Yellow-Diamond-1.html"


def record(url, name, des_url=None, scraped_at="2026-08-15T00:00:00+00:00",
           **extra):
    return {"url": url, "des_url": des_url, "name": name,
            "accords": [{"name": "woody", "width": 100.0, "opacity": 90.0,
                         "color": "#774414"}],
            "when_to_wear": [{"name": "day", "percent": 100.0, "votes": 10,
                              "votes_label": "10"}],
            "scraped_at": scraped_at, **extra}


def workspace(tmp: Path, csv_rows, files):
    """Dựng một thư mục làm việc giả: 1 file CSV + các file .jsonl đã crawl."""
    csv_path = tmp / "Mini.csv"
    csv_path.write_text(
        "source_url,des_url\n" + "".join(f"{s},{d}\n" for s, d in csv_rows),
        encoding="utf-8")
    scan = tmp / "scan"
    scan.mkdir()
    for name, records in files.items():
        (scan / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8")
    return csv_path, scan


def ghep(tmp, csv_rows, files):
    csv_path, scan = workspace(tmp, csv_rows, files)
    pairs = read_url_pairs(csv_path, prefer_host="fragrantica.com",
                               unique=False)
    return mini.collect(pairs, mini.index_records(scan))


def test_thay_des_url():
    """Bản ghi lấy nguyên thông tin đã crawl, chỉ des_url là của bản mini."""
    with tempfile.TemporaryDirectory() as tmp:
        records, missing = ghep(
            Path(tmp),
            [(FIERCE, "https://yupi.vn/products/fierce-mini")],
            {"a.jsonl": [record(FIERCE, "Fierce",
                                "https://yupi.vn/products/fierce-full")]})
    assert missing == []
    assert len(records) == 1
    assert records[0]["name"] == "Fierce"
    assert records[0]["des_url"] == "https://yupi.vn/products/fierce-mini"


def test_lay_ban_moi_nhat():
    """Cùng URL nằm ở nhiều file theo ngày -> lấy bản scraped_at mới nhất."""
    with tempfile.TemporaryDirectory() as tmp:
        records, _ = ghep(
            Path(tmp),
            [(FIERCE, "https://yupi.vn/products/fierce-mini")],
            {"a_150826.jsonl": [record(FIERCE, "Fierce cũ", rating=4.0)],
             "a_170826.jsonl": [record(FIERCE, "Fierce mới", rating=4.25,
                                       scraped_at="2026-08-17T00:00:00+00:00")]})
    assert records[0]["name"] == "Fierce mới"
    assert records[0]["rating"] == 4.25


def test_mot_nguon_nhieu_ban_mini():
    """Một chai full có 2 bản mini -> ra 2 bản ghi, khác nhau ở des_url."""
    with tempfile.TemporaryDirectory() as tmp:
        records, _ = ghep(
            Path(tmp),
            [(GUILTY, "https://yupi.vn/products/guilty-for-women-mini"),
             (GUILTY, "https://yupi.vn/products/guilty-pour-femme-mini")],
            {"g.jsonl": [record(GUILTY, "Gucci Guilty")]})
    assert [r["des_url"] for r in records] == [
        "https://yupi.vn/products/guilty-for-women-mini",
        "https://yupi.vn/products/guilty-pour-femme-mini"]


def test_bo_dong_trung_hoan_toan():
    """Trùng cả source_url lẫn des_url thì chỉ lấy một."""
    with tempfile.TemporaryDirectory() as tmp:
        records, _ = ghep(
            Path(tmp),
            [(FIERCE, "https://yupi.vn/products/fierce-mini"),
             (FIERCE, "https://yupi.vn/products/fierce-mini")],
            {"a.jsonl": [record(FIERCE, "Fierce")]})
    assert len(records) == 1


def test_bao_url_chua_crawl():
    """URL trong CSV mà chưa crawl thì báo ra, không bịa bản ghi rỗng."""
    with tempfile.TemporaryDirectory() as tmp:
        records, missing = ghep(
            Path(tmp),
            [(FIERCE, "https://yupi.vn/products/fierce-mini"),
             (CHUA_CRAWL, "https://yupi.vn/products/yellow-diamond-mini")],
            {"a.jsonl": [record(FIERCE, "Fierce")]})
    assert len(records) == 1
    assert missing == [CHUA_CRAWL]


def test_khop_du_lech_dinh_dang_url():
    """Khác dấu / cuối hay hoa thường vẫn phải khớp."""
    assert url_key(FIERCE + "/") == url_key(FIERCE.upper())
    with tempfile.TemporaryDirectory() as tmp:
        records, missing = ghep(
            Path(tmp),
            [(FIERCE + "/", "https://yupi.vn/products/fierce-mini")],
            {"a.jsonl": [record(FIERCE, "Fierce")]})
    assert missing == [] and len(records) == 1


def test_ghi_ra_jsonl_va_csv():
    """Bản ghi ghép được phải dựng lại thành Perfume để xuất JSONL/CSV."""
    with tempfile.TemporaryDirectory() as tmp:
        records, _ = ghep(
            Path(tmp),
            [(FIERCE, "https://yupi.vn/products/fierce-mini")],
            {"a.jsonl": [record(FIERCE, "Fierce", brand="Abercrombie & Fitch")]})
        perfumes = [Perfume.from_dict(r) for r in records]
        assert perfumes[0].accords[0].name == "woody"
        assert perfumes[0].when_to_wear[0].votes == 10

        out = Path(tmp) / "mini"
        storage.save_jsonl(perfumes, out.with_suffix(".jsonl"))
        storage.save_csv(perfumes, out.with_suffix(".csv"),
                         columns=CSV_COLUMNS)
        lai = storage.load_records(out.with_suffix(".jsonl"), Perfume)
    assert len(lai) == 1
    assert lai[0].des_url == "https://yupi.vn/products/fierce-mini"
    assert lai[0].brand == "Abercrombie & Fitch"


def test_bo_qua_khoa_la():
    """File JSONL cũ có trường lạ vẫn đọc được thay vì văng TypeError."""
    p = Perfume.from_dict(
        {"url": FIERCE, "name": "Fierce", "truong_la": 1})
    assert p.name == "Fierce" and p.accords == []


if __name__ == "__main__":
    raise SystemExit(run(globals()))
