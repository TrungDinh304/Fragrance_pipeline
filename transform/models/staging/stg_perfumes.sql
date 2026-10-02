-- Chi tiết chai + cờ "có bán ở VN".
--
-- Phép ghép đúng như phía Python: `perfumes.des_key` là URL đích do người dùng
-- khai trong file CSV đầu vào, còn `market.market_key` là URL thật của trang
-- sản phẩm đã crawl. Ghép nhầm chiều thì mọi chai đều `listed = false` mà không
-- có lỗi nào.
select
    p.*,
    (m.market_key is not null) as listed,
    m.price                    as market_price
from {{ source('silver', 'perfumes') }} p
left join {{ source('silver', 'market') }} m
       on p.des_key = m.market_key
