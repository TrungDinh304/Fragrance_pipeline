"""Phần dùng chung cho mọi DAG: cách chạy một lệnh của project.

AIRFLOW ĐIỀU PHỐI, ẢNH CỦA PROJECT THỰC THI
Airflow không chứa `perfume_intel`, không chứa Chrome, không chứa model embedding.
Nó gọi `DockerOperator` để chạy ảnh `perfume-intel` đã có sẵn tất cả.

Vì sao không cài `perfume_intel` vào ảnh Airflow:
  - Airflow ghim hàng trăm package; trộn với dbt-duckdb, fastembed, playwright là
    một bài giải ràng buộc phiên bản không có lời giải bền;
  - ảnh sẽ phải thêm Google Chrome (~400 MB) và model ONNX (~220 MB) mà
    scheduler không bao giờ dùng tới;
  - và quan trọng nhất: ranh giới mờ đi. Airflow biết KHI NÀO và THEO THỨ TỰ NÀO;
    ảnh của project biết LÀM THẾ NÀO. Hai thứ đó nên thay được độc lập.

BA CÁI BẪY CỦA DockerOperator, ĐỀU LÀ THỨ IM LẶNG
  1. `mounts` được DAEMON Docker phân giải, không phải container Airflow. Nên
     đường dẫn phải là đường dẫn trên MÁY HOST. Dùng đường dẫn bên trong
     container Airflow thì nó mount một thư mục rỗng — task vẫn chạy, vẫn exit 0,
     và không ghi được gì vào đâu cả.
  2. Không khai `network_mode` thì container mới nằm ở mạng bridge mặc định và
     không phân giải được `minio`, `postgres`. Lỗi hiện ra muộn, dưới dạng
     "không nối được lake".
  3. `auto_remove` mặc định không xoá container, nên chạy mỗi ngày sẽ để lại một
     đống container đã dừng.
"""

from __future__ import annotations

import os

from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

# Ảnh của project. Cùng ảnh mà `docker compose run --rm cli` dùng.
ANH = os.environ.get("PERFUME_IMAGE", "perfume-intel:0.2.0")

# Đường dẫn gốc project TRÊN MÁY HOST. Bắt buộc, vì daemon Docker phân giải mount
# theo đường dẫn host — xem bẫy 1 ở trên.
HOST_DIR = os.environ.get("HOST_PROJECT_DIR", "")

# Mạng của compose, để container chạy được thấy `minio` và `postgres`.
MANG = os.environ.get("COMPOSE_NETWORK", "perfume-intel_default")

MOI_TRUONG = {
    "TZ": os.environ.get("TZ", "Asia/Ho_Chi_Minh"),
    "PYTHONUTF8": "1",
    "LAKE": os.environ.get("LAKE", "s3"),
    "S3_ENDPOINT": os.environ.get("PERFUME_S3_ENDPOINT", "http://minio:9000"),
    "S3_ACCESS_KEY": os.environ.get("S3_ACCESS_KEY", "perfume"),
    "S3_SECRET_KEY": os.environ.get("S3_SECRET_KEY", "perfume-dev-only"),
    "BRONZE_BUCKET": os.environ.get("BRONZE_BUCKET", "bronze"),
    "SILVER_BUCKET": os.environ.get("SILVER_BUCKET", "silver"),
    "PG_DSN": os.environ.get(
        "PERFUME_PG_DSN",
        "postgresql://perfume:perfume-dev-only@postgres:5432/perfume"),
    "EMBED_MODEL": os.environ.get("EMBED_MODEL", ""),
}


def _mounts() -> list[Mount]:
    if not HOST_DIR:
        # Ném ngay lúc phân tích DAG, không đợi tới lúc chạy. Thiếu biến này thì
        # task vẫn chạy và vẫn exit 0, chỉ là không ghi được gì — kiểu hỏng tệ
        # nhất vì nhìn vào tưởng thành công.
        raise ValueError(
            "Thiếu HOST_PROJECT_DIR. Đây phải là đường dẫn project trên MÁY "
            "HOST (daemon Docker phân giải mount theo đường dẫn host, không "
            "theo đường dẫn trong container Airflow).")
    return [
        Mount(source=f"{HOST_DIR}/data", target="/app/data", type="bind"),
        Mount(source=f"{HOST_DIR}/.cache", target="/app/.cache", type="bind"),
    ]


def lenh(task_id: str, *args: str, **kwargs) -> DockerOperator:
    """Một task = một lần chạy `python -m perfume_intel <args>`.

    `shm_size` 1 GB vì Chrome dùng /dev/shm cho bộ nhớ render và chết giữa trang
    với mức mặc định 64 MB — cùng lý do như trong docker-compose.yml.
    """
    return DockerOperator(
        task_id=task_id,
        image=ANH,
        command=list(args),
        entrypoint=["python", "-m", "perfume_intel"],
        docker_url=os.environ.get("DOCKER_URL", "unix://var/run/docker.sock"),
        network_mode=MANG,
        mounts=_mounts(),
        auto_remove="success",
        shm_size=1024 * 1024 * 1024,
        environment={k: v for k, v in MOI_TRUONG.items() if v},
        # Lấy log của container về log của Airflow. Không có thì muốn xem vì sao
        # một lượt crawl dừng sớm phải đi lục `docker logs` của container đã xoá.
        tty=False,
        **kwargs,
    )
