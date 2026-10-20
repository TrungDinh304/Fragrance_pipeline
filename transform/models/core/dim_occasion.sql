-- Grain: một trục hoàn cảnh mà Fragrantica cho vote (4 mùa + ngày/đêm).
-- `kind` giữ lại vì hai nhóm là hai khối vote riêng, mỗi khối tự quy về 100%
-- của chính nó -- không được so lẫn (xem mart_accord_occasion).
select
    {{ surrogate_key('axis') }} as occasion_sk,
    axis                        as occasion,
    kind                        as occasion_kind,
    case axis when 'winter' then 1 when 'spring' then 2 when 'summer' then 3
              when 'fall' then 4 when 'day' then 5 when 'night' then 6
              else 99 end       as sort_order
from (select distinct axis, kind from {{ ref('stg_perfume_wear') }}
      where axis is not null)
