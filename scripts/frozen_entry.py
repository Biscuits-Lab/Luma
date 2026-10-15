"""Absolute-import launcher for PyInstaller; package __main__ stays importable."""
from luma.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
