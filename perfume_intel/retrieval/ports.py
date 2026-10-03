"""Hợp đồng của tầng truy xuất. Đây là ranh giới, không phải nơi xử lý.

Mọi thứ phía trên (CLI, API, chatbot) CHỈ được nhìn thấy file này. Mọi thứ phía
dưới (vector thưa trong RAM, DuckDB, pgvector) đều phải nói đúng ngôn ngữ ở đây.

VÌ SAO CÓ FILE NÀY
Trước đây `similar_cmd` gọi thẳng vào ruột: nó tự dựng vector thưa bằng
`features.query_from_terms(...)`, đọc `index.idf`, và nhận về SỐ THỨ TỰ trong
một list Python. Ba thứ đó không backend nào khác implement nổi — pgvector không
nhận dict Python và không có khái niệm "phần tử thứ 42". Nghĩa là thêm chatbot
hay đổi kho vector thì phải viết lại cả phía trên.

HAI LUẬT GIỮ CHO RANH GIỚI NÀY KHÔNG RÒ
  1. **Khoá là `perfume_key`** (URL đã chuẩn hoá), không bao giờ là vị trí trong
     một cấu trúc nào đó. Khoá phải có nghĩa ở mọi backend.
  2. **Không từ nào ở đây nói tới cách cài đặt.** Không "vector", không "idf",
     không "parquet", không "SQL". Thấy một từ như vậy lọt vào là ranh giới đã
     thủng.

Lý do giống nhau cũng phải đi qua đây (`Reason`): nếu chỉ trả về điểm số thì
chatbot chỉ nói được "hai chai này gần nhau", không nói được "vì cùng có Oud".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, runtime_checkable

# Các khối tín hiệu mà một lý do có thể thuộc về. Tên ở đây là tên NGHIỆP VỤ,
# cố ý khác với tiền tố kỹ thuật mà bản cài đặt dùng bên trong.
ACCORD = "accord"
NOTE = "note"
OCCASION = "occasion"
STRENGTH = "strength"
FAMILY = "family"
BLOCKS = (ACCORD, NOTE, OCCASION, STRENGTH, FAMILY)

# Trục hoàn cảnh mà dữ liệu cộng đồng thật sự có. KHÔNG có "công sở", "hẹn hò" —
# những nhãn đó là suy diễn, không phải thứ người dùng Fragrantica bình chọn.
OCCASIONS = ("winter", "spring", "summer", "fall", "day", "night")


@dataclass(frozen=True)
class Reason:
    """Một chiều đóng góp vào điểm giống nhau."""

    block: str          # một trong BLOCKS
    label: str          # vd "vanilla", "winter"
    weight: float


@dataclass(frozen=True)
class Match:
    """Một chai trong kết quả."""

    perfume_key: str
    score: float
    name: str | None = None
    brand: str | None = None
    url: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    gender: str | None = None
    why: tuple[Reason, ...] = ()


@dataclass(frozen=True)
class BrandMatch:
    brand: str
    perfumes: int
    score: float
    why: tuple[Reason, ...] = ()


@dataclass(frozen=True)
class Query:
    """Một câu hỏi truy xuất, bằng ngôn ngữ người dùng.

    `notes`/`accords` nhận tên người ta hay gọi ("oud"), không bắt gõ đúng tên
    trên nguồn ("Agarwood (Oud)"). Việc khớp là của bản cài đặt, và nó phải BÁO
    LẠI đã khớp ra gì — xem `SearchResult.resolved`.
    """

    like_perfume: str | None = None          # url hoặc tên một chai làm gốc
    notes: tuple[str, ...] = ()
    accords: tuple[str, ...] = ()
    occasions: tuple[str, ...] = ()
    gender: str | None = None
    min_votes: int = 0
    include_same_brand: bool = True
    limit: int = 10
    explain: bool = False

    @property
    def empty(self) -> bool:
        return not (self.like_perfume or self.notes or self.accords
                    or self.occasions)


@dataclass(frozen=True)
class SearchResult:
    """Kết quả, KÈM những gì đã xảy ra với câu hỏi.

    `resolved` và `unknown` không phải để cho đẹp: gõ "oud" mà hệ thống âm thầm
    tìm "agarwood (oud)", hoặc âm thầm bỏ một từ gõ sai, thì người hỏi nhận một
    danh sách trông hợp lý nhưng trả lời câu khác.
    """

    matches: tuple[Match, ...] = ()
    resolved: Mapping[str, str] = field(default_factory=dict)
    unknown: tuple[str, ...] = ()
    seed: Match | None = None                # chai gốc, khi hỏi bằng like_perfume
    ambiguous: tuple[Match, ...] = ()        # tên khớp nhiều chai


@dataclass(frozen=True)
class BrandResult:
    target: BrandMatch | None = None
    matches: tuple[BrandMatch, ...] = ()


class UnknownPerfume(LookupError):
    """`like_perfume` không khớp chai nào."""


class UnknownBrand(LookupError):
    """Không có hãng nào khớp."""


@runtime_checkable
class Retriever(Protocol):
    """Cổng truy xuất. Mọi backend phải nói đúng ngần này.

    Bản cài đặt nào cũng phải qua được `tests/retrieval_contract.py` — bộ test
    đó là định nghĩa thật của hợp đồng này, phần chữ chỉ là giải thích.
    """

    def search(self, query: Query) -> SearchResult:
        """Tìm chai. `query.empty` thì trả về kết quả rỗng, không ném lỗi."""
        ...

    def similar_brands(self, brand: str, limit: int = 10,
                       min_perfumes: int = 1,
                       explain: bool = False) -> BrandResult:
        """Hãng có chân dung mùi gần nhất. Không khớp hãng nào -> UnknownBrand."""
        ...

    def vocabulary(self, block: str) -> Sequence[str]:
        """Những nhãn dùng được của một khối, đã sắp xếp.

        Có hàm này thì tầng trích ý định (LLM) biết được tập giá trị hợp lệ mà
        không phải đoán, và không phải biết gì về cách lưu bên dưới.
        """
        ...

    def __len__(self) -> int:
        """Số chai đang tra cứu được."""
        ...
