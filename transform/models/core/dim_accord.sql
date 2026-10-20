-- Grain: một accord (woody, citrus, ...).
select
    {{ surrogate_key('accord') }} as accord_sk,
    accord                        as accord_name
from (select distinct accord from {{ ref('stg_perfume_accords') }}
      where accord is not null)
