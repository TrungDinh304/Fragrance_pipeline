"""Adapter `Retriever` thứ hai: Postgres + pgvector. MỌI phép tính nằm trong SQL.

VÌ SAO KHÔNG GIỮ DỮ LIỆU TRONG RAM CHO PHẦN THƯA
Cách ít việc hơn hẳn: dùng pgvector cho câu tự do, còn phần tìm theo note và phần
giải thích thì vẫn giữ `VectorIndex` trong bộ nhớ. Không chọn, vì làm vậy thì kho
vector chẳng giải quyết điều gì — tiến trình vẫn phải nạp toàn bộ dữ liệu vào RAM,
tức là vẫn đúng cái giới hạn mà người ta dựng kho vector để thoát khỏi.

Nên ở đây cả hai loại vector đều sống trong Postgres:

    perfume_vectors   embedding vector(N)   -> câu tự do, tìm bằng ngữ nghĩa
    perfume_terms     (block, label, weight) -> tìm theo note/accord/hoàn cảnh
                                                VÀ sinh ra phần "vì sao giống"

GIẢI QUYẾT MÂU THUẪN GIỮA HAI LỰA CHỌN
Vector đặc không có chiều nào có tên, nên tự nó không nói được "vì sao giống" —
mà chatbot lại cần đúng cái đó để không bịa. Cách giải: tìm bằng vector đặc để có
độ phủ ngữ nghĩa, rồi với ĐÚNG những chai trả về, lấy lý do từ bảng `perfume_terms`
bằng một câu join. Được cả hai, và không phải nạp gì vào RAM.

`model_id` ĐƯỢC LƯU VÀ ĐƯỢC KIỂM. Vector của hai model khác nhau vẫn cộng trừ
được với nhau, chỉ là kết quả vô nghĩa — và không có cách nào phát hiện từ con số.
Nên nạp sai model là lỗi phải chặn ở cửa, không phải lỗi để đi tìm sau.

Bản này phải qua `tests/retrieval_contract.py` (26 điều khoản) y như
`InMemoryRetriever`. Qua hết là thay thế được, và không ai phía trên phải sửa.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Sequence

from ..analytics.dataset import Row
from ..core.text import url_key
from ..vectors import features
from . import ports
from .ports import (BrandMatch, BrandResult, Match, Query, Reason, SearchResult,
                    UnknownBrand, UnknownPerfume)

log = logging.getLogger(__name__)

# Dưới mức này coi như câu hỏi không có liên quan gì tới kho. Xem `_theo_cau`.
MIN_SIM = 0.01

BANG_VECTOR = "perfume_vectors"
BANG_TERM = "perfume_terms"

_BLOCK_IN = {features.ACCORD: ports.ACCORD, features.NOTE: ports.NOTE,
             features.OCCASION: ports.OCCASION,
             features.STRENGTH: ports.STRENGTH, features.FAMILY: ports.FAMILY}
_BLOCK_OUT = {v: k for k, v in _BLOCK_IN.items()}


def _tach_cap(dims) -> tuple[list[str], list[str]]:
    """["note:oud", ...] -> (["note", ...], ["oud", ...]).

    Hai mảng song song thay cho một mảng tuple, vì Postgres không nhận tham số
    kiểu composite — xem chú thích trong `_ly_do`.
    """
    blocks, labels = [], []
    for d in dims:
        prefix, _, label = d.partition(":")
        blocks.append(_BLOCK_IN.get(prefix, prefix))
        labels.append(label)
    return blocks, labels


class PgUnavailable(RuntimeError):
    """Không nối được Postgres, hoặc chưa có extension vector."""


def dsn() -> str:
    """Chuỗi kết nối. Mặc định khớp với service `postgres` trong compose."""
    return (os.environ.get("PG_DSN")
            or "postgresql://perfume:perfume-dev-only@postgres:5432/perfume")


def connect(dsn_: str | None = None):
    try:
        import psycopg
    except ImportError as exc:
        raise PgUnavailable(
            'Cần psycopg. Cài bằng: pip install -e ".[vectordb]"') from exc
    try:
        return psycopg.connect(dsn_ or dsn(), autocommit=True)
    except Exception as exc:                            # noqa: BLE001
        raise PgUnavailable(f"Không nối được Postgres: {exc}") from exc


# ------------------------------------------------------------------- lược đồ
def _ddl(dim: int, t_vec: str = BANG_VECTOR,
         t_term: str = BANG_TERM) -> list[str]:
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""CREATE TABLE IF NOT EXISTS {t_vec} (
              perfume_key  TEXT PRIMARY KEY,
              model_id     TEXT NOT NULL,
              embedding    vector({dim}),
              name         TEXT,
              brand        TEXT,
              url          TEXT,
              rating       DOUBLE PRECISION,
              rating_count INTEGER,
              gender       TEXT,
              document     TEXT
            )""",
        f"""CREATE TABLE IF NOT EXISTS {t_term} (
              perfume_key TEXT NOT NULL,
              block       TEXT NOT NULL,
              label       TEXT NOT NULL,
              weight      REAL NOT NULL,
              PRIMARY KEY (perfume_key, block, label)
            )""",
        f"CREATE INDEX IF NOT EXISTS {t_term}_tim ON {t_term} (block, label)",
        f"CREATE INDEX IF NOT EXISTS {t_vec}_brand ON {t_vec} (brand)",
    ]


class PgVectorRetriever:
    """Tra cứu hoàn toàn bằng SQL trên Postgres + pgvector."""

    def __init__(self, con, embedder=None, model_id: str | None = None,
                 prefix: str = "") -> None:
        """`prefix` đổi tên bảng, để TEST không ghi vào dữ liệu thật.

        Đã dính thật: `tests/test_pgvector.py` chạy trước, tạo `perfume_vectors`
        dạng vector(64) của embedder hashing, rồi lần nạp model thật đổ với
        `expected 64 dimensions, not 384`. Số chiều chỉ là triệu chứng; chuyện
        nặng hơn là test đang TRUNCATE đúng bảng mà production dùng.
        """
        self._con = con
        self._embedder = embedder
        self._model_id = model_id or (embedder.model_id if embedder else None)
        self.t_vec = f"{prefix}{BANG_VECTOR}"
        self.t_term = f"{prefix}{BANG_TERM}"

    # --------------------------------------------------------------- khởi tạo
    @classmethod
    def load(cls, rows: Iterable[Row], embedder, con=None,
             dsn_: str | None = None, reset: bool = True,
             prefix: str = "") -> "PgVectorRetriever":
        """Nạp dữ liệu vào Postgres rồi trả về retriever đã sẵn sàng.

        NẠP, không phải DI TRÚ: nguồn sự thật vẫn là bronze/silver, nên dựng lại
        bảng này là chuyện vặt. Vì vậy mặc định `reset=True` — nạp lại từ đầu
        luôn đúng hơn là cố cập nhật từng phần rồi để sót.
        """
        rows = list(rows)
        con = con or connect(dsn_)
        dim = embedder.dimensions
        # `load` là classmethod — chưa có `self` ở đây, nên tên bảng phải ghép từ
        # hằng số module.
        t_vec, t_term = f"{prefix}{BANG_VECTOR}", f"{prefix}{BANG_TERM}"

        with con.cursor() as cur:
            # Số chiều nằm trong KIỂU CỘT, nên `CREATE TABLE IF NOT EXISTS` không
            # sửa được. Đổi model mà giữ bảng cũ thì lỗi hiện ra lúc INSERT:
            # `expected 64 dimensions, not 384`. Bảng này chỉ là kho phục vụ, nạp
            # lại từ Parquet trong vài giây — nên dựng lại là đúng, giữ mới là sai.
            cur.execute(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = to_regclass(%s) AND attname = 'embedding'",
                (t_vec,))
            cu = cur.fetchone()
            if cu and cu[0] not in (None, -1) and int(cu[0]) != dim:
                log.warning("Bảng %s đang là vector(%s) nhưng model cho %d chiều "
                            "— dựng lại bảng.", t_vec, cu[0], dim)
                cur.execute(f"DROP TABLE IF EXISTS {t_vec}")
                cur.execute(f"DROP TABLE IF EXISTS {t_term}")
            for cau in _ddl(dim, t_vec, t_term):
                cur.execute(cau)
            if reset:
                cur.execute(f"TRUNCATE {t_vec}")
                cur.execute(f"TRUNCATE {t_term}")

        self = cls(con, embedder=embedder, model_id=embedder.model_id,
                   prefix=prefix)
        self._nap(rows, dim)
        return self

    def _nap(self, rows: list[Row], dim: int) -> None:
        from ..embedding.document import document

        from ..vectors.index import _usable

        # Lọc chai giống `VectorIndex` làm. Nếu hai adapter không đồng ý chai nào
        # TỒN TẠI thì chúng không thay thế được cho nhau, dù mọi phép tính đều
        # đúng — và chỗ lệch đó sẽ hiện ra dưới dạng "sao bản kia ra nhiều hơn".
        tho = {}
        for row in rows:
            khoa = url_key(row.url)
            if khoa and _usable(row):
                tho[khoa] = row
        khoa_sap = sorted(tho)

        # Khối thưa tính bằng ĐÚNG hàm mà bản in-memory dùng, nên hai adapter
        # không thể lệch nhau về cách chấm điểm theo note.
        idf = features.build_idf([tho[k] for k in khoa_sap])
        tai_lieu = {k: document(tho[k]) for k in khoa_sap}
        dac = self._embedder.embed_documents([tai_lieu[k] for k in khoa_sap])

        hang_v, hang_t = [], []
        for i, k in enumerate(khoa_sap):
            r = tho[k]
            v = list(dac[i])
            if len(v) != dim:
                raise ValueError(f"vector {len(v)} chiều, bảng khai {dim}")
            hang_v.append((k, self._model_id, "[" + ",".join(
                f"{x:.6f}" for x in v) + "]", r.name, r.brand, r.url,
                r.rating, r.rating_count, r.gender, tai_lieu[k]))
            for dim_ten, w in features.vector(r, idf).items():
                prefix, _, label = dim_ten.partition(":")
                hang_t.append((k, _BLOCK_IN.get(prefix, prefix), label,
                               float(w)))

        with self._con.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {self.t_vec} (perfume_key, model_id, embedding, "
                "name, brand, url, rating, rating_count, gender, document) "
                "VALUES (%s,%s,%s::vector,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (perfume_key) DO UPDATE SET "
                "model_id=EXCLUDED.model_id, embedding=EXCLUDED.embedding",
                hang_v)
            cur.executemany(
                f"INSERT INTO {self.t_term} (perfume_key, block, label, weight) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", hang_t)
        log.info("Nạp %d chai, %d dòng term vào Postgres (%s, %d chiều).",
                 len(hang_v), len(hang_t), self._model_id, dim)

    # --------------------------------------------------------------- tiện ích
    def __len__(self) -> int:
        with self._con.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {self.t_vec}")
            return int(cur.fetchone()[0])

    def _kiem_model(self) -> None:
        """Chặn trộn vector hai model ở CỬA, không để đi tìm sau."""
        with self._con.cursor() as cur:
            cur.execute(f"SELECT DISTINCT model_id FROM {self.t_vec}")
            co = [r[0] for r in cur.fetchall()]
        if len(co) > 1:
            raise ports.UnknownPerfume(
                f"Bảng chứa vector của nhiều model: {co}. Nạp lại toàn bộ.")
        if co and self._model_id and co[0] != self._model_id:
            from ..embedding.ports import ModelMismatch
            raise ModelMismatch(
                f"Bảng sinh bởi {co[0]!r} nhưng đang hỏi bằng "
                f"{self._model_id!r}. Nạp lại bằng `perfume-intel vectordb load`.")

    _COT = ("name", "brand", "url", "rating", "rating_count", "gender")

    def _loc_sql(self, q: Query, bien: list) -> str:
        dieu = []
        if q.gender:
            dieu.append("v.gender = %s")
            bien.append(q.gender)
        if q.min_votes:
            dieu.append("COALESCE(v.rating_count, 0) >= %s")
            bien.append(q.min_votes)
        return (" AND " + " AND ".join(dieu)) if dieu else ""

    @staticmethod
    def _match(hang, why=()) -> Match:
        return Match(perfume_key=hang[0], score=round(float(hang[1]), 6),
                     name=hang[2], brand=hang[3], url=hang[4],
                     rating=hang[5], rating_count=hang[6], gender=hang[7],
                     why=tuple(why))

    def _ly_do(self, keys: Sequence[str], dims: Sequence[str],
               so: int = 3) -> dict[str, tuple[Reason, ...]]:
        """Lý do giống nhau, lấy bằng MỘT câu join cho cả trang kết quả.

        Vector đặc không có chiều nào có tên, nên phần này lấy từ bảng thưa. Đây
        là chỗ hai lựa chọn (embedding đặc + chatbot biết giải thích) gặp nhau.
        """
        if not keys or not dims:
            return {}
        blocks, labels = _tach_cap(dims)
        with self._con.cursor() as cur:
            # Postgres KHÔNG nhận tham số kiểu tuple: `(a,b) = ANY(%s)` với list
            # tuple ném `input of anonymous composite types is not implemented`.
            # Cách đúng là hai mảng song song rồi `unnest` chúng thành bảng hai cột.
            cur.execute(
                f"SELECT perfume_key, block, label, weight FROM {self.t_term} "
                "WHERE perfume_key = ANY(%s) AND (block, label) IN "
                "  (SELECT * FROM unnest(%s::text[], %s::text[])) "
                "ORDER BY weight DESC",
                (list(keys), blocks, labels))
            hang = cur.fetchall()
        ra: dict[str, list[Reason]] = {}
        for k, block, label, w in hang:
            if len(ra.setdefault(k, [])) < so:
                ra[k].append(Reason(block=block, label=label,
                                    weight=round(float(w), 4)))
        return {k: tuple(v) for k, v in ra.items()}

    # Khối nói về MÙI. Hoàn cảnh và độ mạnh không thuộc đây: chúng trả lời "dùng
    # khi nào", không trả lời "mùi gì".
    _KHOI_MUI = (ports.ACCORD, ports.NOTE)

    def _them_mui_cua_chai(self, keys: Sequence[str], da_co: dict,
                           so: int = 3) -> dict:
        """Bù thêm lý do bằng mùi mạnh nhất của chính từng chai.

        Giữ nguyên lý do đã có (khớp từ câu hỏi — cụ thể hơn), rồi điền tiếp cho
        tới `so` bằng accord/note nặng nhất của chai đó. Một lần join cho cả trang.
        """
        if not keys:
            return da_co
        with self._con.cursor() as cur:
            cur.execute(
                "SELECT perfume_key, block, label, weight FROM ("
                "  SELECT perfume_key, block, label, weight,"
                "         row_number() OVER (PARTITION BY perfume_key"
                "                            ORDER BY weight DESC) AS hang"
                f"  FROM {self.t_term} WHERE perfume_key = ANY(%s)"
                "    AND block = ANY(%s)) x "
                "WHERE hang <= %s ORDER BY perfume_key, weight DESC",
                (list(keys), list(self._KHOI_MUI), so))
            hang = cur.fetchall()
        ra = {k: list(v) for k, v in da_co.items()}
        for k, block, label, w in hang:
            co = ra.setdefault(k, [])
            if len(co) >= so or any(r.label == label for r in co):
                continue
            co.append(Reason(block=block, label=label, weight=round(float(w), 4)))
        return {k: tuple(v) for k, v in ra.items()}

    # ----------------------------------------------------------------- tìm
    def search(self, query: Query) -> SearchResult:
        if query.empty:
            return SearchResult()
        self._kiem_model()
        if query.like_perfume:
            return self._theo_chai(query)
        # Có note/accord cụ thể -> nhánh term (chính xác, giải thích được).
        if query.notes or query.accords:
            return self._theo_term(query)
        # Có câu tự do -> nhánh ngữ nghĩa, KỂ CẢ khi cũng có hoàn cảnh (hoàn cảnh
        # thành bộ lọc trong `_theo_cau`).
        #
        # Điều kiện cũ có `or query.occasions`, nên chỉ cần có mùa là `text` bị bỏ
        # hẳn. `InMemoryRetriever` đã sửa chỗ này, bản này thì chưa — nên hai
        # adapter cho ra lý do khác nhau trên cùng một Query: bản kia nói được
        # "mùi leather", bản này chỉ nói "hợp mùa lạnh". Thấy đúng cảnh đó khi thử
        # hội thoại 6 lượt qua API.
        if (query.text or "").strip():
            return self._theo_cau(query)
        return self._theo_term(query)

    # --- nhánh 1: câu tự do, tìm bằng vector đặc ---------------------------
    def _theo_cau(self, q: Query) -> SearchResult:
        if self._embedder is None:
            raise PgUnavailable("Chưa có embedder nên không hỏi được câu tự do.")
        v = self._embedder.embed_query(q.text or "")
        vs = "[" + ",".join(f"{x:.6f}" for x in v) + "]"
        bien: list = [vs]
        loc = self._loc_sql(q, bien)

        # Hoàn cảnh làm BỘ LỌC cho nhánh ngữ nghĩa.
        #
        # Thiếu phần này thì câu "mùi gỗ trầm ấm cho buổi tối mùa đông" phải chọn
        # một trong hai: hoặc đi nhánh term và bỏ hẳn "gỗ trầm", hoặc đi nhánh
        # vector và bỏ hẳn "mùa đông". Đã thấy thật: nó ra Al Qiam Gold (khớp
        # winter+night) thay vì Green Wood. Lọc thay vì cộng điểm, vì cộng hai
        # thang điểm khác nhau là chỗ rất dễ tự lừa mình.
        occ = [a.strip().lower() for a in q.occasions
               if (a or "").strip().lower() in ports.OCCASIONS]
        if occ:
            loc += (f" AND EXISTS (SELECT 1 FROM {self.t_term} t "
                    "WHERE t.perfume_key = v.perfume_key "
                    "AND t.block = %s AND t.label = ANY(%s))")
            bien += [ports.OCCASION, occ]

        bien += [vs, q.limit]
        with self._con.cursor() as cur:
            cur.execute(
                "SELECT v.perfume_key, 1 - (v.embedding <=> %s::vector) AS diem,"
                " v.name, v.brand, v.url, v.rating, v.rating_count, v.gender "
                f"FROM {self.t_vec} v WHERE v.embedding IS NOT NULL{loc} "
                "ORDER BY v.embedding <=> %s::vector LIMIT %s", bien)
            hang = cur.fetchall()
        dims = self._dims_tu_cau(q.text or "")

        # SÀN TÍN HIỆU. Tìm bằng vector đặc thì LUÔN có láng giềng gần nhất, kể cả
        # khi câu hỏi chẳng liên quan gì — nên nếu không chặn, một câu vô nghĩa vẫn
        # ra 5 chai trông rất tự tin, và chatbot sẽ đem 5 chai đó đi tư vấn.
        #
        # Sàn đặt rất thấp có chủ đích: nó chỉ bắt trường hợp KHÔNG CÓ TÍN HIỆU
        # NÀO (không một từ nào liên quan). Ngưỡng "đủ tốt để trả lời" là quyết
        # định sản phẩm, cao hơn nhiều, và thuộc tầng trên chứ không thuộc đây.
        if hang and float(hang[0][1]) < MIN_SIM and not dims:
            return SearchResult(unknown=(q.text or "",))

        keys = [h[0] for h in hang]
        # LÝ DO Ở NHÁNH NGỮ NGHĨA PHẢI NÓI VỀ MÙI, KHÔNG CHỈ VỀ DỊP.
        #
        # Câu hỏi tiếng Việt ("mùi gỗ trầm ấm") không chứa tên nhãn tiếng Anh, nên
        # `_dims_tu_cau` thường chỉ khớp được trục hoàn cảnh. Nếu chỉ dựa vào đó
        # thì lý do ra "hợp mùa lạnh, hợp buổi tối" — đúng nhưng vô dụng, vì nó
        # không nói chai đó MÙI GÌ.
        #
        # Phép khớp ở đây là ngữ nghĩa nên không quy được về từng chữ trong câu
        # hỏi. Thứ trung thực và hữu ích hơn: mùi mạnh nhất của chính chai đó.
        ly_do = self._ly_do(keys, dims) if q.explain else {}
        if q.explain:
            ly_do = self._them_mui_cua_chai(keys, ly_do)
        return SearchResult(
            matches=tuple(self._match(h, ly_do.get(h[0], ())) for h in hang),
            resolved={q.text or "": ", ".join(
                d.partition(":")[2] for d in dims)} if dims else {},
            unknown=() if (hang or dims) else (q.text or "",))

    def _dims_tu_cau(self, text: str) -> list[str]:
        """Những nhãn có thật mà câu hỏi nhắc tới — chỉ để GIẢI THÍCH.

        Không dùng để chấm điểm (việc đó của vector đặc), nên không khớp được gì
        cũng không sao: kết quả vẫn đúng, chỉ là không có dòng lý do.
        """
        import re
        tu = {t for t in re.findall(r"[0-9a-zA-ZÀ-ỹ]+", text.lower())
              if len(t) > 1}
        if not tu:
            return []
        # Khớp MỘT PHẦN: người ta gõ "oud", nhãn trong kho là "agarwood (oud)".
        # Khớp đúng tuyệt đối thì gần như không bao giờ tìm ra gì, và dòng giải
        # thích sẽ luôn trống dù chai trả về đúng là có oud.
        with self._con.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT block, label FROM {self.t_term} t "
                "WHERE EXISTS (SELECT 1 FROM unnest(%s::text[]) w "
                "              WHERE t.label = w OR t.label LIKE '%%'||w||'%%')"
                " LIMIT 24", (sorted(tu),))
            return [f"{_BLOCK_OUT.get(b, b)}:{l}" for b, l in cur.fetchall()]

    # --- nhánh 2: theo note/accord/hoàn cảnh, tính trên bảng thưa ----------
    def _theo_term(self, q: Query) -> SearchResult:
        dims, resolved, unknown = self._giai_term(q)
        if not dims:
            return SearchResult(resolved=resolved, unknown=tuple(unknown))
        blocks, labels = _tach_cap(dims)
        bien: list = [blocks, labels]
        loc = self._loc_sql(q, bien)
        bien.append(q.limit)
        with self._con.cursor() as cur:
            cur.execute(
                "SELECT v.perfume_key, SUM(t.weight) AS diem, v.name, v.brand,"
                " v.url, v.rating, v.rating_count, v.gender "
                f"FROM {self.t_term} t JOIN {self.t_vec} v USING (perfume_key) "
                "WHERE (t.block, t.label) IN "
                f"  (SELECT * FROM unnest(%s::text[], %s::text[])){loc} "
                "GROUP BY v.perfume_key, v.name, v.brand, v.url, v.rating,"
                " v.rating_count, v.gender "
                "ORDER BY diem DESC, v.perfume_key LIMIT %s", bien)
            hang = cur.fetchall()
        ly_do = self._ly_do([h[0] for h in hang], dims) if q.explain else {}
        if q.explain:
            # Bù mùi ở nhánh term NỮA, không chỉ nhánh ngữ nghĩa. Hỏi theo hoàn
            # cảnh thuần ("cho mùa đông") thì lý do chỉ có `occasion` — đúng nhưng
            # không nói chai đó mùi gì, mà đó mới là thứ người bán cần.
            ly_do = self._them_mui_cua_chai([h[0] for h in hang], ly_do)
        return SearchResult(
            matches=tuple(self._match(h, ly_do.get(h[0], ())) for h in hang),
            resolved=resolved, unknown=tuple(unknown))

    def _giai_term(self, q: Query) -> tuple[list[str], dict, list[str]]:
        """Tên người ta gõ -> nhãn có thật trong kho. Khớp một phần, và BÁO LẠI."""
        dims: list[str] = []
        resolved: dict[str, str] = {}
        unknown: list[str] = []
        for block, names in ((ports.NOTE, q.notes), (ports.ACCORD, q.accords)):
            for ten in names:
                goc = (ten or "").strip().lower()
                if not goc:
                    continue
                with self._con.cursor() as cur:
                    cur.execute(
                        f"SELECT label, count(*) c FROM {self.t_term} "
                        "WHERE block = %s AND (label = %s OR label LIKE %s) "
                        "GROUP BY label ORDER BY c DESC, length(label) LIMIT 1",
                        (block, goc, f"%{goc}%"))
                    khop = cur.fetchone()
                if not khop:
                    unknown.append(goc)
                    continue
                if khop[0] != goc:
                    resolved[goc] = khop[0]
                dims.append(f"{_BLOCK_OUT[block]}:{khop[0]}")
        for a in q.occasions:
            a = (a or "").strip().lower()
            if a in ports.OCCASIONS:
                dims.append(f"{features.OCCASION}:{a}")
            elif a:
                unknown.append(a)
        return dims, resolved, unknown

    # --- nhánh 3: giống một chai gốc --------------------------------------
    def _theo_chai(self, q: Query) -> SearchResult:
        goc, mo_ho = self._tim_chai(q.like_perfume or "")
        if goc is None:
            raise UnknownPerfume(q.like_perfume or "")
        bien: list = [goc[0], goc[0]]
        loc = self._loc_sql(q, bien)
        if not q.include_same_brand:
            loc += " AND COALESCE(v.brand,'') <> COALESCE((SELECT brand FROM " \
                   f"{self.t_vec} WHERE perfume_key = %s), '')"
            bien.append(goc[0])
        bien.append(q.limit)
        with self._con.cursor() as cur:
            cur.execute(
                "WITH g AS (SELECT embedding FROM " + self.t_vec +
                " WHERE perfume_key = %s) "
                "SELECT v.perfume_key, 1 - (v.embedding <=> g.embedding),"
                " v.name, v.brand, v.url, v.rating, v.rating_count, v.gender "
                f"FROM {self.t_vec} v, g "
                "WHERE v.perfume_key <> %s AND v.embedding IS NOT NULL"
                f"{loc} ORDER BY v.embedding <=> g.embedding LIMIT %s", bien)
            hang = cur.fetchall()
        ly_do = {}
        if q.explain and hang:
            with self._con.cursor() as cur:
                cur.execute(
                    f"SELECT block, label FROM {self.t_term} "
                    "WHERE perfume_key = %s ORDER BY weight DESC LIMIT 12",
                    (goc[0],))
                dims = [f"{_BLOCK_OUT.get(b, b)}:{l}" for b, l in cur.fetchall()]
            ly_do = self._ly_do([h[0] for h in hang], dims)
        # Seed cũng cần lý do về mùi của CHÍNH nó. Không có thì câu trả lời về đúng
        # chai đó phải nói "chưa có đủ dữ liệu về mùi" trong khi dữ liệu có đủ —
        # đã thấy thật khi thử hội thoại. `InMemoryRetriever` làm cùng việc này.
        ly_do_goc = (self._them_mui_cua_chai([goc[0]], {}).get(goc[0], ())
                     if q.explain else ())
        return SearchResult(
            matches=tuple(self._match(h, ly_do.get(h[0], ())) for h in hang),
            seed=self._match((goc[0], 1.0, *goc[1:]), ly_do_goc),
            ambiguous=tuple(self._match((m[0], 1.0, *m[1:]))
                            for m in mo_ho[:5]) if len(mo_ho) > 1 else ())

    def _tim_chai(self, dau_vao: str):
        khoa = url_key(dau_vao)
        with self._con.cursor() as cur:
            cur.execute(
                "SELECT perfume_key, name, brand, url, rating, rating_count,"
                f" gender FROM {self.t_vec} WHERE perfume_key = %s", (khoa,))
            hit = cur.fetchone()
            if hit:
                return hit, [hit]
            cur.execute(
                "SELECT perfume_key, name, brand, url, rating, rating_count,"
                f" gender FROM {self.t_vec} WHERE lower(name) = lower(%s) "
                "OR lower(name) LIKE lower(%s) "
                "ORDER BY COALESCE(rating_count,0) DESC LIMIT 10",
                (dau_vao, f"%{dau_vao}%"))
            nhieu = cur.fetchall()
        return (nhieu[0] if nhieu else None), nhieu

    # ---------------------------------------------------------------- hãng
    def similar_brands(self, brand: str, limit: int = 10,
                       min_perfumes: int = 1,
                       explain: bool = False) -> BrandResult:
        """Chân dung hãng = trung bình vector các chai của hãng, tính trong SQL."""
        with self._con.cursor() as cur:
            cur.execute(
                f"SELECT brand, count(*) FROM {self.t_vec} "
                "WHERE brand IS NOT NULL AND (lower(brand) = lower(%s) "
                "OR lower(brand) LIKE lower(%s)) GROUP BY brand "
                "ORDER BY count(*) DESC LIMIT 1", (brand, f"%{brand}%"))
            dich = cur.fetchone()
            if not dich:
                raise UnknownBrand(brand)
            cur.execute(
                f"""WITH tb AS (
                      SELECT brand, AVG(embedding)::vector AS v, count(*) AS n
                      FROM {self.t_vec} WHERE brand IS NOT NULL
                      GROUP BY brand HAVING count(*) >= %s),
                    g AS (SELECT v FROM tb WHERE brand = %s)
                    SELECT tb.brand, tb.n, 1 - (tb.v <=> g.v)
                    FROM tb, g WHERE tb.brand <> %s
                    ORDER BY tb.v <=> g.v LIMIT %s""",
                (min_perfumes, dich[0], dich[0], limit))
            hang = cur.fetchall()
        return BrandResult(
            target=BrandMatch(brand=dich[0], perfumes=int(dich[1]), score=1.0),
            matches=tuple(BrandMatch(brand=h[0], perfumes=int(h[1]),
                                     score=round(float(h[2]), 6))
                          for h in hang))

    # ------------------------------------------------------------- từ vựng
    def vocabulary(self, block: str) -> Sequence[str]:
        if block == ports.OCCASION:
            return list(ports.OCCASIONS)
        if block not in ports.BLOCKS:
            raise ValueError(f"Khối không có: {block!r}. "
                             f"Chỉ có: {', '.join(ports.BLOCKS)}.")
        with self._con.cursor() as cur:
            cur.execute(f"SELECT DISTINCT label FROM {self.t_term} "
                        "WHERE block = %s ORDER BY label", (block,))
            return [r[0] for r in cur.fetchall()]
