from datetime import datetime, timezone

import pytest

from psycopg.types.json import Json

from eiqora_v2.live.trigger_backtest import (
    compute_atr_brackets,
    resolve_outcome,
    build_result_row,
    prepare_trigger_detail,
)


def test_compute_atr_brackets():
    entry = 100.0
    atr14 = 2.0
    stop_loss, take_profit = compute_atr_brackets(entry, atr14, sl_mult=1.5, tp_mult=3.0)
    assert stop_loss == 97.0
    assert take_profit == 106.0


def test_resolve_outcome_tp_hit_first():
    entry_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    bars = [
        (entry_time, 107.0, 99.0, 106.5),
    ]
    outcome = resolve_outcome(entry_time, 100.0, 97.0, 106.0, bars)
    assert outcome["outcome"] == "TP_HIT"
    assert outcome["bars_to_outcome"] == 1


def test_resolve_outcome_same_bar_tie_sl_wins():
    entry_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    bars = [
        (entry_time, 106.0, 96.0, 100.0),
    ]
    outcome = resolve_outcome(entry_time, 100.0, 97.0, 106.0, bars)
    assert outcome["outcome"] == "SL_HIT"
    assert outcome["same_bar_tie"] is True


def test_resolve_outcome_no_hit_uses_final_close():
    entry_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    bars = [
        (entry_time, 101.0, 99.0, 100.5),
        (datetime(2026, 1, 2, 16, 30, tzinfo=timezone.utc), 101.5, 99.5, 101.0),
    ]
    outcome = resolve_outcome(entry_time, 100.0, 97.0, 106.0, bars)
    assert outcome["outcome"] == "NO_HIT"
    assert outcome["realized_pnl_pct"] == pytest.approx(0.01)


def test_build_result_row_defaults():
    run_id = "00000000-0000-0000-0000-000000000001"
    trigger_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    row = build_result_row(
        run_id=run_id,
        run_name="test-run",
        started_at=trigger_time,
        symbol="AAPL",
        trigger_type="vwap_reclaim",
        trigger_priority="HIGH",
        trigger_time=trigger_time,
        trigger_detail={"foo": "bar"},
        entry_price=100.0,
        atr14=2.0,
        stop_loss=97.0,
        take_profit=106.0,
        outcome="TP_HIT",
        outcome_time=trigger_time,
        bars_to_outcome=1,
        max_favorable_pct=0.07,
        max_adverse_pct=-0.03,
        realized_pnl_pct=0.06,
        sl_mult=1.5,
        tp_mult=3.0,
    )
    assert row[0] == run_id
    assert row[2] == "AAPL"
    assert row[5] == trigger_time
    assert row[9] == 1.5
    assert row[10] == 3.0


def test_prepare_trigger_detail_wraps_dict():
    value = prepare_trigger_detail({"a": 1})
    assert isinstance(value, Json)
