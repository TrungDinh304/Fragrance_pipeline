# perfume-intel

Thu thập dữ liệu nước hoa rồi phân tích thị trường dựa trên **tín hiệu cộng
đồng** (rating, vote accord, vote mùa, độ lưu hương / toả hương) từ
[fragrantica.com](https://www.fragrantica.com/).

Hai nửa tách rời nhau, và chỗ nối là **bronze**:

```
    CHẠM MẠNG  (nhỏ giọt)          │           CHẠY OFFLINE
  ─────────────────────────────────┼──────────────────────────────────────────
                                   │
   crawl · brands                  │   ┌─▶ silver ─▶ gold      Parquet, rồi
   products · daily ──▶ ┌────────┐ │   │   (Parquet)  (dbt)    5 mart DuckDB
                        │ BRONZE │─────┼─▶ analyze             CSV + report.html
   ngân sách/hàng đợi   │ MinIO  │ │   │   (analytics/)
   SQLite, local ──────▶│  (S3)  │ │   │
                        └────────┘ │   └─▶ similar              chai/hãng giống
                                   │       (vectors+retrieval)   nhau, kèm lý do
```

Ba nhánh đọc đều bắt từ **bronze**, không nhánh nào phụ thuộc nhánh khác. `analyze`
cố ý **không** đi qua silver/gold: nó là bên đối chứng độc lập cho dbt — hai đường
tính ra lệch nhau thì có test đỏ.

Crawl chậm và phụ thuộc mạng nên chỉ chạy khi cần dữ liệu mới. Mọi thứ phía sau
bronze chạy **hoàn toàn offline**, sửa công thức rồi chạy lại bao nhiêu lần cũng
được — đó là lý do bronze được giữ nguyên trạng và không bao giờ bị sửa.

## Techstack

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ỨNG DỤNG    cli/ (13 lệnh)  ·  api/ FastAPI  ·  trang test chatbot      │
│                llm/  9router — tách ý định + diễn đạt  ◄── CỔNG 5        │
└────────┬─────────────────────────────────────────────────────────────────┘
         │ phụ thuộc:  retrieval/ports.py          ◄── CỔNG 4
┌────────▼─────────────────────────────────────────────────────────────────┐
│  TRUY XUẤT    retrieval/pgvector_store.py   Postgres + pgvector          │
│                retrieval/memory.py          in-memory (đối chứng)        │
│                embedding/  ONNX local, 384 chiều  ◄── CỔNG 6             │
│                vectors/    khối thưa, cho phần 'vì sao'                  │
│                                                                          │
│  PHÂN TÍCH    analytics/   Python thuần · SVG tự sinh                    │
│  GOLD         transform/   dbt + DuckDB · 5 mart · 17 kiểm tra           │
│  SILVER       warehouse/   Parquet có kiểu  +  embedding Parquet         │
└────────┬─────────────────────────────────────────────────────────────────┘
         │ phụ thuộc:  core/bronze.py             ◄── CỔNG 3
┌────────▼─────────────────────────────────────────────────────────────────┐
│  BRONZE       MinIO / S3 · JSONL thô · chỉ ghi thêm · versioning         │
│                core/lake.py      spool · niêm · kéo về                   │
│                core/objects.py   LocalStore | S3Store  ◄── CỔNG 2        │
└────────▲─────────────────────────────────────────────────────────────────┘
         │ ghi:  append từng dòng, niêm sau mỗi hãng
┌────────┴─────────────────────────────────────────────────────────────────┐
│  THU THẬP     sources/   SiteScraper            ◄── CỔNG 1               │
│                core/http      requests + throttle + cache                │
│                core/browser   Playwright + Chrome THẬT                   │
│                pipelines/state.py   SQLite — hàng đợi, LUÔN local        │
└──────────────────────────────────────────────────────────────────────────┘
```

**Sáu cổng** là chỗ được phép thay ruột mà phía trên không phải sửa:

| | Cổng | Thay được gì | Canh bằng |
|---|---|---|---|
| 1 | `sources/base.py` `SiteScraper` | thêm site mới | `test_parsers.py` · `test_resume_cache.py` |
| 2 | `core/objects.py` `ObjectStore` | MinIO → AWS/R2/SeaweedFS | **hợp đồng 19 điều khoản**, chạy cho cả 2 adapter |
| 3 | `core/bronze.py` | bố cục kho thô, đĩa ↔ S3 | `test_bronze.py` · `test_lake.py` |
| 4 | `retrieval/ports.py` `Retriever` | in-memory ↔ pgvector | **hợp đồng 30 điều khoản**, chạy cho cả 2 adapter |
| 5 | `llm/ports.py` `ChatModel` | 9router → OpenAI/Ollama/bất kỳ | tự xuống cấp được khi mất model |
| 6 | `embedding/ports.py` `Embedder` | ONNX local → API nào khác | **hợp đồng 11 điều khoản** + bộ đo chất lượng riêng |

Hai thứ **không** đi qua cổng, có chủ đích: `pipelines/state.py` (SQLite, luôn
local) và `transform/` (dbt tự là một ranh giới). `vectors/` cũng không — nó là
chi tiết cài đặt nằm sau cổng 4.

Luật đi lại đầy đủ và lý do từng quyết định:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Chọn gì, và vì sao chọn được lâu

| Lớp | Công nghệ | Lý do bền |
|---|---|---|
| Thu thập | `requests` + **Playwright với Chrome thật** | Chromium đóng gói bị Cloudflare chặn 9/10 trang từ cùng IP ⇒ chặn là theo *dấu vân tay*, không theo IP |
| Hàng đợi / ngân sách | **SQLite** (WAL) | một file, đọc được sau hàng chục năm, không cần server |
| Kho đối tượng | **API S3** qua `boto3` | MinIO hôm nay, AWS/R2/Wasabi sau này — cùng một đoạn code, chỉ đổi biến môi trường |
| Bronze | **JSONL** theo entity | ghi thêm được, không khoá schema, đọc được bằng mắt |
| Silver | **Parquet** | chuẩn mở có kiểu; DuckDB/pandas/Polars/Spark/Trino đều đọc |
| Gold | **dbt** + **DuckDB** | dbt là chuẩn ngành; đổi DuckDB → Postgres chỉ là đổi adapter |
| Truy xuất | **cổng + adapter**, vector thưa có IDF | giải thích được *vì sao giống* — thứ embedding đặc không cho |
| Biểu đồ | **SVG tự sinh** | không CDN, không JS ⇒ mở được offline sau nhiều năm |
| Embedding | **ONNX** (fastembed), KHÔNG torch | cùng model, ~250 MB thay vì ~2,5 GB |
| Kho vector | **Postgres + pgvector** | mọi phép tính trong SQL; đã có Postgres thì không thêm hạ tầng |
| Gọi AI | **9router** (endpoint kiểu OpenAI) | gộp 40+ nhà cung cấp; đổi sang API trực tiếp chỉ là đổi env |
| Đóng gói | **Docker Compose** | 8 service, một lệnh bật |
| Test | **stdlib**, không framework | `python tests/test_x.py` chạy được ở mọi nơi; pytest là tuỳ chọn |

### Thứ cố ý CHƯA có

| | Khi nào làm |
|---|---|
| Hội thoại nhiều lượt (nhớ ngữ cảnh) | khi trang test thành sản phẩm thật |
| Tìm kiếm trộn (hybrid: thưa + đặc) | khi thấy câu hỏi có note cụ thể bị trả lời kém |
| State store dùng chung (Postgres) | khi muốn nhiều máy crawl song song |
| Xác thực / giới hạn tần suất cho API | khi API ra khỏi máy cá nhân |

Embedding, API, và vector DB **đã có** — chúng rời khỏi danh sách này khi trang
chatbot trở thành người dùng thật đầu tiên. Đó đúng là cách danh sách này nên hoạt
động: điều kiện kích hoạt đến thì làm, không làm trước.

Danh sách này nằm trong repo có chủ đích: để lần sau không ai phải đoán "có nên
thêm vector DB không" — điều kiện kích hoạt đã ghi sẵn kèm số đo hiện tại.

## Cài đặt

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

Bản lõi chỉ có `requests` + `beautifulsoup4` + `lxml` — crawl và phân tích đã
chạy được. Phần nặng tách thành **extra**, cài thêm khi cần:

| Extra | Kéo theo | Mở ra cái gì |
|---|---|---|
| `render` | `playwright` | cờ `--render`: when-to-wear, độ lưu hương, độ toả hương |
| `warehouse` | `duckdb` (~22 MB, không phụ thuộc gì) | tầng silver (Parquet) + hỏi bằng SQL |
| `marts` | `dbt-duckdb` (~35 gói) | tầng gold: 5 mart có kiểm thử |
| `lake` | `boto3` | bronze/silver trên MinIO hoặc S3 |

```powershell
pip install -e ".[render]"            # rồi: python -m playwright install chromium
pip install -e ".[render,warehouse,marts,lake]"   # tất cả
```

Cài xong có lệnh `perfume-intel`. Không muốn cài thì chạy thẳng
`python -m perfume_intel ...` — hai cách tương đương, tài liệu dưới đây dùng
cách thứ hai.

**Không muốn cài gì lên máy thì dùng Docker** — xem
[Chạy trong Docker](#chạy-trong-docker). Ảnh đã có sẵn Google Chrome thật, đủ
extra, kèm MinIO làm datalake.

## Bố cục dữ liệu

| Thư mục | Nội dung |
|---|---|
| `data/inputs/<site>/` | File CSV chứa link cần crawl (bạn tự bỏ vào) |
| `data/raw/<site>/` | Kết quả crawl, mỗi hãng một file `.jsonl` theo ngày |
| `data/silver/` | Tầng silver: Parquet đã khử trùng, có kiểu |
| `data/warehouse/` | Tầng gold: mart do dbt dựng (DuckDB) |
| `data/state/` | Sổ tiến độ crawl (SQLite) — **luôn chỉ của máy này** |
| `data/processed/` | Đầu ra của `analyze` và `mini` |
| `data/scratch/` | File chạy thử lặt vặt, không ai đọc tới |
| `.cache/html/` | HTML thô đã tải (rất nặng, xoá được bất cứ lúc nào) |

Cả `data/` lẫn `.cache/` đều không commit.

**Khi chạy bằng Docker Compose, `data/raw/` và `data/silver/` KHÔNG còn là bản
gốc** — bản chính thức nằm trên MinIO, còn hai thư mục này tụt xuống thành spool
ghi trước + cache đọc. Xem [Data lake trên MinIO](#data-lake-trên-minio).

## Lệnh make

```powershell
make help                            # xem nhanh các lệnh

# --- chạm mạng (nhỏ giọt) ---
make brands                          # danh mục hãng A-Z  (~12 request)
make products                        # mục lục chai của từng hãng
make crawl                           # crawl data/inputs/fragrantica
make crawl data/inputs/fragrantica   # chỉ định thư mục khác
make daily                           # chạy 1 lát ngân sách
make queue                           # xem tiến độ, không ra mạng

# --- offline ---
make silver                          # bronze -> Parquet          [warehouse]
make marts                           # dbt build -> 5 mart        [marts]
make analyze                         # chỉ số + report.html -> data/processed/<ngày>/
make similar Angham                  # chai nào giống chai này
make similar NOTES="oud,vanilla"     # chai nào nhiều note này nhất
make mini                            # ghép bản mini
make test                            # 18 bộ · 335 test · không cần mạng
make lake                            # datalake: hai bên đang có gì  [lake]

# --- Docker ---
make docker-init                     # lần đầu: bật MinIO + tạo bucket
make docker-up                       # bật MinIO + bộ lên lịch
make docker-logs                     # xem bộ lên lịch đang làm gì
make docker-queue                    # tiến độ trong container
make docker-down
```

`make lake` chỉ xem. Ba việc còn lại (`push` / `pull` / `init`) gọi thẳng bằng CLI
vì chúng dùng theo tình huống chứ không theo thói quen — xem
[Data lake trên MinIO](#data-lake-trên-minio).

`make` không cho truyền thẳng cờ `--recrawl`/`--resume` (nó hiểu là option của
chính `make`), nên dùng biến:

```powershell
make crawl RESUME=1                  # crawl nốt URL còn thiếu
make crawl RECRAWL=1                 # crawl lại cả hãng đã có file kết quả
make crawl DELAY="15 30"             # nghỉ 15-30s giữa 2 request
make crawl LIMIT=5                   # thử nhanh 5 chai mỗi hãng
make crawl FORMAT=both               # xuất thêm CSV bên cạnh JSONL
```

## Crawl

```powershell
# 1. Một hoặc nhiều URL cụ thể
python -m perfume_intel crawl https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html

# 2. Toàn bộ nước hoa của một hãng (lấy link từ trang designer rồi crawl từng chai)
python -m perfume_intel crawl --designer Dior --limit 20

# 3. Một file CSV chứa link
python -m perfume_intel crawl data/inputs/fragrantica/Chanel.csv --render

# 4. Cả một thư mục CSV — mỗi file ra một cặp file kết quả riêng
python -m perfume_intel crawl data/inputs/fragrantica --render --resume

# 5. File text, mỗi dòng một URL (dòng bắt đầu bằng '#' bị bỏ qua)
python -m perfume_intel crawl links.txt

# 7. Chỉ liệt kê link có trong một trang, không crawl chi tiết
python -m perfume_intel links https://www.fragrantica.com/designers/Dior.html
```

Không truyền `--out` thì kết quả vào `data/raw/<site>/<Tên Hãng>_<site>_<ddmmyy>.jsonl`
— ví dụ `data/raw/fragrantica/Chanel_fragrantica_210926.jsonl`.

### Crawl cả thư mục

Đây là cách dùng chính khi gom dữ liệu hàng loạt. Mỗi file `.csv` trong thư mục
được coi là một hãng (lấy theo tên file), crawl xong ra một file kết quả riêng.

Mặc định **chỉ crawl hãng chưa có file kết quả** — chạy lại lệnh cũ sẽ đi tiếp
từ chỗ dừng chứ không làm lại từ đầu:

```powershell
python -m perfume_intel crawl data/inputs/fragrantica --render
```

| Muốn gì | Cờ |
|---|---|
| Crawl lại tất cả, kể cả hãng đã xong | `--recrawl` |
| Crawl nốt URL còn thiếu *bên trong* file kết quả cũ | `--resume` |
| Bị chặn, muốn chạy chậm lại | `--delay 15 30` |

`--resume` dùng lại file kết quả gần nhất của hãng đó thay vì mở file mới theo
ngày hôm nay — nếu không, resume sang ngày khác sẽ crawl lại từ đầu.

### Đọc link từ CSV

File CSV đầu vào do người khác xuất ra nên không chuẩn hoá được. Crawler tự dò:

- dấu phân cách `,` `;` `\t` `|` (Excel tiếng Việt hay xuất bằng `;`);
- có dòng tiêu đề hay không;
- cột nào chứa link — ưu tiên **theo tên miền**: URL thuộc site đang crawl là
  link nguồn, URL khác tên miền là `des_url` (link đích bên mình, chỉ gắn kèm
  vào kết quả để đối chiếu, crawler **không** truy cập).

Tự dò sai thì chỉ định thẳng: `--url-column`, `--dest-column`.

## Danh mục hãng

Crawl **toàn bộ hãng** trên Fragrantica (chỉ hãng, không chạm vào chai nào):

```powershell
python -m perfume_intel brands                 # đủ A-Z -> data/raw/fragrantica/
python -m perfume_intel brands --letters ABC   # thử nhanh vài chữ cái
python -m perfume_intel brands --only-az       # bỏ section chữ có dấu
```

Ra `data/raw/fragrantica/brands_fragrantica_<ddmmyy>.jsonl` + `.csv`, khoảng
**8.200 hãng**. Mỗi bản ghi: `brand_name`, `brand_url`, `alphabet`,
`popular_rank`, `scraped_at`.

Cách chạy:

1. tải `/designers/` **một lần**;
2. đọc khối "Most Popular Brands" ở footer (60 hãng, giữ luôn thứ hạng — bản
   thân thứ hạng đó là một tín hiệu cộng đồng);
3. đọc mục lục A-Z từ `.alphabet-link`, **lấy href thật trong DOM**;
4. tải từng trang mục lục, cắt đúng section của mỗi chữ cái;
5. gộp tất cả, khử trùng theo `brand_url`.

Vài điểm đáng biết, đều đo trên trang thật:

- **26 chữ cái chỉ nằm trên 11 trang.** `/designers-5/` chứa cả F, G và H. Tự
  dựng URL theo chữ cái sẽ sai; phải đọc href. Các trang được gom lại nên cả job
  chỉ tốn **~12 request**, chạy xong trong vài chục giây.
- **Phải cắt theo anchor.** Mỗi chữ cái là `<span id="F">` rồi tới lưới hãng.
  Lấy nguyên trang sẽ gộp nhầm hãng của chữ cái khác.
- **Hãng trùng thì hợp nhất, không đè.** Dior vừa ở "Most Popular" (rank 2) vừa
  ở mục lục (chữ D) — bản ghi cuối giữ cả hai.
- **27 hãng nằm ngoài A-Z.** Mục lục chỉ có A-Z, nhưng trang còn section chữ có
  dấu (`À`, `É`, `Ô`, `Œ`...) chứa những hãng thật như *Água da Madeira*,
  *ÉCLAT*, *éther*. Trang đã tải rồi nên quét thêm không tốn request nào —
  mặc định có lấy, dùng `--only-az` nếu muốn bám đúng 26 chữ cái.
- **Không cần `--render`.** Đã đo: mở `/designers-1/`, `/designers-5/`,
  `/designers-8/` bằng browser thật rồi cuộn hết đáy, số hãng y hệt bản
  `requests` (A=859, F/G/H=300/240/253, M=760). Chỉ ảnh logo là lazy-load; thẻ
  `<a>` tới từng hãng đã có sẵn trong HTML. Cũng không có phân trang trong một
  chữ cái.
- Thiếu bất kỳ chữ cái nào thì lệnh **trả exit code 1** kèm log chỉ rõ chữ nào
  hỏng, thay vì báo thành công với dữ liệu khuyết.

## Sản phẩm của hãng

Từ `brand_url` đi tiếp vào trang hãng để lấy **danh sách chai theo collection**:

```powershell
# một hãng
python -m perfume_intel products https://www.fragrantica.com/designers/Afnan.html

# cả danh mục 8.200 hãng, ghi dần, đứt thì chạy lại với --resume
python -m perfume_intel products --from-brands data/raw/fragrantica/brands_fragrantica_220926.jsonl
python -m perfume_intel products --from-brands ... --resume
```

Ra `data/raw/fragrantica/brand_products_fragrantica_<ddmmyy>.jsonl` + `.csv`.
Mỗi dòng là **một chai trong một collection**:

| Cột | Ví dụ |
|---|---|
| `brand_name`, `brand_url` | `Afnan`, `.../designers/Afnan.html` |
| `collection`, `collection_anchor` | `9AM 9PM`, `9AM-9PM` (trống nếu chai không thuộc dòng nào) |
| `perfume_id`, `perfume_name` | `123313`, `9 PM Night Out` |
| `perfume_url` | link chai — đầu vào cho lệnh `crawl` |
| `year`, `gender`, `comments` | `2026`, `Unisex`, `379` |

Đây là bước **nối** giữa `brands` và `crawl`:

```
brands  ->  products  ->  crawl
(hãng)      (chai nào của hãng)   (tháp hương, accord, vote)
```

Vài điểm đo được trên trang thật (Afnan: 137 chai, 19 collection):

- **Không cần `--render`.** Khối `#brands` do Vue dựng, nhưng markup đã nằm sẵn
  trong `<template>` của HTML tĩnh. Đã đối chiếu bản `requests` với bản browser
  đã cuộn hết, so **từng trường của từng chai**:

  | Hãng | Quy mô | Kết quả |
  |---|---|---|
  | Afnan | 137 chai / 19 collection | trùng khít |
  | Avon | 1.379 chai / 150 collection (HTML 9,6 MB) | trùng khít |

  Avon mới là phép thử đáng tin — hãng lớn nhất thì lazy-load hay phân trang
  phải lộ ra trước. Mỗi hãng vì vậy chỉ tốn **1 request**, không cần mở browser.
  `test_render_khong_them_gi` so bản tĩnh với bản render đã lưu trong
  `tests/fixtures/`, nên nếu Fragrantica đổi sang lazy-load thật thì test đỏ
  trước khi dữ liệu thiếu âm thầm.
- **Bẫy `<template>`:** BeautifulSoup gắn nhãn `TemplateString` cho chữ bên
  trong `<template>` và `get_text()` **mặc định bỏ qua** — tên chai lẫn tên
  collection sẽ rỗng sạch. Phải gọi `get_text(..., types=None)`; xem `_tpl_text`
  trong `parsers.py`.
- **"All Fragrances" không phải một collection.** Nó chứa 31 chai *không thuộc
  dòng nào*, còn 19 collection có tên giữ 106 chai — hai nhóm rời nhau hoàn
  toàn, cộng lại đúng 137. Vì vậy chai trong nhóm đó để `collection` trống chứ
  không ghi "All Fragrances".
- **Năm chưa rõ được trang ghi là `0000`**, đã đổi thành trống.
- HTML tĩnh chứa **cả hai view** (lưới + danh sách) nên header collection xuất
  hiện hai lần; parser chỉ bám view danh sách nên không đếm đôi.

## Kho phân tích: silver (Parquet) và gold (dbt + DuckDB)

Ba tầng, mỗi tầng trả lời một loại câu hỏi khác nhau:

| Tầng | Dạng | Dùng để |
|---|---|---|
| bronze | JSONL thô, mỗi hãng một file theo ngày | ghi khi crawl, không mất gì |
| **silver** | Parquet đã khử trùng, có kiểu, trải phẳng | hỏi bằng SQL |
| **gold** | bảng do dbt dựng, trong một file DuckDB | chỉ số có kiểm thử |

```powershell
pip install -e ".[warehouse]"      # chỉ cần duckdb (~22 MB, không kéo gói nào khác)
python -m perfume_intel silver     # bronze -> data/silver/*.parquet

python -m perfume_intel silver --sql "
  SELECT b.brand_name, COUNT(*) tong, COUNT(p.perfume_key) da_crawl
  FROM brand_perfumes b LEFT JOIN perfumes p USING (perfume_key)
  GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
```

### Tầng silver làm đúng ba việc

1. **Khử trùng** — mỗi chai một dòng, giữ bản `scraped_at` mới nhất
   (789 dòng thô → 629 chai).
2. **Đặt kiểu** — `year` là INTEGER, `rating` là DOUBLE. Để nguyên chuỗi thì
   `"999" > "2015"` và mọi phép so sánh năm đều sai.
3. **Trải phẳng cái lồng nhau** — và đây mới là việc đáng giá.

Bảy bảng, 31.659 dòng, **1,2 MB Parquet** (so với 11 MB JSONL):

```
perfumes         629   chi tiết chai, một dòng một chai
perfume_accords 5347   DÀI: một dòng một (chai, accord) kèm độ mạnh
perfume_notes   5738   DÀI: một dòng một (chai, tầng, note)
perfume_wear    3768   DÀI: một dòng một (chai, trục hoàn cảnh)
brands          8235   danh mục hãng
brand_perfumes  7938   mục lục chai của hãng
```

Khi accord còn nằm trong mảng JSON, câu *“accord nào phổ biến dần lên theo năm”*
bắt buộc phải viết bằng Python. Ở dạng bảng dài nó là một câu `GROUP BY`.

### Tầng gold: marts bằng dbt

```powershell
pip install -e ".[marts]"          # nặng hơn hẳn: ~35 gói
make marts                          # dbt build -> data/warehouse/perfume.duckdb
```

Năm mart: `mart_brand`, `mart_accord`, `mart_accord_occasion`, `mart_coverage`,
`mart_note_pairs`. Kèm 17 test dbt (khoá duy nhất, không NULL, giá trị hợp lệ)
và 3 test tự viết — một trong số đó kiểm *thị phần chú ý của mọi hãng cộng lại
phải ra 100%*, thứ sẽ sai âm thầm nếu mẫu số bị tính trên một tập con.

**`analytics/metrics.py` cố ý KHÔNG đổi một dòng — nó là bộ đối chứng.**
`tests/test_warehouse.py` so từng dòng giữa mart và bản Python:

```
mart_brand      Python   80 hãng   dbt   80 hãng   LỆCH 0
mart_accord     Python   64        dbt   64        LỆCH 0
mart_coverage   Python  437        dbt  437        LỆCH 0
```

Công thức hiệu chỉnh Bayes viết lại bằng SQL rất dễ lệch ở mẫu số hoặc ở chỗ lọc
`rating > 0`, mà lệch kiểu đó không làm hỏng gì — chỉ làm bảng xếp hạng sai một
cách rất thuyết phục.

### Bốn cái bẫy đã gặp, ghi lại để khỏi gặp lại

- **dbt đọc YAML bằng encoding của hệ điều hành.** Trên Windows là cp1252 nên nó
  chết ngay ở dòng tiếng Việt đầu tiên trong `dbt_project.yml`. `make marts` đặt
  sẵn `PYTHONUTF8=1`.
- **Thư mục `target/` của dbt không được nằm trong `data/`.** Nó chứa cache parse
  theo đường dẫn tuyệt đối; để trong bind mount thì container đọc nhầm cache của
  host rồi chết với `KeyError: dbt_duckdb://macros/catalog.sql`.
- **…và cũng không được đóng vào ảnh Docker.** Cùng một lỗi, quay lại bằng đường
  khác: `target/` đã ra khỏi `data/` nhưng vẫn nằm trong build context, nên bị
  `COPY` vào ảnh kèm đường dẫn của máy dựng. `docker compose run --rm marts` chết
  với `KeyError: dbt_duckdb://macros/columns.sql`. `.dockerignore` giờ loại
  `transform/target/` và `transform/logs/`.
- **`PASS=24` ở dòng cuối KHÔNG phải số test.** Đó là tổng số node: 5 mart + 2
  view + **17 test**. Đọc nhầm con số đó một lần rồi, và nó đã nằm sai trong tài
  liệu suốt mấy tuần. Muốn số test thì đọc dòng `Finished running ... 17 data
  tests`, hoặc `dbt ls --resource-type test`.

### Có cần tới mức này không

Thành thật: ở 629 chai thì **không** — DuckDB không giải quyết vấn đề tốc độ nào
cả, mọi chỉ số đã có sẵn bản Python chạy trong một giây. Tầng này mua được hai
thứ khác: hỏi được bằng **SQL** thay vì phải viết Python cho từng câu hỏi mới,
và có **test trên chính phép biến đổi**. Nút thắt thật vẫn là tốc độ crawl
(độ phủ 1,9%).

## Lên lịch nhỏ giọt (orchestration)

Fragrantica chặn thiết bị truy cập quá dày, nên không thể cào một mạch. Cách
sống chung là **nhỏ giọt**: mỗi ngày đụng 1–2 hãng trong một hạn ngân sách
request, phần còn lại để mai. Tiến độ được ghi ở mức **từng chai** nên hãng lớn
tự tràn qua nhiều ngày và hôm sau đi tiếp đúng chỗ dừng.

```powershell
# 1. Nạp hàng đợi từ danh mục hãng đã crawl
python -m perfume_intel queue --seed data/raw/fragrantica/brands_fragrantica_<ddmmyy>.jsonl

# 2. Ghi nhận dữ liệu đã crawl từ trước, để lịch không làm lại
python -m perfume_intel queue --import-existing

# 3. Xem sẽ làm gì, không ra mạng
python -m perfume_intel daily --dry-run

# 4. Chạy một lát ngân sách
python -m perfume_intel daily --render --budget 150 --brands 2

# 5. Theo dõi
python -m perfume_intel queue                      # tiến độ tổng
python -m perfume_intel queue --runs               # lịch sử từng lần chạy
python -m perfume_intel queue --brand "Dior"       # một hãng cụ thể
```

### Đăng ký chạy tự động (Windows Task Scheduler)

```powershell
.\scripts\daily_crawl.ps1 -Install            # 02:30 hằng ngày
.\scripts\daily_crawl.ps1 -Install -At 03:00
schtasks /Run /TN PerfumeIntel-Daily           # chạy thử ngay
.\scripts\daily_crawl.ps1 -Uninstall
```

Log ra `data/logs/daily_<ngày>.log`. Script ép UTF-8 vì console Windows mặc định
cp1252 sẽ làm chết log tiếng Việt. Exit code: `0` xong, `1` lỗi thường,
`2` bị chặn.

Máy tắt vào giờ hẹn thì Task Scheduler bỏ lỡ lần đó — không sao, hàng đợi vẫn
nằm trong sổ, lần sau đi tiếp.

### Chạy trong Docker

Cách này thay cho Task Scheduler và không cần cài Python, Playwright hay Chrome
trên máy — ảnh đã có sẵn Google Chrome thật.

```bash
cp .env.example .env          # sửa TZ / RUN_AT / BUDGET, ĐỔI MẬT KHẨU MinIO
docker compose build
docker compose up -d minio    # bật kho dữ liệu trước
docker compose up minio-init  # tạo bucket + bật versioning (chạy một lần)
docker compose up -d          # bật bộ lên lịch, chạy theo RUN_AT

# hoặc gọn hơn: make docker-build && make docker-init && make docker-up

docker compose logs -f scheduler        # xem nó đang làm gì
docker compose run --rm cli lake status # bronze đang nằm ở đâu, lệch bao nhiêu
docker compose run --rm cli queue       # tiến độ
docker compose run --rm cli daily --render --budget 20   # chạy tay một lượt
docker compose run --rm cli silver      # bronze -> Parquet
docker compose run --rm marts           # dbt build -> bảng gold
docker compose run --rm test            # toàn bộ test, không cần mạng (lake tắt)
docker compose down
```

Xem dữ liệu bằng mắt: <http://localhost:9001> (user/pass lấy từ `.env`).

**Lần đầu, đưa kho đã crawl sẵn lên MinIO:**

```bash
docker compose run --rm cli lake push --dry-run   # xem sẽ niêm gì
docker compose run --rm cli lake push             # niêm thật
```

Muốn biết ngay là nó hoạt động, đừng chờ tới 02:30:

```bash
RUN_ON_START=1 docker compose up   # chạy một lượt liền, để nguyên terminal mà xem
```

Sáu service: `minio` (kho dữ liệu, chạy nền), `minio-init` (tạo bucket rồi thoát),
`scheduler` (chạy nền, `restart: unless-stopped`), `cli` (chạy tay một lệnh bất
kỳ), `marts` (dbt), `test`. Ba cái cuối nằm trong profile `cli` nên
`docker compose up` không đụng tới.

Vài điểm đã cân nhắc, để sau này không phải dò lại:

- **`./data` và `./.cache` là bind mount, không phải named volume.** Trên máy này
  sổ theo dõi và dữ liệu đã crawl đang nằm ở `./data`; dùng named volume thì
  container khởi đầu trên một bản trống và crawl lại từ đầu.
- **Riêng MinIO thì dùng named volume**, vì đó là định dạng nội bộ của nó, không
  phải file để người ta mở. Trên Windows, bind mount đi qua WSL2 còn gây lỗi
  quyền với user 1001 trong ảnh. Sao lưu không phải copy volume đó — chạy
  `cli lake pull` là có lại toàn bộ bronze dưới dạng `.jsonl` trong `data/raw/`.
- **Service `test` đặt `LAKE=off` và không kế thừa khoá MinIO.** Một bài test viết
  sai đường dẫn không được phép ghi vào kho dữ liệu chính thức.
- **Ảnh MinIO pin theo digest.** `minio/minio` trên Docker Hub đã không còn pull
  được và `quay.io/minio/minio` đòi đăng nhập, nên compose dùng
  `bitnamilegacy/minio` ghim bằng `sha256:...`. Đổi sang bản khác (hoặc sang AWS
  S3 / Cloudflare R2) chỉ là đổi `S3_ENDPOINT` và khoá — code chỉ nói API S3.
- **Ảnh có Google Chrome thật**, không phải Chromium đóng gói của Playwright —
  bản đóng gói bị Cloudflare chặn 9/10 trang (xem `core/browser.py:_launch_browser`).
  Nền là `python:3.13-slim-bookworm`, không phải ảnh của Playwright: ảnh đó kéo
  theo Firefox và WebKit không dùng tới, mà lại chỉ có Python 3.12.
- **`shm_size: 1gb`.** `/dev/shm` mặc định 64 MB, Chrome chết giữa trang trên
  những trang nặng như Fragrantica.
- **Lỗi không làm container thoát.** Với `restart: unless-stopped`, thoát
  non-zero nghĩa là Docker bật lại ngay và cào tiếp liền tay — đúng thứ mà cơ chế
  nhỏ giọt tồn tại để tránh. Script ghi log rồi chờ lượt sau.
- **Container dùng chung IP với máy host.** Chặn của Fragrantica là ở mức thiết
  bị/IP, nên chạy song song cả lịch trong Docker lẫn Task Scheduler là tự nhân
  đôi số request. Chọn một.
- **Chrome trong container là bản Linux, còn `config.USER_AGENT` khai Windows.**
  Chuỗi UA bị ghi đè nên JS thấy Windows, nhưng client hints
  (`Sec-CH-UA-Platform`) vẫn nói Linux — hai thứ không khớp nhau. Ba request thử
  đều qua, nhưng nếu tỉ lệ 403 trong Docker cao hơn hẳn khi chạy trực tiếp thì
  đây là chỗ đầu tiên nên nhìn.
- **Mỗi lần chỉ nên có một tiến trình ghi sổ.** SQLite mở ở chế độ WAL và có
  `timeout=30` nên đọc chồng nhau thì ổn, nhưng hai lượt `daily` cùng lúc là tự
  tạo tranh chấp không cần thiết.

### Data lake trên MinIO

MinIO đóng vai trò **datalake**: bronze và silver nằm ở đó, không nằm trên ổ đĩa
máy nào.

```
LAKE=off  (mặc định khi chạy tay)    LAKE=s3  (Docker Compose đặt sẵn)
data/raw/  = BẢN GỐC                 s3://bronze/  = BẢN GỐC
                                     data/raw/     = spool ghi trước + cache đọc
```

Xoá `data/raw/` sau khi đã niêm là an toàn; xoá bucket thì không.

```bash
perfume-intel lake status            # hai bên đang có gì, lệch chỗ nào
perfume-intel lake push              # niêm mọi thứ local lên lake
perfume-intel lake pull              # kéo lake về máy này
perfume-intel lake init              # tạo bucket (Compose đã làm hộ)
```

Lúc chạy bình thường **không cần gọi tay**: crawl tự niêm sau mỗi hãng, và mọi
lệnh đọc tự kéo về. Ba lúc cần dùng tay: di trú lần đầu, máy/container mới, và
dọn phần còn sót sau một lượt crawl bị ngắt giữa hãng.

Chạy CLI ngoài Docker mà vẫn muốn dùng lake:

```bash
export LAKE=s3 S3_ENDPOINT=http://localhost:9000
export S3_ACCESS_KEY=perfume S3_SECRET_KEY=perfume-dev-only
perfume-intel lake status
```

**Ba điều cần biết:**

- **Spool là bắt buộc, không phải tạm bợ.** S3 không có `append`, mà crawler ghi
  từng dòng ngay khi có — đó là lý do mẻ 10 ngày đứt mạng không mất dữ liệu. Nên
  nó append local rồi *niêm* một lần mỗi hãng. Niêm cả khi bị 429: hãng bị chặn ở
  chai thứ 300 vẫn đưa 299 chai kia lên lake.
- **File dài hơn thì thắng.** Bronze chỉ ghi thêm, nên số byte là thước đo bên nào
  có nhiều dữ liệu hơn. Local dài hơn (đang crawl dở) thì lake không ghi đè.
- **Sổ tiến độ vẫn chỉ của máy này.** `data/state/crawl_state.db` là SQLite, không
  chạy được trên S3. Nên **đừng crawl từ hai máy cùng lúc** — chúng sẽ làm trùng
  việc và ghi đè file của nhau. Lake làm dữ liệu dùng chung được, không làm hàng
  đợi dùng chung được.

Chi tiết thiết kế và lý do: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Chatbot: hỏi bằng tiếng Việt (`api` + `embed` + `vectordb`)

```bash
make docker-init                    # lần đầu: MinIO + bucket
docker compose run --rm cli embed       # bronze -> embedding Parquet (~50s)
docker compose run --rm cli vectordb load   # Parquet -> Postgres + pgvector
make api                            # -> http://localhost:8000
```

Trang test hiện **câu trả lời và dữ liệu gốc cạnh nhau**, vì câu trả lời do model
viết còn danh sách là của retriever — có đối chiếu được thì mới tin được.

#### Hội thoại nhiều lượt

Trang giữ ngữ cảnh, nên nói tiếp được:

```
Bạn:  mùi gỗ trầm ấm cho buổi tối mùa đông
Bot:  Green Wood, Red Wood, Wood for Him …
Bạn:  nhẹ hơn chút được không          ← vẫn mùa đông + buổi tối, chai KHÁC
Bot:  London, Inara Black, Oud Wood …
Bạn:  chai thứ 2 thì sao               ← trỏ vào Inara Black
Bot:  Inara Black … mùi leather, note spicy notes …
Bạn:  còn gì nữa không                 ← quay lại mạch tìm kiếm trước đó
Bạn:  mùa hè thì sao                   ← ĐỔI mùa, không phải thêm mùa
Bạn:  cho nữ thì sao                   ← thêm giới tính, giữ mùa hè
```

Mỗi lượt được phân thành một trong **ba việc**, và đây là phần khó nhất:

| | Khi nào | Làm gì |
|---|---|---|
| `moi` | khách hỏi chuyện khác hẳn | bỏ hết điều kiện cũ |
| `loc_them` | "nhẹ hơn", "còn gì nữa", "mùa hè thì sao" | **gộp** với điều kiện cũ |
| `ve_chai` | "chai thứ 2", "cái đầu tiên", "chai đó" | trỏ vào đúng chai đã hiện |

Bốn luật đáng biết, mỗi luật sinh ra từ một lỗi đã gặp khi thử thật:

- **Không đoán chai.** `ve_chai` mà không giải được khách trỏ vào chai nào thì hạ
  xuống `loc_them`, chứ không chọn bừa. Đoán ở đây cho ra một chai CÓ THẬT nên
  không ai thấy sai — chỉ thấy câu trả lời nói về chai khác.
- **Bỏ chai đã hiện.** "nhẹ hơn chút" thường không mang theo điều kiện nào tách
  được, nên truy vấn gần như không đổi và kết quả ra y hệt lượt trước. Lặp lại
  nguyên văn là dấu hiệu "bot hỏng" rõ nhất. Hết chai mới thì **nói thật**.
- **Hỏi về một chai là nhánh rẽ, không phải đổi chủ đề.** Sau lượt `ve_chai`, lượt
  sau quay lại mạch tìm kiếm trước đó — nếu không, "còn gì nữa không" mất hết
  ngữ cảnh và trả về rỗng.
- **Trường khai tường minh thắng chữ trong câu.** Khách nói "mùa hè thì sao" thì
  hoàn cảnh bị GHI ĐÈ, dù câu cộng dồn qua các lượt vẫn còn chữ "mùa đông".

Phiên nằm trong bộ nhớ tiến trình, có trần (200 phiên, 40 lượt, hết hạn 6 giờ).
Nghĩa là **restart API là mất hội thoại đang dở** — hội thoại không phải dữ liệu
cần giữ, nên nó không vào Postgres.

#### Một lượt chat đi qua những đâu

```
"mùi gỗ trầm ấm cho buổi tối mùa đông"
   │  llm/intent.py    9router đề xuất note/accord/dịp, TỪ VỰNG quyết định
   │                   (nhãn model bịa ra bị bỏ) + viết lại câu CÓ DẤU
   ▼
Query(occasions=[winter, night], text="...")
   │  retrieval/pgvector_store.py   vector đặc 384 chiều, dịp làm bộ lọc
   │                                lý do lấy từ bảng thưa bằng một câu join
   ▼
5 chai + "vì: oud, amber, woody"
   │  llm/advise.py    model CHỈ được dùng danh sách này; verify() soát lại
   ▼
câu tư vấn  +  danh sách gốc hiện cạnh nhau
```

#### Ba lớp chặn model bịa

| | Lớp | Chặn được gì |
|---|---|---|
| 1 | prompt nói rõ chỉ dùng danh sách | yếu nhất, nhưng rẻ |
| 2 | `advise.verify()` soát tên trong câu trả lời | **tên chai không có trong kết quả** — lớp tự động duy nhất |
| 3 | giao diện luôn hiện danh sách gốc | mọi thứ còn lại, bằng mắt người đọc |

Lớp 2 không chặn được mọi kiểu bịa (model vẫn có thể nói sai về một chai CÓ trong
danh sách), nhưng nó chặn kiểu tệ nhất: giới thiệu một chai không tồn tại. Tên bịa
trông y hệt tên thật nên không ai soát được bằng mắt.

#### Thiếu khoá thì vẫn dùng được

Không có `LLM_API_KEY` thì trang vẫn chạy: ý định tách bằng từ khoá, câu trả lời
ghép bằng Python. Khô hơn nhưng **đúng tuyệt đối**, và nó nói rõ đang thiếu gì.
Tương tự, mất Postgres thì rơi về retriever in-memory.

`/v1/models` của 9router không đòi khoá nhưng `/v1/chat/completions` thì có — nên
endpoint trả 200 **không** có nghĩa là đã đủ cấu hình.

#### Đổi model embedding = sinh lại toàn bộ

Vector của hai model vẫn cộng trừ được với nhau, chỉ là kết quả vô nghĩa — và
không có cách nào phát hiện từ con số. Nên `model_id` đi kèm mọi vector và bị kiểm
ở cả hai chỗ (lúc đọc Parquet và lúc hỏi Postgres). Đổi `EMBED_MODEL` thì chạy lại
`cli embed` rồi `cli vectordb load`; bảng tự dựng lại khi số chiều đổi.

#### API

| | |
|---|---|
| `GET /` | trang test chatbot |
| `GET /health` | retriever nào đang dùng, bao nhiêu chai, có khoá LLM chưa |
| `POST /chat` | `{message, session_id?}` → câu tư vấn + danh sách + ý định + `session_id` |
| `POST /chat/reset` | `{session_id}` → bỏ một phiên |
| `POST /search` | tra cứu thuần, không qua LLM |
| `GET /vocabulary/{block}` | nhãn dùng được: `note`, `accord`, `occasion`... |

### Sổ theo dõi

`data/state/crawl_state.db` (SQLite), ba bảng:

| Bảng | Nội dung |
|---|---|
| `brands` | mỗi hãng: đã có mục lục chưa, còn nợ bao nhiêu chai, đang nghỉ tới khi nào |
| `perfumes` | mỗi chai: `pending` / `done` / `failed`, số bình luận (dùng để xếp ưu tiên) |
| `runs` | mỗi lần chạy: tiêu bao nhiêu request, được bao nhiêu chai, kết thúc vì gì |

Vài điểm thiết kế đáng biết:

- **Đơn vị công việc là CHAI, không phải hãng.** Nếu lấy hãng làm đơn vị thì
  Avon (1.379 chai) sẽ là một ngày 1.379 request, còn hãng nhỏ là một ngày 3
  request — đúng cái cần tránh.
- **Ngân sách tính TỔNG request**, kể cả request lấy mục lục. Site đếm mọi
  request chứ không riêng request chi tiết.
- **Ưu tiên theo tín hiệu cộng đồng.** Hãng xếp theo thứ hạng "Most Popular
  Brands"; trong một hãng, chai nhiều bình luận đi trước. Với nhịp nhỏ giọt thì
  THỨ TỰ quan trọng hơn tổng thời gian — phần đầu hàng đợi là phần bạn thật sự dùng.
- **Bỏ hẳn phần đuôi: `--min-comments`, mặc định 5.** Đo trên 7.938 chai trong
  sổ, ngưỡng 5 giữ lại 31% số chai nhưng mang theo **96,3% toàn bộ lượng bình
  luận**; 37,6% số chai có đúng 0 bình luận. Đây là khác biệt 53 ngày so với 16
  ngày, đổi lấy 3,7% tín hiệu.

  | ngưỡng | số chai | % bình luận giữ được | ngày @150/ngày |
  |---|---|---|---|
  | 0 | 7.938 | 100% | 53 |
  | **5** | **2.442** | **96,3%** | **16** |
  | 20 | 982 | 85,9% | 7 |

  Ngưỡng KHÔNG đổi thứ tự ưu tiên — nó cho phép *dừng sớm*. Đặt
  `--min-comments 0` để crawl tất cả.
- **Bị chặn thì cho MỌI hãng nghỉ**, không nhảy sang hãng khác. 429 và thử thách
  Cloudflare là tín hiệu ở mức thiết bị; đổi hãng rồi cào tiếp là hiểu sai vấn đề
  và bị chặn sâu hơn. Hãng lỗi lẻ thì nghỉ dần lâu hơn: 6h → 24h → 72h.
- **Bị chặn ở BẤT KỲ lệnh nào cũng được ghi vào sổ chung** (bảng `site_cooldown`),
  và `daily` kiểm sổ trước khi chạy — thấy đang nghỉ thì bỏ lượt, exit code 2.

  Vì sao cần: trước đây chỉ `daily` đụng tới sổ, nên một mẻ `products` dính 429
  không để lại dấu vết nào. Đã xảy ra thật — `23:12:14` site trả 429 lần cuối,
  `23:12:19` lịch `daily` bắt đầu gõ cửa tiếp. Giờ mọi lệnh (`crawl`, `brands`,
  `products`, `links`, `daily`) đều ghi, và lần chặn **nặng hơn luôn thắng** để
  một lệnh ghi 1 giờ không rút ngắn được lần 12 giờ của lệnh khác.

  ```
  $ perfume-intel queue
  !! ĐANG NGHỈ tới 2026-10-02T21:22:17+00:00
     do `products` ghi lúc 2026-10-02T18:22:17+00:00: HTTP 429
     Lịch `daily` sẽ bỏ lượt cho tới mốc đó. Muốn chạy ngay: queue --reset-failed
  ```
- **Chai đã có trên đĩa được ghi nhận, không tải lại.** Dữ liệu crawl từ trước
  tự động vào sổ mà không tốn request nào.

## Ghép bản mini

Bản mini dùng chung mọi thông tin với chai full, chỉ khác link sản phẩm. Lệnh
`mini` lấy bản ghi đã crawl rồi thay `des_url` bằng link bản mini — **không vào
mạng**:

```powershell
python -m perfume_intel mini data/inputs/temp_task/Mini.csv
```

File CSV cần một cột link Fragrantica (nguồn) và một cột link bản mini (đích).
Một chai full ứng với nhiều bản mini thì mỗi dòng CSV ra một bản ghi.

Mặc định quét `data/raw/fragrantica/`; đổi bằng `--scan`. URL nguồn chưa có
trong dữ liệu đã crawl sẽ được liệt kê ra để bạn crawl bổ sung.

## Bố cục kho thô (bronze)

Mỗi **loại** bản ghi một thư mục:

```
data/raw/fragrantica/
  perfumes/   chi tiết chai          <- lệnh crawl, daily
  brands/     danh mục hãng          <- lệnh brands
  products/   mục lục chai của hãng  <- lệnh products
```

Vì sao phải tách, chứ không phải cho gọn: ba loại này có **khoá chính khác
nhau** (`url`, `brand_url`, `perfume_url`). Khi để chung một chỗ, loader của
phần phân tích khoá theo `url` nên hai loại kia rơi hết — đo trên kho thật:
**24.676/25.465 dòng, tức 97%, bị bỏ mà không một dòng log nào**. Không mất file,
nhưng mất tín hiệu: số chai mà một hãng *thật sự* có nằm đúng trong mấy dòng đó.

Hai lớp bảo vệ, cố ý làm cả hai:

1. **Tách thư mục** — đọc một loại thì không nhìn thấy loại khác.
2. **`core/bronze.py` phân loại theo hình dạng bản ghi và ĐẾM phần bỏ qua** —
   vì tách thư mục chỉ đúng khi mọi thứ đã nằm đúng chỗ. Lớp này vẫn chạy đúng
   trên kho cũ chưa dọn, và quan trọng hơn: nó *báo ra* khi bỏ thứ gì.

### Dọn kho cũ (tuỳ chọn)

```powershell
python scripts/migrate_bronze.py            # xem sẽ chuyển gì, KHÔNG đụng file
python scripts/migrate_bronze.py --apply    # làm thật
python scripts/migrate_bronze.py --undo     # trả về chỗ cũ
```

Chỉ **di chuyển** file, không sửa nội dung, và chỉ chuyển file mà mọi dòng cùng
một loại — file lẫn nhiều loại bị bỏ qua kèm cảnh báo, vì chẻ nó ra là sửa dữ
liệu chứ không còn là dọn chỗ. Không chạy cũng không sao: bố cục phẳng kiểu cũ
vẫn đọc được.

### Độ phủ — thứ mà việc tách entity mở ra

`analyze` giờ ghi thêm `coverage.csv`: mỗi hãng có bao nhiêu chai trong mục lục,
đã crawl chi tiết được bao nhiêu.

```
brand,                 catalog_perfumes, detailed, coverage_pct
Lattafa Perfumes,      391,              12,       3.1
Al Haramain Perfumes,  378,              3,        0.8
Ajmal,                 369,              0,        0.0
```

Đây là con số giữ cho mọi kết luận khác khỏi bị đọc quá tay: “Lattafa 4,1 sao”
thực ra là kết luận về **12/391 chai** của Lattafa. `catalog_perfumes` để trống
(không phải 0) khi chưa crawl mục lục hãng đó — *chưa biết tổng* khác hẳn *biết
tổng và mới phủ 0%*.

## Phân tích thị trường

```powershell
python -m perfume_intel analyze
python -m perfume_intel analyze --metric brand --metric accord    # chỉ vài bảng
python -m perfume_intel analyze --out data/processed/thu-nghiem
```

Đọc `data/raw/fragrantica/`, ghi ra
`data/processed/<YYYYMMDD>/`:

| File | Nội dung |
|---|---|
| `brand.csv` | Mỗi hãng: số chai, tổng vote, thị phần chú ý, điểm có hiệu chỉnh |
| `accord.csv` | Mỗi accord: độ phủ, độ mạnh trung bình, điểm có hiệu chỉnh |
| `gender.csv` | Cơ cấu Nam / Nữ / Unisex |
| `season.csv` | Mùa nào đang nhiều/ít hàng (theo vote when-to-wear) |
| `coverage.csv` | Mỗi hãng: có bao nhiêu chai, đã crawl chi tiết bao nhiêu |
| `summary.json` | Số tổng quan của cả lần chạy |
| **`report.html`** | **Sáu biểu đồ để người đọc — mở bằng double-click** |

**Điểm có hiệu chỉnh (`rating_weighted`)**: điểm thô rất dễ đánh lừa — một chai
4.9 sao với 30 vote không nói lên gì về thị trường, còn 4.1 sao với 9.000 vote
thì có. Chỉ số này kéo nhóm ít vote về mốc chung của cả tập (mốc cũng tính có
trọng số theo vote), nhóm càng nhiều vote càng giữ được điểm riêng. Xếp hạng
hãng nên nhìn cột này, không nhìn `rating_avg`.

**Đang thiếu**: số "have it / had it / want it" của Fragrantica là tín hiệu nhu
cầu mạnh nhất nhưng khối đó render sau khi đăng nhập nên HTML đã lưu không có.
Chỗ chờ sẵn đã có (`Row.have_it/had_it/want_it`, `summary.json` báo
`with_ownership`); muốn dùng thì bổ sung parser trong
`perfume_intel/sources/fragrantica/parsers.py` rồi crawl lại — phần metrics
không phải sửa gì.

### Báo cáo HTML (`report.html`)

Mở bằng double-click, **không cần mạng và không cần server**. Tắt bằng
`analyze --no-html`.

| Hình | Dạng | Trả lời câu |
|---|---|---|
| Accord nào cho hoàn cảnh nào | heatmap lưỡng hướng | accord nào nghiêng về mùa/giờ nào hơn mức chung |
| Định vị hãng | scatter, bong bóng = số chai | hãng nào vừa đông người nói vừa được chấm cao |
| Accord nào lưu hương lâu | cột xếp chồng lưỡng hướng | accord nào bám lâu, accord nào bay nhanh |
| Note nào đi với note nào | heatmap có điều kiện | có note A thì bao nhiêu % cũng có note B |
| Số chai theo năm ra mắt | cột xếp chồng theo giới tính | độ phủ dữ liệu theo thời gian |

**Tự vẽ SVG, không dùng thư viện biểu đồ nào.** Ba lý do, theo thứ tự quan
trọng: báo cáo phải mở được khi offline (link CDN thì nửa năm sau là trang
trắng); không thêm phụ thuộc vào một project hiện chỉ cần requests + bs4 + lxml;
và tất định nên test được từng hình.

Mấy quyết định đáng biết, vì đều là chỗ từng làm hình nói sai và đã phải sửa sau
khi nhìn bản dựng thật:

- **Heatmap accord × hoàn cảnh so theo TỪNG CỘT, không theo giá trị thô.**
  “Mùa” và “ngày/đêm” là hai khối vote riêng trên Fragrantica, mỗi khối tự quy
  về 100% của chính nó. Tô theo số thô thì cột “Ngày” đậm đều từ trên xuống —
  trông như một phát hiện, thực ra chỉ là mẫu số khác.
- **Thang lưỡng hướng có mức sàn.** Thước theo độ lệch chuẩn luôn tiêu hết dải
  màu, nên một cột mà mọi giá trị chỉ chênh 1–2 điểm vẫn bị tô từ đỏ đậm sang
  xanh đậm. Sàn 3 điểm chặn việc khuếch đại nhiễu (sigma thật mỗi cột là
  5,9–10,4 điểm nên không làm phẳng tín hiệu thật).
- **Trục log dùng mốc 1–2–5 × 10ⁿ.** Dải vote thật là 24k–250k, chưa tới hai
  bậc, nên nếu chỉ lấy luỹ thừa 10 thì trục còn đúng một mốc.
- **Nền tối đảo thang một sắc.** Bậc ứng với “gần 0” phải lùi về phía mặt nền —
  nền sáng thì bậc nhạt lùi, nền tối thì bậc đậm mới lùi.
- **Hình nào cũng có bảng số gập lại được.** Thang màu không bao giờ là cách
  duy nhất đọc một giá trị.
- **Thiếu dữ liệu thì để TRỐNG kèm lý do, không vẽ bừa.** Hình nào không đủ
  dữ liệu sẽ hiện ô giải thích vì sao, thay vì vẽ một biểu đồ nói sai.

### Thêm chỉ số mới

Viết một hàm thuần `list[Row] -> list[dict]` trong
`perfume_intel/analytics/metrics.py` rồi đăng ký vào `METRICS` ở cuối file. CLI
tự nhận, không phải sửa chỗ nào khác:

```python
def by_perfumer(rows: list[Row]) -> list[dict]:
    ...

METRICS = {..., "perfumer": by_perfumer}
```

Cần thêm dữ liệu đầu vào thì thêm trường vào `Row` và `_to_row()` trong
`analytics/dataset.py`.

## Tìm chai/hãng giống nhau (`similar`)

Vector hoá dữ liệu cộng đồng để trả lời ba câu: chai nào giống chai này, hãng nào
giống hãng này, và chai nào khớp nhất với một bộ note / hoàn cảnh.

```powershell
# 1. Chai nào giống chai này (kèm lý do)
python -m perfume_intel similar "Angham" --explain

# 2. Chai giống nhưng của hãng khác (bỏ các bản flanker cùng dòng)
python -m perfume_intel similar "Angham" --other-brands --min-votes 500

# 3. Hãng nào giống hãng này
python -m perfume_intel similar --brand "Lattafa Perfumes" --explain

# 4. Theo note hương — không cần gõ đúng tên trên Fragrantica
python -m perfume_intel similar --notes "oud,vanilla" --min-votes 1000

# 5. Theo hoàn cảnh sử dụng
python -m perfume_intel similar --occasion "winter,night" --min-votes 500

# JSON để nối vào chỗ khác
python -m perfume_intel similar "Angham" --json
```

Ví dụ thật trên dữ liệu đã crawl:

```
$ python -m perfume_intel similar --occasion "winter,night" --min-votes 500
   0.317  Hypnotic Poison - Dior           4.08* 30.340 vote  Nu
   0.316  Dior Homme Intense 2007 - Dior   4.48*  3.525 vote  Nam
   0.315  Ombre Nomade - Louis Vuitton     4.25*  7.117 vote  Unisex
   0.312  La Nuit Tresor - Lancome         4.08* 12.771 vote  Nu
```

### Vector được dựng thế nào

Năm khối, mỗi chiều là một thứ có tên nên in ra là đọc được:

| Khối | Chiều | Nguồn |
|---|---|---|
| `acc` mùi | 64 | accord + `width` (độ mạnh trên trang) |
| `note` | ~550 | note theo tầng, nhân IDF |
| `occ` hoàn cảnh | 6 | % vote 4 mùa + ngày/đêm |
| `str` cường độ | 2 | độ lưu hương, độ toả hương |
| `fam` họ hương | 31 | `fragrance_family` |

Không dùng model nào: tất định, test được, chạy offline, và **giải thích được** —
`--explain` chỉ ra đúng những chiều tạo nên điểm giống nhau.

Vài quyết định đáng biết, vì chúng đều là chỗ dễ làm sai mà không có lỗi nào báo:

- **Chuẩn hoá L2 từng khối TRƯỚC, rồi mới nhân trọng số khối.** Ghép thẳng rồi
  chuẩn hoá một lần thì 550 chiều note nhấn chìm 6 chiều hoàn cảnh: hai chai
  trái ngược hẳn về mùa vẫn ra 0,98 giống nhau. Đo trên fixture test: cách đúng
  cho 0,857, cách sai cho 0,982.
- **Note base nặng hơn middle, middle nặng hơn top** (1,0 / 0,7 / 0,5). Base là
  thứ còn lại sau vài giờ, top bay trong mươi phút.
- **IDF cho cả note và accord.** Trùng "Musk" (chai nào cũng có) nói ít hơn nhiều
  so với trùng "Oud".
- **Note chỉ xuất hiện 1 lần thì bỏ** (`MIN_DF = 2`). Trên kho hiện tại có
  194/551 note như vậy; giữ lại thì IDF đẩy chúng lên cao nhất và hai chai tình
  cờ trùng một note độc nhất trông như rất giống nhau.
- **Khớp một phần khi gõ tên note.** Gõ `oud` sẽ tự về `agarwood (oud)` — tên
  trên Fragrantica không phải tên người ta hay gọi. Term không khớp được thì
  **báo ra**, không im lặng bỏ.
- **Chân dung hãng CHỈ dùng khối mùi.** Trọng tâm của 120 chai làm loãng hết chi
  tiết note, còn khối hoàn cảnh thì sống sót qua phép trung bình và hội tụ về một
  giá trị chung — giữ nó thì mọi hãng đều "giống nhau" ở fall/winter/night và
  điểm số nén vào dải 0,76-0,82. Đã đo thật rồi sửa.
- **Giới tính và rating KHÔNG phải chiều của vector.** Giới tính là bộ lọc
  (`--gender`); còn hai chai không giống nhau chỉ vì cùng được 4,2 sao.

### Ba điều cần biết trước khi tin kết quả

1. **"Hoàn cảnh" chỉ có mùa và ngày/đêm.** Fragrantica không vote "công sở",
   "hẹn hò", "gym". Mấy nhãn đó là **suy diễn** từ độ toả/độ lưu
   (`features.derive_occasion`) và được in kèm chữ "suy diễn" — đừng coi là dữ
   liệu cộng đồng.
2. **Chỉ so được trong phần đã crawl:** 629/~100k chai. Kết quả đúng trong phạm
   vi đó, không phải trên toàn bộ Fragrantica.
3. **Không có `--min-votes`, đầu bảng hay là chai vô danh.** Cosine thưởng cho
   chai mà note bạn hỏi chiếm tỉ trọng lớn, và đó thường là chai ít ai biết
   (1-5 vote). Mặc định KHÔNG lọc — để không âm thầm giấu dữ liệu — nên muốn kết
   quả phổ thông thì tự thêm `--min-votes 500`.

## Demo nhanh

```powershell
python scripts/demo.py               # 3 chai mẫu, in tháp hương ra màn hình
python scripts/demo.py <url> ...     # chai bạn chọn
python scripts/demo.py --no-render   # chạy nhanh, bỏ dữ liệu vote
```

## Tuỳ chọn chung

| Cờ | Ý nghĩa |
|---|---|
| `--out` | Nơi ghi kết quả (không cần đuôi file) |
| `--format csv\|jsonl\|both` | Định dạng xuất (mặc định `jsonl`) |
| `--limit N` | Giới hạn số bản ghi |
| `--delay MIN MAX` | Khoảng nghỉ ngẫu nhiên giữa 2 request, giây |
| `--resume` | Bỏ qua URL đã có trong file `.jsonl` kết quả |
| `--recrawl` | Khi crawl cả thư mục: crawl lại cả hãng đã có file kết quả |
| `--no-cache` | Không dùng cache HTML |
| `--render` | Tải bằng browser thật để lấy thêm vote mùa, độ lưu/toả hương |
| `--ignore-robots` | Bỏ kiểm tra robots.txt (không khuyến khích) |
| `--url-column`, `--dest-column` | Chỉ định thẳng cột trong CSV |
| `-v` | Log chi tiết |

## Dữ liệu thu được

Mỗi nước hoa gồm 17 trường:

| Trường | Ví dụ |
|---|---|
| `perfume_id`, `url` | `31861` |
| `name`, `brand` | `Sauvage`, `Dior` |
| `gender` | `Nam` / `Nữ` / `Unisex` |
| `year` | `2015` |
| `fragrance_family` | `Aromatic Fougere`, `Oriental Spicy`, `Floral Fruity Gourmand`... |
| `rating`, `rating_count` | `3.85`, `33642` |
| `accords` | xem mục "Chỉ số accord" bên dưới |
| `top_notes`, `middle_notes`, `base_notes` | `Calabrian bergamot`, `Pepper`, ... |
| `general_notes` | dùng khi nước hoa không chia tháp hương |
| `perfumers` | `François Demachy` |
| `description`, `image`, `scraped_at` | |

### When to wear (mùa & ngày/đêm) — cần `--render`

```powershell
python -m perfume_intel crawl <url> --render
python scripts/demo.py <url>
```

```
  WHEN TO WEAR
    winter   ████████████████████  100.0%  (6.3k vote)
    spring   ████················   22.4%  (1.4k vote)
    summer   ██··················   7.97%  (499 vote)
    fall     ███████████████████·  94.88%  (5.9k vote)
    day      █████████████·······  66.81%  (4.2k vote)
    night    █████████████████···  83.16%  (5.2k vote)
```

```json
"when_to_wear": [
  {"name": "winter", "percent": 100.0, "votes": 6300, "votes_label": "6.3k"},
  {"name": "summer", "percent": 7.97, "votes": 499,  "votes_label": "499"}
]
```

**Vì sao phải `--render`?** Dữ liệu này *không có* trong HTML mà `requests` tải về.
Trang nhúng nó dưới dạng biến JS đã mã hoá AES:

```js
let status = {"ct":"XHcv2HMRiKpRLGwOvk7aTg23BBY/1TSQwA1Sqz3DH0MB38Kr...","iv":"...","s":"..."}
```

Component Vue `<seasons-rating-new :perfume_id="804">` giải mã rồi mới vẽ ra DOM.
Nên cách duy nhất là để browser thật chạy JS — đó là việc `--render` làm.

Lưu ý: `votes` quy đổi từ nhãn rút gọn (`6.3k` → `6300`) nên chỉ **gần đúng**;
`votes_label` giữ nguyên chuỗi gốc để đối chiếu.

### Độ lưu hương & độ toả hương — cần `--render`

Lấy **title của mức được vote nhiều nhất** ở mỗi thang:

```
  ĐÁNH GIÁ
    Độ lưu hương : moderate
    Độ toả hương : moderate
```

```json
{"longevity": "moderate", "sillage": "moderate"}
```

Thang điểm: longevity = `very weak / weak / moderate / long lasting / eternal`,
sillage = `intimate / moderate / strong / enormous`. Nhãn `moderate` có ở **cả
hai** thang nên parser luôn tách theo từng card, không quét chung cả trang.

Khối này nằm trong `<lazy-section-new section-id="performance">` — chỉ tải khi
người dùng cuộn tới, nên `BrowserFetcher` tự cuộn hết trang trước khi lấy HTML.

### Chỉ số accord

Khối "main accords" được bóc thành list object, mỗi accord có `width` (độ mạnh)
và `opacity` (độ đậm màu), đều quy về thang **0–100**:

```json
"accords": [
  {"name": "tobacco",  "width": 100.0, "opacity": 100.0, "color": "#ad7727"},
  {"name": "cinnamon", "width": 87.22, "opacity": 89.04, "color": "#d2691e"},
  {"name": "sweet",    "width": 84.36, "opacity": 86.6,  "color": "#ee363b"}
]
```

Trang trả về 2 định dạng khác nhau — HTML gốc dùng `opacity: 89.041%` + hex,
còn DOM sau khi browser render dùng `opacity: 0.890415` + `rgb(210, 105, 30)`.
Parser nhận cả hai và luôn chuẩn hoá về `%` + hex thường.

## Định dạng xuất

Mặc định chỉ xuất `*.jsonl` — mỗi dòng 1 JSON, **ghi ngay sau mỗi chai** nên mất
mạng giữa chừng không mất dữ liệu đã crawl.

Cần CSV thì thêm `--format csv` (hoặc `--format both`). Bản CSV là bản phẳng
(list nối bằng `; `, accord ghi thành `tên:width|opacity`), encoding `utf-8-sig`
để Excel đọc đúng tiếng Việt.

## Cấu trúc

> Ranh giới giữa các lớp và lý do chọn công nghệ: xem
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```
perfume_intel/
  config.py            URL gốc, bố cục thư mục data, delay, retry, headers
  cli/
    app.py             gom lệnh con lại, điểm vào `python -m perfume_intel`
    options.py         cờ dùng chung + dựng Fetcher/BrowserFetcher
    crawl_cmd.py       lệnh crawl (dùng chung cho mọi site)
    links_cmd.py       lệnh links
    mini_cmd.py        lệnh mini
    brands_cmd.py      lệnh brands
    products_cmd.py    lệnh products
    queue_cmd.py       lệnh queue (nạp hàng đợi, xem tiến độ)
    daily_cmd.py       lệnh daily (chạy theo lịch)
    analyze_cmd.py     lệnh analyze
    similar_cmd.py     lệnh similar (chai/hãng giống nhau)
    silver_cmd.py      lệnh silver (bronze -> Parquet)
    lake_cmd.py        lệnh lake (status / init / push / pull)
  core/                hạ tầng, KHÔNG biết gì về site cụ thể
    http.py            session + throttle + retry/backoff + cache đĩa + robots
    browser.py         bản Fetcher chạy bằng Playwright (cho --render)
    csv_input.py       đọc link từ CSV/text của người dùng (đoán dấu phân cách...)
    storage.py         đọc & ghi JSONL / CSV
    text.py            chuẩn hoá giới tính, tên file kết quả, khoá so khớp URL
    bronze.py          CỔNG ĐỌC kho thô: phân loại bản ghi, quét theo từng loại
    objects.py         CỔNG kho đối tượng: LocalStore + S3Store (boto3)
    lake.py            spool ghi trước · niêm sau mỗi hãng · kéo về khi đọc
  sources/             mỗi site một package
    base.py            SiteScraper: vòng lặp crawl dùng chung
    fragrantica/       models.py · parsers.py · scraper.py
  pipelines/
    crawl.py           crawl 1 danh sách URL / cả thư mục CSV, resume, đặt tên file
    mini.py            ghép des_url bản mini với dữ liệu đã crawl
    brands.py          crawl danh mục hãng A-Z, gộp + khử trùng theo brand_url
    brand_products.py  từ trang hãng lấy danh sách chai theo collection
    state.py           sổ theo dõi tiến độ (SQLite): hãng · chai · lần chạy
    daily.py           một lát ngân sách: chọn việc, crawl, đánh dấu
  analytics/           pipeline phân tích, chạy offline
    dataset.py         .jsonl đã crawl -> list[Row] phẳng
    metrics.py         Row -> các bảng chỉ số (hàm thuần, test được)
    report.py          chạy chỉ số rồi ghi ra data/processed/
    svg.py             viên gạch SVG (thang đo, màu, thoát XML) — không thư viện
    charts.py          Row -> 5 Figure (hình + bảng số + chú giải + cảnh báo)
    html_report.py     ghép Figure thành report.html tự chứa
  warehouse/           kho phân tích (cần extra [warehouse])
    silver.py          bronze -> Parquet khử trùng, có kiểu, trải phẳng
  vectors/             vector hoá để đo độ giống nhau, chạy offline
    features.py        Row -> vector thưa (5 khối, có IDF, giải thích được)
    index.py           tra cứu chai/hãng giống nhau + chân dung hãng
  retrieval/           CỔNG truy xuất — phía trên chỉ được nhìn thấy lớp này
    ports.py           Query/Match/Reason/Retriever (không nói gì về cài đặt)
    memory.py          adapter #1: in-memory, bọc vectors/
transform/             dự án dbt: staging + 5 mart + test (extra [marts])
docs/ARCHITECTURE.md   ranh giới giữa các lớp + lý do từng quyết định
docker-compose.yml     6 service: minio · minio-init · scheduler · cli · marts · test
Dockerfile             python:3.13-slim + Google Chrome thật
scripts/docker/scheduler.sh   vòng lặp lên lịch trong container
scripts/demo.py        xem nhanh tháp hương của vài chai
scripts/migrate_bronze.py  dọn kho thô về bố cục mỗi loại một thư mục
tests/                 test offline trên HTML thật đã lưu
  objectstore_contract.py  hợp đồng ObjectStore — 19 điều khoản
  retrieval_contract.py    hợp đồng Retriever — 30 điều khoản
```

Năm quy tắc giữ cho cấu trúc này không rối lại:

1. **`core/` không được import từ `sources/`.** Hạ tầng không biết đang crawl
   site nào; mọi thứ riêng của site (selector, tên miền, JS chờ render) là tham
   số truyền vào.
2. **`analytics/` không đụng tới mạng.** Nó chỉ đọc bronze đã có, nên chạy lại
   bao nhiêu lần cũng được.
3. **`cli/` không được import `vectors/`.** Truy xuất phải đi qua
   `retrieval/ports.py`. Có một test quét `cli/*.py` canh đúng điều này.
4. **Kiểm "có dữ liệu chưa" bằng `bronze.available()`, không bằng
   `Path.exists()`.** `exists()` chỉ thấy ổ đĩa này; trên máy mới nó báo "chưa có
   dữ liệu" trong khi MinIO có đủ. Cũng có test canh.
5. **Thêm site mới = thêm một package trong `sources/`**: `models.py` (dataclass
   có `to_dict`/`to_flat_dict`/`from_dict`), `parsers.py`, và một lớp con của
   `SiteScraper`. Đăng ký vào `SCRAPERS` trong `cli/crawl_cmd.py` là xong —
   không phải sửa pipeline.

Quy tắc 3 và 4 đều có test canh, không chỉ có chữ. Lý do: cả hai là loại lỗi
**không làm đỏ bất cứ test nào khác** — nó chỉ làm phía trên dần dần phụ thuộc
vào ruột, hoặc làm một máy mới báo sai là chưa có dữ liệu.

## Test

```powershell
make test                      # 18 bộ · 335 test
python tests\test_parsers.py   # hoặc từng bộ một
python -m pytest tests/ -v     # nếu có cài pytest
```

| File | Kiểm cái gì |
|---|---|
| `test_parsers.py` | Bóc tách HTML Fragrantica, đọc CSV đầu vào, retry/rate-limit |
| `test_brands.py` | Danh mục hãng: footer, mục lục A-Z, cắt section, retry, khử trùng |
| `test_brand_products.py` | Sản phẩm của hãng: collection, `<template>`, retry, resume |
| `test_resume_cache.py` | `--resume` khớp khoá hai phía, TTL cache, ổ đầy không giết crawl |
| `test_schedule.py` | Sổ theo dõi, ngân sách, chia hãng lớn nhiều ngày, ngắt mạch |
| `test_bronze.py` | Phân loại bản ghi theo hình dạng, bố cục kho thô, di trú |
| `test_objects.py` | **Hợp đồng `ObjectStore`** — chạy cho cả `LocalStore` và MinIO thật |
| `test_lake.py` | Niêm/kéo về, luật "file dài hơn thắng", mất MinIO giữa mẻ crawl |
| `test_embedding.py` | **Hợp đồng `Embedder`** + cách viết tài liệu + đo chất lượng trên dữ liệu thật |
| `test_pgvector.py` | **Hợp đồng `Retriever` trên pgvector** + canh "tính toán nằm trong SQL, không trong RAM" |
| `test_llm.py` | Tách ý định, giọng tư vấn, và ba lớp chặn model bịa — không gọi model thật |
| `test_chat.py` | Hội thoại nhiều lượt: phiên, phân loại lượt, giải "chai thứ 2" |
| `test_analytics.py` | Nạp dữ liệu, các chỉ số, xuất báo cáo |
| `test_charts.py` | Thang đo, màu, chú giải, cảnh báo khi dữ liệu mỏng |
| `test_vectors.py` | Vector thưa: IDF, chuẩn hoá từng khối, chân dung hãng |
| `test_retrieval.py` | **Hợp đồng `Retriever`** (in-memory) + canh `cli/` không import `vectors/` |
| `test_warehouse.py` | Silver khử trùng đúng; mart dbt khớp với `metrics.py` |
| `test_mini.py` | Ghép bản mini |

Test chạy trên file HTML thật đã lưu ở `tests/fixtures/`, **không cần mạng**.
Khi Fragrantica đổi giao diện, test sẽ đỏ và chỉ ra selector nào trong
`parsers.py` cần sửa — hãy tải lại fixture mới rồi chỉnh selector.

**Ba bộ hợp đồng** (`test_objects.py`, `test_retrieval.py`, `test_embedding.py`)
không kiểm một bản cài đặt cụ thể mà kiểm **mọi** bản cài đặt. Thêm backend mới (DuckDB, pgvector,
R2) thì viết adapter rồi thêm một dòng; qua hết hợp đồng là thay thế được và
không ai phía trên phải sửa.

Phần hợp đồng `ObjectStore` chạy với MinIO thật khi có; không có thì **bỏ qua kèm
lý do**, không báo lỗi — `make test` phải xanh được khi không có hạ tầng, nếu
không người ta sẽ học cách phớt lờ màu đỏ.

```powershell
docker compose up -d minio
$env:S3_ENDPOINT="http://localhost:9000"; $env:S3_ACCESS_KEY="perfume"
$env:S3_SECRET_KEY="perfume-dev-only"; python tests\test_objects.py
```

**Chuẩn kiểm chứng của project là mutation test**, không phải độ phủ: sửa code cho
sai rồi xem test có đỏ không. Test không đỏ nghĩa là nó không canh gì — và một
test như vậy còn tệ hơn không có test, vì nó trông y hệt như đang bảo vệ cái gì đó.

## Ghi chú kỹ thuật

- **Cache**: HTML tải về được lưu ở `.cache/html/`. Chạy lại cùng URL sẽ đọc từ đĩa,
  không gọi mạng — rất tiện khi chỉnh parser. Xoá thư mục `.cache/html/` để tải mới.
- **Accept-Encoding**: chỉ khai báo `br`/`zstd` khi máy có package tương ứng.
  Nếu khai mà thiếu thư viện, server trả về body nén và parser sẽ ra rỗng.
- **Selector dễ vỡ nhất** là `pyramid-level-new[notes=...]` (tháp hương) và
  `<h6>main accords</h6>` (accords). Rating dùng microdata `itemprop` nên ổn định hơn.
- Accord có 2 lớp dự phòng: ưu tiên tìm theo tiêu đề `main accords`, nếu hỏng thì
  rơi về selector khối bar `div[class*="max-w-[280px]"]`. Không bám vào chuỗi class
  Tailwind dài (`h-5 md:h-7 rounded-br-lg ...`) vì rất dễ đổi sau mỗi lần trang
  cập nhật giao diện — chỉ cần `div` có `style` chứa `width`.
- Link sang bản dịch (`fragrantica.es`, `.ru`, ...) đã được lọc bỏ khi tìm link.
- **Tháp hương có 2 cấu trúc khác nhau**: HTML gốc dùng thẻ `<pyramid-level-new
  notes="top">`, còn sau khi browser render thẻ đó biến mất, chỉ còn
  `<h4>Top Notes</h4>` + `div.pyramid-level-container`. Parser xử lý cả hai nên
  `--render` cho kết quả giống hệt bản thường (có test đối chiếu).
- Chế độ `--render` cache riêng ở `.cache/html/rendered/`, và tắt cờ automation của
  Chromium — nếu không Cloudflare trả 403 cho một số trang.
- **Chromium đóng gói của Playwright bị Cloudflare chặn nặng.** Đo trên 10 trang
  Fragrantica: Chromium qua được 1/10 (9 lần 403), Edge thật qua 10/10. Đổi
  User-Agent, tắt cờ `navigator.webdriver`, thêm Referer, nghỉ lâu hơn, bỏ cuộn
  trang — đều không cứu được. Vì vậy `BrowserFetcher` thử `chrome` rồi `msedge`
  của máy trước, chỉ rơi về Chromium đóng gói khi không có browser thật (lúc đó
  log sẽ cảnh báo). Đổi thứ tự này ở `browser.BROWSER_CHANNELS`.
- **HTTP 429 = site bảo chậm lại, phải nghe.** Fragrantica có rate limit thật và
  hiện trang giải thích rõ: họ là nhà xuất bản nhỏ, scraping dày làm sập server.
  Gặp 429 thì crawler nghỉ 1 → 5 → 15 phút (ưu tiên header `Retry-After`), vẫn bị
  thì **dừng cả mẻ** (`RateLimited`, exit code 2) chứ không nện tiếp. Bị chặn rồi
  thì cách duy nhất là nghỉ vài giờ và hạ tần suất — đổi IP/máy chỉ làm site siết
  chặt thêm cho mọi người.
- Mặc định `--delay` là 5–10 giây. Crawl chậm mà đều vẫn nhanh hơn bị chặn IP rồi
  phải chờ hàng giờ. Mẻ lớn nên chia nhiều ngày, dùng `RESUME=1` để chạy tiếp.
- **HTTP 408 phải retry, không được bỏ qua.** 408 nằm trong dải 4xx nhưng là
  *timeout*, tức lỗi tạm thời — trang chỉ tải chậm một lần mà bị bỏ hẳn. Danh
  sách mã cần thử lại ở `fetcher.RETRY_STATUSES`. Riêng chế độ browser, sau khi
  timeout thì bỏ luôn context vì page có thể đang kẹt.
- **Lazy-section ngừng tải sau vài trang nếu dùng mãi một browser context.** Đo
  thực tế 8 trang liên tiếp: dùng chung 1 page được 4/8 (từ trang 5 trở đi khối
  LONGEVITY không render nữa), làm mới context thì 8/8. Vì vậy `BrowserFetcher`
  mở context mới sau mỗi `CONTEXT_MAX_PAGES` trang (mặc định 3) — browser vẫn
  giữ nguyên nên rất rẻ. Triệu chứng nếu hạ mức này quá cao: log đầy dòng
  *"Không thấy khối độ lưu hương/toả hương"* sau khi chạy được một lúc.
- Cloudflare còn chặn **theo phiên browser**: mở nhiều trang liên tiếp trong cùng
  một phiên sẽ dính 403 dù từng URL riêng lẻ vẫn vào được. Gặp 403/429 thì
  `BrowserFetcher` đóng browser và mở phiên mới rồi thử lại. Lưu ý Playwright chỉ
  được `start()` một lần cho mỗi object — gọi lần hai sẽ lỗi
  *"Sync API inside the asyncio loop"* — nên chỉ browser được mở lại.
- **Nhóm hương** (`fragrance_family`) lấy từ description theo mẫu
  `"... is a <NHÓM> fragrance for ..."`, **chỉ chấp nhận** nhóm nằm trong danh
  sách 31 nhóm ở `parsers.FRAGRANCE_FAMILIES`; không khớp được thì bỏ trống
  (`null` trong JSON, ô rỗng trong CSV) chứ không đoán. Danh sách được khớp
  **tên dài trước** để `Floral Fruity Gourmand` không bị cắt còn `Floral`.
  Nhiều chai Fragrantica không xếp nhóm — description ghi thẳng
  `"is a fragrance for women"` — nên cột này rỗng là chuyện bình thường.
  Muốn thêm nhóm mới thì bổ sung vào `FRAGRANCE_FAMILIES`.
- **Năm phát hành** ưu tiên lấy từ câu `"<tên> was launched in YYYY"` trong
  description, sau đó mới tới năm **cuối cùng** trong `<title>`. Không được lấy
  năm đầu tiên bắt gặp: tên nước hoa có thể chứa số 4 chữ số — `1969 Parfum de
  Revolte` ra mắt 2001, `Chris 1947` (Dior) ra mắt 2003.

### Giới tính

Cả hai chức năng dùng chung một bộ giá trị, chuẩn hoá ở [common.py](common.py):

| Nguồn | Kết quả |
|---|---|
| `men` (Fragrantica) | `Nam` |
| `women` (Fragrantica) | `Nữ` |
| `women and men` / `men and women` | `Unisex` |

Giá trị lạ được giữ nguyên văn thay vì bỏ trống, để còn nhìn thấy mà bổ sung vào
`GENDER_MAP`.

## Lưu ý pháp lý

- `robots.txt` của Fragrantica hiện cho phép `Allow: /` với mọi bot và không đặt
  `Crawl-delay`; crawler vẫn tự kiểm tra `robots.txt` trước mỗi URL.
- Trang khai báo `Content-Signal: search=yes, ai-train=no, use=reference` —
  tức là **không được dùng dữ liệu này để huấn luyện mô hình AI**.
- Giữ delay mặc định (2–5 giây) để không gây tải cho server. Dữ liệu thuộc bản
  quyền Fragrantica; chỉ dùng cho mục đích cá nhân/nghiên cứu.
