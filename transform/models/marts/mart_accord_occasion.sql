-- Accord nào nghiêng về hoàn cảnh nào, SO VỚI TRUNG BÌNH CỦA CÙNG TRỤC.
--
-- Vì sao phải so theo trục chứ không dùng số thô: "mùa" và "ngày/đêm" là hai
-- khối vote riêng trên Fragrantica, mỗi khối tự quy về 100% của chính nó. Đem
-- hai thang khác mẫu số lên cùng một bảng thì trục "day" cao đều ở mọi accord —
-- trông như một phát hiện, thực ra chỉ là mẫu số khác.
with cell as (
    select a.accord, w.axis, w.kind, avg(w.percent) as pct,
           count(distinct a.perfume_key) as perfumes
    from {{ source('silver', 'perfume_accords') }} a
    join {{ source('silver', 'perfume_wear') }} w using (perfume_key)
    where w.percent is not null
    group by 1, 2, 3
),
per_axis as (
    select axis, avg(pct) as axis_mean, stddev_pop(pct) as axis_sd
    from cell group by 1
)
select
    c.accord,
    c.axis,
    c.kind,
    c.perfumes,
    round(c.pct, 1)                                   as pct_vote,
    round(c.pct - a.axis_mean, 1)                     as delta_vs_axis,
    -- Sàn 3 điểm: thước theo độ lệch chuẩn luôn tiêu hết dải, nên một trục mà
    -- mọi accord chỉ chênh 1-2 điểm vẫn ra "lệch hẳn". Xem analytics/svg.py.
    round((c.pct - a.axis_mean) / greatest(a.axis_sd, 3.0), 2) as z_score
from cell c
join per_axis a using (axis)
order by c.accord, c.axis
