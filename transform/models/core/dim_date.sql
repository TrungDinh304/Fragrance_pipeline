-- Grain: một ngày, liền mạch từ lần crawl đầu tới lần crawl cuối.
--
-- Liền mạch (kể cả ngày không crawl gì) để biểu đồ theo thời gian không tự
-- nối hai điểm cách nhau ba tuần như thể chúng liền kề.
with bounds as (
    -- `scraped_at` là chuỗi ISO giờ UTC: cắt 10 ký tự đầu thay vì ép sang
    -- timestamptz, vì ép kiểu sẽ đổi ngày theo múi giờ của máy đang chạy dbt.
    select min(cast(left(scraped_at, 10) as date)) as d0,
           max(cast(left(scraped_at, 10) as date)) as d1
    from {{ ref('stg_perfume_rating_history') }}
),
days as (
    select cast(unnest(generate_series(d0, d1, interval 1 day)) as date) as date_day
    from bounds
    where d0 is not null
)
select
    cast(strftime(date_day, '%Y%m%d') as integer) as date_sk,
    date_day,
    year(date_day)                                as year,
    quarter(date_day)                             as quarter,
    month(date_day)                               as month,
    strftime(date_day, '%B')                      as month_name,
    weekofyear(date_day)                          as iso_week,
    isodow(date_day)                              as day_of_week,
    isodow(date_day) in (6, 7)                    as is_weekend
from days
