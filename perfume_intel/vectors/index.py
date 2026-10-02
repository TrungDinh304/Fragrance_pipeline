"""Tra cứu chai/hãng giống nhau trên tập vector.

Giữ ba thứ: danh sách chai dùng được, vector của từng chai, và một index đảo
ngược term -> danh sách chai. Index đảo ngược là để không phải chấm điểm toàn bộ
kho cho mỗi truy vấn.

MỘT ĐIỂM CẦN BIẾT VỀ LỌC ỨNG VIÊN
Chỉ những khối CHỌN LỌC (accord, note, family) được đưa vào index đảo ngược.
Khối hoàn cảnh và cường độ có mặt ở ~100% số chai, nên nếu đưa vào thì tập ứng
viên luôn bằng cả kho và việc lọc thành vô nghĩa. Đổi lại: truy vấn CHỈ có hoàn
cảnh (`--occasion winter,night`) không sinh được ứng viên nào từ index, nên
trường hợp đó chấm điểm toàn bộ — có chủ ý, xem `_candidates`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ..analytics.dataset import Row
from . import features

log = logging.getLogger(__name__)


@dataclass
class Hit:
    """Một kết quả kèm lý do."""
    row: Row
    score: float
    why: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class BrandProfile:
    brand: str
    perfumes: int
    vector: dict[str, float]


def _usable(row: Row) -> bool:
    """Chai không có accord lẫn note thì vector rỗng — xếp hạng nó là vô nghĩa.

    Bỏ ở đây thay vì để nó trả về điểm 0 cho mọi truy vấn, vì "điểm 0" trông
    giống một kết quả thật và sẽ lẫn vào cuối danh sách.
    """
    return bool(row.accords or row.notes_by_layer)


class VectorIndex:
    def __init__(self, rows: Iterable[Row],
                 weights: Mapping[str, float] = features.BLOCK_WEIGHTS,
                 min_df: int = features.MIN_DF) -> None:
        all_rows = list(rows)
        self.rows = [r for r in all_rows if _usable(r)]
        skipped = len(all_rows) - len(self.rows)
        if skipped:
            log.info("Bỏ %d chai không có accord lẫn note.", skipped)

        self.idf = features.build_idf(self.rows, min_df=min_df)
        self.vectors = [features.vector(r, self.idf, weights) for r in self.rows]

        self.postings: dict[str, list[int]] = {}
        for i, vec in enumerate(self.vectors):
            for key in vec:
                if key.split(":", 1)[0] in features.SELECTIVE_BLOCKS:
                    self.postings.setdefault(key, []).append(i)

        self._by_url = {r.url.rstrip("/").lower(): i
                        for i, r in enumerate(self.rows)}
        log.info("Index: %d chai, %d chiều có nghĩa, %d hãng.",
                 len(self.rows), len(self.idf),
                 len({r.brand for r in self.rows if r.brand}))

    # ------------------------------------------------------------- tra cứu
    def find(self, query: str) -> int | None:
        """Tìm một chai theo URL, hoặc theo tên (khớp một phần, không phân biệt
        hoa thường). Nhiều kết quả thì lấy chai có nhiều vote nhất — đó gần như
        luôn là bản mà người hỏi đang nghĩ tới."""
        needle = query.strip().lower()
        direct = self._by_url.get(needle.rstrip("/"))
        if direct is not None:
            return direct

        hits = [i for i, r in enumerate(self.rows)
                if r.name and needle in r.name.lower()]
        if not hits:
            return None
        return max(hits, key=lambda i: self.rows[i].rating_count or 0)

    def matches(self, query: str, limit: int = 8) -> list[int]:
        """Mọi chai khớp tên — để báo cho người dùng khi query mơ hồ."""
        needle = query.strip().lower()
        hits = [i for i, r in enumerate(self.rows)
                if r.name and needle in r.name.lower()]
        hits.sort(key=lambda i: self.rows[i].rating_count or 0, reverse=True)
        return hits[:limit]

    def resolve(self, block: str, names: Iterable[str],
                ) -> tuple[list[str], list[str], dict[str, str]]:
        """Đưa tên người dùng gõ về đúng term có trong index.

        Cần bước này vì tên note của Fragrantica không phải tên người ta hay gọi:
        gõ "Oud" thì không khớp gì, vì trên trang nó là "Agarwood (Oud)". Bắt
        người dùng đoán đúng chính tả là vô lý, nên: khớp đúng trước, không có
        thì khớp một phần (chuỗi con), nhiều kết quả thì lấy term PHỔ BIẾN nhất
        (IDF thấp nhất) — đó gần như luôn là tên chính, còn lại là biến thể.

        Trả về (term đã khớp, tên không khớp được, map tên -> term để báo lại).
        """
        prefix = f"{block}:"
        known = [k for k in self.idf if k.startswith(prefix)]
        resolved: list[str] = []
        missing: list[str] = []
        renamed: dict[str, str] = {}

        for raw in names:
            needle = raw.strip().lower()
            if not needle:
                continue
            exact = prefix + needle
            if exact in self.idf:
                resolved.append(exact)
                continue
            partial = [k for k in known if needle in k[len(prefix):]]
            if not partial:
                missing.append(raw)
                continue
            best = min(partial, key=lambda k: self.idf[k])
            resolved.append(best)
            renamed[raw] = best[len(prefix):]
        return resolved, missing, renamed

    # -------------------------------------------------------------- chấm điểm
    def _candidates(self, vec: Mapping[str, float]) -> Sequence[int]:
        found: set[int] = set()
        for key in vec:
            if key.split(":", 1)[0] in features.SELECTIVE_BLOCKS:
                found.update(self.postings.get(key, ()))
        if found:
            return list(found)
        # Truy vấn chỉ có hoàn cảnh/cường độ: không lọc được, chấm cả kho.
        return range(len(self.rows))

    def query(self, vec: Mapping[str, float], limit: int = 10,
              exclude: Iterable[int] = (), gender: str | None = None,
              min_votes: int = 0, brand: str | None = None,
              exclude_brand: str | None = None,
              explain: bool = False) -> list[Hit]:
        if not vec:
            return []
        skip = set(exclude)
        want_gender = (gender or "").strip().lower() or None
        want_brand = (brand or "").strip().lower() or None
        drop_brand = (exclude_brand or "").strip().lower() or None

        hits: list[Hit] = []
        for i in self._candidates(vec):
            if i in skip:
                continue
            row = self.rows[i]
            if want_gender and (row.gender or "").lower() != want_gender:
                continue
            if min_votes and (row.rating_count or 0) < min_votes:
                continue
            row_brand = (row.brand or "").lower()
            if want_brand and row_brand != want_brand:
                continue
            if drop_brand and row_brand == drop_brand:
                continue
            score = features.cosine(vec, self.vectors[i])
            if score <= 0:
                continue
            hits.append(Hit(row=row, score=score))

        hits.sort(key=lambda h: (h.score, h.row.rating_count or 0), reverse=True)
        hits = hits[:limit]
        if explain:
            for hit in hits:
                j = self._by_url[hit.row.url.rstrip("/").lower()]
                hit.why = features.contributions(vec, self.vectors[j])
        return hits

    def similar(self, i: int, limit: int = 10, same_brand: bool = True,
                **kwargs) -> list[Hit]:
        """Chai giống chai thứ `i`.

        `same_brand=False` để loại cùng hãng: hữu ích thật, vì các bản trong cùng
        một dòng (flanker) luôn chiếm hết đầu bảng và che mất thứ đáng xem.
        """
        row = self.rows[i]
        kwargs.setdefault("exclude", ())
        exclude = {i, *kwargs.pop("exclude")}
        if not same_brand and row.brand:
            kwargs["exclude_brand"] = row.brand
        return self.query(self.vectors[i], limit=limit, exclude=exclude,
                          **kwargs)

    # ---------------------------------------------------------------- hãng
    def brand_profiles(self, min_perfumes: int = 1) -> dict[str, BrandProfile]:
        """Chân dung mùi của từng hãng = trọng tâm vector các chai của hãng.

        `min_perfumes` đáng để nâng lên khi so hãng với hãng: trọng tâm dựng từ
        1-2 chai không đại diện cho hãng, nó chỉ đại diện cho 1-2 chai đó — mà
        với độ phủ crawl hiện tại thì phần lớn hãng đúng là như vậy.
        """
        groups: dict[str, list[int]] = {}
        for i, row in enumerate(self.rows):
            if row.brand:
                groups.setdefault(row.brand, []).append(i)

        out: dict[str, BrandProfile] = {}
        for brand, members in groups.items():
            if len(members) < min_perfumes:
                continue
            # CHỈ khối mùi. Giữ hoàn cảnh/cường độ thì mọi hãng đều giống nhau ở
            # fall/winter/night — xem `features.SMELL_BLOCKS`.
            smell = (features.keep_blocks(self.vectors[i], features.SMELL_BLOCKS)
                     for i in members)
            out[brand] = BrandProfile(
                brand=brand,
                perfumes=len(members),
                vector=features.centroid(smell),
            )
        return out

    def similar_brands(self, brand: str, limit: int = 10,
                       min_perfumes: int = 1, explain: bool = False,
                       ) -> tuple[BrandProfile | None, list[Hit]]:
        profiles = self.brand_profiles(min_perfumes=1)
        needle = brand.strip().lower()
        target = next((p for name, p in profiles.items()
                       if name.lower() == needle), None)
        if target is None:
            target = next((p for name, p in profiles.items()
                           if needle in name.lower()), None)
        if target is None:
            return None, []

        scored: list[Hit] = []
        for name, profile in profiles.items():
            if name == target.brand or profile.perfumes < min_perfumes:
                continue
            score = features.cosine(target.vector, profile.vector)
            if score <= 0:
                continue
            hit = Hit(row=Row(url="", name=name, brand=name), score=score)
            hit.row.rating_count = profile.perfumes
            if explain:
                hit.why = features.contributions(target.vector, profile.vector)
            scored.append(hit)

        scored.sort(key=lambda h: h.score, reverse=True)
        return target, scored[:limit]


def build(rows: Iterable[Row], **kwargs) -> VectorIndex:
    return VectorIndex(rows, **kwargs)
