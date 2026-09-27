.PHONY: help crawl brands products nam mini analyze queue daily test

PYTHON ?= python
CLI = $(PYTHON) -m perfume_intel

# Cho phép truyền đường dẫn ngay sau tên lệnh:
#     make crawl data/inputs/fragrantica
# Các từ đứng sau tên lệnh được biến thành target rỗng để make không báo lỗi.
FIRST := $(firstword $(MAKECMDGOALS))
ifneq ($(filter $(FIRST),crawl brands products nam mini analyze queue daily),)
  ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  .PHONY: $(ARGS)
  $(eval $(ARGS):;@:)
endif

# Nguồn mặc định khi không truyền đường dẫn.
FRAG_INPUT ?= data/inputs/fragrantica
NAM_INPUT  ?= data/inputs/namperfume
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
	@echo "make nam   [path]    Crawl namperfume.net         -> data/raw/namperfume/"
	@echo "make mini  [file]    Ghep ban mini voi du lieu da crawl (offline)"
	@echo "make queue           Xem tien do crawl (so theo doi)"
	@echo "make daily           Chay 1 lat ngan sach hom nay (nho giot)"
	@echo "make analyze         Phan tich thi truong         -> data/processed/<ngay>/"
	@echo "make test            Chay toan bo test (khong can mang)"
	@echo ""
	@echo "Bien: RECRAWL=1 RESUME=1 LIMIT=n FORMAT=both DELAY=\"15 30\""

# Fragrantica cần --render mới lấy được when-to-wear, độ lưu hương, độ toả hương.
crawl:
	$(CLI) crawl $(if $(ARGS),$(ARGS),$(FRAG_INPUT)) --render $(FLAGS)

# Danh mục hãng: chỉ ~12 request cho toàn bộ A-Z, chạy vài chục giây.
brands:
	$(CLI) brands $(if $(DELAY),--delay $(DELAY)) $(if $(FORMAT),--format $(FORMAT))

# Chai của từng hãng; mặc định lấy hãng từ file danh mục mới nhất.
BRANDS_FILE ?= $(lastword $(wildcard data/raw/fragrantica/brands_fragrantica_*.jsonl))
products:
	$(CLI) products $(if $(ARGS),$(ARGS),--from-brands $(BRANDS_FILE)) $(FLAGS)

# Nhỏ giọt theo lịch. BUDGET/BRANDS ghi đè mặc định trong config.py.
queue:
	$(CLI) queue $(if $(ARGS),$(ARGS),)

daily:
	$(CLI) daily --render $(if $(BUDGET),--budget $(BUDGET)) $(if $(BRANDS),--brands $(BRANDS)) $(if $(DELAY),--delay $(DELAY))

# namperfume không cần render: dữ liệu có sẵn trong HTML.
nam:
	$(CLI) crawl --site namperfume $(if $(ARGS),$(ARGS),$(NAM_INPUT)) $(FLAGS)

mini:
	$(CLI) mini $(if $(ARGS),$(ARGS),$(MINI_INPUT)) $(FLAGS)

analyze:
	$(CLI) analyze $(if $(ARGS),$(ARGS),)

test:
	$(PYTHON) tests/test_parsers.py
	$(PYTHON) tests/test_namperfume.py
	$(PYTHON) tests/test_mini.py
	$(PYTHON) tests/test_brands.py
	$(PYTHON) tests/test_brand_products.py
	$(PYTHON) tests/test_resume_cache.py
	$(PYTHON) tests/test_schedule.py
	$(PYTHON) tests/test_analytics.py
