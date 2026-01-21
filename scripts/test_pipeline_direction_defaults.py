#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eiqora_v2.live.pipeline import LiveTradingPipeline
from eiqora_v2.live.trigger_monitor import Trigger
import eiqora_v2.live.analysis_logger as analysis_logger
import eiqora_v2.services.signal_quality as signal_quality
import eiqora_v2.tools.positions as positions


CAPTURED: dict[str, object] = {}


async def fake_is_trigger_processed(details, symbol):
    return False


async def fake_log_analysis(**_kwargs):
    return "analysis-id"


async def fake_log_suppressed_trigger(**_kwargs):
    return None


async def fake_has_open_position(_symbol):
    return False


async def fake_open_position(*, direction, time_stop_days, **_kwargs):
    CAPTURED["direction"] = direction
    CAPTURED["time_stop_days"] = time_stop_days
    return "position-id"


def fake_assess_full_signal_quality(_details):
    return {
        "quality_score": 0.9,
        "quality_level": "HIGH",
        "quality_flags": ["ok"],
    }


class FakeEnricher:
    async def enrich(self, **_kwargs):
        return {
            "profile": {},
            "profile_score": None,
            "market_data": {"hourly_indicators": {"current_price": 100.0}},
            "data_freshness": {"daily_ok": True, "hourly_ok": True},
            "errors": [],
        }


class FakeOrchestrator:
    async def run(self, _initial_state):
        return {
            "decision": {"final_call": "GO", "rule": {}},
            "exit_policy": {"bracket": {"sl_level": 95.0, "tp_level": 110.0}},
            "context": {"current_price": 100.0, "atr14": 2.0},
            "ideas": {
                "ideas": [{"idea_id": "primary", "conviction": "MEDIUM"}],
                "primary_idea_id": "primary",
            },
        }


class FakeSignalManager:
    async def store_signal(self, _signal):
        return "signal-id"


async def run_test() -> None:
    analysis_logger.is_trigger_processed = fake_is_trigger_processed
    analysis_logger.log_analysis = fake_log_analysis
    analysis_logger.log_suppressed_trigger = fake_log_suppressed_trigger
    positions.has_open_position = fake_has_open_position
    positions.open_position = fake_open_position
    signal_quality.assess_full_signal_quality = fake_assess_full_signal_quality

    pipeline = LiveTradingPipeline()
    pipeline.context_enricher = FakeEnricher()
    pipeline.orchestrator = FakeOrchestrator()
    pipeline.signal_manager = FakeSignalManager()

    trigger = Trigger(
        symbol="CAT",
        trigger_type="volatility_compression",
        priority="HIGH",
        details={"technical_score": 0.9},
        detected_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
    )

    result = await pipeline.process_trigger(trigger)
    assert result is not None
    assert CAPTURED["direction"] == "LONG"
    assert CAPTURED["time_stop_days"] == 30


def main() -> None:
    asyncio.run(run_test())


if __name__ == "__main__":
    main()
