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
NAM = "https://namperfume.net/products/dior-sauvage-edp"


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


def workspace(tmp: Path):
    """Một thư mục data/raw nhỏ: 2 chai Fragrantica, 1 chai có bán ở namperfume."""
    write_jsonl(tmp / "community" / "Dior_fragrantica_010926.jsonl", [
        raw(FRAG, "Sauvage", "Dior", 9000, 4.1,
            accords=[("fresh spicy", 100.0), ("citrus", 70.0)],
            seasons=[("summer", 100.0), ("winter", 40.0)],
            gender="Nam", des_url=NAM),
        raw(FRAG + "?x=2", "Fahrenheit", "Dior", 30, 4.9,
            accords=[("citrus", 50.0)], gender="Nam"),
    ])
    write_jsonl(tmp / "market" / "Dior_namperfume_010926.jsonl", [
        {"url": NAM, "name": "Dior Sauvage EDP", "price": "3.200.000",
         "standard_size": ["60ml", "100ml"]},
    ])
    return tmp / "community", tmp / "market"


def test_nap_va_lam_phang():
    with tempfile.TemporaryDirectory() as td:
        community, market = workspace(Path(td))
        rows = dataset.build(community, market)

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


def test_ghep_gia_namperfume():
    with tempfile.TemporaryDirectory() as td:
        community, market = workspace(Path(td))
        rows = dataset.build(community, market)

    sauvage = next(r for r in rows if r.name == "Sauvage")
    fahrenheit = next(r for r in rows if r.name == "Fahrenheit")
    assert sauvage.listed and sauvage.price == "3.200.000"
    assert sauvage.sizes == ["60ml", "100ml"]
    assert not fahrenheit.listed


def test_khong_co_du_lieu_thi_truong():
    """Không truyền `market` thì vẫn chạy, chỉ là mọi chai đều chưa có giá."""
    with tempfile.TemporaryDirectory() as td:
        community, _ = workspace(Path(td))
        rows = dataset.build(community)

    assert rows and not any(r.listed for r in rows)


def test_rating_co_hieu_chinh_theo_vote():
    """Nhóm 4.9 sao / 30 vote không được xếp trên nhóm 4.1 sao / 9.000 vote."""
    tap = [Row(url="a", brand="To", rating=4.1, rating_count=9000),
           Row(url="b", brand="Nho", rating=4.9, rating_count=30)]
    out = {r["brand"]: r for r in metrics.by_brand(tap)}

    # Điểm thô: hãng nhỏ hơn hẳn 0.8 sao.
    raw_gap = out["Nho"]["rating_avg"] - out["To"]["rating_avg"]
    assert round(raw_gap, 2) == 0.8
    # Sau hiệu chỉnh khoảng cách gần như biến mất: 30 vote là bằng chứng quá
    # mỏng, chỉ đủ nhấc nhóm đó lên trên mốc chung một chút.
    weighted_gap = out["Nho"]["rating_weighted"] - out["To"]["rating_weighted"]
    assert 0 < weighted_gap < raw_gap * 0.15


def test_hieu_chinh_khong_dung_khi_du_vote():
    """Nhóm nhiều vote thì điểm hiệu chỉnh gần như giữ nguyên điểm thô."""
    tap = [Row(url="a", brand="To", rating=4.6, rating_count=50_000),
           Row(url="b", brand="Khac", rating=3.5, rating_count=40_000)]
    out = {r["brand"]: r for r in metrics.by_brand(tap)}

    assert abs(out["To"]["rating_weighted"] - 4.6) < 0.02


def test_chi_so_theo_hang():
    rows = [
        Row(url="a", brand="Dior", rating=4.1, rating_count=9000, listed=True),
        Row(url="b", brand="Dior", rating=4.9, rating_count=1000),
        Row(url="c", brand="Chanel", rating=4.5, rating_count=500),
    ]
    out = {r["brand"]: r for r in metrics.by_brand(rows)}

    assert out["Dior"]["perfumes"] == 2
    assert out["Dior"]["rating_votes"] == 10_000
    assert out["Dior"]["listed_on_market"] == 1
    # Thị phần chú ý cộng lại phải đủ 100%.
    assert round(sum(r["attention_share_pct"] for r in out.values())) == 100


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


def test_khoang_trong_thi_truong():
    """Chai nhiều vote mà chưa bán phải đứng đầu danh sách."""
    rows = [
        Row(url="a", name="Chua ban", rating_count=9000),
        Row(url="b", name="Da ban", rating_count=8000, listed=True),
        Row(url="c", name="It ai biet", rating_count=5),
    ]
    gap = metrics.market_gap(rows)

    assert [r["name"] for r in gap] == ["Chua ban", "It ai biet"]


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
