-- Grain: một chai đã crawl chi tiết, trạng thái MỚI NHẤT.
--
-- Fact trạng thái hiện tại: rating và số vote ở lần crawl gần nhất. Lịch sử các
-- lần trước nằm ở fct_rating_snapshot.
select
    {{ surrogate_key('p.perfume_key') }}                          as perfume_sk,
    coalesce({{ surrogate_key('p.brand_nk') }}, {{ unknown_key() }}) as brand_sk,
    cast(strftime(cast(left(p.scraped_at, 10) as date), '%Y%m%d') as integer)
                                                                  as date_sk,
    p.rating,
    p.rating_count
from {{ ref('stg_perfumes') }} p
