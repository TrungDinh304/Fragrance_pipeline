-- Có note A thì bao nhiêu phần trăm cũng có note B.
--
-- Dùng tỉ lệ CÓ ĐIỀU KIỆN chứ không phải số đếm thô: số đếm thô chỉ phản ánh
-- note nào phổ biến, nên hàng nào có Musk cũng cao và bảng không nói thêm gì.
-- Bảng KHÔNG đối xứng, và đó là chủ ý: "có Oud thì 60% có Musk" khác hẳn
-- "có Musk thì 6% có Oud".
--
-- Đọc từ star schema: fct_perfume_note x dim_note. `distinct` bỏ tầng: cùng
-- một note ở cả top lẫn base của một chai vẫn chỉ tính là chai đó có note ấy.
with note_of as (
    select distinct n.perfume_sk as perfume_key, d.note_name as note
    from {{ ref('fct_perfume_note') }} n
    join {{ ref('dim_note') }} d using (note_sk)
),
freq as (
    select note, count(*) as perfumes from note_of group by 1
),
kept as (
    select note from freq
    where perfumes >= {{ var('min_note_perfumes') }}
),
pair as (
    select a.note as note_a, b.note as note_b, count(*) as together
    from note_of a
    join note_of b on a.perfume_key = b.perfume_key and a.note <> b.note
    where a.note in (select note from kept)
      and b.note in (select note from kept)
    group by 1, 2
)
select
    p.note_a,
    p.note_b,
    f.perfumes                                        as perfumes_with_a,
    p.together,
    round(100.0 * p.together / f.perfumes, 1)         as pct_b_given_a
from pair p
join freq f on f.note = p.note_a
order by f.perfumes desc, pct_b_given_a desc
