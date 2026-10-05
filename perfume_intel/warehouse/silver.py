"""Tầng silver: từ JSONL thô ra bảng Parquet đã khử trùng và có KIỂU.

Bronze là thứ crawl được, nguyên trạng: mỗi hãng một file theo ngày, crawl lại
thì thêm file mới, một chai xuất hiện ở nhiều file. Hợp cho việc ghi, rất dở cho
việc hỏi.

Silver là cùng dữ liệu đó, sau ba phép biến đổi và không thêm gì:

  1. **Khử trùng** — mỗi chai đúng một dòng, giữ bản `scraped_at` mới nhất.
  2. **Đặt kiểu** — `year` là INTEGER, `rating` là DOUBLE, không còn là chuỗi.
  3. **Trải phẳng cái lồng nhau** — accord / note / vote hoàn cảnh vốn là mảng
     lồng trong mỗi bản ghi; ở đây mỗi thứ thành một bảng DÀI riêng.

Bước 3 mới là bước đáng giá. Khi accord còn nằm trong mảng, câu "accord nào phổ
biến dần lên theo năm" bắt buộc phải viết bằng Python. Ở dạng bảng dài thì nó là
một câu GROUP BY.

Vì sao Parquet mà không phải CSV: có kiểu thật, giữ được NULL (CSV không phân
biệt nổi "chưa biết" với chuỗi rỗng — mà phân biệt đó là cả ý nghĩa của cột
`catalog_perfumes` trong bảng độ phủ), và đọc được bằng DuckDB/pandas/Polars mà
không cần khai lại schema.

Chỉ dùng `duckdb`, KHÔNG dùng pandas/pyarrow: duckdb là một wheel 21 MB không
phụ thuộc gói nào, còn pandas kéo theo cả numpy. Ở mức 25 nghìn dòng thì chèn
thẳng bằng `executemany` nhanh hơn thời gian import pandas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .. import config
from ..core import bronze, lake
from ..core.text import url_key

log = logging.getLogger(__name__)

SEASONS = ("winter", "spring", "summer", "fall")
DAY_NIGHT = ("day", "night")


def connect(database: str = ":memory:"):
    """Mở DuckDB, báo lỗi dễ hiểu nếu chưa cài."""
    try:
        import duckdb
    except ImportError as exc:                      # pragma: no cover
        raise RuntimeError(
            "Cần DuckDB cho tầng silver. Cài bằng:\n"
            '    pip install -e ".[warehouse]"'
        ) from exc
    return duckdb.connect(database)


# --------------------------------------------------------------------- schema
# Khai kiểu tay chứ không để DuckDB tự đoán. Tự đoán thì một cột toàn NULL ở mẻ
# này ra VARCHAR, mẻ sau có số lại ra BIGINT — bảng đổi kiểu theo dữ liệu, và
# mọi truy vấn phía sau vỡ vào một ngày không ai đụng gì tới nó.
TABLES: dict[str, list[tuple[str, str]]] = {
    "perfumes": [
        ("perfume_key", "VARCHAR"), ("url", "VARCHAR"),
        ("perfume_id", "VARCHAR"), ("name", "VARCHAR"), ("brand", "VARCHAR"),
        ("gender", "VARCHAR"), ("year", "INTEGER"),
        ("fragrance_family", "VARCHAR"),
        ("rating", "DOUBLE"), ("rating_count", "BIGINT"),
        ("longevity", "VARCHAR"), ("sillage", "VARCHAR"),
        ("description", "VARCHAR"), ("image", "VARCHAR"),
        ("des_key", "VARCHAR"), ("scraped_at", "VARCHAR"),
    ],
    "perfume_accords": [
        ("perfume_key", "VARCHAR"), ("accord", "VARCHAR"),
        ("width", "DOUBLE"), ("opacity", "DOUBLE"), ("rank", "INTEGER"),
    ],
    "perfume_notes": [
        ("perfume_key", "VARCHAR"), ("layer", "VARCHAR"),
        ("note", "VARCHAR"), ("position", "INTEGER"),
    ],
    "perfume_wear": [
        ("perfume_key", "VARCHAR"), ("axis", "VARCHAR"), ("kind", "VARCHAR"),
        ("percent", "DOUBLE"), ("votes", "BIGINT"),
    ],
    "brands": [
        ("brand_key", "VARCHAR"), ("brand_url", "VARCHAR"),
        ("brand_name", "VARCHAR"), ("alphabet", "VARCHAR"),
        ("popular_rank", "INTEGER"), ("scraped_at", "VARCHAR"),
    ],
    "brand_perfumes": [
        ("perfume_key", "VARCHAR"), ("perfume_url", "VARCHAR"),
        ("brand_key", "VARCHAR"), ("brand_name", "VARCHAR"),
        ("perfume_name", "VARCHAR"), ("collection", "VARCHAR"),
        ("year", "INTEGER"), ("gender", "VARCHAR"), ("comments", "BIGINT"),
    ],
}


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest(records: Iterable[dict], key_field: str) -> dict[str, dict]:
    """Khử trùng theo khoá URL, giữ bản `scraped_at` lớn nhất."""
    out: dict[str, dict] = {}
    stamps: dict[str, str] = {}
    for record in records:
        key = url_key(record.get(key_field))
        if not key:
            continue
        stamp = record.get("scraped_at") or ""
        if key not in out or stamp >= stamps[key]:
            out[key], stamps[key] = record, stamp
    return out


# ------------------------------------------------------------------ trải phẳng
def _perfume_rows(records: dict[str, dict]) -> dict[str, list[tuple]]:
    perfumes, accords, notes, wear = [], [], [], []
    for key, raw in records.items():
        perfumes.append((
            key, raw.get("url"), raw.get("perfume_id"), raw.get("name"),
            raw.get("brand"), raw.get("gender"), _int(raw.get("year")),
            raw.get("fragrance_family"), _float(raw.get("rating")),
            _int(raw.get("rating_count")), raw.get("longevity"),
            raw.get("sillage"), raw.get("description"), raw.get("image"),
            url_key(raw.get("des_url")), raw.get("scraped_at"),
        ))
        for rank, item in enumerate(raw.get("accords") or []):
            if item.get("name"):
                accords.append((key, item["name"], _float(item.get("width")),
                                _float(item.get("opacity")), rank))
        for layer in ("top", "middle", "base", "general"):
            for pos, note in enumerate(raw.get(f"{layer}_notes") or []):
                if note:
                    notes.append((key, layer, note, pos))
        for item in raw.get("when_to_wear") or []:
            axis = (item.get("name") or "").strip().lower()
            if not axis:
                continue
            kind = ("season" if axis in SEASONS
                    else "daynight" if axis in DAY_NIGHT else "other")
            wear.append((key, axis, kind, _float(item.get("percent")),
                         _int(item.get("votes"))))
    return {"perfumes": perfumes, "perfume_accords": accords,
            "perfume_notes": notes, "perfume_wear": wear}


def _brand_rows(records: dict[str, dict]) -> list[tuple]:
    return [(key, raw.get("brand_url"), raw.get("brand_name"),
             raw.get("alphabet"), _int(raw.get("popular_rank")),
             raw.get("scraped_at"))
            for key, raw in records.items()]


def _brand_perfume_rows(records: dict[str, dict]) -> list[tuple]:
    return [(key, raw.get("perfume_url"), url_key(raw.get("brand_url")),
             raw.get("brand_name"), raw.get("perfume_name"),
             raw.get("collection"), _int(raw.get("year")), raw.get("gender"),
             _int(raw.get("comments")))
            for key, raw in records.items()]


# -------------------------------------------------------------------- xây bảng
@dataclass
class BuildReport:
    out_dir: Path
    counts: dict[str, int]

    def total(self) -> int:
        return sum(self.counts.values())


def _create(con, name: str, rows: Sequence[tuple]) -> None:
    columns = TABLES[name]
    ddl = ", ".join(f'"{c}" {t}' for c, t in columns)
    con.execute(f'CREATE OR REPLACE TABLE "{name}" ({ddl})')
    if rows:
        holes = ", ".join("?" * len(columns))
        con.executemany(f'INSERT INTO "{name}" VALUES ({holes})', list(rows))


def build(community: Path | None = None, out_dir: Path | None = None,
          database: str | None = None) -> BuildReport:
    """Đọc bronze -> ghi Parquet silver. Trả về số dòng mỗi bảng."""
    community = Path(community or config.raw_dir("fragrantica"))
    out_dir = Path(out_dir or config.SILVER_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    perfume_src = _latest(bronze.read(community, bronze.PERFUME), "url")
    brand_src = _latest(bronze.read(community, bronze.BRAND), "brand_url")
    product_src = _latest(bronze.read(community, bronze.BRAND_PERFUME),
                          "perfume_url")

    data = _perfume_rows(perfume_src)
    data["brands"] = _brand_rows(brand_src)
    data["brand_perfumes"] = _brand_perfume_rows(product_src)

    con = connect(database or ":memory:")
    try:
        counts: dict[str, int] = {}
        for name in TABLES:
            rows = data.get(name, [])
            _create(con, name, rows)
            target = (out_dir / f"{name}.parquet").as_posix()
            con.execute(f"COPY \"{name}\" TO '{target}' (FORMAT PARQUET)")
            counts[name] = len(rows)
            log.info("%-16s %6d dòng -> %s", name, len(rows),
                     out_dir / f"{name}.parquet")
    finally:
        con.close()

    # Niêm sau khi đóng kết nối, không phải trong vòng lặp: DuckDB ghi Parquet
    # theo từng khối và chỉ đóng footer khi COPY xong. Đọc file giữa lúc đó sẽ
    # đưa lên lake một file Parquet thiếu footer — vẫn có kích thước, vẫn trông
    # như xong, và chỉ hỏng ở lúc ai đó đọc nó.
    lake.seal_dir(out_dir, lake.SILVER)
    return BuildReport(out_dir=out_dir, counts=counts)


def read_silver(con, out_dir: Path | None = None) -> None:
    """Đăng ký mọi bảng Parquet thành view trong một kết nối DuckDB."""
    out_dir = Path(out_dir or config.SILVER_DIR)
    # Máy này có thể chưa dựng silver mà lake đã có (máy khác dựng rồi). Kéo về
    # trước khi kết luận là thiếu bảng.
    lake.ensure_local(out_dir, lake.SILVER)
    for name in TABLES:
        path = out_dir / f"{name}.parquet"
        if path.exists():
            con.execute(f'CREATE OR REPLACE VIEW "{name}" AS '
                        f"SELECT * FROM read_parquet('{path.as_posix()}')")
