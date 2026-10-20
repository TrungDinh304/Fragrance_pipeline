-- Accord nào đang phủ rộng và được chấm cao.
-- Đọc từ star schema: fct_perfume_accord x fct_perfume_rating (drill-across
-- qua perfume_sk) x dim_accord.
with total as (select count(*) as n from {{ ref('fct_perfume_rating') }}),
accord_perfume as (
    select d.accord_name as accord, a.perfume_sk, a.strength,
           p.rating, p.rating_count
    from {{ ref('fct_perfume_accord') }} a
    join {{ ref('dim_accord') }} d using (accord_sk)
    join {{ ref('fct_perfume_rating') }} p using (perfume_sk)
),
per_accord as (
    select
        accord,
        count(distinct perfume_sk)                 as perfumes,
        avg(strength)                              as avg_strength,
        sum(coalesce(rating_count, 0))             as rating_votes
    from accord_perfume
    group by 1
),
weighted as (
    select accord,
           sum(rating * coalesce(rating_count, 0))
               / nullif(sum(coalesce(rating_count, 0)), 0) as vote_weighted,
           sum(coalesce(rating_count, 0))                  as rated_votes
    from accord_perfume
    where rating is not null and rating > 0
    group by 1
)
select
    x.accord,
    x.perfumes,
    round(100.0 * x.perfumes / nullif(t.n, 0), 2) as coverage_pct,
    round(x.avg_strength, 3)                      as avg_strength,
    round(
        case when coalesce(w.rated_votes, 0) = 0 then g.overall
             else (w.rated_votes::double / (w.rated_votes + g.prior_votes))
                      * w.vote_weighted
                + (1 - w.rated_votes::double / (w.rated_votes + g.prior_votes))
                      * g.overall
        end, 3)                                   as rating_weighted,
    x.rating_votes
from per_accord x
left join weighted w using (accord)
cross join total t
cross join {{ ref('stg_global_rating') }} g
order by x.perfumes desc
