"""Một lần chạy theo lịch: nhỏ giọt vài hãng, trong hạn ngân sách request.

Fragrantica chặn thiết bị truy cập quá dày, nên cách duy nhất sống chung là
chia việc ra nhiều ngày. Mỗi lần chạy:

    1. lấy tối đa `max_brands` hãng ở đầu hàng đợi (ưu tiên cao trước);
    2. hãng nào chưa có mục lục thì tiêu 1 request lấy mục lục trước;
    3. crawl chi tiết các chai còn nợ của hãng đó, tới khi hết ngân sách;
    4. đánh dấu từng chai ngay khi xong, rồi ghi lại cả lần chạy.

Đơn vị công việc là CHAI nên hãng lớn tự tràn sang ngày sau: Avon 1.379 chai
với ngân sách 150/ngày là 10 ngày, và hôm sau đi tiếp đúng chai còn lại.

Ngắt mạch: dính `RateLimited` hoặc `Blocked` thì cho MỌI hãng nghỉ (đó là tín
hiệu ở mức thiết bị, không phải ở mức một hãng) rồi dừng lần chạy — phần đã
crawl vẫn nằm trong sổ và trong file kết quả.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..core import bronze
from ..core import storage
from ..core.http import Blocked, Fetcher, RateLimited
from ..sources.fragrantica.parsers import parse_brand_perfumes
from ..sources.fragrantica.scraper import SITE, FragranticaScraper
from ..core.text import output_stem, url_key
from .crawl import CrawlOptions, crawl_urls, out_base_for
from .state import CrawlState

log = logging.getLogger(__name__)

# Bị chặn ở mức thiết bị thì nghỉ hẳn, không thử hãng khác.
BLOCK_COOLDOWN_HOURS = 12


def _file_stem(brand) -> str:
    """Tên hãng dùng làm tên file. Không bao giờ để URL lọt vào tên file.

    `BrandWork.label` rơi về `brand_url` khi thiếu tên, mà URL chứa `/` và `:`
    nên sẽ tạo ra đường dẫn rác. Lấy slug cuối của URL làm phương án dự phòng.
    """
    if brand.brand_name:
        return brand.brand_name
    slug = brand.brand_url.rstrip("/").rsplit("/", 1)[-1]
    return slug.removesuffix(".html") or "khong-ro-ten"


@dataclass
class BrandOutcome:
    """Kết quả của một hãng trong lần chạy này."""

    label: str
    products_fetched: bool = False
    perfumes_done: int = 0
    perfumes_failed: int = 0
    pending_left: int = 0


@dataclass
class DailyReport:
    run_id: str = ""
    budget: int = 0
    requests_used: int = 0
    brands: list[BrandOutcome] = field(default_factory=list)
    stopped_reason: str = "hết việc"

    @property
    def perfumes_done(self) -> int:
        return sum(b.perfumes_done for b in self.brands)

    @property
    def perfumes_failed(self) -> int:
        return sum(b.perfumes_failed for b in self.brands)

    # Hai lý do dừng được coi là KHÔNG ổn, vì cả hai đều nghĩa là site đang
    # không muốn tiếp: vừa bị chặn, hoặc đang trong thời gian nghỉ do một lệnh
    # khác ghi lại. Bộ lên lịch dựa vào exit code này để ghi cảnh báo.
    NOT_OK = ("bị chặn", "site đang nghỉ")

    @property
    def ok(self) -> bool:
        return self.stopped_reason not in self.NOT_OK


class _Budget:
    """Đếm request đã tiêu. Mọi lần ra mạng đều phải đi qua đây."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.used = 0

    @property
    def left(self) -> int:
        return max(0, self.total - self.used)

    def spend(self, n: int = 1) -> None:
        self.used += n


def _fetch_products(fetcher: Fetcher, state: CrawlState, brand,
                    outcome: BrandOutcome, budget: _Budget,
                    out_dir: Path) -> bool:
    """Lấy mục lục chai của một hãng (1 request) rồi nạp vào sổ."""
    budget.spend()
    html = fetcher.get(brand.brand_url)
    if html is None:
        state.mark_products(brand.brand_key, ok=False, error="không tải được trang hãng")
        log.warning("  mục lục %s: không tải được", brand.label)
        return False

    perfumes = parse_brand_perfumes(html, brand.brand_url)
    for p in perfumes:
        p.brand_name = p.brand_name or brand.brand_name

    state.mark_products(brand.brand_key, ok=True)
    added = state.seed_perfumes(brand.brand_key, perfumes)
    outcome.products_fetched = True

    # Mục lục cũng là dữ liệu, không chỉ là đầu vào của hàng đợi.
    if perfumes:
        path = out_dir / f"{output_stem('brand_products', SITE)}.jsonl"
        storage.save_jsonl(perfumes, path, append=True)

    log.info("  mục lục %s: %d chai (%d mới)", brand.label, len(perfumes), added)
    return True


def _crawl_details(scraper: FragranticaScraper, state: CrawlState, brand,
                   outcome: BrandOutcome, budget: _Budget, out_dir: Path,
                   opts: CrawlOptions, min_comments: int = 0) -> None:
    """Crawl chi tiết các chai còn nợ của một hãng, trong hạn ngân sách."""
    todo = state.pending_perfumes(brand.brand_key, budget.left, min_comments)
    if not todo:
        return

    # Ghi vào đúng file của hãng theo quy ước sẵn có; resume=True để hôm sau
    # nối tiếp vào file cũ thay vì mở file mới theo ngày.
    out_base = out_base_for(out_dir, _file_stem(brand), SITE, resume=True)

    # Chai đã nằm trong file kết quả thì `crawl_urls` sẽ bỏ qua, nên nó không
    # bao giờ tới `on_item`. Không xử lý trước ở đây thì chúng bị đánh dấu LỖI
    # thay vì XONG — vừa sai tiến độ, vừa bị thử lại mãi. Đây cũng là cách dữ
    # liệu crawl từ trước tự động được ghi nhận, không tốn request nào.
    co_san = storage.load_scraped_urls(out_base.with_suffix(".jsonl"))
    if co_san:
        san = [(k, u) for k, u in todo if url_key(u) in co_san]
        for key, _url in san:
            state.mark_perfume(key, ok=True)
        if san:
            log.info("  %s: %d chai đã có sẵn trong file — ghi nhận, không tải lại.",
                     brand.label, len(san))
        todo = [(k, u) for k, u in todo if url_key(u) not in co_san]
        if not todo:
            return

    by_url = {url: key for key, url in todo}
    urls = [url for _key, url in todo]

    done_urls: set[str] = set()

    def on_item(record) -> None:
        done_urls.add(record.url)
        key = by_url.get(record.url)
        if key:
            state.mark_perfume(key, ok=True)

    # Tiêu ngân sách TRƯỚC khi crawl, theo số URL dự định. Cố ý tính thừa: nếu
    # tiến trình chết giữa chừng thì lần sau vẫn coi như đã tiêu, tức là nghiêng
    # về phía lịch sự với site thay vì phía crawl thêm.
    budget.spend(len(urls))
    count, _attempted = crawl_urls(scraper, urls, {}, out_base, opts,
                                   on_item=on_item)

    for url, key in by_url.items():
        if url not in done_urls:
            state.mark_perfume(key, ok=False, error="không lấy được chi tiết")

    outcome.perfumes_done = count
    outcome.perfumes_failed = len(urls) - count
    log.info("  chi tiết %s: %d/%d chai", brand.label, count, len(urls))


def run_once(state: CrawlState, scraper: FragranticaScraper,
             budget: int = config.DAILY_BUDGET,
             max_brands: int = config.DAILY_BRANDS,
             out_dir: Path | None = None,
             fmt: str = "jsonl",
             min_comments: int = config.DAILY_MIN_COMMENTS) -> DailyReport:
    """Chạy một lát ngân sách. `scraper.fetcher` dùng cho cả mục lục lẫn chi tiết."""
    out_dir = out_dir or config.raw_dir(SITE, bronze.PERFUME)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = DailyReport(budget=budget)

    # Site vừa chặn mình (có thể do lệnh KHÁC, vd một mẻ `products`)? Thì bỏ
    # lượt này. Không có bước kiểm này thì lịch cứ thế lao vào đúng lúc site
    # đang khó chịu nhất — đã xảy ra thật: 23:12:14 site trả 429 lần cuối,
    # 23:12:19 `daily` bắt đầu gõ cửa tiếp.
    cooldown = state.site_cooldown(SITE)
    if cooldown:
        report.run_id = state.start_run("daily", budget)
        report.stopped_reason = "site đang nghỉ"
        log.warning("Bỏ lượt: %s đang trong thời gian nghỉ tới %s (do `%s` ghi "
                    "lúc %s: %s). Chạy lại sau mốc đó, hoặc xoá bằng "
                    "`queue --reset-failed`.",
                    SITE, cooldown["blocked_until"], cooldown["source"],
                    cooldown["recorded_at"], cooldown["reason"])
        state.finish_run(report.run_id, requests_used=0, brands_touched=0,
                         perfumes_done=0, perfumes_failed=0,
                         stopped_reason=report.stopped_reason)
        return report

    report.run_id = state.start_run("daily", budget)
    counter = _Budget(budget)
    opts = CrawlOptions(format=fmt, resume=True)

    brands = state.next_brands(max_brands, min_comments)
    if not brands:
        log.info("Hàng đợi trống (hoặc mọi hãng đang nghỉ) — không có gì làm.")
        state.finish_run(report.run_id, requests_used=0, brands_touched=0,
                         perfumes_done=0, perfumes_failed=0,
                         stopped_reason=report.stopped_reason)
        return report

    log.info("Lần chạy %s — ngân sách %d request, tối đa %d hãng%s.",
             report.run_id, budget, max_brands,
             f", bỏ chai dưới {min_comments} bình luận" if min_comments else "")

    try:
        for brand in brands:
            if counter.left <= 0:
                report.stopped_reason = "hết ngân sách"
                break

            outcome = BrandOutcome(label=brand.label)
            report.brands.append(outcome)
            log.info("[%s] còn nợ %d/%d chai",
                     brand.label, brand.pending, brand.perfume_total)

            if not brand.products_done:
                if not _fetch_products(scraper.fetcher, state, brand,
                                       outcome, counter, out_dir):
                    continue
                brand = next((b for b in state.next_brands(max_brands,
                                                           min_comments)
                              if b.brand_key == brand.brand_key), brand)

            _crawl_details(scraper, state, brand, outcome, counter,
                           out_dir, opts, min_comments)
            detail = state.brand_detail(brand.brand_url)
            outcome.pending_left = detail["perfumes_pending"] if detail else 0
    except (RateLimited, Blocked) as exc:
        report.stopped_reason = "bị chặn"
        state.block_site(SITE, BLOCK_COOLDOWN_HOURS,
                         str(exc).splitlines()[0], "daily")
        n = state.block_all(BLOCK_COOLDOWN_HOURS, str(exc).splitlines()[0])
        log.error("Bị chặn — cho %d hãng nghỉ %d giờ. %s",
                  n, BLOCK_COOLDOWN_HOURS, str(exc).splitlines()[0])
    except KeyboardInterrupt:
        report.stopped_reason = "người dùng dừng"
        log.warning("Đã dừng theo yêu cầu người dùng.")

    if counter.left <= 0 and report.stopped_reason == "hết việc":
        report.stopped_reason = "hết ngân sách"

    report.requests_used = counter.used
    state.finish_run(report.run_id, requests_used=counter.used,
                     brands_touched=len(report.brands),
                     perfumes_done=report.perfumes_done,
                     perfumes_failed=report.perfumes_failed,
                     stopped_reason=report.stopped_reason)

    log.info("Xong %s: %d chai mới, %d lỗi, tiêu %d/%d request (%s).",
             report.run_id, report.perfumes_done, report.perfumes_failed,
             counter.used, budget, report.stopped_reason)
    return report
