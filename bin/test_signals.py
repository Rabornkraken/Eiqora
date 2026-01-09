#!/usr/bin/env python
"""
Convenience wrapper for the signal generation dry-run.
"""

from __future__ import annotations

import runpy
from pathlib import Path


def main() -> None:
    script_path = Path(__file__).with_name("test_signals_no_alpaca.py")
    runpy.run_path(str(script_path), run_name="__main__")


if __name__ == "__main__":
    main()
