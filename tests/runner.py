"""Chạy test không cần cài pytest: `python tests/test_mini.py`.

Import module này cũng đặt luôn đường dẫn gốc vào `sys.path`, nên phải đứng
trước các import `perfume_intel.*` trong mỗi file test.
"""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _canh_test_bi_bo_sot(namespace: dict) -> None:
    """Báo động khi file có `def test_` nhiều hơn số hàm thu được.

    Bẫy đã dính thật: thêm test vào CUỐI file, tức là SAU khối
    `if __name__ == "__main__": run(globals())`. Python chạy khối đó trước, lúc
    ấy các hàm mới chưa tồn tại, nên chúng không bao giờ chạy — mà bộ test vẫn
    báo "tất cả PASS". Một test không chạy còn tệ hơn một test đỏ, vì nó trông
    y hệt như đang bảo vệ cái gì đó.
    """
    path = namespace.get("__file__")
    if not path:
        return
    try:
        src = Path(path).read_text(encoding="utf-8")
    except OSError:
        return
    trong_file = len(re.findall(r"^def (test_\w+)", src, re.M))
    thu_duoc = sum(1 for k, v in namespace.items()
                   if k.startswith("test_") and callable(v))
    if trong_file > thu_duoc:
        print(f"CẢNH BÁO: file có {trong_file} hàm `test_` nhưng chỉ chạy được "
              f"{thu_duoc}. Nhiều khả năng có test nằm SAU khối "
              f"`if __name__ == \"__main__\"` nên không bao giờ chạy.")


def run(namespace: dict) -> int:
    """Gọi mọi hàm `test_*` trong namespace, trả về exit code."""
    # Console Windows mặc định là cp1252 -> chữ có dấu làm chết cả lần chạy.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    _canh_test_bi_bo_sot(namespace)

    failed = 0
    for name, fn in sorted(namespace.items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:
            # Bắt MỌI lỗi, không riêng AssertionError. Trước đây một test ném
            # ParseError/TypeError sẽ giết cả lần chạy: những test còn lại không
            # chạy nữa và dòng tổng kết không bao giờ in ra — nhìn vào chỉ thấy
            # một traceback, rất dễ tưởng là hỏng môi trường chứ không phải có
            # test đang đỏ. Gặp thật khi chạy mutation test.
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            print(f"PASS  {name}")

    print("\nKết quả:", "tất cả PASS" if not failed else f"{failed} test lỗi")
    return 1 if failed else 0
