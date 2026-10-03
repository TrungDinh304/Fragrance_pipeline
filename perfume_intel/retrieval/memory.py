"""Bản cài đặt `Retriever` chạy trong bộ nhớ, dựa trên `vectors/`.

Đây là adapter ĐẦU TIÊN, không phải adapter duy nhất. Nó bọc `VectorIndex` lại
và dịch sang ngôn ngữ của `ports.py`; mọi chi tiết (vector thưa, IDF, tiền tố
khối, số thứ tự trong list) dừng lại ở file này.

Hợp với: một tiến trình, dữ liệu vừa RAM. Đo trên 631 chai — dựng index 24 ms,
một truy vấn 0,24 ms. Ở mức ~100k chai vẫn còn thoải mái.

Khi nào cần adapter khác: dữ liệu không vừa RAM, nhiều tiến trình dùng chung,
hoặc phục vụ nhiều người đồng thời. Lúc đó viết `DuckDBRetriever` hoặc
`PgVectorRetriever` cho qua `tests/retrieval_contract.py` — KHÔNG ai phía trên
phải sửa.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Sequence

from .. import config
from ..analytics import dataset
from ..analytics.dataset import Row
from ..vectors import features
from ..vectors.index import VectorIndex
from . import ports
from .ports import (BrandMatch, BrandResult, Match, Query, Reason, SearchResult,
                    UnknownBrand, UnknownPerfume)

log = logging.getLogger(__name__)

# Tiền tố kỹ thuật bên trong <-> tên khối trong hợp đồng. Hai cái cố ý tách rời:
# đổi cách mã hoá bên trong không được làm đổi thứ phía trên nhìn thấy.
_BLOCK_IN = {features.ACCORD: ports.ACCORD, features.NOTE: ports.NOTE,
             features.OCCASION: ports.OCCASION,
             features.STRENGTH: ports.STRENGTH, features.FAMILY: ports.FAMILY}
_BLOCK_OUT = {v: k for k, v in _BLOCK_IN.items()}


def _reasons(pairs: Iterable[tuple[str, float]]) -> tuple[Reason, ...]:
    out = []
    for dim, weight in pairs:
        prefix, _, label = dim.partition(":")
        out.append(Reason(block=_BLOCK_IN.get(prefix, prefix), label=label,
                          weight=round(weight, 4)))
    return tuple(out)


class InMemoryRetriever:
    """Giữ toàn bộ chỉ mục trong RAM."""

    def __init__(self, index: VectorIndex) -> None:
        self._index = index

    # ------------------------------------------------------------- khởi tạo
    @classmethod
    def from_rows(cls, rows: Iterable[Row]) -> "InMemoryRetriever":
        return cls(VectorIndex(list(rows)))

    @classmethod
    def from_bronze(cls, community: Path | None = None) -> "InMemoryRetriever":
        return cls.from_rows(
            dataset.build(community or config.raw_dir("fragrantica")))

    def __len__(self) -> int:
        return len(self._index.rows)

    # --------------------------------------------------------------- dịch ra
    def _match(self, position: int, score: float,
               why: tuple[Reason, ...] = ()) -> Match:
        row = self._index.rows[position]
        return self._match_of(row, score, why)

    @staticmethod
    def _match_of(row: Row, score: float,
                  why: tuple[Reason, ...] = ()) -> Match:
        # Khoá là URL đã chuẩn hoá — có nghĩa ở mọi backend, khác hẳn số thứ tự
        # trong một list Python.
        from ..core.text import url_key
        return Match(perfume_key=url_key(row.url) or "", score=round(score, 6),
                     name=row.name, brand=row.brand, url=row.url or None,
                     rating=row.rating, rating_count=row.rating_count,
                     gender=row.gender, why=why)

    # ----------------------------------------------------------------- tìm
    def search(self, query: Query) -> SearchResult:
        if query.empty:
            return SearchResult()

        if query.like_perfume:
            return self._search_like(query)
        return self._search_terms(query)

    def _search_like(self, q: Query) -> SearchResult:
        position = self._index.find(q.like_perfume)
        if position is None:
            raise UnknownPerfume(q.like_perfume)

        others = self._index.matches(q.like_perfume)
        hits = self._index.similar(
            position, limit=q.limit, same_brand=q.include_same_brand,
            gender=q.gender, min_votes=q.min_votes, explain=q.explain)
        return SearchResult(
            matches=tuple(self._match_of(h.row, h.score, _reasons(h.why))
                          for h in hits),
            seed=self._match(position, 1.0),
            ambiguous=tuple(self._match(i, 1.0) for i in others[:5])
            if len(others) > 1 else ())

    def _search_terms(self, q: Query) -> SearchResult:
        terms: list[str] = []
        unknown: list[str] = []
        resolved: dict[str, str] = {}
        for block, names in ((features.NOTE, q.notes),
                             (features.ACCORD, q.accords)):
            found, missing, renamed = self._index.resolve(block, names)
            terms += found
            unknown += missing
            resolved.update(renamed)

        axes = [a.strip().lower() for a in q.occasions]
        unknown += [a for a in axes if a not in ports.OCCASIONS]
        axes = [a for a in axes if a in ports.OCCASIONS]

        vector = features.query_from_terms(terms, occasion=axes,
                                           idf=self._index.idf)
        if not vector:
            return SearchResult(resolved=resolved, unknown=tuple(unknown))

        hits = self._index.query(vector, limit=q.limit, gender=q.gender,
                                 min_votes=q.min_votes, explain=q.explain)
        return SearchResult(
            matches=tuple(self._match_of(h.row, h.score, _reasons(h.why))
                          for h in hits),
            resolved=resolved, unknown=tuple(unknown))

    # ---------------------------------------------------------------- hãng
    def similar_brands(self, brand: str, limit: int = 10,
                       min_perfumes: int = 1,
                       explain: bool = False) -> BrandResult:
        target, hits = self._index.similar_brands(
            brand, limit=limit, min_perfumes=min_perfumes, explain=explain)
        if target is None:
            raise UnknownBrand(brand)
        return BrandResult(
            target=BrandMatch(brand=target.brand, perfumes=target.perfumes,
                              score=1.0),
            matches=tuple(BrandMatch(brand=h.row.name or "",
                                     perfumes=h.row.rating_count or 0,
                                     score=round(h.score, 6),
                                     why=_reasons(h.why)) for h in hits))

    # ------------------------------------------------------------- từ vựng
    def vocabulary(self, block: str) -> Sequence[str]:
        if block == ports.OCCASION:
            return list(ports.OCCASIONS)
        prefix = _BLOCK_OUT.get(block)
        if prefix is None:
            raise ValueError(f"Khối không có: {block!r}. "
                             f"Chỉ có: {', '.join(ports.BLOCKS)}.")
        head = f"{prefix}:"
        return sorted(k[len(head):] for k in self._index.idf
                      if k.startswith(head))
