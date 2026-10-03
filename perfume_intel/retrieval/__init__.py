"""Tầng truy xuất: một cổng (`ports`) và các bản cài đặt sau nó.

Phía trên (CLI, API, chatbot) chỉ import từ đây, không bao giờ import thẳng
`perfume_intel.vectors`.
"""

from .memory import InMemoryRetriever
from .ports import (ACCORD, BLOCKS, FAMILY, NOTE, OCCASION, OCCASIONS, STRENGTH,
                    BrandMatch, BrandResult, Match, Query, Reason, Retriever,
                    SearchResult, UnknownBrand, UnknownPerfume)

__all__ = [
    "Retriever", "InMemoryRetriever",
    "Query", "SearchResult", "Match", "Reason",
    "BrandResult", "BrandMatch",
    "UnknownPerfume", "UnknownBrand",
    "ACCORD", "NOTE", "OCCASION", "STRENGTH", "FAMILY", "BLOCKS", "OCCASIONS",
]
