#!/bin/bash
# Entrypoint cho container Airflow.
#
# `airflow standalone` tự sinh mật khẩu admin ngẫu nhiên; wrapper này tạo sẵn
# admin từ AIRFLOW_ADMIN_USER / AIRFLOW_ADMIN_PASSWORD (đọc từ .env qua compose)
# để thông tin đăng nhập tất định và sống qua `docker compose down` + `up`, miễn
# là volume postgres còn.
#
# Một container thay cho bốn. `airflow standalone` trên Airflow 3 chạy đúng bốn
# thành phần mà bản compose mẫu tách rời: api-server, scheduler, dag-processor,
# triggerer. Gộp lại không mất gì và bớt ~1 GB RAM.
set -euo pipefail

say() { echo "[start.sh] $*"; }

# Nếu khởi động bằng root (ảnh để USER root) thì sửa quyền volume trước khi hạ
# xuống user airflow.
#
#   /opt/airflow/logs: named volume. Trước khi có nhánh runuser này, scheduler
#     chạy as root nên tạo log dir mode 755 root-owned. Sau khi hạ xuống UID
#     50000, mọi subdir cũ trong volume vẫn thuộc root → scheduler spam
#     `PermissionError: '/opt/airflow/logs/scheduler/<date>/<dag>.log'`,
#     DagFileProcessor chết trong vòng lặp im lặng → UI hiện "0 DAGs" dù CLI
#     `airflow dags list` vẫn đầy đủ. Chown idempotent, an toàn chạy mỗi lần bật.
if [ "$(id -u)" = "0" ]; then
    say "Đang là root: sửa quyền /opt/airflow/logs"
    chown -R 50000:0 /opt/airflow/logs 2>/dev/null || true
    # Socket Docker: DockerOperator cần đọc/ghi nó. Socket thuộc root:root mode
    # 660, và user airflow có GID 0, nên nhóm đã đủ quyền — không chown để khỏi
    # đụng vào thứ của host.
    say "Chạy lại bằng user airflow (50000)"
    exec runuser -u airflow -- "$0" "$@"
fi

export ADMIN_USER="${AIRFLOW_ADMIN_USER:-admin}"
export ADMIN_PASS="${AIRFLOW_ADMIN_PASSWORD:-admin}"

say "Chạy airflow db migrate"
airflow db migrate

# Kiểm NGAY sau migrate. `db migrate` có lúc exit 0 trong khi database chưa dùng
# được (sai DSN, database chưa tồn tại), và khi đó standalone bật lên rồi chết
# trong vòng lặp — nhìn vào chỉ thấy container restart mãi mà không rõ vì sao.
say "Kiểm kết nối database"
airflow db check

# Mat khau cho SimpleAuthManager, ghi tu env.
#
# VI SAO KHONG DUNG `airflow users create/reset-password` NHU BAN MAU
# Ban mau viet cho Airflow 2, noi `standalone` dung FabAuthManager nen tao san
# admin trong bang FAB la cach dung. Airflow 3 thi KHAC: `standalone` ghi de cung
#
#     os.environ["AIRFLOW__CORE__AUTH_MANAGER"] = simple_auth_manager_classpath
#
# (xem airflow/cli/commands/standalone_command.py). Nen du khai AUTH_MANAGER la
# FAB, api-server van chay SimpleAuthManager — user trong bang FAB co ton tai
# nhung khong ai tra toi, va dang nhap tra "Invalid credentials". Da dinh dung
# canh do: `airflow users list` hien admin day du ma UI van khong cho vao.
#
# SimpleAuthManager doc mat khau tu mot file JSON {"user": "pass"}, khai qua
# `[core] simple_auth_manager_passwords_file`. Ghi file do o day dat dung muc dich
# ban dau cua ban mau: thong tin dang nhap tat dinh tu .env, song qua restart.
#
# Dung python de sinh JSON, khong dung printf: mat khau co dau ngoac kep hay dau
# gach cheo se lam JSON hong, va luc do Airflow chi bao "Invalid credentials".
PW_FILE="${AIRFLOW_HOME:-/opt/airflow}/simple_auth_passwords.json"
say "Ghi mat khau admin '${ADMIN_USER}' vao ${PW_FILE}"
python - "$PW_FILE" <<'PY'
import json, os, sys
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({os.environ["ADMIN_USER"]: os.environ["ADMIN_PASS"]}, f)
PY
chmod 600 "$PW_FILE"

# Dọn PID/socket còn sót từ lần chạy trước. `airflow standalone` từ chối bật
# webserver nếu mấy file này còn ("Error: Already running on PID ..."), và lần
# trước có thể đã chết mà không kịp xoá chúng.
say "Dọn file PID cũ"
rm -f /opt/airflow/airflow-webserver.pid \
      /opt/airflow/airflow-apiserver.pid \
      /opt/airflow/airflow-scheduler.pid \
      /opt/airflow/airflow-dag-processor.pid \
      /opt/airflow/airflow-triggerer.pid \
      /opt/airflow/standalone_admin_password.txt

say "Chuyển sang airflow standalone"
exec airflow standalone
