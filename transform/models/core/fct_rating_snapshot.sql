-- Grain: một chai x một lần crawl (periodic snapshot).
--
-- Đây là fact duy nhất trả lời được "rating / số vote đổi thế nào theo thời
-- gian": tầng silver `perfumes` chỉ giữ bản mới nhất. `*_delta` so với lần
-- crawl NGAY TRƯỚC của cùng chai; lần đầu thì NULL (không phải 0 -- "chưa có gì
-- để so" khác "không đổi").
with snap as (
    select
        h.perfume_key,
        h.scraped_at,
        h.rating,
        h.rating_count,
        p.brand_nk
    from {{ ref('stg_perfume_rating_history') }} h
    left join {{ ref('stg_perfumes') }} p using (perfume_key)
)
select
    {{ surrogate_key('perfume_key') }}                            as perfume_sk,
    coalesce({{ surrogate_key('brand_nk') }}, {{ unknown_key() }}) as brand_sk,
    cast(strftime(cast(left(scraped_at, 10) as date), '%Y%m%d') as integer)
                                                                  as date_sk,
    scraped_at,
    rating,
    rating_count,
    -- Làm tròn 2 chữ số như trên trang: trừ hai DOUBLE ra -0.00999... chứ
    -- không ra -0.01, và con số đó lọt nguyên vào biểu đồ.
    round(rating - lag(rating) over w, 2)                         as rating_delta,
    rating_count - lag(rating_count) over w                       as votes_delta,
    row_number() over (partition by perfume_key
                       order by scraped_at desc) = 1              as is_latest
from snap
window w as (partition by perfume_key order by scraped_at)
