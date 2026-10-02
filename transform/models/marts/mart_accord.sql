-- Accord nào đang phủ rộng và được chấm cao.
with total as (select count(*) as n from {{ ref('stg_perfumes') }}),
per_accord as (
    select
        a.accord,
        count(distinct a.perfume_key)              as perfumes,
        avg(a.width)                               as avg_strength,
        sum(coalesce(p.rating_count, 0))           as rating_votes
    from {{ source('silver', 'perfume_accords') }} a
    join {{ ref('stg_perfumes') }} p using (perfume_key)
    group by 1
),
weighted as (
    select a.accord,
           sum(p.rating * coalesce(p.rating_count, 0))
               / nullif(sum(coalesce(p.rating_count, 0)), 0) as vote_weighted,
           sum(coalesce(p.rating_count, 0))                  as rated_votes
    from {{ source('silver', 'perfume_accords') }} a
    join {{ ref('stg_perfumes') }} p using (perfume_key)
    where p.rating is not null and p.rating > 0
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
