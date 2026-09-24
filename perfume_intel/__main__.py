"""Cho phép chạy `python -m perfume_intel ...`."""

from .cli.app import main

if __name__ == "__main__":
    raise SystemExit(main())
