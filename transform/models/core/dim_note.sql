-- Grain: một note hương. Tầng (top/middle/base) KHÔNG ở đây: cùng một note có
-- thể là top ở chai này và base ở chai khác, nên tầng là thuộc tính của từng
-- lần xuất hiện -- nằm trong fct_perfume_note.
select
    {{ surrogate_key('note') }} as note_sk,
    note                        as note_name
from (select distinct note from {{ ref('stg_perfume_notes') }}
      where note is not null)
