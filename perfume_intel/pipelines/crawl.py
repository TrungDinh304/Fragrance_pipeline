"""Điều phối crawl: một danh sách URL, hoặc cả một thư mục CSV.

Phần này không biết mình đang crawl site nào — mọi thứ khác nhau giữa các site
đã nằm trong `SiteScraper`. Nhờ vậy `fragrantica` và `namperfume` dùng chung
một luồng, kể cả cách đặt tên file kết quả và cách `--resume` hoạt động.
"""

from __future__ import annotations

import glob as globlib
import logging
from dataclasses import dataclass
from pathlib import Path

from ..core import csv_input, lake, storage
from ..core.text import output_stem, url_key
from ..sources.base import SiteScraper

log = logging.getLogger(__name__)


@dataclass
class CrawlOptions:
    """Các lựa chọn của một lần chạy, gom lại để khỏi chuyền `args` khắp nơi."""

    out: Path | None = None
    format: str = "jsonl"          # jsonl | csv | both
    limit: int | None = None
    resume: bool = False
    recrawl: bool = False
    url_column: str | None = None
    dest_column: str | None = None

    @property
    def write_jsonl(self) -> bool:
        return self.format in ("jsonl", "both")

    @property
    def write_csv(self) -> bool:
        return self.format in ("csv", "both")


# ------------------------------------------------------------ nơi ghi kết quả
def _brand_files(out_dir: Path, brand: str, site: str) -> list[Path]:
    """File kết quả đã có của một hãng.

    `glob.escape` là bắt buộc: tên hãng đi thẳng vào mẫu glob, mà đầu vào đã có
    `Viktor & Rolf`, `Victoria's Secret`... Chỉ cần một hãng kiểu
    `Amouage [Library]` là `[...]` bị hiểu thành character class, mẫu không khớp
    chính file của nó, và cả hãng bị crawl lại từ đầu mà không báo gì.

    Không đệ quy có chủ đích: file nằm trong thư mục con là dữ liệu đã lưu trữ,
    không phải nơi để ghi nối thêm.
    """
    # Hàm này trả lời đúng câu hỏi của `--resume`: "hãng này đã có sẵn gì". Nên
    # nó cũng là chỗ đúng để kéo lake về trước khi trả lời — thiếu bước này thì
    # một container mới dựng sẽ không thấy gì và crawl lại từ đầu, dù dữ liệu
    # đang nằm nguyên trên MinIO. `ensure_local` chỉ `list` một lần mỗi tiến trình.
    lake.ensure_local(out_dir)
    pattern = f"{globlib.escape(brand)}_{globlib.escape(site)}_*.jsonl"
    return list(out_dir.glob(pattern))


def out_base_for(out_dir: Path, brand: str, site: str, resume: bool) -> Path:
    """Tên file kết quả cho một hãng: '<Tên Hãng>_<site>_<ddmmyy>'.

    Khi `--resume`, dùng lại file gần nhất của hãng đó (nếu có) thay vì tạo file
    mới theo ngày hôm nay — nếu không, resume qua ngày khác sẽ crawl lại từ đầu.
    """
    if resume:
        existing = _brand_files(out_dir, brand, site)
        if existing:
            newest = max(existing, key=lambda p: p.stat().st_mtime)
            return newest.with_suffix("")
    return out_dir / output_stem(brand, site)


def done_file_for(out_dir: Path, brand: str, site: str,
                  opts: CrawlOptions) -> Path | None:
    """File kết quả đã có của hãng này, nếu lần chạy hiện tại nên bỏ qua nó.

    Mặc định crawl cả thư mục chỉ làm những hãng CHƯA có file kết quả.
    `--recrawl` crawl lại tất cả; `--resume` thì vào file cũ để crawl nốt phần
    còn thiếu nên cũng không bỏ qua.
    """
    if opts.recrawl or opts.resume:
        return None
    existing = _brand_files(out_dir, brand, site)
    return max(existing, key=lambda p: p.stat().st_mtime) if existing else None


# ------------------------------------------------------------------ crawl URL
def crawl_urls(scraper: SiteScraper, urls: list[str],
               des_urls: dict[str, str], out_base: Path,
               opts: CrawlOptions, on_item=None) -> tuple[int, int]:
    """Crawl một danh sách URL rồi ghi ra `out_base`.jsonl / .csv.

    Trả về (số bản ghi lấy được, số URL thực sự phải crawl). Số thứ hai bằng 0
    nghĩa là `--resume` đã bỏ qua hết — đó là thành công, không phải lỗi.

    `on_item(record)` được gọi thêm sau mỗi bản ghi, NGAY SAU khi đã ghi xuống
    đĩa. Phần lên lịch dùng nó để đánh dấu tiến độ từng chai, nên thứ tự đó là
    quan trọng: đánh dấu xong mà dữ liệu chưa kịp ghi thì lần sau mất chai đó.
    """
    jsonl_path = out_base.with_suffix(".jsonl")
    csv_path = out_base.with_suffix(".csv")
    skip = storage.load_scraped_urls(jsonl_path) if opts.resume else set()

    if opts.write_jsonl and not opts.resume:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path.write_text("", encoding="utf-8")

    # Ghi từng bản ghi ngay khi có -> mất mạng giữa chừng không mất dữ liệu.
    def _write(record) -> None:
        if opts.write_jsonl:
            storage.save_jsonl([record], jsonl_path, append=True)
        if on_item is not None:
            on_item(record)

    try:
        records = scraper.scrape_many(urls, skip=skip, on_item=_write,
                                      des_urls=des_urls)
    finally:
        # NIÊM TRONG `finally`, không phải sau khi xong êm đẹp. Hãng bị 429 ở
        # chai thứ 300 vẫn phải đưa 299 chai kia lên lake — đó đúng là lúc mẻ
        # crawl dễ mất dữ liệu nhất, vì `products` từng thoát bằng exit code 2 và
        # để lại tất cả trong spool.
        if opts.write_jsonl:
            lake.seal(jsonl_path)

    if opts.write_csv:
        # Khi resume, CSV phải gồm cả bản ghi cũ trong JSONL, không chỉ phần mới.
        export = (storage.load_records(jsonl_path, scraper.record_cls)
                  if (opts.write_jsonl and opts.resume) else records)
        storage.save_csv(export, csv_path, columns=scraper.csv_columns)
        lake.seal(csv_path)

    # Đếm đúng phần phải crawl thay vì `len(urls) - len(skip)`: file kết quả có
    # thể chứa URL không nằm trong danh sách lần này (vd CSV đầu vào bị cắt bớt),
    # khi đó phép trừ ra số âm và nhánh "attempted == 0 -> coi như xong" ở
    # cli/crawl_cmd.py hiểu sai thành đã crawl hết.
    attempted = sum(1 for u in urls if url_key(u) not in skip)
    return len(records), attempted


def read_pairs(scraper: SiteScraper, path: Path, opts: CrawlOptions,
               unique: bool = True) -> list[tuple[str, str | None]]:
    """Đọc (url_nguon, url_dich) từ CSV, ưu tiên nhận URL theo tên miền của site."""
    return csv_input.read_url_pairs(
        path, opts.url_column, opts.dest_column,
        base_url=scraper.base_url, prefer_host=scraper.host, unique=unique)


# ------------------------------------------------------------ crawl cả thư mục
def crawl_directory(scraper: SiteScraper, folder: Path, out_dir: Path,
                    opts: CrawlOptions) -> int:
    """Crawl mọi file .csv trong thư mục, mỗi hãng ra một cặp file kết quả.

    Trả về exit code: 0 nếu có ít nhất một hãng xong hoặc đã có sẵn.
    """
    files = csv_input.iter_csv_files(folder)
    if not files:
        log.error("Không có file .csv nào trong %s", folder)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Tìm thấy %d file .csv trong %s -> ghi vào %s/",
             len(files), folder, out_dir)

    total = ok_files = done_files = 0
    failed: list[str] = []

    for i, path in enumerate(files, 1):
        done = done_file_for(out_dir, path.stem, scraper.site, opts)
        if done is not None:
            log.info("[%d/%d] Bỏ qua %s — đã có %s (dùng --recrawl để crawl lại).",
                     i, len(files), path.name, done.name)
            done_files += 1
            continue

        log.info("=== [%d/%d] %s ===", i, len(files), path.name)
        try:
            pairs = read_pairs(scraper, path, opts)
        except ValueError as exc:
            log.error("%s — bỏ qua file này.", exc)
            failed.append(path.name)
            continue

        urls = [u for u, _ in pairs]
        if opts.limit:
            urls = urls[: opts.limit]
        if not urls:
            log.warning("%s không có URL nào để crawl.", path.name)
            failed.append(path.name)
            continue

        count, attempted = crawl_urls(
            scraper, urls, {u: d for u, d in pairs if d},
            out_base_for(out_dir, path.stem, scraper.site, opts.resume), opts)
        total += count
        # attempted == 0: --resume đã bỏ qua hết -> vẫn tính là xong.
        if count or attempted == 0:
            ok_files += 1
        else:
            failed.append(path.name)

    log.info("Xong cả thư mục: %d bản ghi mới, %d file crawl, "
             "%d hãng đã có sẵn, %d file lỗi.",
             total, ok_files, done_files, len(failed))
    if failed:
        log.warning("File không lấy được bản ghi nào: %s", ", ".join(failed))
    return 0 if (ok_files or done_files) else 1
