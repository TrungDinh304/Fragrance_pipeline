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
- Thứ duy nhất không dựng lại được là `data/raw/` (phải crawl lại, nhiều ngày)
  và `data/state/` (tiến độ nhỏ giọt). Chỉ hai thứ đó cần sao lưu.

## Các lớp

```
                        ┌──────────────────────────────┐
  nguồn ngoài           │  fragrantica.com             │
                        └───────────────┬──────────────┘
                                        │  SiteScraper (cổng)
  ┌─────────────────────────────────────▼──────────────────────────┐
  │ THU THẬP   sources/ · core/http · core/browser                 │
  │            pipelines/{crawl,brands,brand_products,daily}       │
  │            trạng thái: pipelines/state.py  (SQLite)            │
  └─────────────────────────────────────┬──────────────────────────┘
                                        │  core/bronze.py (cổng đọc)
  ┌─────────────────────────────────────▼──────────────────────────┐
  │ BRONZE     data/raw/<site>/{perfumes,brands,products}/*.jsonl  │
  │            thô, chỉ ghi thêm, không sửa                        │
  └──────────────┬──────────────────────────────┬──────────────────┘
                 │ warehouse/silver.py          │ analytics/dataset.py
  ┌──────────────▼───────────────┐   ┌──────────▼─────────────────┐
  │ SILVER   data/silver/*.parquet│   │ analytics/ (Python thuần)  │
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
| Chỉ `core/bronze.py` đọc `data/raw/` | đổi bố cục hoặc đổi sang S3 = sửa một chỗ |
| Chỉ `config.raw_dir()` sinh đường dẫn kho thô | như trên |
| `cli/` **không** import `vectors/` | truy xuất phải đi qua cổng — có test canh |
| dbt đọc **silver**, không đọc bronze | khử trùng và đặt kiểu đã xong và đã có test |
| `analytics/metrics.py` là hàm thuần | làm bộ đối chứng cho dbt (`tests/test_warehouse.py`) |

## Lựa chọn công nghệ và lý do bền

| Lớp | Chọn | Vì sao giữ được lâu |
|---|---|---|
| Thu thập | requests + Playwright (Chrome thật) | không khoá nhà cung cấp |
| Hàng đợi | SQLite (WAL) | một file, đọc được sau hàng chục năm |
| Bronze | JSONL theo entity | ghi thêm được, không khoá schema |
| Silver | **Parquet** | chuẩn mở có kiểu; DuckDB/pandas/Polars/Spark/Trino đều đọc |
| Gold | **dbt** + DuckDB | dbt là chuẩn ngành; đổi DuckDB → Postgres chỉ là đổi adapter |
| Truy xuất | **cổng + adapter** | xem bên dưới |
| Biểu đồ | SVG tự sinh | không CDN ⇒ mở được offline sau nhiều năm |

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
| Object storage cho bronze | khi nhiều máy cùng đọc/ghi, hoặc bronze vượt hàng trăm GB |

Dự phóng hiện tại: bronze đủ danh mục ≈ **0,26 GB**; 100k chai × 768 chiều ≈
**307 MB**, vét cạn hết vài chục mili-giây. Chưa cái nào chạm ngưỡng.

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
