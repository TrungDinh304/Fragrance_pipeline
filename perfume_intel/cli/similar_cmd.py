"""Lệnh `similar` — tìm chai/hãng giống nhau từ dữ liệu cộng đồng đã crawl.

Bốn cách hỏi:

    similar "Sauvage"                      chai nào giống chai này
    similar --brand "Lattafa Perfumes"     hãng nào giống hãng này
    similar --notes oud,vanilla            chai nào nhiều note này nhất
    similar --occasion winter,night        chai nào hợp hoàn cảnh này

Tên lệnh là `similar` chứ không phải `recommend` cho đúng việc nó làm: đây là độ
giống nhau tính từ dữ liệu cộng đồng, không phải gợi ý cá nhân hoá — không có dữ
liệu người dùng nào để cá nhân hoá.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .. import config
from ..analytics import dataset
from ..analytics.dataset import DAY_NIGHT, SEASONS
from ..vectors import features
from ..vectors.index import VectorIndex

log = logging.getLogger(__name__)

OCCASION_AXES = (*SEASONS, *DAY_NIGHT)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("similar",
                       help="Tìm chai/hãng giống nhau theo mùi, note, hoàn cảnh")
    p.add_argument("query", nargs="?",
                   help="Tên hoặc URL một chai đã crawl")
    p.add_argument("--brand", metavar="TÊN",
                   help="Tìm hãng giống hãng này (thay vì tìm chai)")
    p.add_argument("--notes", metavar="a,b,c",
                   help="Tìm theo note hương, cách nhau bởi dấu phẩy")
    p.add_argument("--accords", metavar="a,b,c",
                   help="Tìm theo accord (woody, sweet, citrus...)")
    p.add_argument("--occasion", metavar="a,b",
                   help=f"Tìm theo hoàn cảnh: {', '.join(OCCASION_AXES)}")

    p.add_argument("--limit", type=int, default=10, help="Số kết quả (mặc định 10)")
    p.add_argument("--gender", choices=["Nam", "Nữ", "Unisex"],
                   help="Chỉ lấy chai thuộc nhóm này")
    p.add_argument("--min-votes", dest="min_votes", type=int, default=0,
                   metavar="N",
                   help="Bỏ chai có dưới N vote — lọc bớt chai chưa ai đánh giá")
    p.add_argument("--other-brands", dest="other_brands", action="store_true",
                   help="Loại các chai cùng hãng (các bản flanker luôn chiếm "
                        "hết đầu bảng và che mất thứ đáng xem)")
    p.add_argument("--min-perfumes", dest="min_perfumes", type=int, default=3,
                   metavar="N",
                   help="Khi so hãng: chỉ tính hãng có ít nhất N chai đã crawl "
                        "(mặc định 3 — trọng tâm dựng từ 1 chai không đại diện "
                        "cho hãng)")
    p.add_argument("--explain", action="store_true",
                   help="In những chiều góp nhiều nhất vào điểm giống nhau")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Xuất JSON thay vì bảng")
    p.add_argument("--community", type=Path,
                   help="Thư mục .jsonl Fragrantica "
                        "(mặc định: data/raw/fragrantica)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=run)


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


# Nhãn khối cho phần giải thích. Phải giữ khối lại, không cắt bỏ: nhiều tên
# tồn tại ở CẢ hai khối ("cacao" vừa là accord vừa là note), nên bỏ tiền tố đi
# thì dòng giải thích trông như bị lặp mà không nói được vì sao.
_BLOCK_LABEL = {"acc": "mùi", "note": "note", "occ": "dịp",
                "str": "độ", "fam": "họ"}


def _why(hit) -> str:
    parts = []
    for dim, weight in hit.why:
        block, _, name = dim.partition(":")
        parts.append(f"{_BLOCK_LABEL.get(block, block)}:{name} {weight:.3f}")
    return ", ".join(parts)


def _label(row) -> str:
    bits = [row.name or "(không tên)"]
    if row.brand:
        bits.append(f"— {row.brand}")
    return " ".join(bits)


def _print_hits(hits, explain: bool) -> None:
    width = max((len(_label(h.row)) for h in hits), default=10)
    width = min(max(width, 20), 52)
    for hit in hits:
        row = hit.row
        extra = []
        if row.rating is not None:
            extra.append(f"{row.rating:.2f}★")
        if row.rating_count:
            extra.append(f"{row.rating_count:,} vote".replace(",", "."))
        if row.gender:
            extra.append(row.gender)
        print(f"  {hit.score:6.3f}  {_label(row)[:width]:<{width}}  "
              f"{'  '.join(extra)}")
        if explain and hit.why:
            print(f"  {'':6}  └─ {_why(hit)}")


def _print_brands(target, hits, explain: bool) -> None:
    print(f"Hãng giống {target.brand} "
          f"({target.perfumes} chai đã crawl):")
    print()
    width = min(max((len(h.row.name or "") for h in hits), default=20), 46)
    for hit in hits:
        print(f"  {hit.score:6.3f}  {(hit.row.name or '')[:width]:<{width}}  "
              f"{hit.row.rating_count} chai")
        if explain and hit.why:
            print(f"  {'':6}  └─ {_why(hit)}")


def _as_json(hits) -> str:
    return json.dumps([{
        "score": round(h.score, 4),
        "name": h.row.name,
        "brand": h.row.brand,
        "url": h.row.url or None,
        "rating": h.row.rating,
        "rating_count": h.row.rating_count,
        "gender": h.row.gender,
        "why": [{"dim": k, "weight": round(v, 4)} for k, v in h.why],
    } for h in hits], ensure_ascii=False, indent=2)


def _mode_count(args) -> int:
    return sum(bool(x) for x in
               (args.query, args.brand, args.notes or args.accords,
                args.occasion))


def run(args: argparse.Namespace) -> int:
    if _mode_count(args) == 0:
        log.error("Cần một trong: tên chai, --brand, --notes/--accords, "
                  "--occasion.\nVí dụ: perfume-intel similar \"Sauvage\"")
        return 1
    if args.query and args.brand:
        log.error("Chọn một: tìm chai (tham số vị trí) HOẶC tìm hãng (--brand).")
        return 1

    community = args.community or config.raw_dir("fragrantica")
    if not community.exists():
        log.error("Chưa có dữ liệu: %s", community)
        return 1

    rows = dataset.build(community)
    if not rows:
        log.error("Không đọc được bản ghi nào trong %s", community)
        return 1

    index = VectorIndex(rows)
    if not index.rows:
        log.error("Không có chai nào đủ dữ liệu (cần accord hoặc note). "
                  "Crawl bằng --render để lấy đủ.")
        return 1

    # --- hãng giống hãng ---
    if args.brand:
        target, hits = index.similar_brands(
            args.brand, limit=args.limit,
            min_perfumes=args.min_perfumes, explain=args.explain)
        if target is None:
            log.error("Không có hãng nào khớp %r trong dữ liệu đã crawl.",
                      args.brand)
            return 1
        if not hits:
            log.error("Không có hãng nào khác đạt --min-perfumes %d. "
                      "Hạ ngưỡng xuống, hoặc crawl thêm.", args.min_perfumes)
            return 1
        if args.as_json:
            print(_as_json(hits))
        else:
            _print_brands(target, hits, args.explain)
        return 0

    # --- chai giống chai ---
    if args.query:
        i = index.find(args.query)
        if i is None:
            log.error("Không tìm thấy chai nào khớp %r trong dữ liệu đã crawl.",
                      args.query)
            return 1
        found = index.rows[i]
        others = index.matches(args.query)
        if len(others) > 1:
            log.info("%r khớp %d chai; dùng chai nhiều vote nhất: %s.",
                     args.query, len(others), _label(found))

        hits = index.similar(i, limit=args.limit,
                             same_brand=not args.other_brands,
                             gender=args.gender, min_votes=args.min_votes,
                             explain=args.explain)
        if not args.as_json:
            print(f"Giống {_label(found)}:")
            occasion = features.derive_occasion(found)
            if occasion:
                print(f"  (hoàn cảnh suy diễn: {', '.join(occasion)})")
            print()
        if not hits:
            log.error("Không có kết quả nào sau khi lọc. Nới --min-votes "
                      "hoặc bỏ --gender.")
            return 1
        print(_as_json(hits)) if args.as_json else _print_hits(hits, args.explain)
        return 0

    # --- theo note / accord / hoàn cảnh ---
    notes, accords = _split(args.notes), _split(args.accords)
    occasion = [a.lower() for a in _split(args.occasion)]

    bad_axes = [a for a in occasion if a not in OCCASION_AXES]
    if bad_axes:
        log.error("Hoàn cảnh không có: %s. Chỉ có: %s",
                  ", ".join(bad_axes), ", ".join(OCCASION_AXES))
        return 1

    terms: list[str] = []
    missing: list[str] = []
    renamed: dict[str, str] = {}
    for block, names in ((features.NOTE, notes), (features.ACCORD, accords)):
        found, absent, mapped = index.resolve(block, names)
        terms += found
        missing += absent
        renamed.update(mapped)

    for typed, actual in renamed.items():
        log.info("%r -> %r", typed, actual)
    if missing:
        log.warning("Bỏ qua term không có trong dữ liệu đã crawl: %s",
                    ", ".join(missing))

    vec = features.query_from_terms(terms, occasion=occasion, idf=index.idf)
    if not vec:
        log.error("Không còn term nào dùng được để tìm. "
                  "Xem note/accord đang có bằng `analyze`.")
        return 1

    hits = index.query(vec, limit=args.limit, gender=args.gender,
                       min_votes=args.min_votes, explain=args.explain)
    if not hits:
        log.error("Không có kết quả nào sau khi lọc.")
        return 1
    if args.as_json:
        print(_as_json(hits))
    else:
        asked = notes + accords + occasion
        print(f"Khớp nhất với: {', '.join(asked)}")
        print()
        _print_hits(hits, args.explain)
    return 0
