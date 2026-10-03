-- Quy mô và mức độ được chú ý của từng hãng.
--
-- `rating_weighted` là điểm đã hiệu chỉnh kiểu Bayes: nhóm càng ít vote càng bị
-- kéo về mốc chung. Nếu không có bước này thì một hãng 5 sao / 3 vote đứng trên
-- Chanel, và bảng xếp hạng thành vô dụng.
--
-- Công thức phải khớp ĐÚNG `analytics/metrics.py:_weighted_rating` — phía Python
-- là bộ đối chứng, có test so từng dòng (tests/test_marts.py).
with rated as (
    select brand, rating, coalesce(rating_count, 0) as votes
    from {{ ref('stg_perfumes') }}
    where brand is not null and rating is not null and rating > 0
),
per_brand as (
    select
        brand,
        count(*)                                           as perfumes,
        sum(coalesce(rating_count, 0))                     as rating_votes,
        avg(rating)                                        as rating_avg
    from {{ ref('stg_perfumes') }}
    where brand is not null
    group by 1
),
weighted as (
    select
        brand,
        sum(rating * votes) / nullif(sum(votes), 0) as vote_weighted,
        sum(votes)                                  as rated_votes
    from rated
    group by 1
)
select
    b.brand,
    b.perfumes,
    b.rating_votes,
    round(100.0 * b.rating_votes
          / nullif(sum(b.rating_votes) over (), 0), 2)      as attention_share_pct,
    round(b.rating_avg, 3)                                  as rating_avg,
    round(
        case
            when coalesce(w.rated_votes, 0) = 0 then g.overall
            else (w.rated_votes::double / (w.rated_votes + g.prior_votes))
                     * w.vote_weighted
               + (1 - w.rated_votes::double / (w.rated_votes + g.prior_votes))
                     * g.overall
        end, 3)                                             as rating_weighted
from per_brand b
left join weighted w using (brand)
cross join {{ ref('stg_global_rating') }} g
order by b.rating_votes desc
