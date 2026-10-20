"""DAG thu thập: mỗi ngày một lát ngân sách.

ĐÂY LÀ DAG NGUY HIỂM NHẤT TRONG REPO, VÌ NÓ CHẠM MẠNG
Ba mặc định của Airflow đều đánh nhau với cơ chế lịch sự với Fragrantica. Cả ba
đều được tắt tường minh ở đây, và mỗi cái có một bài test canh trong
`tests/test_airflow.py`:

  1. `catchup=False`
     Mặc định của Airflow là True. Bật nó lên thì ngay lần đầu, Airflow xếp hàng
     MỌI khoảng thời gian đã lỡ kể từ `start_date` và chạy chúng — hàng chục lượt
     crawl cùng lúc. Với một site có rate limit thật thì đó là cách nhanh nhất để
     bị chặn.

  2. `max_active_runs=1`
     Hai lượt `daily` chạy song song sẽ cùng đọc một sổ ngân sách SQLite, cùng
     tưởng mình còn 150 request, và gõ cửa site với tốc độ gấp đôi.

  3. `retries=0`
     Mặc định retry là đúng cho hầu hết task, và SAI hẳn ở đây. `daily` thoát với
     mã 2 khi bị chặn (429/Cloudflare); retry nghĩa là bị chặn rồi vẫn gõ tiếp.
     Sổ `site_cooldown` trong SQLite đã lo việc lùi lại — để Airflow retry chồng
     lên là phá đúng cơ chế đó. Lượt hỏng thì đợi mai; dữ liệu không mất, vì tiến
     độ được ghi ở mức từng chai.

QUY ƯỚC EXIT CODE CỦA `daily`, GIỮ NGUYÊN
    0 = xong        1 = lỗi thường        2 = bị chặn
Airflow coi khác 0 là fail. Mã 2 hiện ra ở UI dưới dạng task đỏ — đúng mong muốn:
nó là chuyện cần biết, chỉ không phải chuyện cần thử lại ngay.
"""

from __future__ import annotations

import os
import pendulum
from airflow.sdk import Asset, dag

from _chung import lenh

# Tầng bronze. DAG `refresh_marts` chờ asset này thay vì chờ theo giờ — nghĩa là
# chuỗi offline chạy KHI CÓ dữ liệu mới, không phải khi tới giờ đoán chừng.
BRONZE = Asset("perfume://bronze")

MUI_GIO = os.environ.get("TZ", "Asia/Ho_Chi_Minh")
GIO, PHUT = (os.environ.get("RUN_AT", "18:00").split(":") + ["0"])[:2]


@dag(
    dag_id="fragrantica_crawl",
    description="Một lát ngân sách mỗi ngày (nhỏ giọt, tôn trọng rate limit)",
    schedule=f"{int(PHUT)} {int(GIO)} * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz=MUI_GIO),
    catchup=False,          # xem docstring, mục 1
    max_active_runs=1,      # mục 2
    default_args={
        "retries": 0,       # mục 3
        "depends_on_past": False,
    },
    tags=["crawl", "mang"],
    doc_md=__doc__,
)
def fragrantica_crawl():
    daily = lenh(
        "daily",
        "daily", "--render",
        outlets=[BRONZE],
        # Một lượt ăn hết ngân sách 150 request với delay 5-10 giây mỗi request,
        # cộng thời gian render. Trần 6 tiếng là rộng rãi; nó chỉ để một tiến
        # trình treo không giữ chỗ tới tận lượt hôm sau.
        execution_timeout=pendulum.duration(hours=6),
    )
    return daily


fragrantica_crawl()
