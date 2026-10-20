-- Grain của bảng fact: tổ hợp cột này phải duy nhất.
--
-- Viết lại bản nhỏ của dbt_utils.unique_combination_of_columns, cùng lý do với
-- macro surrogate_key: không thêm gói ngoài cho một câu GROUP BY.
{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
