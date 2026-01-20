#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eiqora_v2.live.trigger_backtest import compute_atr_brackets  # noqa: F401


def main() -> None:
    raise AssertionError("should not reach: module missing")


if __name__ == "__main__":
    main()
