#!/usr/bin/env bash
#
# Bộ lên lịch trong container — bản tương đương của scripts/daily_crawl.ps1.
#
# Không dùng cron: cron trong container không thấy biến môi trường của Docker,
# ghi log vào chỗ `docker logs` không đọc được, và im lặng khi lệnh chết. Một
# vòng lặp sleep thì thấy hết, và đằng nào cũng chỉ chạy một lần mỗi ngày.
#
# Quy ước exit code của `daily` được giữ nguyên:
#     0 = xong    1 = lỗi thường    2 = bị chặn (429/Cloudflare)
set -uo pipefail

RUN_AT="${RUN_AT:-02:30}"
RUN_ON_START="${RUN_ON_START:-0}"
RENDER="${RENDER:-1}"
BUDGET="${BUDGET:-}"
BRANDS="${BRANDS:-}"
DELAY="${DELAY:-}"

LOG_DIR="/app/data/logs"
mkdir -p "$LOG_DIR" || {
    echo "FATAL: không ghi được $LOG_DIR — kiểm tra quyền của bind mount ./data" >&2
    exit 1
}

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"; }

# Bị SIGTERM (docker compose down / restart) thì thoát ngay thay vì để Docker
# đợi hết 10 giây rồi SIGKILL. `sleep` chạy nền + `wait` là cách duy nhất để
# trap chen được vào giữa một giấc ngủ dài.
SLEEP_PID=""
on_term() {
    say "Nhận tín hiệu dừng — thoát."
    [ -n "$SLEEP_PID" ] && kill "$SLEEP_PID" 2>/dev/null
    exit 0
}
trap on_term TERM INT

nap() {
    sleep "$1" &
    SLEEP_PID=$!
    wait "$SLEEP_PID"
    SLEEP_PID=""
}

seconds_until() {
    local now next
    now=$(date +%s)
    next=$(date -d "today $RUN_AT" +%s 2>/dev/null) || return 1
    [ "$next" -le "$now" ] && next=$(date -d "tomorrow $RUN_AT" +%s)
    echo $((next - now))
}

run_once() {
    local log args code
    log="$LOG_DIR/daily_$(date +%Y%m%d).log"

    args=(-m perfume_intel daily)
    [ "$RENDER" = "1" ] && args+=(--render)
    [ -n "$BUDGET" ] && args+=(--budget "$BUDGET")
    [ -n "$BRANDS" ] && args+=(--brands "$BRANDS")
    # DELAY là hai số cách nhau bởi dấu cách: DELAY="15 30".
    [ -n "$DELAY" ] && args+=(--delay $DELAY)

    say "=== chạy: python ${args[*]}" | tee -a "$log"
    python "${args[@]}" 2>&1 | tee -a "$log"
    code=${PIPESTATUS[0]}
    say "=== kết thúc, exit=$code" | tee -a "$log"

    # KHÔNG thoát khi lỗi. Service dùng `restart: unless-stopped`, nên thoát
    # non-zero nghĩa là Docker khởi động lại ngay và crawl lại liền tay — đúng
    # thứ mà cơ chế nhỏ giọt tồn tại để tránh. Sổ theo dõi đã ghi cooldown, hôm
    # sau đi tiếp đúng chỗ dừng.
    case "$code" in
        0) : ;;
        2) say "Bị chặn (429/Cloudflare). Mọi hãng đã được cho nghỉ; chờ lượt sau." ;;
        *) say "Lần chạy lỗi (exit=$code). Xem $log. Chờ lượt sau." ;;
    esac
}

say "Bộ lên lịch đã bật. Giờ chạy $RUN_AT (TZ=${TZ:-UTC}), render=$RENDER," \
    "budget=${BUDGET:-mặc định}, brands=${BRANDS:-mặc định}."

if [ "$RUN_ON_START" = "1" ]; then
    say "RUN_ON_START=1 — chạy một lượt ngay bây giờ."
    run_once
fi

while true; do
    secs=$(seconds_until) || { say "FATAL: RUN_AT=$RUN_AT không phải giờ hợp lệ (cần HH:MM)."; exit 1; }
    say "Lượt kế tiếp sau $((secs / 3600))h$(((secs % 3600) / 60))m — lúc $RUN_AT."
    nap "$secs"
    run_once
done
