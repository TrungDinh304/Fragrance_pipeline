-- Thị phần chú ý của mọi hãng cộng lại phải ra 100% (sai số làm tròn 0,5).
-- Không ra nghĩa là mẫu số bị tính nhầm trên một tập con.
select sum(attention_share_pct) as total
from {{ ref('mart_brand') }}
having abs(sum(attention_share_pct) - 100) > 0.5
