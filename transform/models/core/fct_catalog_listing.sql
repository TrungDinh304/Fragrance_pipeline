-- Grain: một chai trong mục lục của hãng (factless fact).
--
-- Không có số đo chính -- bản thân việc "chai này có trong mục lục" là sự kiện.
-- Đếm dòng ra quy mô danh mục của hãng; nối với fct_perfume_rating ra độ phủ.
-- `comments` là số bình luận hiện trên mục lục, dùng để xếp ưu tiên crawl.
select
    coalesce({{ surrogate_key('brand_key') }}, {{ unknown_key() }}) as brand_sk,
    {{ surrogate_key('perfume_key') }}                            as perfume_sk,
    comments
from {{ ref('stg_brand_perfumes') }}
