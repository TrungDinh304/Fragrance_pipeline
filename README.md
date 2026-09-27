# perfume-intel

Thu thập dữ liệu nước hoa rồi phân tích thị trường dựa trên **tín hiệu cộng
đồng** (rating, vote accord, vote mùa, độ lưu/toả hương) từ
[fragrantica.com](https://www.fragrantica.com/), đối chiếu với giá và độ phủ
hàng thật trên [namperfume.net](https://namperfume.net).

Hai nửa tách rời nhau:

```
data/inputs/   →  crawl   →  data/raw/   →  analyze  →  data/processed/
(CSV link)        (mạng)     (.jsonl)       (offline)   (CSV + summary.json)
```

Crawl chậm và phụ thuộc mạng nên chỉ chạy khi cần dữ liệu mới; phân tích chạy
hoàn toàn offline trên `data/raw/`, sửa công thức rồi chạy lại bao nhiêu lần
cũng được.

## Cài đặt

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .

# Chỉ cần nếu muốn lấy "when to wear", độ lưu hương, độ toả hương (cờ --render):
pip install -e ".[render]"
python -m playwright install chromium
```

Cài xong có lệnh `perfume-intel`. Không muốn cài thì chạy thẳng
`python -m perfume_intel ...` — hai cách tương đương, tài liệu dưới đây dùng
cách thứ hai.

## Bố cục dữ liệu

| Thư mục | Nội dung |
|---|---|
| `data/inputs/<site>/` | File CSV chứa link cần crawl (bạn tự bỏ vào) |
| `data/raw/<site>/` | Kết quả crawl, mỗi hãng một file `.jsonl` theo ngày |
| `data/processed/` | Đầu ra của `analyze` và `mini` |
| `data/scratch/` | File chạy thử lặt vặt, không ai đọc tới |
| `.cache/html/` | HTML thô đã tải (rất nặng, xoá được bất cứ lúc nào) |

Cả `data/` lẫn `.cache/` đều không commit.

## Lệnh make

```powershell
make help                            # xem nhanh các lệnh
make crawl                           # crawl data/inputs/fragrantica -> data/raw/fragrantica
make crawl data/inputs/fragrantica   # chỉ định thư mục khác
make nam                             # crawl namperfume.net
make mini                            # ghép bản mini (offline)
make brands                          # danh mục hãng -> data/raw/fragrantica/
make products                        # chai của từng hãng -> data/raw/fragrantica/
make queue                           # xem tiến độ crawl
make daily                           # chạy 1 lát ngân sách (nhỏ giọt)
make analyze                         # phân tích -> data/processed/<ngày>/
make test                            # toàn bộ test, không cần mạng
```

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

# 6. namperfume.net (không cần --render, dữ liệu có sẵn trong HTML)
python -m perfume_intel crawl --site namperfume data/inputs/namperfume

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
- **Bị chặn thì cho MỌI hãng nghỉ**, không nhảy sang hãng khác. 429 và thử thách
  Cloudflare là tín hiệu ở mức thiết bị; đổi hãng rồi cào tiếp là hiểu sai vấn đề
  và bị chặn sâu hơn. Hãng lỗi lẻ thì nghỉ dần lâu hơn: 6h → 24h → 72h.
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

## Phân tích thị trường

```powershell
python -m perfume_intel analyze
python -m perfume_intel analyze --metric brand --metric accord    # chỉ vài bảng
python -m perfume_intel analyze --out data/processed/thu-nghiem
```

Đọc `data/raw/fragrantica/` (và `data/raw/namperfume/` nếu có), ghi ra
`data/processed/<YYYYMMDD>/`:

| File | Nội dung |
|---|---|
| `brand.csv` | Mỗi hãng: số chai, tổng vote, thị phần chú ý, điểm có hiệu chỉnh |
| `accord.csv` | Mỗi accord: độ phủ, độ mạnh trung bình, điểm có hiệu chỉnh |
| `gender.csv` | Cơ cấu Nam / Nữ / Unisex |
| `season.csv` | Mùa nào đang nhiều/ít hàng (theo vote when-to-wear) |
| `market_gap.csv` | Chai nhiều vote nhưng chưa thấy bán ở namperfume |
| `summary.json` | Số tổng quan của cả lần chạy |

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
  core/                hạ tầng, KHÔNG biết gì về site cụ thể
    http.py            session + throttle + retry/backoff + cache đĩa + robots
    browser.py         bản Fetcher chạy bằng Playwright (cho --render)
    csv_input.py       đọc link từ CSV/text của người dùng (đoán dấu phân cách...)
    storage.py         đọc & ghi JSONL / CSV
    text.py            chuẩn hoá giới tính, tên file kết quả, khoá so khớp URL
  sources/             mỗi site một package
    base.py            SiteScraper: vòng lặp crawl dùng chung
    fragrantica/       models.py · parsers.py · scraper.py
    namperfume/        models.py · parsers.py · scraper.py
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
scripts/demo.py        xem nhanh tháp hương của vài chai
tests/                 test offline trên HTML thật đã lưu
```

Ba quy tắc giữ cho cấu trúc này không rối lại:

1. **`core/` không được import từ `sources/`.** Hạ tầng không biết đang crawl
   site nào; mọi thứ riêng của site (selector, tên miền, JS chờ render) là tham
   số truyền vào.
2. **`analytics/` không đụng tới mạng.** Nó chỉ đọc `.jsonl` đã có, nên chạy lại
   bao nhiêu lần cũng được.
3. **Thêm site mới = thêm một package trong `sources/`**: `models.py` (dataclass
   có `to_dict`/`to_flat_dict`/`from_dict`), `parsers.py`, và một lớp con của
   `SiteScraper`. Đăng ký vào `SCRAPERS` trong `cli/crawl_cmd.py` là xong —
   không phải sửa pipeline.

## Test

```powershell
make test                      # cả 4 bộ
python tests\test_parsers.py   # hoặc từng bộ một
python -m pytest tests/ -v     # nếu có cài pytest
```

| File | Kiểm cái gì |
|---|---|
| `test_parsers.py` | Bóc tách HTML Fragrantica, đọc CSV đầu vào, retry/rate-limit |
| `test_namperfume.py` | Bóc tách trang namperfume, xuất file |
| `test_mini.py` | Ghép bản mini |
| `test_brands.py` | Danh mục hãng: footer, mục lục A-Z, cắt section, retry, khử trùng |
| `test_brand_products.py` | Sản phẩm của hãng: collection, `<template>`, retry, resume |
| `test_schedule.py` | Sổ theo dõi, ngân sách, chia hãng lớn nhiều ngày, ngắt mạch |
| `test_analytics.py` | Nạp dữ liệu, các chỉ số, xuất báo cáo |

Test chạy trên file HTML thật đã lưu ở `tests/fixtures/`, **không cần mạng**.
Khi Fragrantica đổi giao diện, test sẽ đỏ và chỉ ra selector nào trong
`parsers.py` cần sửa — hãy tải lại fixture mới rồi chỉnh selector.

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
| `Nam` / `Nữ` / `Unisex` (namperfume `data-gender`) | giữ nguyên |

Giá trị lạ được giữ nguyên văn thay vì bỏ trống, để còn nhìn thấy mà bổ sung vào
`GENDER_MAP`.

### namperfume.net

- **Không cần render.** Đã so bản `requests` với bản Playwright trên 6 sản phẩm:
  giống hệt nhau, kể cả khối Standard Size. Bật `--render` chỉ chậm hơn và còn
  làm bẩn `name` (JS chèn thêm chữ "Nữ"/"Nam" vào `<h1>`).
- **Thứ tự trong `data-variant-title` không cố định**: có sản phẩm ghi
  `"90ml / Eau de Parfum"`, sản phẩm khác ghi `"Eau de Parfum/105ml"`. Parser
  nhận phần khớp dạng số + `ml` là dung tích, phần còn lại là nồng độ — không
  dựa vào vị trí.
- Mỗi thuộc tính xuất hiện **2 lần** trong HTML (bản desktop và mobile), nên
  `parse_attributes` chỉ giữ lần đầu.
- `robots.txt` của namperfume chỉ chặn `/admin`, `/cart`, `/checkout`, `/search`...
  Trang `/products/` được phép crawl. Fetcher tự tải robots.txt theo đúng tên miền.

## Lưu ý pháp lý

- `robots.txt` của Fragrantica hiện cho phép `Allow: /` với mọi bot và không đặt
  `Crawl-delay`; crawler vẫn tự kiểm tra `robots.txt` trước mỗi URL.
- Trang khai báo `Content-Signal: search=yes, ai-train=no, use=reference` —
  tức là **không được dùng dữ liệu này để huấn luyện mô hình AI**.
- Giữ delay mặc định (2–5 giây) để không gây tải cho server. Dữ liệu thuộc bản
  quyền Fragrantica; chỉ dùng cho mục đích cá nhân/nghiên cứu.
