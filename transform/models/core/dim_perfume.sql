-- Grain: một chai -- cả chai đã crawl chi tiết LẪN chai mới chỉ thấy trong mục
-- lục hãng. Gộp cả hai để fct_catalog_listing (độ phủ) có dim mà trỏ vào;
-- `is_detailed` phân biệt hai nhóm.
--
-- Hãng KHÔNG nằm ở đây mà ở dim_brand, nối qua fact: giữ star schema phẳng
-- một tầng thay vì snowflake perfume -> brand.
with catalog as (
    select perfume_key, perfume_name, perfume_url, collection, year, gender
    from {{ ref('stg_brand_perfumes') }}
),
detailed as (
    select
        p.perfume_key, p.perfume_id, p.name, p.url, c.collection,
        p.gender, p.year, p.fragrance_family, p.longevity, p.sillage,
        p.description, p.image, true as is_detailed
    from {{ ref('stg_perfumes') }} p
    left join catalog c using (perfume_key)
),
catalog_only as (
    select
        perfume_key, cast(null as varchar), perfume_name, perfume_url,
        collection, gender, year, cast(null as varchar), cast(null as varchar),
        cast(null as varchar), cast(null as varchar), cast(null as varchar),
        false
    from catalog
    where perfume_key not in (select perfume_key from detailed)
)
select
    {{ surrogate_key('perfume_key') }} as perfume_sk,
    *
from (select * from detailed union all select * from catalog_only)
