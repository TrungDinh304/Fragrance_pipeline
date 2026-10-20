#!/bin/sh
# Chốt schema silver rồi mới gọi dbt.
#
# Dùng bởi HAI đường chạy dbt trong container: service `marts` của compose, và
# task `marts` trong DAG `fragrantica_refresh`. Cả hai đọc `data/silver` qua
# BIND MOUNT từ máy host, nên Parquet ở đó có thể được dựng bằng code cũ hơn
# code trong ảnh — còn `transform/` thì đến từ ảnh. Hai bên lệch schema là
# chuyện bình thường, không phải ca hiếm.
#
# Gọi dbt trực tiếp như trước thì lệch đó hiện ra ở model đầu tiên dưới dạng
# `Binder Error: Referenced column "brand_key" not found in FROM clause!`, 65
# node sau chuyển sang SKIP, và dòng tổng kết trông như chỉ có 1 lỗi. Xem
# `silver.kiem_tra` để biết phép kiểm so những gì.
set -e

cd /app
python -m perfume_intel silver --check

cd /app/transform
exec python -m dbt.cli.main "$@"
