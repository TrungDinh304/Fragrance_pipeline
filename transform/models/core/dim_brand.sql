-- Grain: một hãng.
--
-- Ba nguồn thành viên, theo thứ tự:
--   1. danh mục hãng (`brands`) -- thành viên chính thức;
--   2. hãng chỉ thấy ở chai hoặc mục lục mà danh mục chưa có -- "inferred
--      member" kiểu Kimball, để fact không phải trỏ vào hư không;
--   3. thành viên "không rõ" (khoá -1) cho chai không có tên hãng.
with directory as (
    select brand_key, brand_name, alphabet, popular_rank,
           true as in_directory
    from {{ ref('stg_brands') }}
),
inferred as (
    select brand_nk as brand_key, any_value(brand) as brand_name
    from {{ ref('stg_perfumes') }}
    where brand_nk is not null
      and brand_nk not in (select brand_key from directory)
    group by 1
    union
    select brand_key, any_value(brand_name)
    from {{ ref('stg_brand_perfumes') }}
    where brand_key is not null
      and brand_key not in (select brand_key from directory)
    group by 1
),
members as (
    select * from directory
    union all
    select brand_key, brand_name,
           cast(null as varchar), cast(null as integer), false
    from inferred
)
select
    {{ surrogate_key('brand_key') }} as brand_sk,
    brand_key,
    brand_name,
    alphabet,
    popular_rank,
    in_directory
from members
union all
select {{ unknown_key() }}, null, null, null, null, false
