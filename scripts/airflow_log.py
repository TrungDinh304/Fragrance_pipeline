"""Đọc log task của Airflow từ dòng lệnh, không cần mở UI.

VÌ SAO CẦN SCRIPT NÀY
Airflow 3 ghi log dạng JSON-lines (structured logging). Mỗi dòng là một object
kèm `dag_id`, `task_id`, `ti_id`, `logger`, `filename`, `lineno`... nên một dòng
log thật dài 400+ ký tự và phần nội dung chiếm chưa tới 20%. `tail` hay `grep`
trên file đó gần như không đọc được.

UI của Airflow hiển thị đẹp, nhưng:
  - log nằm trong named volume `airflow-logs`, không ở trên host, nên không mở
    bằng editor được;
  - và khi cần dán log vào đâu đó (báo lỗi, ghi chú, chat) thì bản sạch mới dùng
    được.

CHẠY TRONG CONTAINER AIRFLOW, vì đó là nơi duy nhất thấy volume log:

    make airflow-log                      lượt crawl gần nhất
    make airflow-log ARGS="-d fragrantica_refresh -t vectordb_load"
    make airflow-log ARGS="--loi"         chỉ dòng warning/error
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

GOC = pathlib.Path("/opt/airflow/logs")

# Airflow lồng timestamp của chính nó vào `event` khi gom log của container con:
#   "20:48:18 INFO    OK  Eau Sauvage ..."
# Bỏ phần đó đi vì cột timestamp bên ngoài đã có, và hai mốc giờ cạnh nhau (một
# UTC, một giờ địa phương của container) chỉ làm người đọc tưởng log bị lặp.
_GIO_LONG = re.compile(r"^\d{2}:\d{2}:\d{2}\s+(DEBUG|INFO|WARNING|ERROR)\s+")


def _thu_muc_run(dag: str, task: str | None) -> list[pathlib.Path]:
    goc = GOC / f"dag_id={dag}"
    if not goc.is_dir():
        co = sorted(p.name.removeprefix("dag_id=") for p in GOC.glob("dag_id=*"))
        sys.exit(f"Không có log cho DAG {dag!r}. Đang có: {', '.join(co) or '(chưa có)'}")
    ra = []
    for run in sorted(goc.glob("run_id=*")):
        for t in sorted(run.glob("task_id=*")):
            if task and t.name != f"task_id={task}":
                continue
            ra += sorted(t.glob("attempt=*.log"))
    return ra


def _moi_nhat(files: list[pathlib.Path]) -> pathlib.Path | None:
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _doc(path: pathlib.Path, chi_loi: bool, so_dong: int) -> None:
    run = next((p.name.removeprefix("run_id=") for p in path.parents
                if p.name.startswith("run_id=")), "?")
    task = next((p.name.removeprefix("task_id=") for p in path.parents
                 if p.name.startswith("task_id=")), "?")
    print(f"=== {task}  ·  {run} ===")

    ra = []
    for dong in path.read_text(encoding="utf-8", errors="replace").splitlines():
        dong = dong.strip()
        if not dong.startswith("{"):
            # Dòng không phải JSON vẫn in ra: traceback của Python nhiều lúc rơi
            # ra ngoài khuôn structured log, và đó thường là dòng cần đọc nhất.
            if dong and not chi_loi:
                ra.append(("", "", dong))
            continue
        try:
            j = json.loads(dong)
        except json.JSONDecodeError:
            continue
        muc = (j.get("level") or "").lower()
        if chi_loi and muc not in ("warning", "error", "critical"):
            continue
        noi_dung = _GIO_LONG.sub("", str(j.get("event") or ""))
        ra.append((str(j.get("timestamp") or "")[11:19], muc[:4], noi_dung))

    if not ra:
        print("  (không có dòng nào khớp)")
        return
    if 0 < so_dong < len(ra):
        print(f"  ... bỏ {len(ra) - so_dong} dòng đầu, xem đủ bằng --so-dong 0")
        ra = ra[-so_dong:]
    for gio, muc, noi_dung in ra:
        dau = "!" if muc in ("warn", "erro", "crit") else " "
        print(f"{gio:>8} {dau} {noi_dung}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Đọc log task Airflow ở dạng đọc được.")
    p.add_argument("-d", "--dag", default="fragrantica_crawl")
    p.add_argument("-t", "--task", help="Mặc định: task có log mới nhất")
    p.add_argument("-r", "--run", help="Khớp một phần run_id; mặc định mới nhất")
    p.add_argument("--tat-ca", dest="tat_ca", action="store_true",
                   help="In log của MỌI task trong lượt, không chỉ một task")
    p.add_argument("--loi", action="store_true",
                   help="Chỉ dòng warning/error")
    p.add_argument("--so-dong", dest="so_dong", type=int, default=40,
                   help="Số dòng cuối (0 = tất cả, mặc định 40)")
    p.add_argument("--liet-ke", dest="liet_ke", action="store_true",
                   help="Chỉ liệt kê các lượt có log")
    a = p.parse_args(argv)

    files = _thu_muc_run(a.dag, a.task)
    if a.run:
        files = [f for f in files if a.run in str(f)]
    if not files:
        sys.exit(f"Không có log nào khớp (dag={a.dag}, task={a.task}, run={a.run}).")

    if a.liet_ke:
        for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
            print(f.relative_to(GOC))
        return 0

    if a.tat_ca:
        # Cùng một lượt chạy: lấy run_id của file mới nhất rồi in mọi task trong đó.
        moi = _moi_nhat(files)
        run_dir = next(q for q in moi.parents if q.name.startswith("run_id="))
        for f in sorted(run_dir.rglob("attempt=*.log")):
            _doc(f, a.loi, a.so_dong)
            print()
        return 0

    _doc(_moi_nhat(files), a.loi, a.so_dong)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
