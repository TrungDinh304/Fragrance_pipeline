-- Điểm hiệu chỉnh phải nằm trong thang 0-5. Ra ngoài nghĩa là công thức Bayes
-- sai dấu hoặc chia nhầm -- và nó sẽ sai ÂM THẦM, vì bảng vẫn có đủ dòng.
select brand, rating_weighted
from {{ ref('mart_brand') }}
where rating_weighted is not null
  and (rating_weighted < 0 or rating_weighted > 5)
