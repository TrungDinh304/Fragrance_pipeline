# Kiến trúc

Tài liệu này nói về **ranh giới** — chỗ nào được phép biết chỗ nào. Nó không mô
tả tính năng (xem `README.md`), mà mô tả những cam kết giữ cho project không
phải mổ lại mỗi khi thêm một lớp mới.

## Nguyên tắc gốc

> **Nguồn sự thật luôn là tầng file (bronze/silver). Mọi thứ khác là hình chiếu
> dựng lại được.**

Hệ quả thực tế:

- Không bao giờ để một kho phục vụ (vector DB, cache, search engine) là nơi lưu
  **gốc** của bất cứ thứ gì. Đổi kho = nạp lại, không phải di trú.
- Embedding, chỉ số, mart đều là **artifact dẫn xuất**. Xoá đi dựng lại được
  trong vài giây tới vài phút.
- Thứ duy nhất không dựng lại được là **bronze** (phải crawl lại, nhiều ngày) và
  `data/state/` (tiến độ nhỏ giọt). Chỉ hai thứ đó cần sao lưu.

"Tầng file" ở đây là *tầng file*, không phải *ổ đĩa này*. Khi lake bật, bronze và
silver nằm trên MinIO; `data/raw/` và `data/silver/` tụt xuống thành spool ghi
trước + cache đọc. Xem [Data lake](#data-lake-minio--s3).

## Các lớp

```
                        ┌──────────────────────────────┐
  nguồn ngoài           │  fragrantica.com             │
                        └───────────────┬──────────────┘
                                        │  SiteScraper (cổng)
  ┌─────────────────────────────────────▼──────────────────────────┐
  │ THU THẬP   sources/ · core/http · core/browser                 │
  │            pipelines/{crawl,brands,brand_products,daily}       │
  │            trạng thái: pipelines/state.py  (SQLite, LUÔN local) │
  └─────────────────────────────────────┬──────────────────────────┘
                                        │ append từng dòng vào SPOOL
  ┌─────────────────────────────────────▼──────────────────────────┐
  │ SPOOL      data/raw/<site>/{perfumes,brands,products}/*.jsonl  │
  │            ghi trước + cache đọc — KHÔNG phải bản gốc          │
  └─────────────────────────────────────┬──────────────────────────┘
                     core/lake.py       │  niêm sau mỗi hãng  ▲ kéo về khi đọc
                     core/objects.py    ▼                     │
  ┌─────────────────────────────────────────────────────────────────┐
  │ BRONZE     s3://bronze/<site>/<kind>/*.jsonl    (MinIO/S3)      │
  │            NƠI LƯU CHÍNH THỨC · versioning · chỉ ghi thêm       │
  └──────────────┬──────────────────────────────┬───────────────────┘
                 │ warehouse/silver.py          │ analytics/dataset.py
  ┌──────────────▼───────────────┐   ┌──────────▼─────────────────┐
  │ SILVER   s3://silver/*.parquet│   │ analytics/ (Python thuần)  │
  │ khử trùng · có kiểu · dài     │   │ metrics · charts · report  │
  └──────────────┬───────────────┘   └────────────────────────────┘
                 │ transform/ (dbt)                    ▲
  ┌──────────────▼───────────────┐                     │ bộ đối chứng
  │ GOLD   data/warehouse/*.duckdb├─────────────────────┘
  │ 5 mart · 24 kiểm tra          │
  └───────────────────────────────┘

  ┌────────────────────────────────────────────────────────────────┐
  │ TRUY XUẤT   retrieval/ports.py   ◄── CỔNG                      │
  │             retrieval/memory.py  (adapter #1, in-memory)       │
  │             vectors/ (chi tiết cài đặt, KHÔNG ai ngoài gọi)    │
  └─────────────────────────────┬──────────────────────────────────┘
                                │ chỉ nhìn thấy ports
  ┌─────────────────────────────▼──────────────────────────────────┐
  │ ỨNG DỤNG    cli/  ·  (sau này) api/  ·  (sau này) chatbot/     │
  └────────────────────────────────────────────────────────────────┘
```

## Luật đi lại

| Luật | Vì sao |
|---|---|
| `core/` không import từ `sources/` | hạ tầng không được biết đang crawl site nào |
| Chỉ `core/bronze.py` đọc kho thô | nhờ đó việc chuyển sang MinIO gói gọn trong một hàm `scan()` |
| Chỉ `config.raw_dir()` sinh đường dẫn kho thô | như trên |
| Kiểm "có dữ liệu chưa" bằng `bronze.available()`, **không** bằng `Path.exists()` | `exists()` chỉ thấy ổ đĩa này; trên máy mới nó báo "chưa có dữ liệu" trong khi lake có đủ |
| Chỉ `core/lake.py` biết tới `core/objects.py` | tầng trên không được biết bronze nằm trên file hay trên S3 |
| `cli/` **không** import `vectors/` | truy xuất phải đi qua cổng — có test canh |
| dbt đọc **silver**, không đọc bronze | khử trùng và đặt kiểu đã xong và đã có test |
| `analytics/metrics.py` là hàm thuần | làm bộ đối chứng cho dbt (`tests/test_warehouse.py`) |

## Lựa chọn công nghệ và lý do bền

| Lớp | Chọn | Vì sao giữ được lâu |
|---|---|---|
| Thu thập | requests + Playwright (Chrome thật) | không khoá nhà cung cấp |
| Hàng đợi | SQLite (WAL) | một file, đọc được sau hàng chục năm |
| Kho đối tượng | **API S3** qua boto3 | MinIO hôm nay, AWS/R2/Wasabi sau này — cùng một đoạn code |
| Bronze | JSONL theo entity | ghi thêm được, không khoá schema |
| Silver | **Parquet** | chuẩn mở có kiểu; DuckDB/pandas/Polars/Spark/Trino đều đọc |
| Gold | **dbt** + DuckDB | dbt là chuẩn ngành; đổi DuckDB → Postgres chỉ là đổi adapter |
| Truy xuất | **cổng + adapter** | xem bên dưới |
| Biểu đồ | SVG tự sinh | không CDN ⇒ mở được offline sau nhiều năm |

## Data lake (MinIO / S3)

Bronze và silver nằm trên MinIO. Local giữ lại hai vai, cả hai đều **bỏ đi được**:
spool ghi trước và cache đọc.

```
LAKE=off  (mặc định)        LAKE=s3
data/raw/  = BẢN GỐC        s3://bronze/  = BẢN GỐC
                            data/raw/     = spool + cache
```

Mặc định tắt, có chủ đích: bật mặc định nghĩa là mọi test và mọi lần chạy tay đều
đòi một MinIO đang sống. `docker-compose.yml` bật nó lên.

### Vì sao có spool, không ghi thẳng lên S3

**S3 không có `append`.** Mà `crawl_urls` ghi từng dòng NGAY KHI có — đó chính là
lý do một mẻ 10 ngày đứt mạng không mất dữ liệu. Ghi thẳng chỉ còn hai cách, cả
hai đều tệ:

| Cách | Vì sao không |
|---|---|
| PUT lại nguyên file sau mỗi chai | file Avon (1.379 chai) tới cuối ~4 MB ⇒ tổng lưu lượng ≈ **2,8 GB** cho một hãng. MinIO ở localhost thì chịu được, S3 thật là trả tiền cho việc vô nghĩa |
| Mỗi chai một object | 150k object nhỏ, và `list` thành thứ đắt nhất pipeline — mà `list` chạy ở **mọi** lần đọc |

Spool giải cả hai: append local (rẻ, an toàn khi mất điện), **niêm** một lần mỗi
hãng ⇒ ~8k object, mỗi object một PUT.

Niêm đặt trong `finally`, không phải sau khi xong êm đẹp: hãng bị 429 ở chai thứ
300 vẫn phải đưa 299 chai kia lên lake. Đó đúng là lúc dữ liệu dễ mất nhất.

### Luật đồng bộ: file dài hơn thắng

Bronze là append-only, nên **số byte LÀ** thước đo "bên nào có nhiều dữ liệu hơn".

| | Làm gì |
|---|---|
| local dài hơn lake | giữ local (đang crawl dở, chưa niêm), rồi niêm lên |
| lake dài hơn local | tải về (máy khác đã crawl thêm) |
| bằng nhau | không làm gì |

Cùng tinh thần với `site_cooldown` ("khoá lâu hơn thắng"): hai bên không đồng ý
thì chọn phía **không mất mát**. `test_khong_ghi_de_khi_local_dai_hon` là thứ duy
nhất đứng giữa một mẻ crawl dở và việc bị lake ghi đè mất.

### Hai bucket, không phải hai prefix

| | Versioning | Vì sao |
|---|---|---|
| `bronze` | **bật** | tầng duy nhất không dựng lại được; ghi đè nhầm vẫn lấy lại được bản cũ |
| `silver` | tắt | `make silver` dựng lại trong vài giây, versioning chỉ tốn chỗ |

Chung một bucket thì không tách được hai chính sách đó — đó là cả lý do tách.

### Hợp đồng, và vì sao nó quan trọng hơn hợp đồng của Retriever

`tests/objectstore_contract.py` — **19 điều khoản**, chạy cho **cả** `LocalStore`
lẫn `S3Store`.

`Retriever` có hai bản cài đặt cùng chạy trong một tiến trình, lệch nhau thì thấy
ngay. Kho đối tượng thì không: bản local chạy trong mọi test, bản S3 chỉ chạy khi
có MinIO. Một chỗ lệch — ví dụ `prefix` hiểu theo *thư mục* thay vì theo *chuỗi*,
đúng kiểu rất dễ cài sai ở bản local — sẽ xanh suốt trên máy dev và chỉ vỡ sau
khi bronze đã nằm trên MinIO.

### Giới hạn còn lại, cố ý không giải

`data/state/crawl_state.db` là SQLite, **không chạy được trên S3** (nó cần lock
theo byte-range trên một file tại chỗ). Nên lake làm cho *dữ liệu thu được* dùng
chung được, nhưng **hàng đợi vẫn của riêng từng máy**.

Hệ quả phải biết: **hai máy crawl cùng lúc sẽ làm trùng việc và ghi đè file của
nhau.** Muốn crawl song song nhiều máy thì phải chuyển state sang Postgres trước.
Hiện tại một máy là đủ — ngân sách 150 request/ngày không phải thứ chia ra nhiều
máy làm nhanh hơn được.

### Sao lưu

Không phải copy volume của MinIO. `perfume-intel lake pull` mang toàn bộ bronze về
dưới dạng `.jsonl` trong `data/raw/` — đúng thứ vẫn đang sao lưu từ trước.

## Cổng truy xuất (`retrieval/ports.py`)

Đây là ranh giới quan trọng nhất, vì nó là chỗ ứng dụng (CLI hôm nay, API và
chatbot ngày mai) chạm vào dữ liệu.

**Hai luật giữ cho nó không rò:**

1. **Khoá là `perfume_key`** (URL đã chuẩn hoá), không bao giờ là vị trí trong
   một cấu trúc. Khoá phải có nghĩa ở mọi backend.
2. **Không từ nào trong cổng nói tới cách cài đặt.** Không "vector", "idf",
   "parquet", "SQL". Thấy một từ như vậy lọt vào là ranh giới đã thủng.

Lý do giống nhau (`Reason`) cũng đi qua cổng: thiếu nó thì chatbot chỉ nói được
"hai chai gần nhau", không nói được "vì cùng có Oud".

### Hợp đồng nằm ở test, không nằm ở chữ

`tests/retrieval_contract.py` — **23 điều khoản** — là định nghĩa thật. Thêm
backend mới thì viết adapter rồi thêm một dòng:

```python
def test_hop_dong_duckdb():
    contract.check_all(lambda rows: DuckDBRetriever.from_rows(rows))
```

Qua hết là thay thế được, và **không ai phía trên phải sửa**. Không qua là chưa
thay thế được, dù chạy đúng tới đâu trong thử nghiệm riêng lẻ.

### Lộ trình adapter

```
InMemoryRetriever        ◄── đang dùng. 631 chai: dựng 24 ms, hỏi 0,24 ms
      │  khi dữ liệu không vừa RAM / nhiều tiến trình dùng chung
      ▼
DuckDBRetriever          DuckDB đã là phụ thuộc sẵn
      │  khi phục vụ nhiều người đồng thời
      ▼
PgVectorRetriever
```

Mỗi bước chỉ làm khi bước trước **đau thật**, không làm trước.

## Embedding: artifact dẫn xuất, không phải nguồn sự thật

Khi làm tìm kiếm ngữ nghĩa (hỏi bằng câu tự do thay vì gọi tên note):

- Model đứng sau cổng `Embedder`, **thay được**.
- Vector ghi ra `data/silver/perfume_embeddings.parquet` kèm `model_id`.
- Vector store (nếu có) chỉ **nạp** từ Parquet, không bao giờ là nơi lưu gốc.

Lý do: model embedding thay mỗi vài tháng, vector store thay vài năm một lần.
Ghép hai thứ lại là tự buộc hai nhịp thay đổi khác nhau vào nhau.

## Những gì CHƯA có (cố ý)

| | Khi nào làm |
|---|---|
| `Embedder` + bảng embedding | khi câu hỏi không còn là danh sách note |
| API (FastAPI) | khi có người dùng ngoài CLI |
| `DuckDBRetriever` | khi dữ liệu không vừa RAM |
| Cổng LLM | khi làm chatbot |
| Vector DB (pgvector/Qdrant) | khi phục vụ nhiều người đồng thời |
| State store dùng chung (Postgres) | khi muốn **nhiều máy crawl song song** — xem giới hạn của lake |
| Đọc silver trực tiếp trên S3 (DuckDB `httpfs`) | khi silver không còn vừa đĩa local, hoặc có bên thứ ba cần hỏi mà không chép file |

Dự phóng hiện tại: bronze đủ danh mục ≈ **0,26 GB**; 100k chai × 768 chiều ≈
**307 MB**, vét cạn hết vài chục mili-giây. Chưa cái nào chạm ngưỡng.

Riêng *object storage cho bronze* đã rời khỏi danh sách này — xem
[Data lake](#data-lake-minio--s3).

**Lý do viết ra danh sách này:** để lần sau không ai phải đoán "có nên thêm
vector DB không" — điều kiện kích hoạt đã ghi sẵn.

## Quyết định đã chốt, kèm lý do

| Quyết định | Lý do |
|---|---|
| Đơn vị công việc crawl là **chai**, không phải hãng | hãng làm đơn vị thì Avon = 1.379 request một ngày, hãng nhỏ = 3 |
| Ngân sách đếm **mọi** request, kể cả lấy mục lục | site đếm mọi request |
| Bị chặn → cho **mọi** hãng nghỉ, và ghi vào sổ chung | 429 là tín hiệu mức thiết bị, không phải mức hãng |
| Ngưỡng `--min-comments` mặc định 5 | giữ 96,3% lượng bình luận với 31% số request |
| Chân dung hãng chỉ dùng khối **mùi** | giữ khối hoàn cảnh thì mọi hãng đều "giống nhau" |
| Chuẩn hoá L2 **từng khối** trước khi ghép | nếu không, 550 chiều note nhấn chìm 6 chiều hoàn cảnh |
| Chỉ crawl Fragrantica | đã bỏ phần ghép namperfume để tập trung tín hiệu cộng đồng |
| Bronze trên MinIO, nhưng **ghi qua spool local** | S3 không append; mất mạng giữa mẻ không được mất dữ liệu |
| Khoá trên lake = đúng đường dẫn tương đối dưới `data/raw/` | round-trip chính xác, và file phẳng kiểu cũ lên lake không cần luật riêng |
| Đường dẫn ngoài `data/raw/` **không bao giờ** chạm lake | chốt giữ cho ~270 test chạy trên thư mục tạm mà không đụng mạng; đã kiểm bằng cách chạy cả bộ test với `LAKE=s3` và đếm object trước/sau |
| `boto3`, không phải SDK của MinIO | `import minio` sẽ neo project vào một nhà cung cấp ở ngay tầng thấp nhất |
| Image MinIO pin theo **digest** | `minio/minio` trên Docker Hub đã không còn pull được; một repo đã đóng băng thì `latest` có thể bị gỡ |
| Niêm thất bại **không** ném lỗi ra ngoài | hạ tầng chết không được giết mẻ crawl 10 ngày; dữ liệu còn trong spool, niêm lại bằng `lake push` |
