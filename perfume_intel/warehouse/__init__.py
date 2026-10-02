"""Kho phân tích: tầng silver (Parquet) và tầng gold/marts (DuckDB + dbt).

Cần cài thêm:  pip install -e ".[warehouse]"
"""

from .silver import build, connect, read_silver

__all__ = ["build", "connect", "read_silver"]
