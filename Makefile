.PHONY: help crawl brands products mini analyze similar silver marts queue daily test \
        docker-build docker-up docker-down docker-logs docker-test docker-queue

PYTHON ?= python
CLI = $(PYTHON) -m perfume_intel

# Cho phép truyền đường dẫn ngay sau tên lệnh:
#     make crawl data/inputs/fragrantica
# Các từ đứng sau tên lệnh được biến thành target rỗng để make không báo lỗi.
FIRST := $(firstword $(MAKECMDGOALS))
ifneq ($(filter $(FIRST),crawl brands products mini analyze similar silver marts queue daily),)
  ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  .PHONY: $(ARGS)
  $(eval $(ARGS):;@:)
endif

# Nguồn mặc định khi không truyền đường dẫn.
FRAG_INPUT ?= data/inputs/fragrantica
MINI_INPUT ?= data/inputs/temp_task/Mini.csv

# make không cho truyền thẳng cờ '--recrawl'/'--resume' (nó hiểu là option của
# chính make), nên dùng biến:
#     make crawl data/inputs/fragrantica RECRAWL=1   -> crawl lại tất cả
#     make crawl data/inputs/fragrantica RESUME=1    -> crawl nốt URL còn thiếu
#     make crawl data/inputs/fragrantica DELAY="15 30"  -> nghỉ 15-30s mỗi request
FLAGS = $(if $(RECRAWL),--recrawl) $(if $(RESUME),--resume) \
        $(if $(DELAY),--delay $(DELAY)) $(if $(LIMIT),--limit $(LIMIT)) \
        $(if $(FORMAT),--format $(FORMAT))

# Text help de ASCII: console Windows (cp1252) hien chu co dau thanh mojibake.
help:
	@echo "make crawl [path]    Crawl Fragrantica (--render) -> data/raw/fragrantica/"
	@echo "make brands          Crawl danh muc hang (8000+)  -> data/raw/fragrantica/"
	@echo "make products        Chai cua tung hang           -> data/raw/fragrantica/"
	@echo "make mini  [file]    Ghep ban mini voi du lieu da crawl (offline)"
	@echo "make queue           Xem tien do crawl (so theo doi)"
	@echo "make daily           Chay 1 lat ngan sach hom nay (nho giot)"
	@echo "make analyze         Phan tich thi truong + report.html -> data/processed/<ngay>/"
	@echo "make similar [q]     Tim chai/hang giong nhau (mui, note, hoan canh)"
	@echo "make silver          Nen kho tho -> Parquet co kieu (can duckdb)"
	@echo "make marts           Dung bang gold bang dbt (can dbt-duckdb)"
	@echo "make test            Chay toan bo test (khong can mang)"
	@echo ""
	@echo "make lake            Xem datalake (MinIO): hai ben dang co gi"
	@echo "make embed           Sinh embedding -> data/silver/*.parquet"
	@echo "make vectordb        Xem/nap kho vector (Postgres + pgvector)"
	@echo "make api             Bat API + trang test chatbot (localhost:8000)"
	@echo ""
	@echo "Airflow (dieu phoi):"
	@echo "make airflow-init    Lan dau: build anh + tao DB airflow"
	@echo "make airflow-up      Bat Airflow (UI: localhost:8081)"
	@echo "make airflow-check   Kiem DAG co nap duoc khong"
	@echo "make airflow-logs    Xem log scheduler (stream)"
	@echo "make airflow-log     Log thuc thi cua task (doc duoc)"
	@echo "make airflow-down    Tat Airflow"
	@echo ""
	@echo "Docker (khong can cai Python/Chrome tren may):"
	@echo "make docker-build    Build anh (co Google Chrome that ben trong)"
	@echo "make docker-init     Bat MinIO + tao bucket (chay mot lan dau tien)"
	@echo "make docker-up       Bat MinIO + bo len lich chay nen (gio theo RUN_AT)"
	@echo "make docker-logs     Xem bo len lich dang lam gi"
	@echo "make docker-queue    Xem tien do trong container"
	@echo "make docker-test     Chay toan bo test trong container"
	@echo "make docker-down     Tat"
	@echo ""
	@echo "Bien: RECRAWL=1 RESUME=1 LIMIT=n FORMAT=both DELAY=\"15 30\""

# Fragrantica cần --render mới lấy được when-to-wear, độ lưu hương, độ toả hương.
crawl:
	$(CLI) crawl $(if $(ARGS),$(ARGS),$(FRAG_INPUT)) --render $(FLAGS)

# Danh mục hãng: chỉ ~12 request cho toàn bộ A-Z, chạy vài chục giây.
brands:
	$(CLI) brands $(if $(DELAY),--delay $(DELAY)) $(if $(FORMAT),--format $(FORMAT))

# Chai cua tung hang; mac dinh lay hang tu file danh muc moi nhat.
# Tim o CA HAI cho: thu muc moi brands/ va thu muc goc (bo cuc cu, truoc khi
# chay scripts/migrate_bronze.py). Thieu mot trong hai la `make products` chay
# voi --from-brands RONG.
BRANDS_GLOB ?= data/raw/fragrantica/brands/brands_fragrantica_*.jsonl                data/raw/fragrantica/brands_fragrantica_*.jsonl
# Luu y: ten file la ddmmyy nen sort theo chu cai chi dung trong cung thang.
# Qua thang (300925 vs 011025) se chon nham -- luc do truyen BRANDS_FILE tay.
BRANDS_FILE ?= $(lastword $(sort $(wildcard $(BRANDS_GLOB))))
products:
	@if [ -z "$(ARGS)" ] && [ -z "$(BRANDS_FILE)" ]; then 	  echo "LOI: khong tim thay file danh muc hang."; 	  echo "     Da tim: $(BRANDS_GLOB)"; 	  echo "     Chay \`make brands\` truoc, hoac truyen BRANDS_FILE=<duong-dan>."; 	  exit 1; 	fi
	$(CLI) products $(if $(ARGS),$(ARGS),--from-brands $(BRANDS_FILE)) $(FLAGS)

# --- Kho phan tich -----------------------------------------------------------
# Tang silver: JSONL tho -> Parquet da khu trung, co kieu.
#   pip install -e ".[warehouse]"
silver:
	$(CLI) silver $(if $(ARGS),$(ARGS),)

# Tang gold/marts bang dbt.  pip install -e ".[marts]"
# PYTHONUTF8=1 la BAT BUOC: dbt doc YAML bang encoding mac dinh cua he dieu hanh,
# tren Windows la cp1252 va chet ngay o dong tieng Viet dau tien trong
# dbt_project.yml. DBT_TARGET_PATH thay cho target-path (dbt 1.12 da bo).
DBT_ENV = PYTHONUTF8=1 DBT_PROFILES_DIR=.
# Chot schema truoc khi goi dbt. Guard cu la `test -d data/silver`, chi thay thu
# muc CO -- nen parquet dung bang code cu van di qua, roi dbt chet o model dau
# tien voi `Binder Error: Referenced column ... not found` va 65 node sau SKIP.
# `silver --check` doc truc tiep silver.TABLES, khong nhac lai schema o day.
marts:
	$(CLI) silver --check
	cd transform && $(DBT_ENV) $(PYTHON) -m dbt.cli.main build

# Nhỏ giọt theo lịch. BUDGET/BRANDS ghi đè mặc định trong config.py.
queue:
	$(CLI) queue $(if $(ARGS),$(ARGS),)

daily:
	$(CLI) daily --render $(if $(BUDGET),--budget $(BUDGET)) $(if $(BRANDS),--brands $(BRANDS)) $(if $(DELAY),--delay $(DELAY))

mini:
	$(CLI) mini $(if $(ARGS),$(ARGS),$(MINI_INPUT)) $(FLAGS)

analyze:
	$(CLI) analyze $(if $(ARGS),$(ARGS),)

# Vi du: make similar Angham | make similar BRAND="Lattafa Perfumes"
similar:
	$(CLI) similar $(if $(ARGS),$(ARGS),) $(if $(BRAND),--brand "$(BRAND)") $(if $(NOTES),--notes "$(NOTES)") $(if $(OCCASION),--occasion "$(OCCASION)") $(if $(LIMIT),--limit $(LIMIT)) $(if $(EXPLAIN),--explain)

test:
	$(PYTHON) tests/test_parsers.py
	$(PYTHON) tests/test_mini.py
	$(PYTHON) tests/test_brands.py
	$(PYTHON) tests/test_brand_products.py
	$(PYTHON) tests/test_resume_cache.py
	$(PYTHON) tests/test_schedule.py
	$(PYTHON) tests/test_analytics.py
	$(PYTHON) tests/test_vectors.py
	$(PYTHON) tests/test_retrieval.py
	$(PYTHON) tests/test_objects.py
	$(PYTHON) tests/test_lake.py
	$(PYTHON) tests/test_embedding.py
	$(PYTHON) tests/test_pgvector.py
	$(PYTHON) tests/test_llm.py
	$(PYTHON) tests/test_chat.py
	$(PYTHON) tests/test_airflow.py
	$(PYTHON) tests/test_charts.py
	$(PYTHON) tests/test_bronze.py
	$(PYTHON) tests/test_warehouse.py

# --- Docker ---------------------------------------------------------------
# Wrapper mong cho docker compose; xem README muc "Chay trong Docker".
COMPOSE ?= docker compose

# Xem datalake. push/pull/init goi thang bang CLI vi chung dung theo tinh huong.
lake:
	$(CLI) lake $(if $(ARGS),$(ARGS),status)

# --- Tang vector + chatbot ---------------------------------------------------
# embed can model ONNX; tren Windows thuong loi DLL nen chay trong container.
embed:
	$(CLI) embed $(if $(ARGS),$(ARGS),)

vectordb:
	$(CLI) vectordb $(if $(ARGS),$(ARGS),status)

# --- Airflow: dieu phoi -----------------------------------------------------
# MOT container (airflow standalone). Anh build tu scripts/airflow/.
#
# `exec -u airflow`: anh de USER root de start.sh chown duoc volume log roi ha
# quyen. Nhung Airflow cai trong /home/airflow/.local nen goi `airflow` bang root
# se ModuleNotFoundError.
AF = $(COMPOSE) exec -u airflow airflow

# Lan dau: tao database `airflow` trong Postgres dang co.
# (migrate + tai khoan admin do start.sh lo, moi lan bat.)
airflow-init:
	$(COMPOSE) build airflow
	$(COMPOSE) up airflow-db

airflow-up:
	$(COMPOSE) up -d airflow
	@echo "UI Airflow: http://localhost:$${AIRFLOW_PORT:-8082}"

airflow-down:
	$(COMPOSE) stop airflow

# Kiem DAG bang chinh Airflow + kiem anh perfume-intel co cu hon code khong.
#
# Phep kiem anh quan trong hon ve mat van hanh: Airflow chay task bang ANH, nen
# sua code ma khong build lai thi lich cu chay ban cu va KHONG bao loi gi.
airflow-check:
	@$(PYTHON) scripts/check_image_fresh.py || true
	$(AF) airflow dags list
	$(AF) airflow dags list-import-errors

airflow-logs:
	$(COMPOSE) logs -f airflow

# Log THUC THI cua mot task, dang doc duoc (Airflow 3 ghi log JSON-lines).
#   make airflow-log
#   make airflow-log ARGS="-d fragrantica_refresh -t vectordb_load"
#   make airflow-log ARGS="--liet-ke"
airflow-log:
	$(COMPOSE) exec -u airflow airflow 	  python /opt/airflow/scripts/airflow_log.py $(ARGS)

# Bat ca kho vector + API + trang test chatbot.
api:
	$(COMPOSE) up -d postgres api
	@echo "Trang test chatbot: http://localhost:8000"

docker-build:
	$(COMPOSE) build

# Chay mot lan dau tien: bat MinIO roi tao bucket + bat versioning cho bronze.
docker-init:
	$(COMPOSE) up -d minio
	$(COMPOSE) up minio-init

docker-up:
	$(COMPOSE) up -d

docker-down:
	$(COMPOSE) down

docker-logs:
	$(COMPOSE) logs -f scheduler

docker-queue:
	$(COMPOSE) run --rm cli queue

docker-test:
	$(COMPOSE) run --rm test
