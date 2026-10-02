"""Chạy test không cần cài pytest: `python tests/test_mini.py`.

Import module này cũng đặt luôn đường dẫn gốc vào `sys.path`, nên phải đứng
trước các import `perfume_intel.*` trong mỗi file test.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run(namespace: dict) -> int:
    """Gọi mọi hàm `test_*` trong namespace, trả về exit code."""
    # Console Windows mặc định là cp1252 -> chữ có dấu làm chết cả lần chạy.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
