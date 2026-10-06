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

# Hoàn cảnh nói bằng tiếng Việt -> trục mà dữ liệu cộng đồng thật sự có. Đây KHÔNG
# phải từ điển note (đã bỏ khỏi phạm vi): chỉ sáu trục, và sáu trục này là tên cột
# trong dữ liệu chứ không phải thứ suy diễn.
_HOAN_CANH_VI = {
    "mùa đông": "winter", "mùa xuân": "spring", "mùa hè": "summer",
    "mùa thu": "fall", "ban ngày": "day", "buổi tối": "night",
    "ban đêm": "night", "buổi sáng": "day",
}

# Từ nối tiếng Việt: không phải note, cũng không đáng báo là "không hiểu".
_TU_BO_QUA = {
    "nước", "hoa", "mùi", "chai", "cho", "của", "và", "với", "có", "nào",
    "gì", "một", "những", "các", "thì", "là", "đi", "dùng", "hợp", "phù",
    "tìm", "muốn", "cần", "thơm", "loại", "kiểu", "hơi", "rất", "khá",
}


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
        # Có note/accord cụ thể -> nhánh term (chính xác, giải thích được).
        if query.notes or query.accords:
            return self._search_terms(query)
        # Có câu tự do -> nhánh câu tự do, KỂ CẢ khi cũng có hoàn cảnh.
        #
        # Trước đây điều kiện là `or query.occasions`, nên chỉ cần có mùa là `text`
        # bị bỏ hẳn: "mùi gỗ trầm ấm cho buổi tối mùa đông" chỉ còn tìm theo
        # winter+night. Tệ hơn, `PgVectorRetriever` lại dùng hoàn cảnh làm bộ lọc
        # trên nhánh ngữ nghĩa — nên hai adapter cho ra kết quả khác nhau trên
        # cùng một Query, đúng thứ bộ hợp đồng tồn tại để ngăn.
        if (query.text or "").strip():
            return self._search_text(query)
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
            # Seed cũng cần lý do: không có thì câu trả lời về đúng chai đó phải
            # nói "chưa có đủ dữ liệu về mùi của nó" trong khi dữ liệu có đủ.
            seed=self._match_of(self._index.rows[position], 1.0,
                                self._mui_cua_chai(self._index.rows[position],
                                                   ()) if q.explain else ()),
            ambiguous=tuple(self._match(i, 1.0) for i in others[:5])
            if len(others) > 1 else ())

    # ------------------------------------------------------------ câu tự do
    def _tach_tu(self, text: str) -> tuple[list[str], list[str], list[str]]:
        """Câu tiếng Việt -> (note/accord tìm được, hoàn cảnh, từ không hiểu).

        Bản này khớp theo TỪ KHOÁ, không có ngữ nghĩa: "mùi gỗ" sẽ không tự tìm ra
        `woody`. Đó là giới hạn thật của adapter in-memory, và cũng chính là lý do
        `PgVectorRetriever` tồn tại — nó khớp bằng vector nên hiểu được câu không
        chứa đúng tên note.

        Vẫn làm phần này tử tế thay vì trả rỗng, vì nó là đường chạy khi chưa
        dựng kho vector, và vì hợp đồng đòi mọi adapter phải xử lý được `text`.
        """
        import re
        tu = [t for t in re.findall(r"[0-9a-zA-ZÀ-ỹ]+", text.lower())
              if len(t) > 1]
        hoan_canh = [t for t in tu if t in ports.OCCASIONS]
        hoan_canh += [en for vi, en in _HOAN_CANH_VI.items() if vi in text.lower()]

        tim_duoc: list[str] = []
        khong_hieu: list[str] = []
        for t in tu:
            if t in ports.OCCASIONS or t in _TU_BO_QUA:
                continue
            found, _missing, _renamed = self._index.resolve(features.NOTE, [t])
            if not found:
                found, _m, _r = self._index.resolve(features.ACCORD, [t])
            if found:
                tim_duoc += found
            else:
                khong_hieu.append(t)
        return tim_duoc, list(dict.fromkeys(hoan_canh)), khong_hieu

    def _search_text(self, q: Query) -> SearchResult:
        terms, axes, khong_hieu = self._tach_tu(q.text or "")
        # TRƯỜNG KHAI TƯỜNG MINH THẮNG CHỮ ĐỌC RA TỪ CÂU.
        #
        # `q.occasions` do tầng tách ý định quyết định; nếu nó có giá trị thì đó là
        # quyết định, và mấy trục đọc được từ `text` bị bỏ.
        #
        # Vì sao quan trọng: trong hội thoại, `text` CỘNG DỒN qua các lượt. Khách
        # hỏi mùa đông rồi sau đó nói "mùa hè thì sao" — ý định đã đổi sang summer,
        # nhưng câu cộng dồn vẫn còn chữ "mùa đông". Trộn cả hai thì kết quả vừa
        # hợp mùa nóng vừa hợp mùa lạnh, tức là không đổi gì cả.
        khai = [a.strip().lower() for a in q.occasions
                if (a or "").strip().lower() in ports.OCCASIONS]
        axes = list(dict.fromkeys(khai)) if khai else axes
        vector = features.query_from_terms(terms, occasion=axes,
                                           idf=self._index.idf)
        if not vector:
            return SearchResult(unknown=tuple(khong_hieu))
        hits = self._index.query(vector, limit=q.limit, gender=q.gender,
                                 min_votes=q.min_votes, explain=q.explain)
        return SearchResult(
            matches=tuple(self._match_of(
                h.row, h.score,
                self._mui_cua_chai(h.row, _reasons(h.why)) if q.explain
                else _reasons(h.why)) for h in hits),
            # Báo lại ĐÃ HIỂU câu đó thành gì — nếu không, người hỏi nhận một
            # danh sách trông hợp lý mà không biết nó trả lời câu nào.
            resolved={q.text or "": ", ".join(
                [t.partition(":")[2] for t in terms] + axes)} if terms or axes
            else {},
            unknown=tuple(khong_hieu))

    @staticmethod
    def _mui_cua_chai(row: Row, da_co: tuple[Reason, ...],
                      so: int = 3) -> tuple[Reason, ...]:
        """Bù lý do bằng mùi mạnh nhất của chính chai đó.

        Câu hỏi tiếng Việt không chứa tên nhãn tiếng Anh, nên phần khớp được
        thường chỉ là trục hoàn cảnh — lý do ra "hợp mùa lạnh, hợp buổi tối":
        đúng nhưng không nói chai đó MÙI GÌ. `PgVectorRetriever` làm cùng việc này
        bằng SQL; hai adapter phải nói cùng một thứ.
        """
        ra = list(da_co)
        manh = sorted((row.accords or {}).items(), key=lambda kv: -(kv[1] or 0))
        for ten, _diem in manh:
            if len(ra) >= so:
                break
            if not any(r.label == ten for r in ra):
                ra.append(Reason(block=ports.ACCORD, label=ten, weight=0.0))
        for ten in (row.notes or []):
            if len(ra) >= so:
                break
            if not any(r.label.lower() == ten.lower() for r in ra):
                ra.append(Reason(block=ports.NOTE, label=ten, weight=0.0))
        return tuple(ra)

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
            matches=tuple(self._match_of(
                h.row, h.score,
                self._mui_cua_chai(h.row, _reasons(h.why)) if q.explain
                else _reasons(h.why)) for h in hits),
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
