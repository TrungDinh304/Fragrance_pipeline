"""Test tầng phân tích: nạp dữ liệu, tính chỉ số, xuất báo cáo.

    python tests/test_analytics.py
"""

import json
import tempfile
from pathlib import Path

from runner import run  # noqa: E402  (dat sys.path)

from perfume_intel.analytics import dataset, metrics, report  # noqa: E402
from perfume_intel.analytics.dataset import Row  # noqa: E402

FRAG = "https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html"


def raw(url, name, brand, votes, rating, accords=(), seasons=(), **extra):
    """Một dòng JSONL giống hệt bản ghi crawl thật."""
    return {
        "url": url, "name": name, "brand": brand,
        "rating": rating, "rating_count": votes,
        "accords": [{"name": n, "width": w} for n, w in accords],
        "when_to_wear": [{"name": n, "percent": p} for n, p in seasons],
        "scraped_at": "2026-09-01T00:00:00+00:00",
        **extra,
    }


def write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def workspace(tmp: Path) -> Path:
    """Một thư mục data/raw nhỏ: 2 chai Fragrantica."""
    write_jsonl(tmp / "community" / "Dior_fragrantica_010926.jsonl", [
        raw(FRAG, "Sauvage", "Dior", 9000, 4.1,
            accords=[("fresh spicy", 100.0), ("citrus", 70.0)],
            seasons=[("summer", 100.0), ("winter", 40.0)],
            gender="Nam"),
        raw(FRAG + "?x=2", "Fahrenheit", "Dior", 30, 4.9,
            accords=[("citrus", 50.0)], gender="Nam"),
    ])
    return tmp / "community"


def test_nap_va_lam_phang():
    with tempfile.TemporaryDirectory() as td:
        rows = dataset.build(workspace(Path(td)))

    row = next(r for r in rows if r.name == "Sauvage")
    assert row.top_accord == "fresh spicy"
    assert row.top_season == "summer"
    assert row.accords["citrus"] == 70.0


def test_giu_ban_crawl_moi_nhat():
    """Cùng một URL nằm ở nhiều file ngày khác nhau -> chỉ giữ bản mới nhất."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        write_jsonl(tmp / "a_fragrantica_010926.jsonl",
                    [dict(raw(FRAG, "Sauvage", "Dior", 10, 4.0),
                          scraped_at="2026-09-01T00:00:00+00:00")])
        write_jsonl(tmp / "b_fragrantica_020926.jsonl",
                    [dict(raw(FRAG, "Sauvage", "Dior", 99, 4.4),
                          scraped_at="2026-09-02T00:00:00+00:00")])
        rows = dataset.build(tmp)

    assert len(rows) == 1 and rows[0].rating_count == 99


def test_chi_so_theo_accord():
    rows = [
        Row(url="a", accords={"citrus": 100.0, "woody": 50.0}, rating=4.0,
            rating_count=100),
        Row(url="b", accords={"citrus": 60.0}, rating=4.5, rating_count=200),
    ]
    out = {r["accord"]: r for r in metrics.by_accord(rows)}

    assert out["citrus"]["perfumes"] == 2
    assert out["citrus"]["coverage_pct"] == 100.0
    assert out["citrus"]["avg_strength"] == 80.0
    assert out["woody"]["perfumes"] == 1


def test_bao_cao_ghi_ra_file():
    rows = [Row(url="a", brand="Dior", gender="Nam", rating=4.1,
                rating_count=9000, accords={"citrus": 90.0},
                seasons={"summer": 100.0})]
    with tempfile.TemporaryDirectory() as td:
        out_dir = report.run(rows, Path(td) / "out")
        files = sorted(p.name for p in out_dir.iterdir())
        summary = json.loads((out_dir / "summary.json").read_text("utf-8"))

    assert "brand.csv" in files and "summary.json" in files
    assert summary["overview"]["perfumes"] == 1
    # Chưa crawl được own/had/want -> phải báo 0 chứ không âm thầm bỏ qua.
    assert summary["overview"]["with_ownership"] == 0


def test_bao_loi_chi_so_khong_co():
    try:
        report.run([Row(url="a")], only=["khong_ton_tai"])
    except ValueError as exc:
        assert "khong_ton_tai" in str(exc)
    else:
        raise AssertionError("phải báo lỗi khi tên chỉ số sai")


if __name__ == "__main__":
    raise SystemExit(run(globals()))
