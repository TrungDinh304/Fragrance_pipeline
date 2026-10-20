"""DAG chế biến: bronze -> silver -> gold, và các artifact dẫn xuất.

CHẠY KHI CÓ DỮ LIỆU MỚI, KHÔNG PHẢI KHI TỚI GIỜ
`schedule=[BRONZE]` nghĩa là DAG này khởi động khi DAG crawl ghi xong tầng bronze.
Đặt một giờ cố định kiểu "19:00" sẽ sai theo cả hai chiều: chạy không khi lượt
crawl bị site chặn, và chạy trước khi crawl xong vào hôm nó kéo dài.

ĐÂY LÀ PHẦN AIRFLOW THẬT SỰ ĐÁNG GIÁ Ở PROJECT NÀY
Chuỗi offline có phụ thuộc thật và trước giờ phải chạy tay đúng thứ tự. Phần
crawl thì chỉ là một task mỗi ngày — nó chạy được bằng một vòng lặp bash.

    bronze ─┬─▶ silver ──▶ marts        (Parquet có kiểu -> 5 mart dbt)
            ├─▶ analyze                 (đọc THẲNG bronze, cố ý)
            └─▶ embed  ──▶ vectordb     (Parquet vector -> nạp vào pgvector)

`analyze` KHÔNG phụ thuộc `silver`: nó đọc thẳng bronze bằng Python thuần, và đó
là chủ đích — nó là bên đối chứng độc lập cho dbt. Nối nó sau `silver` sẽ làm mất
tính độc lập đó, dù đồ thị trông gọn hơn.

RETRY Ở ĐÂY LÀ AN TOÀN, KHÁC HẲN DAG CRAWL
Mọi task dưới đây đều offline và tất định: chạy lại cho ra cùng kết quả, không
đụng tới Fragrantica, không tiêu ngân sách request. Nên `retries=2`.
"""

from __future__ import annotations

import pendulum
from airflow.sdk import Asset, dag

from _chung import lenh

BRONZE = Asset("perfume://bronze")
SILVER = Asset("perfume://silver")
EMBEDDING = Asset("perfume://embedding")


@dag(
    dag_id="fragrantica_refresh",
    description="bronze -> silver -> gold + báo cáo + kho vector",
    schedule=[BRONZE],       # chạy khi bronze đổi, không theo giờ
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Ho_Chi_Minh"),
    catchup=False,
    max_active_runs=1,
    default_args={
        # An toàn vì mọi task đều offline và tất định — xem docstring.
        "retries": 2,
        "retry_delay": pendulum.duration(minutes=2),
    },
    tags=["offline"],
    doc_md=__doc__,
)
def fragrantica_refresh():
    silver = lenh("silver", "silver", outlets=[SILVER],
                  execution_timeout=pendulum.duration(minutes=30))

    # `marts` là dbt, không phải lệnh của project — cùng ảnh, khác entrypoint.
    marts = _marts()

    analyze = lenh("analyze", "analyze",
                   execution_timeout=pendulum.duration(minutes=20))

    embed = lenh("embed", "embed", outlets=[EMBEDDING],
                 execution_timeout=pendulum.duration(minutes=30))

    nap_vector = lenh("vectordb_load", "vectordb", "load",
                      execution_timeout=pendulum.duration(minutes=20))

    silver >> marts
    embed >> nap_vector
    # `analyze` đứng riêng: nó đọc thẳng bronze, không chờ silver.
    return [marts, analyze, nap_vector]


def _marts():
    """dbt chạy trong cùng ảnh nhưng khác entrypoint và khác thư mục làm việc.

    `PYTHONUTF8=1` là bắt buộc: dbt đọc YAML bằng encoding mặc định của hệ điều
    hành và chết ở dòng tiếng Việt đầu tiên trong `dbt_project.yml`.

    `--no-partial-parse`: cache parse của dbt ghim đường dẫn tuyệt đối, và container
    mới mỗi lần chạy thì cache cũ chỉ gây `KeyError: dbt_duckdb://macros/...`.
    """
    import os

    from airflow.providers.docker.operators.docker import DockerOperator

    from _chung import ANH, MANG, _mounts

    return DockerOperator(
        task_id="marts",
        image=ANH,
        # Qua `scripts/marts.sh`, không gọi dbt trực tiếp: nó chốt schema
        # Parquet trước. Thường thì `silver >> marts` đã lo, nhưng chạy lại
        # RIÊNG task `marts` (clear một task) thì không có ai lo.
        entrypoint=["sh", "/app/scripts/marts.sh"],
        command=["build", "--no-partial-parse"],
        working_dir="/app/transform",
        docker_url=os.environ.get("DOCKER_URL", "unix://var/run/docker.sock"),
        network_mode=MANG,
        mounts=_mounts(),
        auto_remove="success",
        environment={"PYTHONUTF8": "1", "DBT_PROFILES_DIR": ".",
                     "TZ": os.environ.get("TZ", "Asia/Ho_Chi_Minh")},
        execution_timeout=pendulum.duration(minutes=15),
    )


fragrantica_refresh()
