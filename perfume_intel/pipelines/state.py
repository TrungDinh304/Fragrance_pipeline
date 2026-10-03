"""Sổ theo dõi tiến độ crawl (SQLite) — nền của phần lên lịch.

Trước đây trạng thái được suy ra từ tên file và từ việc đọc lại file kết quả:
đủ dùng cho một mẻ chạy liền, nhưng không trả lời được những câu mà việc nhỏ
giọt hằng ngày bắt buộc phải trả lời:

    hãng nào đã xong, hãng nào đang dở, dở tới chai nào?
    chai nào lỗi, lỗi mấy lần, khi nào nên thử lại?
    hôm qua chạy cái gì, tiêu bao nhiêu request?

Hai bảng công việc + một bảng lịch sử:

    brands    một dòng một hãng — đã lấy mục lục chưa, còn nợ bao nhiêu chai
    perfumes  một dòng một chai — pending / done / failed
    runs      một dòng một lần chạy — để đối chiếu khi có chuyện

Đơn vị công việc là CHAI, không phải hãng. Nhờ vậy một hãng lớn (Avon 1.379
chai) tự tràn qua nhiều ngày mà hôm sau vẫn đi tiếp đúng chỗ dừng.

Mọi khoá URL đều đi qua `url_key` — cùng quy ước với phần còn lại của project.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config
from ..core.text import url_key

log = logging.getLogger(__name__)

PENDING, DONE, FAILED = "pending", "done", "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS brands (
    brand_key       TEXT PRIMARY KEY,
    brand_url       TEXT NOT NULL,
    brand_name      TEXT,
    alphabet        TEXT,
    popular_rank    INTEGER,
    priority        REAL NOT NULL DEFAULT 0,
    products_status TEXT NOT NULL DEFAULT 'pending',
    products_at     TEXT,
    perfume_total   INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    blocked_until   TEXT,
    last_error      TEXT,
    added_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS perfumes (
    perfume_key  TEXT PRIMARY KEY,
    perfume_url  TEXT NOT NULL,
    brand_key    TEXT NOT NULL,
    perfume_name TEXT,
    comments     INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',
    crawled_at   TEXT,
    fail_count   INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
);

CREATE INDEX IF NOT EXISTS ix_perfumes_brand  ON perfumes(brand_key, status);
CREATE INDEX IF NOT EXISTS ix_perfumes_status ON perfumes(status, comments DESC);
CREATE INDEX IF NOT EXISTS ix_brands_queue    ON brands(products_status, priority DESC);

-- Site nói "dừng lại" (429 / Cloudflare) thì đó là chuyện của CẢ IP này, không
-- phải của riêng một hãng hay một lệnh. Để riêng một bảng vì ngữ nghĩa khác hẳn
-- `brands.blocked_until` (hãng đó lỗi) — và vì mọi lệnh đều phải ghi được vào
-- đây, kể cả lệnh không đụng gì tới hàng đợi hãng như `brands` hay `links`.
CREATE TABLE IF NOT EXISTS site_cooldown (
    site          TEXT PRIMARY KEY,
    blocked_until TEXT NOT NULL,
    reason        TEXT,
    source        TEXT,
    recorded_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    budget        INTEGER,
    requests_used INTEGER NOT NULL DEFAULT 0,
    brands_touched INTEGER NOT NULL DEFAULT 0,
    perfumes_done INTEGER NOT NULL DEFAULT 0,
    perfumes_failed INTEGER NOT NULL DEFAULT 0,
    stopped_reason TEXT,
    note          TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class BrandWork:
    """Một hãng lấy ra từ hàng đợi, kèm phần còn nợ."""

    brand_key: str
    brand_url: str
    brand_name: str | None
    products_done: bool
    perfume_total: int
    pending: int

    @property
    def label(self) -> str:
        return self.brand_name or self.brand_url


class CrawlState:
    """Sổ theo dõi. Mở/đóng quanh mỗi thao tác, không giữ kết nối lâu."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or config.STATE_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        # Ghi dần suốt nhiều giờ: WAL chịu ngắt điện tốt hơn mặc định.
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------ nạp hàng đợi
    def seed_brands(self, brands: list[dict]) -> tuple[int, int]:
        """Nạp danh mục hãng vào hàng đợi -> (số thêm mới, số đã có).

        Chạy lại nhiều lần được: hãng đã có chỉ được cập nhật phần metadata
        (tên, chữ cái, thứ hạng), KHÔNG chạm tới tiến độ.
        """
        added = seen = 0
        now = _now()
        with self._conn() as conn:
            for raw in brands:
                url = raw.get("brand_url")
                key = url_key(url)
                if not key:
                    continue
                rank = raw.get("popular_rank")
                cur = conn.execute(
                    "SELECT 1 FROM brands WHERE brand_key = ?", (key,)).fetchone()
                if cur:
                    seen += 1
                    conn.execute(
                        "UPDATE brands SET brand_name = COALESCE(?, brand_name),"
                        " alphabet = COALESCE(?, alphabet),"
                        " popular_rank = COALESCE(?, popular_rank),"
                        " priority = ? WHERE brand_key = ?",
                        (raw.get("brand_name"), raw.get("alphabet"), rank,
                         _brand_priority(rank), key))
                    continue
                conn.execute(
                    "INSERT INTO brands (brand_key, brand_url, brand_name,"
                    " alphabet, popular_rank, priority, added_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, url, raw.get("brand_name"), raw.get("alphabet"),
                     rank, _brand_priority(rank), now))
                added += 1
        return added, seen

    def seed_perfumes(self, brand_key: str, perfumes: list) -> int:
        """Nạp mục lục chai của một hãng -> số chai mới thêm.

        Chai đã có thì chỉ cập nhật `comments` (tín hiệu ưu tiên có thể tăng),
        không bao giờ đặt lại `status` — nếu không mỗi lần làm mới mục lục sẽ
        xoá sạch tiến độ.
        """
        added = 0
        with self._conn() as conn:
            for p in perfumes:
                key = url_key(getattr(p, "perfume_url", None))
                if not key:
                    continue
                comments = getattr(p, "comments", None) or 0
                row = conn.execute(
                    "SELECT 1 FROM perfumes WHERE perfume_key = ?",
                    (key,)).fetchone()
                if row:
                    conn.execute(
                        "UPDATE perfumes SET comments = MAX(comments, ?),"
                        " perfume_name = COALESCE(?, perfume_name)"
                        " WHERE perfume_key = ?",
                        (comments, getattr(p, "perfume_name", None), key))
                    continue
                conn.execute(
                    "INSERT INTO perfumes (perfume_key, perfume_url, brand_key,"
                    " perfume_name, comments) VALUES (?, ?, ?, ?, ?)",
                    (key, p.perfume_url, brand_key,
                     getattr(p, "perfume_name", None), comments))
                added += 1

            total = conn.execute(
                "SELECT COUNT(*) FROM perfumes WHERE brand_key = ?",
                (brand_key,)).fetchone()[0]
            conn.execute("UPDATE brands SET perfume_total = ? WHERE brand_key = ?",
                         (total, brand_key))
        return added

    # ----------------------------------------------------------- chọn việc
    def next_brands(self, limit: int,
                    min_comments: int = 0) -> list[BrandWork]:
        """Các hãng nên làm tiếp, ưu tiên cao trước.

        Bỏ qua hãng đang trong thời gian nghỉ (`blocked_until`) và hãng đã xong
        hẳn (có mục lục và không còn chai nào pending).

        `min_comments` phải vào TẬN ĐÂY, không chỉ ở `pending_perfumes`: nếu chỉ
        lọc ở bước sau thì một hãng còn 300 chai pending nhưng đều dưới ngưỡng
        vẫn được chọn, rồi `pending_perfumes` trả về rỗng — lượt chạy tiêu mất
        một suất hãng mà không làm gì, và `--dry-run` thì báo số nợ sai.
        """
        now = _now()
        sql = """
        SELECT b.brand_key, b.brand_url, b.brand_name, b.products_status,
               b.perfume_total,
               (SELECT COUNT(*) FROM perfumes p
                 WHERE p.brand_key = b.brand_key AND p.status = 'pending'
                   AND p.comments >= ?)
               AS pending
          FROM brands b
         WHERE (b.blocked_until IS NULL OR b.blocked_until <= ?)
           AND (b.products_status = 'pending' OR pending > 0)
         ORDER BY b.priority DESC, b.popular_rank IS NULL, b.popular_rank,
                  b.brand_name
         LIMIT ?
        """
        with self._conn() as conn:
            rows = conn.execute(sql, (min_comments, now, limit)).fetchall()
        return [BrandWork(r["brand_key"], r["brand_url"], r["brand_name"],
                          r["products_status"] == DONE, r["perfume_total"],
                          r["pending"]) for r in rows]

    def pending_perfumes(self, brand_key: str, limit: int,
                         min_comments: int = 0) -> list[tuple[str, str]]:
        """Chai còn nợ của một hãng -> [(key, url)], nhiều bình luận trước.

        `comments` là tín hiệu cộng đồng: chai nhiều người bàn vừa đáng lấy
        trước, vừa là chai hay đổi số liệu nhất. `min_comments` cắt hẳn phần
        đuôi — xem `config.DAILY_MIN_COMMENTS`.
        """
        if limit <= 0:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT perfume_key, perfume_url FROM perfumes"
                " WHERE brand_key = ? AND status = 'pending' AND comments >= ?"
                " ORDER BY comments DESC, perfume_key LIMIT ?",
                (brand_key, min_comments, limit)).fetchall()
        return [(r["perfume_key"], r["perfume_url"]) for r in rows]

    # ------------------------------------------------------------- đánh dấu
    def mark_products(self, brand_key: str, ok: bool,
                      error: str | None = None) -> None:
        with self._conn() as conn:
            if ok:
                conn.execute(
                    "UPDATE brands SET products_status = ?, products_at = ?,"
                    " fail_count = 0, blocked_until = NULL, last_error = NULL"
                    " WHERE brand_key = ?", (DONE, _now(), brand_key))
            else:
                conn.execute(
                    "UPDATE brands SET fail_count = fail_count + 1,"
                    " last_error = ? WHERE brand_key = ?", (error, brand_key))
        if not ok:
            self._apply_cooldown(brand_key)

    def mark_perfume(self, perfume_key: str, ok: bool,
                     error: str | None = None) -> None:
        with self._conn() as conn:
            if ok:
                conn.execute(
                    "UPDATE perfumes SET status = ?, crawled_at = ?,"
                    " fail_count = 0, last_error = NULL WHERE perfume_key = ?",
                    (DONE, _now(), perfume_key))
            else:
                conn.execute(
                    "UPDATE perfumes SET status = ?, fail_count = fail_count + 1,"
                    " last_error = ? WHERE perfume_key = ?",
                    (FAILED, error, perfume_key))

    def mark_done_by_url(self, urls: list[str]) -> int:
        """Đánh dấu xong theo URL, chỉ với chai ĐÃ có trong sổ -> số dòng đổi.

        Dùng để ghi nhận dữ liệu crawl từ trước khi có phần lên lịch. Chai chưa
        có trong sổ thì bỏ qua: không biết nó thuộc hãng nào, và khi mục lục của
        hãng đó được lấy thì `daily` sẽ tự nhận ra file đã có dữ liệu.
        """
        keys = [url_key(u) for u in urls]
        keys = [k for k in keys if k]
        if not keys:
            return 0
        now = _now()
        changed = 0
        with self._conn() as conn:
            for i in range(0, len(keys), 500):     # tránh giới hạn tham số SQLite
                lot = keys[i:i + 500]
                marks = ",".join("?" * len(lot))
                cur = conn.execute(
                    f"UPDATE perfumes SET status = ?, crawled_at = COALESCE(crawled_at, ?)"
                    f" WHERE perfume_key IN ({marks}) AND status != ?",
                    (DONE, now, *lot, DONE))
                changed += cur.rowcount
        return changed

    def brand_key_for(self, brand_url: str) -> str | None:
        """Khoá hãng nếu hãng đó đã có trong sổ."""
        key = url_key(brand_url)
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM brands WHERE brand_key = ?",
                               (key,)).fetchone()
        return key if row else None

    def _apply_cooldown(self, brand_key: str) -> None:
        """Hãng lỗi liên tiếp thì nghỉ dần lâu hơn, khỏi húc đầu vào tường."""
        with self._conn() as conn:
            row = conn.execute("SELECT fail_count FROM brands WHERE brand_key = ?",
                               (brand_key,)).fetchone()
            if row is None:
                return
            fails = row["fail_count"]
            hours = config.BRAND_COOLDOWN_HOURS[
                min(fails, len(config.BRAND_COOLDOWN_HOURS)) - 1]
            until = (datetime.now(timezone.utc)
                     + timedelta(hours=hours)).isoformat(timespec="seconds")
            conn.execute("UPDATE brands SET blocked_until = ? WHERE brand_key = ?",
                         (until, brand_key))
        log.info("Hãng %s nghỉ %d giờ (lỗi lần %d).", brand_key, hours, fails)

    # ------------------------------------------------- site nghỉ (chung cả IP)
    def block_site(self, site: str, hours: float, reason: str,
                   source: str = "") -> str:
        """Ghi nhận site đang chặn mình. Trả về mốc hết nghỉ.

        KHÔNG rút ngắn thời gian nghỉ đang có: nếu một lệnh khác vừa ghi 12 giờ
        mà lệnh này chỉ muốn 1 giờ, giữ 12. Lần chặn nặng hơn luôn thắng — rút
        ngắn thời gian nghỉ là thứ duy nhất ở đây có thể gây hại thật.
        """
        until = (datetime.now(timezone.utc)
                 + timedelta(hours=hours)).isoformat(timespec="seconds")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT blocked_until FROM site_cooldown WHERE site = ?",
                (site,)).fetchone()
            if row and row["blocked_until"] > until:
                return row["blocked_until"]
            conn.execute(
                "INSERT INTO site_cooldown"
                " (site, blocked_until, reason, source, recorded_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(site) DO UPDATE SET"
                " blocked_until = excluded.blocked_until,"
                " reason = excluded.reason, source = excluded.source,"
                " recorded_at = excluded.recorded_at",
                (site, until, reason, source, _now()))
        return until

    def site_cooldown(self, site: str) -> dict | None:
        """Site có đang trong thời gian nghỉ không? Hết hạn thì trả None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM site_cooldown WHERE site = ? AND blocked_until > ?",
                (site, _now())).fetchone()
        return dict(row) if row else None

    def clear_site_cooldown(self, site: str | None = None) -> int:
        with self._conn() as conn:
            if site:
                cur = conn.execute("DELETE FROM site_cooldown WHERE site = ?",
                                   (site,))
            else:
                cur = conn.execute("DELETE FROM site_cooldown")
            return cur.rowcount

    def block_all(self, hours: float, reason: str) -> int:
        """Bị chặn cả IP -> cho MỌI hãng nghỉ, không riêng hãng đang làm.

        429 hay thử thách Cloudflare là tín hiệu ở mức thiết bị; nghỉ đúng một
        hãng rồi nhảy sang hãng khác là hiểu sai vấn đề và bị chặn sâu hơn.
        """
        until = (datetime.now(timezone.utc)
                 + timedelta(hours=hours)).isoformat(timespec="seconds")
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE brands SET blocked_until = ?, last_error = ?"
                " WHERE blocked_until IS NULL OR blocked_until < ?",
                (until, reason, until))
            return cur.rowcount

    def reset_failed(self) -> tuple[int, int]:
        """Cho các bản ghi lỗi về lại pending -> (số chai, số hãng được mở)."""
        with self._conn() as conn:
            chai = conn.execute(
                "UPDATE perfumes SET status = ?, last_error = NULL"
                " WHERE status = ?", (PENDING, FAILED)).rowcount
            hang = conn.execute(
                "UPDATE brands SET blocked_until = NULL, fail_count = 0,"
                " last_error = NULL WHERE blocked_until IS NOT NULL").rowcount
        return chai, hang

    # ------------------------------------------------------------ lịch sử run
    def start_run(self, kind: str, budget: int) -> str:
        # Có milli-giây: khoá chỉ tới giây thì hai lần chạy trong cùng một giây
        # sẽ trùng run_id và `INSERT OR REPLACE` XOÁ mất lần trước — lịch sử
        # chạy là thứ duy nhất để đối chiếu khi có chuyện, không được mất.
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S%f")[:-3]
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, kind, started_at, budget)"
                " VALUES (?, ?, ?, ?)", (run_id, kind, _now(), budget))
        return run_id

    def finish_run(self, run_id: str, *, requests_used: int, brands_touched: int,
                   perfumes_done: int, perfumes_failed: int,
                   stopped_reason: str, note: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, requests_used = ?,"
                " brands_touched = ?, perfumes_done = ?, perfumes_failed = ?,"
                " stopped_reason = ?, note = ? WHERE run_id = ?",
                (_now(), requests_used, brands_touched, perfumes_done,
                 perfumes_failed, stopped_reason, note, run_id))

    def recent_runs(self, limit: int = 10) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY run_id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- tiến độ
    def progress(self, min_comments: int = 0) -> dict:
        """Số liệu tổng quan để lệnh `queue` in ra.

        Khi `min_comments > 0`, thêm hai số: phần còn nợ ĐẠT ngưỡng (phần thật
        sự sẽ được crawl) và phần bị ngưỡng cắt. Thiếu hai số này thì `queue`
        báo "còn nợ 7.784" trong khi lịch chỉ định làm 2.288 — con số đúng
        nhưng trả lời câu hỏi khác.
        """
        with self._conn() as conn:
            def one(sql, *args):
                return conn.execute(sql, args).fetchone()[0]

            now = _now()
            return {
                "brands_total": one("SELECT COUNT(*) FROM brands"),
                "brands_products_done": one(
                    "SELECT COUNT(*) FROM brands WHERE products_status = ?", DONE),
                "brands_blocked": one(
                    "SELECT COUNT(*) FROM brands WHERE blocked_until > ?", now),
                "brands_finished": one(
                    "SELECT COUNT(*) FROM brands b WHERE b.products_status = ?"
                    " AND NOT EXISTS (SELECT 1 FROM perfumes p"
                    " WHERE p.brand_key = b.brand_key AND p.status = ?)",
                    DONE, PENDING),
                "perfumes_total": one("SELECT COUNT(*) FROM perfumes"),
                "perfumes_done": one(
                    "SELECT COUNT(*) FROM perfumes WHERE status = ?", DONE),
                "perfumes_pending": one(
                    "SELECT COUNT(*) FROM perfumes WHERE status = ?", PENDING),
                "perfumes_failed": one(
                    "SELECT COUNT(*) FROM perfumes WHERE status = ?", FAILED),
                "min_comments": min_comments,
                "pending_above": one(
                    "SELECT COUNT(*) FROM perfumes WHERE status = ?"
                    " AND comments >= ?", PENDING, min_comments),
                "pending_below": one(
                    "SELECT COUNT(*) FROM perfumes WHERE status = ?"
                    " AND comments < ?", PENDING, min_comments),
            }

    def brand_detail(self, brand: str) -> dict | None:
        """Tiến độ của một hãng, tìm theo URL hoặc theo tên."""
        key = url_key(brand)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM brands WHERE brand_key = ?"
                " OR LOWER(brand_name) = LOWER(?) LIMIT 1",
                (key, brand)).fetchone()
            if row is None:
                return None
            out = dict(row)
            for status in (PENDING, DONE, FAILED):
                out[f"perfumes_{status}"] = conn.execute(
                    "SELECT COUNT(*) FROM perfumes WHERE brand_key = ?"
                    " AND status = ?", (row["brand_key"], status)).fetchone()[0]
            return out

    def in_progress_brands(self, limit: int = 10) -> list[dict]:
        """Hãng đã lấy mục lục mà còn nợ chai — chỗ công việc đang dở."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT b.brand_name, b.brand_url, b.perfume_total,"
                " (SELECT COUNT(*) FROM perfumes p WHERE p.brand_key = b.brand_key"
                "   AND p.status = 'done') AS done"
                " FROM brands b WHERE b.products_status = 'done'"
                " AND EXISTS (SELECT 1 FROM perfumes p"
                "   WHERE p.brand_key = b.brand_key AND p.status = 'pending')"
                " ORDER BY b.priority DESC, b.brand_name LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]


def _brand_priority(popular_rank: int | None) -> float:
    """Hãng trong 'Most Popular Brands' được làm trước, theo đúng thứ hạng.

    Đây là tín hiệu cộng đồng sẵn có và miễn phí — với nhịp nhỏ giọt thì THỨ TỰ
    quan trọng hơn tổng thời gian, vì phần đầu hàng đợi là phần bạn thật sự dùng.
    """
    if popular_rank is None:
        return 0.0
    return 1000.0 - float(popular_rank)


def open_state(path: Path | None = None) -> CrawlState:
    return CrawlState(path)


__all__ = ["CrawlState", "BrandWork", "open_state", "PENDING", "DONE", "FAILED"]


# Bị chặn thì nghỉ bao lâu, nếu phía gọi không nói gì. Khớp
# `daily.BLOCK_COOLDOWN_HOURS` để mọi lệnh cư xử như nhau.
DEFAULT_BLOCK_HOURS = 12.0


def record_block(site: str, exc: BaseException, source: str,
                 hours: float = DEFAULT_BLOCK_HOURS,
                 db: Path | None = None) -> str | None:
    """Ghi vào sổ rằng site vừa chặn mình. Dùng chung cho MỌI lệnh.

    Vì sao cần: trước đây chỉ `daily` biết chuyện bị chặn, vì chỉ nó đụng tới
    sổ. Một mẻ `products` dính 429 thì không để lại dấu vết nào, nên lượt `daily`
    ngay sau đó lao vào đúng lúc site đang khó chịu nhất. Đã xảy ra thật:
    23:12:14 site trả 429 lần cuối, 23:12:19 `daily` bắt đầu gõ cửa tiếp.

    Hàm này KHÔNG được làm hỏng lệnh đang chạy: sổ ghi không được thì chỉ cảnh
    báo rồi thôi — mất một ghi chú còn hơn mất cả mẻ dữ liệu vừa crawl.
    """
    try:
        state = open_state(db)
        until = state.block_site(site, hours, str(exc).splitlines()[0], source)
        log.warning("Đã ghi vào sổ: tạm nghỉ %s tới %s (nguồn: %s). "
                    "Mọi lệnh khác sẽ thấy và tránh ra.", site, until, source)
        return until
    except Exception:
        log.warning("Không ghi được ghi chú bị chặn vào sổ.", exc_info=True)
        return None
