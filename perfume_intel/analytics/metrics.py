"""Chỉ số thị trường tính từ tín hiệu cộng đồng.

Mỗi chỉ số là một hàm thuần: `list[Row]` -> `list[dict]` (một dòng một nhóm),
không đọc file, không in ra màn hình. Nhờ vậy test được bằng vài Row dựng tay,
và `report.py` chỉ việc ghép kết quả lại rồi ghi ra file.

Thêm chỉ số mới = thêm một hàm rồi đăng ký vào `METRICS` ở cuối file.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Iterable

from .dataset import Row

Metric = Callable[[list[Row]], list[dict[str, Any]]]


# --------------------------------------------------------------------- helpers
def _mean(values: Iterable[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    return round(sum(nums) / len(nums), 3) if nums else None


def _global_mean(rows: list[Row]) -> float | None:
    """Mốc chung của cả tập để kéo các nhóm ít vote về.

    Tính có trọng số theo số vote: nếu lấy trung bình thô thì vài chai 5 sao /
    20 vote sẽ tự tay kéo chính cái mốc lên, làm việc hiệu chỉnh mất tác dụng.
    """
    rated = [(r.rating, r.rating_count or 0) for r in rows if r.rating]
    if not rated:
        return None
    votes = sum(c for _, c in rated)
    if votes == 0:
        return round(sum(v for v, _ in rated) / len(rated), 3)
    return round(sum(v * c for v, c in rated) / votes, 3)


def _weighted_rating(rows: list[Row], overall: float | None,
                     prior_votes: int = 500) -> float | None:
    """Điểm trung bình có hiệu chỉnh theo số vote (kiểu Bayesian).

    Một chai 5.0 sao với 3 vote không nói lên điều gì về thị trường, trong khi
    4.2 sao với 8.000 vote thì có. Nhóm càng ít vote càng bị kéo về `overall`
    (trung bình toàn tập), nên so sánh giữa các hãng mới công bằng.

    `prior_votes` là "số vote ảo" của mốc chung: nhóm phải có nhiều vote hơn
    chừng đó thì điểm riêng của nó mới lấn át được mốc.
    """
    rated = [(r.rating, r.rating_count or 0) for r in rows if r.rating]
    if not rated:
        return None
    if overall is None:
        overall = sum(v for v, _ in rated) / len(rated)

    total_votes = sum(c for _, c in rated)
    if total_votes == 0:
        return round(overall, 3)
    weighted = sum(v * c for v, c in rated) / total_votes
    share = total_votes / (total_votes + prior_votes)
    return round(share * weighted + (1 - share) * overall, 3)


def _group(rows: list[Row], key: Callable[[Row], Any]) -> dict[Any, list[Row]]:
    buckets: dict[Any, list[Row]] = defaultdict(list)
    for row in rows:
        value = key(row)
        if value:
            buckets[value].append(row)
    return dict(buckets)


def _sorted_rows(records: list[dict[str, Any]], by: str) -> list[dict[str, Any]]:
    return sorted(records, key=lambda r: r.get(by) or 0, reverse=True)


# --------------------------------------------------------------------- metrics
def by_brand(rows: list[Row]) -> list[dict[str, Any]]:
    """Quy mô và mức độ được chú ý của từng hãng."""
    total_votes = sum(r.rating_count or 0 for r in rows) or 1
    overall = _global_mean(rows)
    out = []
    for brand, group in _group(rows, lambda r: r.brand).items():
        votes = sum(r.rating_count or 0 for r in group)
        out.append({
            "brand": brand,
            "perfumes": len(group),
            "rating_votes": votes,
            # Thị phần chú ý: hãng này chiếm bao nhiêu % tổng lượt vote.
            "attention_share_pct": round(100 * votes / total_votes, 2),
            "rating_avg": _mean(r.rating for r in group),
            "rating_weighted": _weighted_rating(group, overall),
        })
    return _sorted_rows(out, "rating_votes")


def by_accord(rows: list[Row]) -> list[dict[str, Any]]:
    """Accord nào đang phủ rộng thị trường và được đánh giá cao."""
    buckets: dict[str, list[Row]] = defaultdict(list)
    strength: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for accord, width in row.accords.items():
            buckets[accord].append(row)
            strength[accord].append(width)

    overall = _global_mean(rows)
    out = []
    for accord, group in buckets.items():
        out.append({
            "accord": accord,
            "perfumes": len(group),
            "coverage_pct": round(100 * len(group) / len(rows), 2) if rows else None,
            "avg_strength": _mean(strength[accord]),
            "rating_weighted": _weighted_rating(group, overall),
            "rating_votes": sum(r.rating_count or 0 for r in group),
        })
    return _sorted_rows(out, "perfumes")


def by_gender(rows: list[Row]) -> list[dict[str, Any]]:
    """Cơ cấu Nam / Nữ / Unisex."""
    overall = _global_mean(rows)
    out = []
    for gender, group in _group(rows, lambda r: r.gender).items():
        out.append({
            "gender": gender,
            "perfumes": len(group),
            "share_pct": round(100 * len(group) / len(rows), 2) if rows else None,
            "rating_weighted": _weighted_rating(group, overall),
            "rating_votes": sum(r.rating_count or 0 for r in group),
        })
    return _sorted_rows(out, "perfumes")


def by_season(rows: list[Row]) -> list[dict[str, Any]]:
    """Mùa nào đang thiếu/thừa hàng — tính trên chai có dữ liệu when-to-wear."""
    voted = [r for r in rows if r.seasons]
    buckets: dict[str, list[Row]] = defaultdict(list)
    for row in voted:
        top = row.top_season
        if top:
            buckets[top].append(row)

    overall = _global_mean(voted)
    out = []
    for season, group in buckets.items():
        out.append({
            "season": season,
            "perfumes": len(group),
            "share_pct": round(100 * len(group) / len(voted), 2) if voted else None,
            "rating_weighted": _weighted_rating(group, overall),
        })
    return _sorted_rows(out, "perfumes")


def coverage(rows: list[Row], catalog) -> list[dict[str, Any]]:
    """Phân tích mỗi hãng đang dựa trên bao nhiêu phần của hãng đó.

    KHÔNG nằm trong `METRICS` vì nó cần tham số thứ hai (danh mục hãng), còn mọi
    chỉ số khác là `list[Row] -> list[dict]` thuần. Nhét nó vào đây thì phải đổi
    chữ ký của cả nhóm chỉ để chiều một trường hợp.

    Đây cũng là lý do thật sự để tách entity: `catalog` chính là phần dữ liệu
    trước đây bị loader bỏ im lặng. Thiếu nó thì "Avon điểm 4,1" nghe như kết
    luận về Avon, trong khi thực tế đó là kết luận về 12/1.379 chai của Avon.
    """
    by_brand = _group(rows, lambda r: r.brand)
    names = set(by_brand)
    for key, items in catalog.products.items():
        for item in items:
            if item.get("brand_name"):
                names.add(item["brand_name"])
            break

    out = []
    for name in names:
        group = by_brand.get(name, [])
        total = catalog.total_for(catalog.url_of(name))
        done = len(group)
        out.append({
            "brand": name,
            "catalog_perfumes": total or None,
            "detailed": done,
            # None (không phải 0) khi chưa crawl mục lục: "chưa biết tổng" khác
            # hẳn "đã biết tổng và phủ 0%".
            "coverage_pct": round(100 * done / total, 1) if total else None,
            "rating_votes": sum(r.rating_count or 0 for r in group),
        })
    out.sort(key=lambda r: (r["catalog_perfumes"] or 0, r["detailed"]),
             reverse=True)
    return out


# Đăng ký ở đây thì lệnh `analyze` tự có thêm bảng, không phải sửa CLI.
METRICS: dict[str, Metric] = {
    "brand": by_brand,
    "accord": by_accord,
    "gender": by_gender,
    "season": by_season,
}
