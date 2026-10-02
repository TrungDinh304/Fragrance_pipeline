-- Mỗi hãng: mục lục có bao nhiêu chai, đã crawl chi tiết được bao nhiêu.
--
-- Đây là con số giữ cho mọi mart khác khỏi bị đọc quá tay: "Lattafa 4,1 sao"
-- thật ra là kết luận về 12/391 chai của Lattafa.
--
-- `catalog_perfumes` để NULL (không phải 0) khi chưa crawl mục lục hãng đó.
-- "Chưa biết tổng" và "biết tổng, mới phủ 0%" là hai câu khác hẳn; trộn lại thì
-- bảng này nói sai về đúng những hãng chưa đụng tới.
with catalog as (
    select brand_name as brand, count(*) as catalog_perfumes
    from {{ source('silver', 'brand_perfumes') }}
    where brand_name is not null
    group by 1
),
crawled as (
    select brand, count(*) as detailed,
           sum(coalesce(rating_count, 0)) as rating_votes
    from {{ ref('stg_perfumes') }}
    where brand is not null
    group by 1
)
select
    coalesce(c.brand, d.brand)                  as brand,
    c.catalog_perfumes,
    coalesce(d.detailed, 0)                     as detailed,
    case when c.catalog_perfumes is null then null
         else round(100.0 * coalesce(d.detailed, 0) / c.catalog_perfumes, 1)
    end                                         as coverage_pct,
    coalesce(d.rating_votes, 0)                 as rating_votes
from catalog c
full outer join crawled d on c.brand = d.brand
order by c.catalog_perfumes desc nulls last, detailed desc
