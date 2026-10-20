-- Bảng `perfume_rating_history` của tầng silver, nguyên trạng. Model phía sau đọc qua đây chứ
-- không gọi thẳng source(), để đổi nguồn chỉ phải sửa một chỗ.
select *
from {{ source('silver', 'perfume_rating_history') }}
