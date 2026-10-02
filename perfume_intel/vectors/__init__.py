"""Vector hoá dữ liệu nước hoa để tìm chai/hãng giống nhau.

`features.py` dựng vector, `index.py` tra cứu. Xem lệnh CLI `similar`.
"""

from .features import cosine, query_vector, vector
from .index import BrandProfile, Hit, VectorIndex, build

__all__ = ["build", "cosine", "vector", "query_vector",
           "VectorIndex", "Hit", "BrandProfile"]
