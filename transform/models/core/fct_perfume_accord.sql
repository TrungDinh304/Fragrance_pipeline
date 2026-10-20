-- Grain: một chai x một accord.
-- `strength` là độ rộng thanh accord trên trang (0-100): độ mạnh TƯƠNG ĐỐI của
-- accord đó trong chính chai này, không so được giữa hai chai khác nhau.
select
    {{ surrogate_key('a.perfume_key') }}                          as perfume_sk,
    coalesce({{ surrogate_key('p.brand_nk') }}, {{ unknown_key() }}) as brand_sk,
    {{ surrogate_key('a.accord') }}                               as accord_sk,
    a.rank                                                        as accord_rank,
    a.width                                                       as strength,
    a.opacity
from {{ ref('stg_perfume_accords') }} a
left join {{ ref('stg_perfumes') }} p using (perfume_key)
where a.accord is not null
