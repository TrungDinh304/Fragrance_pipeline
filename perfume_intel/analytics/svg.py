"""Mấy viên gạch SVG để vẽ biểu đồ, không cần thư viện nào.

Vì sao tự vẽ SVG thay vì dùng matplotlib/plotly/Chart.js:

  - Báo cáo phải MỞ ĐƯỢC KHI OFFLINE. Nhúng link CDN thì mở lại sau nửa năm,
    hoặc mở trên máy không mạng, là trang trắng.
  - Không thêm phụ thuộc. Cả project hiện chỉ cần requests + bs4 + lxml; kéo
    matplotlib vào chỉ để vẽ sáu cái hình là đổi hẳn chi phí cài đặt.
  - TẤT ĐỊNH nên test được. Cùng dữ liệu vào thì ra đúng một chuỗi SVG, nên
    test khẳng định được từng hình chứ không chỉ "có chạy không lỗi".

MÀU NẰM TRONG CSS, KHÔNG NẰM TRONG SVG
Mọi chỗ tô màu đều trỏ tới biến CSS (`var(--series-1)`, `var(--seq-7)`), không
ghi mã hex trực tiếp. Nhờ vậy một bản SVG duy nhất chạy được cả nền sáng lẫn
nền tối — đổi theme chỉ là đổi giá trị biến, không phải vẽ lại hình.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

# --- Bảng màu -------------------------------------------------------------
# Lấy nguyên từ bảng màu tham chiếu đã được kiểm định (xem README mục biểu đồ).
# Cột sáng / cột tối là HAI BƯỚC MÀU ĐƯỢC CHỌN cho hai mặt nền khác nhau, không
# phải phép đảo tự động.
TOKENS: dict[str, tuple[str, str]] = {
    "surface": ("#fcfcfb", "#1a1a19"),
    "plane": ("#f9f9f7", "#0d0d0d"),
    "ink": ("#0b0b0b", "#ffffff"),
    "ink-2": ("#52514e", "#c3c2b7"),
    "muted": ("#898781", "#898781"),
    "grid": ("#e1e0d9", "#2c2c2a"),
    "axis": ("#c3c2b7", "#383835"),
    "border": ("rgba(11,11,11,0.10)", "rgba(255,255,255,0.10)"),
    # Ba slot phân loại đầu tiên — bộ ba này đã qua kiểm định cho MỌI cặp (không
    # chỉ cặp kề nhau), nên dùng được cả cho scatter lẫn cột xếp chồng. Slot thứ
    # tư đặt vàng cạnh cam và trượt ngưỡng, nên ở đây dừng ở ba.
    "series-1": ("#2a78d6", "#3987e5"),
    "series-2": ("#eb6834", "#d95926"),
    "series-3": ("#1baf7a", "#199e70"),
    # Cặp đối cực cho thang lưỡng hướng: lạnh <-> nóng, giữa là xám trung tính.
    # Xám ở giữa là bắt buộc — đặt một màu có sắc ở giữa thì "không nghiêng bên
    # nào" lại trông như một trạng thái riêng.
    "div-neg-2": ("#d03b3b", "#e66767"),
    "div-neg-1": ("#ec835a", "#e8a07f"),
    # Xám giữa ĐẬM HƠN mức "gần như vô hình" thường dùng cho mốc 0. Ở đây mốc
    # giữa không phải số 0 mà là một HẠNG MỤC THẬT ("lưu hương vừa"), và nó lại
    # là hạng mục đông nhất — tô bằng xám sát màu nền thì đoạn to nhất của biểu
    # đồ trông như chỗ trống. Đã nhìn bản dựng rồi sửa.
    "div-mid": ("#c3c2b7", "#52514e"),
    "div-pos-1": ("#86b6ef", "#256abf"),
    "div-pos-2": ("#2a78d6", "#3987e5"),
}

# Thang lưỡng hướng 5 bậc, từ "kém trung bình nhiều" tới "hơn trung bình nhiều".
DIVERGING = ["var(--div-neg-2)", "var(--div-neg-1)", "var(--div-mid)",
             "var(--div-pos-1)", "var(--div-pos-2)"]

# Thang một sắc (xanh) cho độ lớn liên tục: nhạt = gần 0.
SEQ_LIGHT = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
             "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281",
             "#0d366b"]
# Trên nền tối thì ĐẢO thang lại, không phải vì tiện mà vì đúng lý do gốc của
# thang một sắc: bước ứng với "gần 0" phải lùi về phía mặt nền. Nền sáng thì
# bước nhạt lùi về nền; nền tối thì bước đậm mới lùi về nền.
SEQ_DARK = list(reversed(SEQ_LIGHT))
SEQ_STEPS = len(SEQ_LIGHT)


def esc(value: object) -> str:
    """Thoát ký tự cho nội dung/thuộc tính XML.

    Tên hãng và tên note là dữ liệu từ web: đã gặp `Viktor & Rolf`,
    `Victoria's Secret`. Không thoát thì một dấu `&` làm cả file SVG thành
    XML không hợp lệ và browser bỏ hiển thị từ đó trở đi.
    """
    return (str(value)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _attrs(extra: dict[str, object]) -> str:
    out = []
    for key, value in extra.items():
        if value is None:
            continue
        out.append(f'{key.replace("_", "-")}="{esc(value)}"')
    return (" " + " ".join(out)) if out else ""


def _titled(body: str, title: str | None) -> str:
    """Bọc một mark kèm <title> — đó là tooltip native của SVG.

    Dùng <title> thay vì tự viết tooltip bằng JS: browser tự hiện khi hover, và
    trình đọc màn hình cũng đọc được. Không JS nghĩa là không có gì để hỏng.
    """
    if not title:
        return body
    return f"<g><title>{esc(title)}</title>{body}</g>"


def rect(x: float, y: float, w: float, h: float, fill: str,
         rx: float = 0, title: str | None = None, **extra) -> str:
    body = (f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(w, 0):.2f}" '
            f'height="{max(h, 0):.2f}" fill="{fill}"'
            + (f' rx="{rx:.2f}"' if rx else "") + _attrs(extra) + "/>")
    return _titled(body, title)


def circle(cx: float, cy: float, r: float, fill: str,
           title: str | None = None, **extra) -> str:
    body = (f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
            f'fill="{fill}"' + _attrs(extra) + "/>")
    return _titled(body, title)


def line(x1: float, y1: float, x2: float, y2: float,
         cls: str = "grid", **extra) -> str:
    return (f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'class="{cls}"' + _attrs(extra) + "/>")


def text(x: float, y: float, value: object, cls: str = "lbl",
         anchor: str = "start", **extra) -> str:
    return (f'<text x="{x:.2f}" y="{y:.2f}" class="{cls}" '
            f'text-anchor="{anchor}"' + _attrs(extra) + f">{esc(value)}</text>")


def svg(width: float, height: float, body: str, label: str = "") -> str:
    """Khung ngoài. `role="img"` + aria-label để trình đọc màn hình có gì đọc."""
    return (f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" '
            f'height="auto" role="img" aria-label="{esc(label)}" '
            f'preserveAspectRatio="xMidYMid meet">{body}</svg>')


# --- Thang đo -------------------------------------------------------------
class Linear:
    def __init__(self, lo: float, hi: float, a: float, b: float) -> None:
        # lo == hi (mọi giá trị bằng nhau) thì chia cho 0; đẩy ra một chút để
        # mọi mark nằm giữa thay vì biến mất.
        if hi == lo:
            hi = lo + 1.0
        self.lo, self.hi, self.a, self.b = lo, hi, a, b

    def __call__(self, value: float) -> float:
        t = (value - self.lo) / (self.hi - self.lo)
        return self.a + t * (self.b - self.a)


class Log10:
    """Thang log cho số vote: dải thật là 1 tới ~32.000.

    Thang thẳng sẽ dồn 90% số chai vào 5% chiều ngang bên trái — hình vẫn vẽ ra
    nhưng không đọc được gì.
    """

    def __init__(self, lo: float, hi: float, a: float, b: float) -> None:
        self.lin = Linear(math.log10(max(lo, 1)), math.log10(max(hi, 10)), a, b)

    def __call__(self, value: float) -> float:
        return self.lin(math.log10(max(value, 1)))


def seq_token(value: float, lo: float, hi: float) -> str:
    """Giá trị -> biến CSS của một bước trong thang một sắc."""
    if hi <= lo:
        return "var(--seq-0)"
    t = (value - lo) / (hi - lo)
    step = min(SEQ_STEPS - 1, max(0, round(t * (SEQ_STEPS - 1))))
    return f"var(--seq-{step})"


def div_token(value: float, mean: float, sigma: float,
              floor: float = 0.0) -> str:
    """Lệch bao nhiêu so với trung bình -> một trong 5 bậc lưỡng hướng.

    Dùng độ lệch chuẩn của chính cột đó làm thước, không dùng ngưỡng cố định:
    mỗi cột có dải rộng hẹp khác nhau, ngưỡng cứng sẽ làm cột hẹp thành một màu
    phẳng lì.

    `floor` là MỨC SÀN của thước, tính bằng đơn vị của chính dữ liệu, và nó cần
    thiết: thước theo sigma luôn tiêu hết dải màu, nên một cột mà mọi giá trị
    chỉ chênh nhau 1-2 điểm phần trăm vẫn bị tô từ đỏ đậm sang xanh đậm — người
    đọc kết luận "accord này hợp mùa đông hơn hẳn" trong khi chênh lệch thật
    không đáng kể. Có sàn thì chênh lệch nhỏ ra màu trung tính, đúng như nó là.
    Đo trên dữ liệu thật: sigma mỗi cột rơi vào 5,9-10,4 điểm, nên sàn 3 điểm
    không làm phẳng tín hiệu thật.
    """
    scale = max(sigma, floor)
    if scale <= 0:
        return DIVERGING[2]
    z = (value - mean) / scale
    if z <= -1.0:
        return DIVERGING[0]
    if z <= -0.33:
        return DIVERGING[1]
    if z < 0.33:
        return DIVERGING[2]
    if z < 1.0:
        return DIVERGING[3]
    return DIVERGING[4]


def mean_sigma(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return mean, math.sqrt(var)


def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    """Mốc trục "tròn" (1, 2, 2.5, 5, 10 × 10^n)."""
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / max(count, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mag * mult:
            step = mag * mult
            break
    else:
        step = mag * 10
    start = math.ceil(lo / step) * step
    out: list[float] = []
    value = start
    while value <= hi + step * 1e-9:
        out.append(round(value, 10))
        value += step
    return out


def log_ticks(lo: float, hi: float) -> list[float]:
    """Mốc cho trục log: 1, 2, 5 × 10^n nằm TRONG dải.

    Không dùng riêng luỹ thừa 10: dải thật của số vote là 24.000-250.000, chưa
    tới hai bậc, nên chỉ có đúng một luỹ thừa 10 rơi vào trong — trục còn mỗi
    một mốc và thành vô dụng. Đã gặp đúng như vậy.
    """
    lo, hi = max(lo, 1), max(hi, 10)
    out: list[float] = []
    for e in range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1):
        for mult in (1, 2, 5):
            value = mult * 10 ** e
            if lo <= value <= hi:
                out.append(float(value))
    # Dải quá hẹp đến mức không mốc tròn nào lọt vào: lấy hai đầu cho đỡ trống.
    return out or [lo, hi]


def compact(value: float) -> str:
    """1234 -> '1,2k'. Dấu phẩy thập phân cho khớp cách đọc tiếng Việt."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}tr".replace(".", ",")
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k".replace(".", ",")
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def thousands(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}".replace(",", ".")


def decimal(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


def spread_labels(positions: Sequence[float], every: int) -> Iterable[int]:
    """Chỉ số của những nhãn được in, để trục không chồng chữ lên nhau."""
    return range(0, len(positions), max(every, 1))
