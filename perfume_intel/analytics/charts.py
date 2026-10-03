"""Sáu hình cho báo cáo HTML. Mỗi hàm thuần: `list[Row]` -> `Figure`.

Dạng hình được chọn theo VIỆC mà người đọc phải làm, không theo thứ gì trông
đẹp:

| Việc | Dạng | Màu |
|---|---|---|
| So độ lớn trên một lưới | heatmap | thang một sắc (xanh) |
| Vị trí trên hai trục | scatter | một sắc, nhãn chọn lọc |
| Chia tỉ lệ trên thang CÓ THỨ TỰ (yếu -> rất lâu) | cột xếp chồng lưỡng hướng | hai cực + xám giữa |
| Phân biệt 3 nhóm qua thời gian | cột xếp chồng | 3 slot phân loại |

Hai nguyên tắc được giữ xuyên suốt, vì bỏ là hình nói sai:

1. **Không ghi số lên từng mark.** Giá trị chính xác nằm ở bảng đi kèm và ở
   tooltip; nhồi số vào từng ô heatmap thì vừa rối vừa phải tự xử lý tương phản
   chữ-trên-nền cho từng bước màu.
2. **Hình nào cũng có bảng số đi kèm.** Thang màu liên tục không bao giờ được là
   cách duy nhất để đọc một giá trị.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from . import svg
from .dataset import DAY_NIGHT, SEASONS, Row
from .metrics import _global_mean, _weighted_rating

W = 760                      # chiều ngang mọi hình, để các card thẳng hàng

SEASON_VI = {"winter": "Đông", "spring": "Xuân", "summer": "Hè",
             "fall": "Thu", "day": "Ngày", "night": "Đêm"}

# Thang thứ tự của độ lưu hương, từ yếu tới mạnh. "moderate" là mốc giữa.
# Chênh lệch dưới ngần này (điểm phần trăm) thì coi như không đáng kể, tô trung
# tính. Xem `svg.div_token`: thiếu sàn thì thước theo sigma khuếch đại cả nhiễu.
OCCASION_MIN_SPREAD = 3.0

LONGEVITY_ORDER = ["very weak", "weak", "moderate", "long lasting", "eternal"]
LONGEVITY_VI = {"very weak": "rất yếu", "weak": "yếu", "moderate": "vừa",
                "long lasting": "lâu", "eternal": "rất lâu"}
# Hai cực + xám ở giữa. Thứ tự khớp LONGEVITY_ORDER.
LONGEVITY_TOKEN = ["var(--div-neg-2)", "var(--div-neg-1)", "var(--div-mid)",
                   "var(--div-pos-1)", "var(--div-pos-2)"]

# Màu gán CỐ ĐỊNH theo nhóm, không theo thứ hạng. Lọc bớt một nhóm thì hai nhóm
# còn lại phải giữ nguyên màu, nếu không người đã nhớ "Nam màu xanh" bị dẫn sai.
GENDER_TOKEN = {"Nam": "var(--series-1)", "Nữ": "var(--series-2)",
                "Unisex": "var(--series-3)"}
GENDER_ORDER = ["Nam", "Nữ", "Unisex"]


@dataclass
class Figure:
    key: str
    title: str
    how: str                                     # cách đọc hình
    svg: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    legend: list[tuple[str, str]] = field(default_factory=list)
    scale: tuple[str, str] | None = None         # (nhãn thấp, nhãn cao)
    note: str = ""                               # giới hạn của dữ liệu
    empty: str = ""                              # có = không vẽ được, kèm lý do


# ----------------------------------------------------------------- số tổng quan
def headline(rows: list[Row], catalog=None) -> list[tuple[str, str, str]]:
    """Mấy con số mở đầu. Dạng thẻ số, không phải biểu đồ một cột."""
    accords = {a for r in rows for a in r.accords}
    notes = {n for r in rows for n in r.notes}
    votes = sum(r.rating_count or 0 for r in rows)
    tiles = [
        ("Chai có chi tiết", svg.thousands(len(rows)), "đã crawl xong"),
        ("Hãng", svg.thousands(len({r.brand for r in rows if r.brand})),
         "có ít nhất 1 chai"),
        ("Lượt vote", svg.compact(votes), "tổng tín hiệu cộng đồng"),
        ("Accord / note", f"{len(accords)} / {len(notes)}", "từ vựng mùi"),
    ]
    # Độ phủ là con số khiêm tốn nhất ở đây, và vì vậy là con số cần nhất: nó
    # nói mọi hình bên dưới đang đại diện cho bao nhiêu phần của các hãng ấy.
    if catalog is not None and not catalog.empty:
        total = sum(len(v) for v in catalog.products.values())
        done = sum(1 for r in rows
                   if catalog.total_for(catalog.url_of(r.brand)))
        tiles.append((
            "Độ phủ", f"{100 * done / total:.1f}%" if total else "—",
            f"{svg.thousands(done)}/{svg.thousands(total)} chai của "
            f"{svg.thousands(len(catalog.products))} hãng đã có mục lục"))
    return tiles


# ------------------------------------------------------- 1. accord x hoàn cảnh
def accord_season(rows: list[Row], top: int = 14) -> Figure:
    axes = [*SEASONS, *DAY_NIGHT]
    voted = [r for r in rows if r.seasons or r.day_night]
    if not voted:
        return Figure("accord_season", "Accord nào cho hoàn cảnh nào",
                      "", empty="Chưa có chai nào có dữ liệu when-to-wear. "
                                "Crawl bằng --render mới lấy được khối này.")

    counts = Counter(a for r in voted for a in r.accords)
    names = [a for a, _ in counts.most_common(top)]
    if not names:
        return Figure("accord_season", "Accord nào cho hoàn cảnh nào", "",
                      empty="Chưa có chai nào có accord.")

    # Ô = % vote trung bình của trục đó, trên những chai mang accord đó.
    grid: dict[str, dict[str, float]] = {}
    for accord in names:
        group = [r for r in voted if accord in r.accords]
        cell: dict[str, float] = {}
        for axis in axes:
            seasonal = axis in SEASONS
            vals = [value for value in
                    ((row.seasons if seasonal else row.day_night).get(axis)
                     for row in group)
                    if value is not None]
            cell[axis] = round(sum(vals) / len(vals), 1) if vals else 0.0
        grid[accord] = cell

    # Tô màu theo ĐỘ LỆCH SO VỚI TRUNG BÌNH CỦA CHÍNH CỘT ĐÓ, không theo giá trị
    # thô. Lý do là một lỗi đọc số thật: "mùa" và "ngày/đêm" là HAI khối vote
    # riêng trên trang, mỗi khối tự quy về 100% theo cột cao nhất CỦA NÓ. Đem
    # hai thang khác mẫu số ấy lên cùng một dải màu thì cột "Ngày" đậm đều từ
    # trên xuống dưới — trông như một phát hiện, thực ra chỉ là mẫu số khác.
    # So trong từng cột thì mẫu số triệt tiêu, và câu trả lời ("accord này
    # nghiêng về hoàn cảnh này hơn hay kém các accord khác") mới là câu cần.
    stats = {axis: svg.mean_sigma([grid[a][axis] for a in names])
             for axis in axes}

    pad_l, pad_t, pad_r, pad_b = 136, 34, 12, 26
    cw = (W - pad_l - pad_r) / len(axes)
    ch = 26
    height = pad_t + ch * len(names) + pad_b

    parts: list[str] = []
    for j, axis in enumerate(axes):
        parts.append(svg.text(pad_l + cw * (j + 0.5), pad_t - 12,
                              SEASON_VI[axis], cls="lbl", anchor="middle"))
    for i, accord in enumerate(names):
        y = pad_t + i * ch
        parts.append(svg.text(pad_l - 10, y + ch / 2 + 4, accord,
                              cls="lbl", anchor="end"))
        for j, axis in enumerate(axes):
            value = grid[accord][axis]
            mean, sigma = stats[axis]
            delta = value - mean
            # Chừa 2px làm khe giữa các ô: tách bằng khoảng trống của mặt nền,
            # không viền quanh từng ô.
            parts.append(svg.rect(
                pad_l + j * cw + 1, y + 1, cw - 2, ch - 2,
                svg.div_token(value, mean, sigma, OCCASION_MIN_SPREAD), rx=3,
                title=f"{accord} · {SEASON_VI[axis]}: {svg.decimal(value, 1)}% "
                      f"vote ({'+' if delta >= 0 else ''}"
                      f"{svg.decimal(delta, 1)} so với trung bình cột)"))

    table = [[a] + [svg.decimal(grid[a][x], 1) for x in axes] for a in names]
    return Figure(
        key="accord_season",
        title="Accord nào cho hoàn cảnh nào",
        how="Mỗi ô so accord ở hàng với TRUNG BÌNH CỦA CỘT đó: xanh = cộng đồng "
            "gắn accord này với hoàn cảnh đó nhiều hơn mức chung, đỏ = ít hơn, "
            "xám = ngang mức chung. So theo cột vì “mùa” và “ngày/đêm” là hai "
            "khối vote riêng, mỗi khối tự quy về 100% của nó nên số thô giữa "
            "hai khối không so thẳng được. Hover để xem % thật.",
        svg=svg.svg(W, height, "".join(parts),
                    "Bản đồ nhiệt accord theo mùa và ngày/đêm"),
        columns=["Accord"] + [SEASON_VI[a] for a in axes],
        rows=table,
        legend=[("kém nhiều", svg.DIVERGING[0]), ("kém", svg.DIVERGING[1]),
                ("ngang mức chung", svg.DIVERGING[2]),
                ("hơn", svg.DIVERGING[3]), ("hơn nhiều", svg.DIVERGING[4])],
        note=f"Tính trên {svg.thousands(len(voted))} chai có dữ liệu "
             f"when-to-wear, {top} accord phổ biến nhất. Bảng số bên dưới là "
             f"% vote THÔ, chưa trừ trung bình cột.",
    )


# --------------------------------------------------------------- 2. định vị hãng
def brand_map(rows: list[Row], top: int = 40, labelled: int = 6) -> Figure:
    overall = _global_mean(rows)
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if row.brand:
            groups[row.brand].append(row)
    items = []
    for brand, group in groups.items():
        votes = sum(r.rating_count or 0 for r in group)
        score = _weighted_rating(group, overall)
        if votes <= 0 or score is None:
            continue
        items.append((brand, votes, score, len(group)))
    if not items:
        return Figure("brand_map", "Định vị hãng", "",
                      empty="Chưa có hãng nào vừa có vote vừa có điểm.")

    items.sort(key=lambda it: it[1], reverse=True)
    items = items[:top]

    xs = [it[1] for it in items]
    ys = [it[2] for it in items]
    pad_l, pad_t, pad_r, pad_b = 48, 18, 16, 44
    height = 330
    x = svg.Log10(min(xs), max(xs), pad_l, W - pad_r)
    lo_y, hi_y = min(ys), max(ys)
    span = max(hi_y - lo_y, 0.2)
    y = svg.Linear(lo_y - span * 0.15, hi_y + span * 0.15,
                   height - pad_b, pad_t)

    parts: list[str] = []
    for tick in svg.nice_ticks(lo_y - span * 0.15, hi_y + span * 0.15, 4):
        ty = y(tick)
        parts.append(svg.line(pad_l, ty, W - pad_r, ty))
        parts.append(svg.text(pad_l - 8, ty + 4, svg.decimal(tick, 1),
                              cls="tick", anchor="end"))
    parts.append(svg.line(pad_l, height - pad_b, W - pad_r, height - pad_b,
                          cls="axis"))
    for tick in svg.log_ticks(min(xs), max(xs)):
        tx = x(tick)
        if tx < pad_l - 1 or tx > W - pad_r + 1:
            continue
        parts.append(svg.text(tx, height - pad_b + 16, svg.compact(tick),
                              cls="tick", anchor="middle"))

    biggest = max(it[3] for it in items)
    for brand, votes, score, count in items:
        r = 5 + 11 * (count / biggest) ** 0.5
        parts.append(svg.circle(
            x(votes), y(score), r, "var(--series-1)",
            title=f"{brand}: {svg.thousands(votes)} vote · "
                  f"{svg.decimal(score)}★ · {count} chai",
            opacity="0.72", stroke="var(--surface)", stroke_width="2"))
    # Chỉ đặt nhãn cho vài hãng đầu — ghi tên cả 40 là thành đám chữ chồng nhau.
    # Ngay cả vài nhãn cũng có thể đè lên nhau hoặc tràn mép phải (các hãng
    # nhiều vote nhất dồn hết về bên phải), nên: thử đặt trên bong bóng, không
    # được thì thử dưới, vẫn không được thì BỎ nhãn đó — giá trị vẫn còn trong
    # tooltip và trong bảng, còn chữ chồng chữ thì không đọc được gì.
    placed: list[tuple[float, float, float, float]] = []
    for brand, votes, score, count in items[:labelled]:
        r = 5 + 11 * (count / biggest) ** 0.5
        cx, cy = x(votes), y(score)
        width = len(brand) * 6.0 + 4
        anchor, tx = "middle", cx
        if cx + width / 2 > W - pad_r:
            anchor, tx = "end", min(cx + r, W - pad_r)
        elif cx - width / 2 < pad_l:
            anchor, tx = "start", max(cx - r, pad_l)
        left = {"middle": tx - width / 2, "end": tx - width, "start": tx}[anchor]

        for ty in (cy - r - 6, cy + r + 13):
            box = (left, ty - 10, left + width, ty + 2)
            if any(box[0] < p[2] and p[0] < box[2]
                   and box[1] < p[3] and p[1] < box[3] for p in placed):
                continue
            placed.append(box)
            parts.append(svg.text(tx, ty, brand, cls="mark-lbl", anchor=anchor))
            break

    parts.append(svg.text(pad_l, height - 8, "lượt vote (thang log) →",
                          cls="axis-title"))
    parts.append(svg.text(pad_l - 8, pad_t - 4, "điểm đã hiệu chỉnh",
                          cls="axis-title", anchor="start"))

    return Figure(
        key="brand_map",
        title="Định vị hãng: được chú ý nhiều hay được chấm cao",
        how="Ngang = tổng lượt vote (độ được chú ý), dọc = điểm đã hiệu chỉnh "
            "theo số vote, kích thước bong bóng = số chai đã crawl. Góc trên "
            "phải là hãng vừa đông người nói vừa được chấm cao.",
        svg=svg.svg(W, height, "".join(parts), "Biểu đồ phân tán định vị hãng"),
        columns=["Hãng", "Chai", "Lượt vote", "Điểm hiệu chỉnh"],
        rows=[[b, str(c), svg.thousands(v), svg.decimal(s)]
              for b, v, s, c in items],
        note=f"{len(items)} hãng nhiều vote nhất. Điểm hiệu chỉnh kéo hãng ít "
             f"vote về mốc chung, nên 5★/3 vote không nhảy lên đầu. Trục ngang "
             f"là thang log vì dải vote của nhóm này trải từ "
             f"{svg.thousands(min(xs))} tới {svg.thousands(max(xs))}. Nhãn chỉ "
             f"đặt cho vài hãng đầu và bị bỏ khi chồng nhau — tên đầy đủ nằm "
             f"trong tooltip và bảng số.",
    )


# ------------------------------------------------- 3. độ lưu hương theo accord
def longevity_by_accord(rows: list[Row], top: int = 10) -> Figure:
    have = [r for r in rows if r.longevity and r.accords]
    if not have:
        return Figure("longevity", "Accord nào lưu hương lâu", "",
                      empty="Chưa có chai nào có cả độ lưu hương và accord. "
                            "Khối này chỉ có khi crawl bằng --render.")

    counts = Counter(a for r in have for a in r.accords)
    names = [a for a, _ in counts.most_common(top)]
    dist: dict[str, list[float]] = {}
    sizes: dict[str, int] = {}
    for accord in names:
        group = [r for r in have if accord in r.accords]
        sizes[accord] = len(group)
        tally = Counter((r.longevity or "").strip().lower() for r in group)
        total = sum(tally[k] for k in LONGEVITY_ORDER) or 1
        dist[accord] = [100 * tally[k] / total for k in LONGEVITY_ORDER]

    pad_l, pad_t, pad_r, pad_b = 136, 10, 16, 30
    bh, gap = 24, 8
    height = pad_t + (bh + gap) * len(names) + pad_b
    plot = W - pad_l - pad_r
    # Trục căn giữa ở mốc "vừa": nửa trái là yếu dần, nửa phải là lâu dần.
    # Thang +-100% cố định cho mọi hàng, nếu không thì các hàng không so được.
    unit = plot / 200.0
    mid_x = pad_l + plot / 2

    parts: list[str] = []
    parts.append(svg.line(mid_x, pad_t, mid_x, height - pad_b, cls="axis"))
    for i, accord in enumerate(names):
        y = pad_t + i * (bh + gap)
        pct = dist[accord]
        parts.append(svg.text(pad_l - 10, y + bh / 2 + 4, accord,
                              cls="lbl", anchor="end"))
        # Nửa của "vừa" nằm mỗi bên vạch giữa.
        left_edge = mid_x - pct[2] / 2 * unit
        right_edge = mid_x + pct[2] / 2 * unit
        parts.append(svg.rect(left_edge, y, (right_edge - left_edge), bh,
                              LONGEVITY_TOKEN[2],
                              title=f"{accord} · vừa: {svg.decimal(pct[2], 1)}%"))
        cursor = left_edge
        for k in (1, 0):                              # yếu rồi rất yếu, ra trái
            w = pct[k] * unit
            cursor -= w
            if w > 0:
                parts.append(svg.rect(
                    cursor + 1, y, w - 2, bh, LONGEVITY_TOKEN[k], rx=3,
                    title=f"{accord} · {LONGEVITY_VI[LONGEVITY_ORDER[k]]}: "
                          f"{svg.decimal(pct[k], 1)}%"))
        cursor = right_edge
        for k in (3, 4):                              # lâu rồi rất lâu, ra phải
            w = pct[k] * unit
            if w > 0:
                parts.append(svg.rect(
                    cursor + 1, y, w - 2, bh, LONGEVITY_TOKEN[k], rx=3,
                    title=f"{accord} · {LONGEVITY_VI[LONGEVITY_ORDER[k]]}: "
                          f"{svg.decimal(pct[k], 1)}%"))
            cursor += w

    for offset in (-100, -50, 0, 50, 100):
        tx = mid_x + offset * unit
        parts.append(svg.text(tx, height - pad_b + 18, f"{abs(offset)}%",
                              cls="tick", anchor="middle"))

    return Figure(
        key="longevity",
        title="Accord nào lưu hương lâu",
        how="Mỗi hàng là 100% số chai mang accord đó, xếp theo thang độ lưu "
            "hương. Vạch giữa là mốc “vừa”. Hàng nghiêng phải = accord đó "
            "thường lưu lâu; nghiêng trái = thường bay nhanh.",
        svg=svg.svg(W, height, "".join(parts),
                    "Cột xếp chồng lưỡng hướng độ lưu hương theo accord"),
        columns=["Accord", "Số chai"] + [LONGEVITY_VI[k] for k in LONGEVITY_ORDER],
        rows=[[a, str(sizes[a])] + [svg.decimal(v, 1) for v in dist[a]]
              for a in names],
        legend=[(LONGEVITY_VI[k], LONGEVITY_TOKEN[i])
                for i, k in enumerate(LONGEVITY_ORDER)],
        note=f"Tính trên {svg.thousands(len(have))} chai có độ lưu hương. "
             f"Một chai mang nhiều accord nên được tính ở nhiều hàng.",
    )


# ------------------------------------------------------- 4. note đi cùng nhau
def note_pairs(rows: list[Row], top: int = 16) -> Figure:
    have = [r for r in rows if r.notes]
    if not have:
        return Figure("note_pairs", "Note nào đi với note nào", "",
                      empty="Chưa có chai nào có note.")

    counts = Counter(n for r in have for n in set(r.notes))
    names = [n for n, _ in counts.most_common(top)]
    index = {n: i for i, n in enumerate(names)}
    pair = [[0] * len(names) for _ in names]
    for row in have:
        present = sorted({index[n] for n in set(row.notes) if n in index})
        for a in present:
            for b in present:
                if a != b:
                    pair[a][b] += 1

    # P(cột | hàng): "trong các chai có note ở HÀNG, bao nhiêu % cũng có note ở
    # CỘT". Dùng tỉ lệ có điều kiện thay vì số đếm thô, vì số đếm thô chỉ phản
    # ánh note nào phổ biến — hàng nào có Musk cũng đậm, không nói thêm được gì.
    share = [[(100 * pair[i][j] / counts[names[i]]) if i != j else None
              for j in range(len(names))] for i in range(len(names))]
    flat = [v for line in share for v in line if v is not None]
    lo, hi = (min(flat), max(flat)) if flat else (0.0, 1.0)

    pad_l, pad_t, pad_r, pad_b = 124, 92, 12, 10
    cell = (W - pad_l - pad_r) / len(names)
    height = pad_t + cell * len(names) + pad_b

    parts: list[str] = []
    for j, name in enumerate(names):
        cx = pad_l + cell * (j + 0.5)
        parts.append(f'<g transform="translate({cx:.2f},{pad_t - 8:.2f}) '
                     f'rotate(-45)">'
                     + svg.text(0, 0, name, cls="lbl", anchor="start")
                     + "</g>")
    for i, name in enumerate(names):
        y = pad_t + i * cell
        parts.append(svg.text(pad_l - 8, y + cell / 2 + 4, name,
                              cls="lbl", anchor="end"))
        for j in range(len(names)):
            value = share[i][j]
            x0 = pad_l + j * cell
            if value is None:
                parts.append(svg.rect(x0 + 1, y + 1, cell - 2, cell - 2,
                                      "var(--grid)", rx=3))
                continue
            parts.append(svg.rect(
                x0 + 1, y + 1, cell - 2, cell - 2,
                svg.seq_token(value, lo, hi), rx=3,
                title=f"Có {names[i]} → {svg.decimal(value, 1)}% cũng có "
                      f"{names[j]} ({pair[i][j]}/{counts[names[i]]} chai)"))

    return Figure(
        key="note_pairs",
        title="Note nào đi với note nào",
        how="Đọc theo HÀNG: trong các chai có note ở hàng, bao nhiêu phần trăm "
            "cũng có note ở cột. Bảng không đối xứng — “có Oud thì 60% có Musk” "
            "khác “có Musk thì 6% có Oud”. Ô xám trên đường chéo là chính nó.",
        svg=svg.svg(W, height, "".join(parts),
                    "Bản đồ nhiệt note đi cùng nhau"),
        columns=["Có note này"] + names,
        rows=[[names[i]] + ["—" if share[i][j] is None
                            else svg.decimal(share[i][j], 1)
                            for j in range(len(names))]
              for i in range(len(names))],
        scale=(f"{svg.decimal(lo, 1)}%", f"{svg.decimal(hi, 1)}%"),
        note=f"{top} note phổ biến nhất trên {svg.thousands(len(have))} chai.",
    )


# ------------------------------------------------------------- 5. theo năm
def by_year(rows: list[Row], since: int = 2000) -> Figure:
    have = [r for r in rows if r.year and r.year >= since]
    if not have:
        return Figure("by_year", "Số chai theo năm ra mắt", "",
                      empty=f"Chưa có chai nào có năm ra mắt từ {since}.")

    years = sorted({r.year for r in have})
    tally: dict[int, Counter] = {y: Counter() for y in years}
    for row in have:
        tally[row.year][row.gender or "Unisex"] += 1
    tallest = max(sum(c.values()) for c in tally.values())

    pad_l, pad_t, pad_r, pad_b = 40, 14, 12, 40
    height = 300
    cw = (W - pad_l - pad_r) / len(years)
    y = svg.Linear(0, tallest, height - pad_b, pad_t)

    parts: list[str] = []
    for tick in svg.nice_ticks(0, tallest, 4):
        ty = y(tick)
        parts.append(svg.line(pad_l, ty, W - pad_r, ty))
        parts.append(svg.text(pad_l - 8, ty + 4, svg.compact(tick),
                              cls="tick", anchor="end"))
    parts.append(svg.line(pad_l, height - pad_b, W - pad_r, height - pad_b,
                          cls="axis"))

    for i, year in enumerate(years):
        x0 = pad_l + i * cw
        base = height - pad_b
        for gender in GENDER_ORDER:
            count = tally[year][gender]
            if not count:
                continue
            h = (height - pad_b) - y(count)
            # Bớt 2px làm khe giữa hai lớp xếp chồng.
            parts.append(svg.rect(
                x0 + 1.5, base - h, max(cw - 3, 1), max(h - 2, 1),
                GENDER_TOKEN[gender], rx=2,
                title=f"{year} · {gender}: {count} chai"))
            base -= h
    for i in svg.spread_labels(years, 3):
        parts.append(svg.text(pad_l + cw * (i + 0.5), height - pad_b + 16,
                              years[i], cls="tick", anchor="middle"))
    parts.append(svg.text(pad_l, height - 8, "năm ra mắt →", cls="axis-title"))

    return Figure(
        key="by_year",
        title="Số chai theo năm ra mắt",
        how="Mỗi cột là số chai ĐÃ CRAWL ra mắt năm đó, chia theo nhóm giới "
            "tính. Đây là độ phủ của dữ liệu đang có, KHÔNG phải sản lượng "
            "thật của thị trường.",
        svg=svg.svg(W, height, "".join(parts),
                    "Cột xếp chồng số chai theo năm và nhóm giới tính"),
        columns=["Năm"] + GENDER_ORDER + ["Tổng"],
        rows=[[str(yr)] + [str(tally[yr][g]) for g in GENDER_ORDER]
              + [str(sum(tally[yr].values()))] for yr in years],
        legend=[(g, GENDER_TOKEN[g]) for g in GENDER_ORDER],
        note=f"Từ năm {since} trở lại đây. Không nằm trong hình: "
             f"{svg.thousands(sum(1 for r in rows if not r.year))} chai không "
             f"rõ năm và "
             f"{svg.thousands(sum(1 for r in rows if r.year and r.year < since))} "
             f"chai ra mắt trước {since}.",
    )


FIGURES = (accord_season, brand_map, longevity_by_accord, note_pairs,
           by_year)


def build_all(rows: list[Row]) -> list[Figure]:
    return [fn(rows) for fn in FIGURES]
