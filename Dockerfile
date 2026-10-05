# syntax=docker/dockerfile:1.7
#
# Đóng gói perfume_intel kèm một browser THẬT.
#
# Vì sao không dùng ảnh nền của Playwright (mcr.microsoft.com/playwright/python):
# ảnh đó kéo theo Firefox và WebKit mà project không bao giờ dùng (~1 GB chết),
# lại chỉ có Python 3.12 trong khi project đang chạy và được test trên 3.13.
# Ở đây chỉ cài đúng Chrome và phần thư viện hệ thống mà nó cần.
#
# Nền là bookworm (Debian 12) chứ không phải `slim` mới nhất: `playwright install
# --with-deps` chỉ biết danh sách gói của những bản phân phối nó hỗ trợ, và
# trixie chưa nằm trong đó — chọn sai thì build chết ở bước cài thư viện.
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Log và dữ liệu của project là tiếng Việt; không ép UTF-8 thì stdout mặc
    # định ASCII và chết ngay dòng log đầu tiên.
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# Dependency trước, code sau: sửa một dòng Python không phải cài lại thư viện.
# `playwright` ghim cứng để bản build hôm nay và bản build tháng sau giống nhau —
# đây là thứ điều khiển một browser thật, nâng cấp nó nên là việc có chủ ý.
COPY pyproject.toml README.md ./
RUN mkdir -p perfume_intel && touch perfume_intel/__init__.py \
    && pip install -e ".[render,warehouse,marts,lake]" "playwright==1.57.0"

# Google Chrome THẬT, không phải Chromium đóng gói. Đây không phải sở thích: đo
# trên 10 trang Fragrantica, Chromium của Playwright qua được 1/10 (9 lần
# Cloudflare trả 403), Chrome/Edge thật qua 10/10 — xem core/browser.py:
# `_launch_browser`, nó thử channel "chrome" trước rồi mới rơi về bản đóng gói.
# Không có dòng này thì container vẫn chạy, chỉ là gần như mọi trang đều 403.
#
# Cài thẳng bản .deb của Google thay vì `playwright install --with-deps chrome`:
# cờ `--with-deps` cài một danh sách CỨNG dùng chung cho cả ba engine, kéo theo
# xvfb và cả chùm font X mà Chrome headless không cần. Bản .deb tự khai dependency
# của nó nên apt cài đúng phần cần — ít gói hơn nghĩa là build nhanh hơn và ít
# chỗ để một lần apt hỏng mạng làm chết cả bản build (đã gặp thật một lần).
#
# `fonts-liberation`: không có font nào thì Chrome vẫn chạy nhưng mọi chữ thành ô
# vuông. Với trang render bằng JS thì đó là hỏng thật, không phải chuyện thẩm mỹ.
#
# `make` để service `test` gọi `make test`: danh sách 8 bộ test được liệt kê tay
# trong Makefile (không auto-discovery), nên gọi make là cách duy nhất không tạo
# ra một danh sách thứ hai rồi lệch dần với bản gốc.
#
# Tải và cài trong CÙNG một RUN, không dùng `ADD <url>`: `ADD` để lại file .deb
# 120 MB trong layer của nó, và `rm` ở layer sau không lấy lại được chỗ đó.
#
# `wget` ĐƯỢC GIỮ LẠI, dù chỉ dùng đúng một lần ở đây: bản .deb của Chrome khai
# `Depends: wget` (cho cơ chế tự cập nhật của nó), nên `apt-get purge -y wget`
# lôi luôn Chrome đi. Build vẫn xanh tới dòng cuối rồi chết ở
# `google-chrome --version` với exit 127. Đã gặp đúng như vậy, hai lần.
#
# `google-chrome --version` ở cuối là cái chốt: thiếu nó thì một lần cài hỏng chỉ
# lộ ra lúc chạy thật, dưới dạng mọi trang đều 403 vì đã rơi về Chromium.
RUN set -eux; \
    apt-get update -o Acquire::Retries=3; \
    apt-get install -y --no-install-recommends -o Acquire::Retries=3 \
        make fonts-liberation wget ca-certificates; \
    wget --tries=3 --timeout=30 -qO /tmp/chrome.deb \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb; \
    apt-get install -y --no-install-recommends -o Acquire::Retries=3 /tmp/chrome.deb; \
    rm -f /tmp/chrome.deb; \
    rm -rf /var/lib/apt/lists/*; \
    google-chrome --version

COPY perfume_intel ./perfume_intel
COPY transform ./transform
COPY tests ./tests
COPY scripts ./scripts
COPY Makefile ./

# Chạy non-root: Chrome từ chối khởi động dưới quyền root nếu không có
# --no-sandbox, mà tắt sandbox của browser để cào một site lạ là đổi ngược hướng.
# uid 1000 để khớp user thường của host Linux — bind mount ./data phải ghi được.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p data/raw data/inputs data/processed data/state data/logs \
               data/silver data/warehouse .cache/html \
    && chown -R app:app /app

USER app

ENTRYPOINT ["python", "-m", "perfume_intel"]
CMD ["queue"]
