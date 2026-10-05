"""Lệnh `similar` — tìm chai/hãng giống nhau từ dữ liệu cộng đồng đã crawl.

Bốn cách hỏi:

    similar "Sauvage"                      chai nào giống chai này
    similar --brand "Lattafa Perfumes"     hãng nào giống hãng này
    similar --notes oud,vanilla            chai nào nhiều note này nhất
    similar --occasion winter,night        chai nào hợp hoàn cảnh này

Tên lệnh là `similar` chứ không phải `recommend` cho đúng việc nó làm: đây là độ
giống nhau tính từ dữ liệu cộng đồng, không phải gợi ý cá nhân hoá.

File này CHỈ nói chuyện qua `retrieval.ports` — không biết gì về vector thưa,
IDF hay cách lưu trữ. Đổi backend (DuckDB, pgvector) không phải sửa dòng nào ở
đây; `tests/test_retrieval.py` có một test canh đúng điều đó.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .. import config
from ..core import bronze
from ..retrieval import (InMemoryRetriever, Query, Retriever, UnknownBrand,
                         UnknownPerfume, ports)

log = logging.getLogger(__name__)


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
                   help=f"Tìm theo hoàn cảnh: {', '.join(ports.OCCASIONS)}")

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


def _split(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


# Nhãn khối cho phần giải thích. Giữ khối lại chứ không cắt bỏ: nhiều tên tồn
# tại ở CẢ hai khối ("cacao" vừa là accord vừa là note), bỏ đi thì dòng giải
# thích trông như bị lặp mà không nói được vì sao.
_BLOCK_LABEL = {ports.ACCORD: "mùi", ports.NOTE: "note",
                ports.OCCASION: "dịp", ports.STRENGTH: "độ",
                ports.FAMILY: "họ"}


def _why(match) -> str:
    return ", ".join(
        f"{_BLOCK_LABEL.get(w.block, w.block)}:{w.label} {w.weight:.3f}"
        for w in match.why)


def _label(match) -> str:
    bits = [match.name or "(không tên)"]
    if match.brand:
        bits.append(f"— {match.brand}")
    return " ".join(bits)


def _print_matches(matches, explain: bool) -> None:
    width = max((len(_label(m)) for m in matches), default=10)
    width = min(max(width, 20), 52)
    for m in matches:
        extra = []
        if m.rating is not None:
            extra.append(f"{m.rating:.2f}★")
        if m.rating_count:
            extra.append(f"{m.rating_count:,} vote".replace(",", "."))
        if m.gender:
            extra.append(m.gender)
        print(f"  {m.score:6.3f}  {_label(m)[:width]:<{width}}  "
              f"{'  '.join(extra)}")
        if explain and m.why:
            print(f"  {'':6}  └─ {_why(m)}")


def _print_brands(result, explain: bool) -> None:
    print(f"Hãng giống {result.target.brand} "
          f"({result.target.perfumes} chai đã crawl):")
    print()
    width = min(max((len(m.brand) for m in result.matches), default=20), 46)
    for m in result.matches:
        print(f"  {m.score:6.3f}  {m.brand[:width]:<{width}}  {m.perfumes} chai")
        if explain and m.why:
            print(f"  {'':6}  └─ {_why(m)}")


def _as_json(matches) -> str:
    return json.dumps([{
        "perfume_key": m.perfume_key,
        "score": round(m.score, 4),
        "name": m.name, "brand": m.brand, "url": m.url,
        "rating": m.rating, "rating_count": m.rating_count,
        "gender": m.gender,
        "why": [{"block": w.block, "label": w.label, "weight": w.weight}
                for w in m.why],
    } for m in matches], ensure_ascii=False, indent=2)


def _brands_json(result) -> str:
    return json.dumps([{
        "brand": m.brand, "perfumes": m.perfumes, "score": round(m.score, 4),
        "why": [{"block": w.block, "label": w.label, "weight": w.weight}
                for w in m.why],
    } for m in result.matches], ensure_ascii=False, indent=2)


def _report(result) -> None:
    """Nói ra những gì đã xảy ra với câu hỏi, trước khi in kết quả."""
    for typed, actual in result.resolved.items():
        log.info("%r -> %r", typed, actual)
    if result.unknown:
        log.warning("Bỏ qua term không có trong dữ liệu đã crawl: %s",
                    ", ".join(result.unknown))


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
    if not bronze.available(community):
        log.error("Chưa có dữ liệu: %s", community)
        return 1

    retriever: Retriever = InMemoryRetriever.from_bronze(community)
    if not len(retriever):
        log.error("Không có chai nào đủ dữ liệu (cần accord hoặc note). "
                  "Crawl bằng --render để lấy đủ.")
        return 1

    if args.brand:
        return _run_brands(retriever, args)

    return _run_perfumes(retriever, args)


def _run_brands(retriever: Retriever, args: argparse.Namespace) -> int:
    try:
        result = retriever.similar_brands(
            args.brand, limit=args.limit,
            min_perfumes=args.min_perfumes, explain=args.explain)
    except UnknownBrand:
        log.error("Không có hãng nào khớp %r trong dữ liệu đã crawl.",
                  args.brand)
        return 1
    if not result.matches:
        log.error("Không có hãng nào khác đạt --min-perfumes %d. "
                  "Hạ ngưỡng xuống, hoặc crawl thêm.", args.min_perfumes)
        return 1
    if args.as_json:
        print(_brands_json(result))
    else:
        _print_brands(result, args.explain)
    return 0


def _run_perfumes(retriever: Retriever, args: argparse.Namespace) -> int:
    query = Query(
        like_perfume=args.query,
        notes=_split(args.notes),
        accords=_split(args.accords),
        occasions=_split(args.occasion),
        gender=args.gender,
        min_votes=args.min_votes,
        include_same_brand=not args.other_brands,
        limit=args.limit,
        explain=args.explain,
    )

    try:
        result = retriever.search(query)
    except UnknownPerfume:
        log.error("Không tìm thấy chai nào khớp %r trong dữ liệu đã crawl.",
                  args.query)
        return 1

    _report(result)

    if not args.as_json:
        if result.seed is not None:
            if result.ambiguous:
                log.info("%r khớp %d chai; dùng chai nhiều vote nhất: %s.",
                         args.query, len(result.ambiguous),
                         _label(result.seed))
            print(f"Giống {_label(result.seed)}:")
        else:
            asked = [*query.notes, *query.accords, *query.occasions]
            print(f"Khớp nhất với: {', '.join(asked)}")
        print()

    if not result.matches:
        log.error("Không có kết quả nào sau khi lọc. Nới --min-votes, "
                  "bỏ --gender, hoặc kiểm lại tên note.")
        return 1

    if args.as_json:
        print(_as_json(result.matches))
    else:
        _print_matches(result.matches, args.explain)
    return 0
