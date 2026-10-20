ARG AIRFLOW_VERSION=3.3.2
ARG PYTHON_VERSION=3.12
FROM apache/airflow:${AIRFLOW_VERSION}-python${PYTHON_VERSION}

# KHÔNG cài phụ thuộc của project vào ảnh này, và đó là điểm khác duy nhất so
# với bản mẫu trong các project khác.
#
# Bản mẫu dựng một venv riêng (/opt/project-venv) để chạy crawler/dbt ngay trong
# container Airflow, vì project bên đó không có ảnh runtime riêng. Project này thì
# CÓ: `perfume-intel` đã chứa Google Chrome thật (~400 MB), model embedding ONNX
# (~220 MB), dbt-duckdb, duckdb, fastembed. Nhân bản cả bộ đó vào ảnh Airflow là
# thêm ~1 GB và hai bản phụ thuộc phải giữ cho khớp nhau mãi.
#
# Nên các task đi qua `DockerOperator`: Airflow bật container từ ảnh
# `perfume-intel`. Ranh giới cũng rõ hơn — Airflow biết KHI NÀO và THEO THỨ TỰ
# NÀO, ảnh của project biết LÀM THẾ NÀO. Xem airflow/dags/_chung.py.
#
# `apache-airflow-providers-docker` đã có sẵn trong ảnh gốc nên không cài thêm gì.

USER root

# `runuser` để start.sh chown volume dưới quyền root rồi tự hạ xuống UID 50000.
# `curl` cho healthcheck của compose. Cả hai thường có sẵn, nhưng khai rõ để lần
# nâng ảnh gốc sau không âm thầm mất một trong hai.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl util-linux \
    && rm -rf /var/lib/apt/lists/*

# Wrapper tạo sẵn tài khoản admin từ biến môi trường trước khi chuyển sang
# `airflow standalone` — standalone tự sinh mật khẩu ngẫu nhiên nếu không làm vậy.
COPY --chmod=0755 start.sh /opt/airflow/start.sh

# Ở lại root để start.sh chown được volume log trước khi hạ quyền. start.sh tự
# gọi lại chính nó qua `runuser` để phần còn lại chạy bằng UID 50000 — `airflow
# standalone` không bao giờ chạy bằng root.
USER root
WORKDIR /opt/airflow

ENTRYPOINT ["/opt/airflow/start.sh"]
