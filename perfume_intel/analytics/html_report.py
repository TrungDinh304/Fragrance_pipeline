"""Ghép các hình thành một file HTML tự chứa.

"Tự chứa" theo nghĩa chặt: không link CDN, không file kèm, không JS. Mở được
bằng double-click, kể cả khi máy không có mạng, kể cả nửa năm sau.

Ba thứ bắt buộc có ở mỗi hình, không phải để cho đẹp:

  1. **Bảng số đi kèm** (trong thẻ `<details>` gập lại được). Thang màu liên tục
     không bao giờ được là cách duy nhất đọc được một giá trị — người không
     phân biệt được màu, hoặc in ra giấy đen trắng, vẫn phải lấy được số.
  2. **Chú giải khi có từ 2 nhóm trở lên.** Danh tính không bao giờ chỉ dựa vào
     màu.
  3. **Câu “cách đọc”.** Một heatmap không tự nói nó đang đo gì.

Nền tối là MỘT BỘ BƯỚC MÀU ĐƯỢC CHỌN, không phải phép đảo sáng-tối tự động, và
được khai ở cả hai nơi: theo thiết lập hệ điều hành
(`prefers-color-scheme`) và theo lựa chọn ghim trên thẻ `<html data-theme>`.
"""

from __future__ import annotations

from datetime import datetime

from . import svg
from .charts import Figure

FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'


def _vars(mode: int) -> str:
    """Khối biến CSS cho một chế độ. mode 0 = sáng, 1 = tối."""
    lines = [f"  --{name}: {pair[mode]};" for name, pair in svg.TOKENS.items()]
    ramp = svg.SEQ_LIGHT if mode == 0 else svg.SEQ_DARK
    lines += [f"  --seq-{i}: {hexa};" for i, hexa in enumerate(ramp)]
    return "\n".join(lines)


def _css() -> str:
    return f"""
:root {{
  color-scheme: light;
{_vars(0)}
}}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) {{
    color-scheme: dark;
{_vars(1)}
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
{_vars(1)}
}}

* {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; }}
body {{
  margin: 0; padding: 24px 16px 64px;
  background: var(--plane); color: var(--ink);
  font-family: {FONT}; font-size: 15px; line-height: 1.55;
}}
.wrap {{ max-width: 860px; margin: 0 auto; }}
h1 {{ font-size: 26px; line-height: 1.25; margin: 0 0 4px; }}
h2 {{ font-size: 18px; margin: 0 0 2px; }}
.sub {{ color: var(--ink-2); margin: 0 0 28px; }}

.kpis {{
  display: grid; gap: 10px; margin: 0 0 28px;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
}}
.kpi {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px;
}}
.kpi .k {{ color: var(--ink-2); font-size: 12px; text-transform: uppercase;
          letter-spacing: .04em; }}
/* Số lớn dùng chữ số tỉ lệ (không tabular): ở cỡ to, chữ số đều nhau làm
   "121" trông rời rạc. tabular-nums chỉ dành cho cột số trong bảng. */
.kpi .v {{ font-size: 26px; font-weight: 650; margin: 2px 0 0; }}
.kpi .h {{ color: var(--ink-2); font-size: 12px; }}

.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 18px 18px 14px; margin: 0 0 20px;
}}
.how {{ color: var(--ink-2); font-size: 13.5px; margin: 6px 0 14px; }}
.note {{ color: var(--ink-2); font-size: 12.5px; margin: 10px 0 0;
        padding-top: 10px; border-top: 1px solid var(--border); }}
.empty {{
  color: var(--ink-2); font-size: 13.5px; white-space: pre-line;
  background: var(--plane); border: 1px dashed var(--axis);
  border-radius: 8px; padding: 14px;
}}

.legend {{ display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 0 0 10px; }}
.legend span {{ display: inline-flex; align-items: center; gap: 6px;
               font-size: 12.5px; color: var(--ink-2); }}
.legend i {{ width: 12px; height: 12px; border-radius: 3px; flex: none; }}

.scale {{ display: flex; align-items: center; gap: 8px; margin: 12px 0 0;
         font-size: 12px; color: var(--ink-2); }}
.scale .bar {{ flex: 1; height: 8px; border-radius: 4px;
  background: linear-gradient(to right, {", ".join(
      f"var(--seq-{i})" for i in range(svg.SEQ_STEPS))}); }}

/* Trục và lưới: nét mảnh, liền, lùi về sau. Không dùng nét đứt — nét đứt đọc
   thành "ngưỡng" hoặc "dự báo" trong khi nó chỉ là lưới. */
svg .grid {{ stroke: var(--grid); stroke-width: 1; }}
svg .axis {{ stroke: var(--axis); stroke-width: 1; }}
svg .lbl {{ fill: var(--ink-2); font-size: 11.5px; }}
svg .tick {{ fill: var(--muted); font-size: 11px;
            font-variant-numeric: tabular-nums; }}
svg .mark-lbl {{ fill: var(--ink); font-size: 11px; font-weight: 600; }}
svg .axis-title {{ fill: var(--muted); font-size: 11px; }}
svg text {{ font-family: {FONT}; }}

details {{ margin: 12px 0 0; }}
summary {{ cursor: pointer; color: var(--ink-2); font-size: 12.5px;
          padding: 4px 0; }}
.tw {{ overflow-x: auto; margin-top: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; }}
th, td {{ padding: 5px 9px; text-align: right; white-space: nowrap;
         border-bottom: 1px solid var(--border); }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ color: var(--ink-2); font-weight: 600; position: sticky; top: 0;
     background: var(--surface); }}
td {{ font-variant-numeric: tabular-nums; }}

footer {{ color: var(--ink-2); font-size: 12.5px; margin-top: 28px; }}
@media print {{
  body {{ background: #fff; }}
  .card {{ break-inside: avoid; }}
  details {{ display: none; }}
}}
"""


def _legend(figure: Figure) -> str:
    if len(figure.legend) < 2:          # một nhóm thì tiêu đề đã nói rõ là gì
        return ""
    items = "".join(
        f'<span><i style="background:{token}"></i>{svg.esc(label)}</span>'
        for label, token in figure.legend)
    return f'<div class="legend">{items}</div>'


def _scale(figure: Figure) -> str:
    if not figure.scale:
        return ""
    lo, hi = figure.scale
    return (f'<div class="scale"><span>{svg.esc(lo)}</span>'
            f'<span class="bar"></span><span>{svg.esc(hi)}</span></div>')


def _table(figure: Figure) -> str:
    if not figure.rows:
        return ""
    head = "".join(f"<th>{svg.esc(c)}</th>" for c in figure.columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{svg.esc(c)}</td>" for c in row) + "</tr>"
        for row in figure.rows)
    return (f'<details><summary>Xem bảng số ({len(figure.rows)} dòng)</summary>'
            f'<div class="tw"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div></details>")


def _card(figure: Figure) -> str:
    out = [f'<section class="card"><h2>{svg.esc(figure.title)}</h2>']
    if figure.empty:
        out.append(f'<div class="empty">{svg.esc(figure.empty)}</div>')
        out.append("</section>")
        return "".join(out)
    if figure.how:
        out.append(f'<p class="how">{svg.esc(figure.how)}</p>')
    out.append(_legend(figure))
    out.append(figure.svg)
    out.append(_scale(figure))
    out.append(_table(figure))
    if figure.note:
        out.append(f'<p class="note">{svg.esc(figure.note)}</p>')
    out.append("</section>")
    return "".join(out)


def render(figures: list[Figure], kpis: list[tuple[str, str, str]],
           when: datetime | None = None, source: str = "") -> str:
    when = when or datetime.now()
    tiles = "".join(
        f'<div class="kpi"><div class="k">{svg.esc(k)}</div>'
        f'<p class="v">{svg.esc(v)}</p><div class="h">{svg.esc(h)}</div></div>'
        for k, v, h in kpis)
    cards = "".join(_card(f) for f in figures)
    skipped = [f.title for f in figures if f.empty]

    foot = (f"Sinh lúc {when.strftime('%d/%m/%Y %H:%M')}"
            + (f" từ {svg.esc(source)}" if source else "")
            + ". Mọi hình đều có bảng số đi kèm; không có hình nào cần mạng để "
              "mở.")
    if skipped:
        foot += (" Hình để trống vì thiếu dữ liệu: "
                 + ", ".join(svg.esc(t) for t in skipped) + ".")

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phân tích thị trường nước hoa</title>
<style>{_css()}</style>
</head>
<body>
<div class="wrap">
<h1>Phân tích thị trường nước hoa</h1>
<p class="sub">Tín hiệu cộng đồng từ Fragrantica — accord, note, hoàn cảnh sử
dụng, độ lưu hương.</p>
<div class="kpis">{tiles}</div>
{cards}
<footer>{foot}</footer>
</div>
</body>
</html>
"""
