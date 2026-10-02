-- Mốc chung để hiệu chỉnh điểm: trung bình CÓ TRỌNG SỐ theo số vote.
--
-- Không lấy trung bình thô: vài chai 5 sao / 20 vote sẽ tự tay kéo chính cái
-- mốc lên, làm việc hiệu chỉnh mất tác dụng. Một dòng duy nhất, để các mart
-- cross join vào.
select
    sum(rating * coalesce(rating_count, 0))
        / nullif(sum(coalesce(rating_count, 0)), 0) as overall,
    {{ var('prior_votes') }}                        as prior_votes
from {{ source('silver', 'perfumes') }}
where rating is not null and rating > 0
