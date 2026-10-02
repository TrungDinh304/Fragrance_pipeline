-- "Chưa biết tổng" phải nhất quán: catalog_perfumes NULL thì coverage_pct cũng
-- phải NULL, và ngược lại. Lệch một bên là bảng đang nói "phủ 0%" cho hãng mà
-- ta chưa hề biết nó có bao nhiêu chai.
select brand, catalog_perfumes, coverage_pct
from {{ ref('mart_coverage') }}
where (catalog_perfumes is null) <> (coverage_pct is null)
