-- Surrogate key cho bảng dim: md5 của khoá tự nhiên.
--
-- Tự viết thay vì kéo dbt_utils: một gói ngoài chỉ để băm một cột, và
-- `dbt deps` cần mạng -- container chạy dbt trong DAG không nên phụ thuộc vào đó.
-- Băm (không đánh số tăng dần) để khoá TẤT ĐỊNH: dựng lại kho từ đầu thì mọi
-- khoá vẫn y nguyên, fact và dim không bao giờ lệch nhau giữa hai lần build.
{% macro surrogate_key(expr) -%}
    md5(cast({{ expr }} as varchar))
{%- endmacro %}

-- Khoá của thành viên "không rõ": fact nào không nối được dim thì trỏ vào đây
-- thay vì để NULL, nên INNER JOIN fact-dim không âm thầm rơi dòng.
{% macro unknown_key() -%}
    '-1'
{%- endmacro %}
