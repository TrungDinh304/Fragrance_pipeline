"""Ảnh `perfume-intel` có cũ hơn code không?

VÌ SAO CẦN KIỂM NÀY
Airflow chạy task bằng cách bật container từ ảnh `perfume-intel`. Sửa code trong
repo KHÔNG đổi gì trong ảnh — nên lịch cứ chạy bản cũ, không báo lỗi, không đỏ ở
đâu cả.

Đã dính thật: ảnh build ngày 06/10, `vectordb load` được sửa ngày 16/10 để đọc
Parquet thay vì embed lại. DAG chạy mỗi ngày vẫn embed lại (~90 giây thay vì 11
giây) suốt 10 ngày, và không có dấu hiệu nào nhìn thấy được — cả hai cách đều cho
ra kết quả đúng, chỉ khác thời gian.

Đây là cái giá của ranh giới "Airflow điều phối, ảnh thực thi": đổi lấy việc không
phải trộn phụ thuộc, ta nhận thêm một bước phải nhớ. Nên biến nó thành thứ máy
nhớ, không phải người nhớ.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

GOC = pathlib.Path(__file__).resolve().parent.parent
ANH = "perfume-intel:0.2.0"

# Thứ ĐƯỢC ĐÓNG VÀO ảnh. Sửa ở đây thì ảnh thành cũ.
# `airflow/dags/` KHÔNG nằm trong danh sách: nó được mount vào Airflow lúc chạy,
# nên sửa DAG có hiệu lực ngay mà không cần build lại.
THEO_DOI = ("perfume_intel", "transform", "scripts", "tests",
            "pyproject.toml", "Dockerfile", "Makefile")

BO_QUA = {"__pycache__", ".pytest_cache", "target", "logs", "fixtures"}


def _gio_build() -> datetime | None:
    try:
        ra = subprocess.run(
            ["docker", "image", "inspect", ANH, "--format", "{{json .Created}}"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if ra.returncode != 0:
        return None
    try:
        chu = json.loads(ra.stdout.strip())
    except json.JSONDecodeError:
        return None
    # Docker trả ISO với nano giây; Python chỉ nhận tới micro giây.
    chu = chu.replace("Z", "+00:00")
    if "." in chu:
        dau, _, duoi = chu.partition(".")
        so, _, mui = duoi.partition("+")
        chu = f"{dau}.{so[:6]}+{mui}" if mui else f"{dau}.{so[:6]}"
    try:
        return datetime.fromisoformat(chu)
    except ValueError:
        return None


def _file_moi_nhat() -> tuple[pathlib.Path | None, datetime | None]:
    moi_nhat: pathlib.Path | None = None
    mtime = 0.0
    for ten in THEO_DOI:
        goc = GOC / ten
        if goc.is_file():
            ds = [goc]
        elif goc.is_dir():
            ds = [p for p in goc.rglob("*")
                  if p.is_file() and not (BO_QUA & set(p.parts))]
        else:
            continue
        for p in ds:
            t = p.stat().st_mtime
            if t > mtime:
                mtime, moi_nhat = t, p
    if moi_nhat is None:
        return None, None
    return moi_nhat, datetime.fromtimestamp(mtime, tz=timezone.utc)


def main() -> int:
    build = _gio_build()
    if build is None:
        print(f"Chưa có ảnh {ANH}. Chạy: make docker-build")
        return 1

    path, sua = _file_moi_nhat()
    if sua is None:
        print("Không đọc được thời gian sửa của code — bỏ qua phép kiểm.")
        return 0

    if sua <= build:
        print(f"Ảnh {ANH} mới hơn code (build {build:%Y-%m-%d %H:%M} UTC). OK.")
        return 0

    tre = sua - build
    ngay = tre.days
    gio = tre.seconds // 3600
    print(f"ẢNH CŨ HƠN CODE — Airflow đang chạy bản cũ.")
    print(f"  ảnh build : {build:%Y-%m-%d %H:%M} UTC")
    print(f"  code sửa  : {sua:%Y-%m-%d %H:%M} UTC  ({path.relative_to(GOC)})")
    print(f"  lệch      : {ngay} ngày {gio} giờ")
    print()
    print("  Airflow chạy task bằng ảnh, không bằng code trong repo. Sửa code mà")
    print("  không build lại thì lịch cứ chạy bản cũ và KHÔNG báo lỗi gì.")
    print()
    print("  Chạy: make docker-build")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
