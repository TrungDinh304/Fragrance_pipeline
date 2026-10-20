-- Chi tiết chai, nguyên trạng từ tầng silver, thêm đúng một cột: `brand_nk`.
--
-- `brand_nk` là khoá tự nhiên của hãng dùng cho tầng dimensional. Silver đã
-- gắn `brand_key` cho gần hết chai; chai nào có tên hãng mà không khớp danh mục
-- thì lấy chính tên đó làm khoá (tiền tố `name:` để không bao giờ trùng một khoá
-- URL thật). Nhờ vậy không chai nào rơi khỏi bảng xếp hạng hãng chỉ vì danh
-- mục hãng chưa crawl tới.
--
-- Trước đây model này còn ghép giá namperfume để gắn cờ "có bán ở VN". Phần đó
-- đã bỏ: project tập trung vào tín hiệu cộng đồng Fragrantica, không làm đối
-- chiếu thị trường nữa.
select
    *,
    coalesce(brand_key,
             case when brand is not null then 'name:' || brand end) as brand_nk
from {{ source('silver', 'perfumes') }}
