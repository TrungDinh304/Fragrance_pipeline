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
  │ 5 mart · 17 kiểm tra          │
  └───────────────────────────────┘

  ┌────────────────────────────────────────────────────────────────┐
  │ TRUY XUẤT   retrieval/ports.py   ◄── CỔNG                      │
  │             retrieval/memory.py        adapter in-memory       │
  │             retrieval/pgvector_store.py  Postgres + pgvector   │
  │             embedding/   ◄── CỔNG (ONNX local, 384 chiều)      │
  │             vectors/ (chi tiết cài đặt, KHÔNG ai ngoài gọi)    │
  └─────────────────────────────┬──────────────────────────────────┘
                                │ chỉ nhìn thấy ports
  ┌─────────────────────────────▼──────────────────────────────────┐
  │ ỨNG DỤNG    cli/  ·  api/ (FastAPI + trang test chatbot)       │
  │             llm/   ◄── CỔNG (9router, endpoint kiểu OpenAI)    │
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
| `api/` chỉ nhìn thấy `Retriever` / `ChatModel` / `Embedder` | đổi backend nào cũng không phải sửa tầng API |
| Đầu ra của LLM là **dữ liệu**, không phải lệnh | nhãn model bịa ra bị lọc qua `vocabulary()`; tên chai bịa ra bị `advise.verify()` soát |
| `model_id` đi kèm MỌI vector | vector hai model vẫn cộng trừ được với nhau nhưng vô nghĩa, và không phát hiện được từ con số |
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
| Truy xuất | **cổng + adapter**; thưa (IDF) + đặc (embedding) | thưa giải thích được *vì sao giống*, đặc hiểu được câu tự do — giữ cả hai |
| Embedding | **ONNX** (fastembed), KHÔNG torch | cùng model, ~250 MB thay vì ~2,5 GB |
| Kho vector | **Postgres + pgvector** | mọi phép tính trong SQL, không nạp vào RAM |
| Gọi AI | **9router** (endpoint kiểu OpenAI) | gộp 40+ nhà cung cấp; đổi sang API trực tiếp chỉ là đổi env |
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
InMemoryRetriever        đối chứng · chạy mọi nơi · không cần hạ tầng
PgVectorRetriever        đang dùng cho API · 781 chai · mọi phép tính trong SQL
```

Cả hai qua **cùng 30 điều khoản**, nên đổi qua lại chỉ là một biến môi trường
(`RETRIEVER=memory`). `DuckDBRetriever` trong lộ trình cũ đã bị bỏ: nó là bước
trung gian để thoát khỏi RAM, mà pgvector làm xong việc đó rồi.

Giữ bản in-memory KHÔNG phải vì luyến tiếc: nó là bên đối chứng. Hai bản cùng chạy
một bộ hợp đồng thì chỗ lệch nhau sẽ đỏ ngay — còn một cổng chỉ có một bản cài đặt
thì không chứng minh được điều gì về tính thay thế được.

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
| Tìm kiếm trộn (hybrid: thưa + đặc cùng lúc) | khi câu hỏi có note cụ thể bị trả lời kém |
| Xác thực / giới hạn tần suất cho API | khi API ra khỏi máy cá nhân |
| `DuckDBRetriever` | có lẽ không bao giờ — pgvector đã làm xong việc đó |
| State store dùng chung (Postgres) | khi muốn **nhiều máy crawl song song** — xem giới hạn của lake |
| Đọc silver trực tiếp trên S3 (DuckDB `httpfs`) | khi silver không còn vừa đĩa local, hoặc có bên thứ ba cần hỏi mà không chép file |

Dự phóng hiện tại: bronze đủ danh mục ≈ **0,26 GB**; 100k chai × 768 chiều ≈
**307 MB**, vét cạn hết vài chục mili-giây. Chưa cái nào chạm ngưỡng.

Bốn thứ đã rời khỏi danh sách này vì điều kiện kích hoạt đã đến: *object storage
cho bronze* (xem [Data lake](#data-lake-minio--s3)), *embedding*, *vector DB* và
*cổng LLM* — cả ba cái sau cùng được kích hoạt bởi một người dùng thật: trang test
chatbot. Đó đúng là cách danh sách này nên hoạt động.

**Lý do viết ra danh sách này:** để lần sau không ai phải đoán "có nên thêm
vector DB không" — điều kiện kích hoạt đã ghi sẵn.

## Chatbot: LLM ở đâu, và ở đâu thì KHÔNG

```
câu tiếng Việt ─▶ llm/intent.py ─▶ Query ─▶ retrieval ─▶ llm/advise.py ─▶ câu trả lời
                  (model đề xuất,            (pgvector)    (chỉ dùng kết quả,
                   TỪ VỰNG quyết định)                      verify() soát lại)
```

LLM chỉ xuất hiện ở **hai đầu**, không bao giờ ở giữa. Nó không chọn chai, không
chấm điểm, không biết kho dữ liệu. Giữa hai đầu đó là SQL.

### Đầu ra của model là dữ liệu, không phải lệnh

| Chỗ | Chặn bằng |
|---|---|
| Nhãn note/accord model đề xuất | lọc qua `retriever.vocabulary()` — nhãn bịa bị bỏ |
| Hoàn cảnh | chỉ nhận 6 giá trị cộng đồng thật sự bình chọn |
| Tên chai trong câu trả lời | `advise.verify()` soát lại với danh sách đã trả về |
| Mọi thứ còn lại | giao diện hiện danh sách gốc cạnh câu trả lời |

Lý do không phải là lo model "nói dối": một chai bịa ra trông **y hệt** một chai
thật, nên không ai kiểm được bằng mắt, và cái sai đó đi thẳng tới người dùng cuối.

### Vì sao vector đặc mà vẫn giải thích được

Vector đặc không có chiều nào có tên, nên tự nó chỉ nói được "hai chai gần nhau".
Cách giải: tìm bằng vector đặc (được độ phủ ngữ nghĩa), rồi với ĐÚNG những chai
trả về mới lấy lý do từ bảng thưa bằng một câu join. Hai bảng, một lần hỏi:

    perfume_vectors   embedding vector(384)    -> câu tự do
    perfume_terms     (block, label, weight)   -> tìm theo note + phần "vì sao"

### Hoàn cảnh là BỘ LỌC, không phải điểm cộng

Câu "mùi gỗ trầm ấm cho buổi tối mùa đông" có cả phần mùi lẫn phần dịp. Nếu chọn
một trong hai nhánh thì mất một nửa câu hỏi — đã thấy thật: đi nhánh theo dịp thì
nó bỏ hẳn "gỗ trầm". Nên dịp được dùng làm bộ lọc trên nhánh ngữ nghĩa.

Lọc chứ không cộng điểm, vì cộng hai thang điểm khác nhau (cosin và trọng số IDF)
là chỗ rất dễ tự lừa mình: con số ra trông vẫn hợp lý nhưng không còn nghĩa gì.

### Hội thoại nhiều lượt: ba việc, bốn luật

```
moi       khách hỏi chuyện khác hẳn      -> bỏ hết điều kiện cũ
loc_them  vẫn chuyện cũ, thêm điều kiện  -> GỘP với điều kiện cũ
ve_chai   hỏi về một chai đã hiện        -> trỏ vào đúng chai đó
```

Bốn luật, mỗi luật sinh ra từ một lỗi đã thấy khi thử 6 lượt liền:

| Luật | Không có nó thì |
|---|---|
| **Không đoán chai** — `ve_chai` không giải được thì hạ xuống `loc_them` | trả lời chắc chắn về một chai CÓ THẬT nhưng không phải chai khách hỏi; không ai phát hiện được |
| **Bỏ chai đã hiện** ở lượt `loc_them`, và nói thật khi hết | "nhẹ hơn chút" trả lời y nguyên lượt trước — dấu hiệu "bot hỏng" rõ nhất |
| **`ve_chai` là nhánh rẽ**, lượt sau quay lại mạch tìm kiếm trước đó | "còn gì nữa không" sau đó gộp với một query `like_perfume` và ra rỗng |
| **Trường khai tường minh thắng chữ trong câu** | "mùa hè thì sao" ra kết quả vừa hợp mùa nóng vừa hợp mùa lạnh, tức là không đổi gì |

Phiên nằm trong bộ nhớ tiến trình, có trần (200 phiên / 40 lượt / 6 giờ). Không đưa
vào Postgres vì hội thoại chỉ có **một nửa** điều kiện đáng lưu bền: nó không dựng
lại được, nhưng cũng không ai cần nó sau khi đóng tab.

Hệ quả nói thẳng: restart API là mất hội thoại đang dở, và chạy nhiều tiến trình
API thì mỗi tiến trình có một bộ phiên riêng.

### Tự xuống cấp, không tự chết

| Mất gì | Còn làm được gì |
|---|---|
| `LLM_API_KEY` | vẫn tìm được; ý định tách bằng từ khoá, câu trả lời ghép bằng Python |
| Postgres | rơi về `InMemoryRetriever` |
| MinIO | đọc được bản cache trong `data/raw/` |
| Model embedding | mất nhánh câu tự do; tìm theo note vẫn chạy |

Một trang test báo lỗi trắng thì không test được gì — nên mỗi tầng thiếu đều phải
nói ra là đang thiếu gì, rồi chạy tiếp bằng phần còn lại.

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
| Phiên hội thoại giữ trong bộ nhớ, không vào Postgres | nó không dựng lại được nhưng cũng không còn giá trị sau khi đóng tab |
| `ve_chai` không giải được thì HẠ cấp, không đoán | đoán cho ra một chai có thật, nên sai mà không ai phát hiện |
| Lượt nói tiếp BỎ những chai đã hiện | truy vấn gần như không đổi nên kết quả lặp y nguyên |
| Hoàn cảnh khai tường minh thắng chữ đọc ra từ câu | `text` cộng dồn qua các lượt nên "mùa đông" cũ còn mãi |
| Embedding chạy ONNX, KHÔNG chạy torch | cùng model, ~250 MB thay vì ~2,5 GB; đo được 5/5 trên câu hỏi tiếng Việt |
| Model embedding nhỏ (384 chiều) thay vì lớn (1024) | đo trên đúng việc mình làm: MiniLM 5/5, e5-large 4/5 — không chọn theo bảng xếp hạng chung |
| Tài liệu embedding giữ NGUYÊN tên note tiếng Anh | ba cách viết đều 5/5, nên không cần từ điển — và một tầng dịch không cần thiết là một tầng có thể dịch sai |
| GIỮ dấu tiếng Việt, không chuẩn hoá bỏ dấu | bỏ dấu cả hai phía làm 5/5 tụt xuống 2/5 |
| `PgVectorRetriever` tính TẤT CẢ trong SQL | giữ dữ liệu trong RAM thì kho vector không giải quyết gì — có test canh |
| Bảng pgvector tự dựng lại khi số chiều đổi | số chiều nằm trong kiểu cột nên `CREATE IF NOT EXISTS` không sửa được; bảng là kho phục vụ, dựng lại là đúng |
| Test pgvector dùng bảng có TIỀN TỐ riêng | đã dính thật: test TRUNCATE đúng bảng production đang dùng |
| Niêm thất bại **không** ném lỗi ra ngoài | hạ tầng chết không được giết mẻ crawl 10 ngày; dữ liệu còn trong spool, niêm lại bằng `lake push` |
