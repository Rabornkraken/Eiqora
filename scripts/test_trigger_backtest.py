#!/usr/bin/env python3

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eiqora_v2.live.trigger_backtest import (
    compute_atr_brackets,
    resolve_outcome,
    prepare_trigger_detail,
)
from eiqora_v2.live.backtest_triggers_only import build_hourly_bars_query


def main() -> None:
    entry = 100.0
    atr14 = 2.0
    stop_loss, take_profit = compute_atr_brackets(entry, atr14)
    assert stop_loss == 97.0
    assert take_profit == 106.0

    bar_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    bars = [(bar_time, 107.0, 99.0, 106.5)]
    outcome = resolve_outcome(bar_time, entry, stop_loss, take_profit, bars)
    assert outcome["outcome"] == "TP_HIT"

    assert prepare_trigger_detail({"a": 1}) is not None

    query, params = build_hourly_bars_query("2025-05-01", "2025-06-01", None)
    assert "datetime::date >= %s" in query
    assert "datetime::date <= %s" in query
    assert params[0] == "2025-05-01"
    assert params[1] == "2025-06-01"


if __name__ == "__main__":
    main()
