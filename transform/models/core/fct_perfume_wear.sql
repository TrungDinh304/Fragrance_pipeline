-- Grain: một chai x một trục hoàn cảnh (mùa hoặc ngày/đêm).
-- `vote_pct` là % trong khối vote của trục đó (xem dim_occasion.occasion_kind).
select
    {{ surrogate_key('w.perfume_key') }}                          as perfume_sk,
    coalesce({{ surrogate_key('p.brand_nk') }}, {{ unknown_key() }}) as brand_sk,
    {{ surrogate_key('w.axis') }}                                 as occasion_sk,
    w.percent                                                     as vote_pct,
    w.votes
from {{ ref('stg_perfume_wear') }} w
left join {{ ref('stg_perfumes') }} p using (perfume_key)
where w.axis is not null
