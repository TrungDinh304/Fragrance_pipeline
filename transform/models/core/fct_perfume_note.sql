-- Grain: một chai x một tầng x một note.
-- `layer` (top/middle/base/general) là degenerate dimension: chỉ có 4 giá trị
-- và không có thuộc tính nào khác, tách thành dim riêng không thêm được gì.
select
    {{ surrogate_key('n.perfume_key') }}                          as perfume_sk,
    coalesce({{ surrogate_key('p.brand_nk') }}, {{ unknown_key() }}) as brand_sk,
    {{ surrogate_key('n.note') }}                                 as note_sk,
    n.layer,
    n.position
from {{ ref('stg_perfume_notes') }} n
left join {{ ref('stg_perfumes') }} p using (perfume_key)
where n.note is not null
