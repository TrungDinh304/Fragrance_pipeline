-- Chi tiết chai, nguyên trạng từ tầng silver.
--
-- Trước đây model này còn ghép giá namperfume để gắn cờ "có bán ở VN". Phần đó
-- đã bỏ: project tập trung vào tín hiệu cộng đồng Fragrantica, không làm đối
-- chiếu thị trường nữa.
select *
from {{ source('silver', 'perfumes') }}
