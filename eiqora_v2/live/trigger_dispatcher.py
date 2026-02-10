"""
Unified trigger dispatcher for live pipeline actions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable, Any
from zoneinfo import ZoneInfo

from eiqora_v2.tools.positions import refresh_account_state

logger = logging.getLogger(__name__)

EASTERN_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

WATCHLIST_SOURCES = {"market_bar_daily"}
TRIGGER_SOURCES = {"market_bar_hourly", "yfinance_news", "earnings_event", "sec_filing"}
ACCOUNT_REFRESH_SOURCES = {"market_bar_hourly"}


def is_market_open(dt: datetime) -> bool:
    """Check if a datetime falls within US equity market hours (Mon-Fri 9:30-16:00 ET)."""
    et = dt.astimezone(EASTERN_TZ)
    # Monday=0 ... Friday=4; Saturday=5, Sunday=6
    if et.weekday() >= 5:
        return False
    return MARKET_OPEN <= et.time() < MARKET_CLOSE


@dataclass(frozen=True)
class TriggerEvent:
    sources: set[str]
    scan_time: datetime
    latest_at: datetime | None = None
    reason: str | None = None


def build_event_from_payloads(
    payloads: Iterable[str],
    *,
    now: datetime,
    tz: ZoneInfo,
) -> TriggerEvent:
    sources: set[str] = set()
    latest_at: datetime | None = None

    for raw in payloads:
        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        source = data.get("source")
        if source:
            sources.add(source)

        ts = data.get("latest_at")
        if ts:
            try:
                parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=tz)
                else:
                    parsed = parsed.astimezone(tz)
                if latest_at is None or parsed > latest_at:
                    latest_at = parsed
            except ValueError:
                continue

    scan_time = min(latest_at, now) if latest_at else now
    return TriggerEvent(
        sources=sources,
        scan_time=scan_time,
        latest_at=latest_at,
        reason="ingest",
    )


class TriggerDispatcher:
    """Routes trigger events to the appropriate pipeline actions."""

    def __init__(self, pipeline: Any):
        self.pipeline = pipeline

    async def dispatch(self, event: TriggerEvent) -> None:
        sources = event.sources
        scan_time = event.scan_time

        if not sources:
            logger.debug("Ignoring trigger event with no sources")
            return

        if sources & WATCHLIST_SOURCES:
            try:
                if await self.pipeline._watchlist_exists(scan_time.date()):
                    logger.info("Watchlist already built for %s; skipping rebuild", scan_time.date())
                else:
                    logger.info("Daily bars update received; building watchlist for %s", scan_time.date())
                    await self.pipeline.build_daily_watchlist(scan_time)
            except Exception as exc:
                logger.warning("Failed to build daily watchlist: %s", exc)

        if sources & ACCOUNT_REFRESH_SOURCES:
            try:
                logger.info("Hourly bars update received; refreshing account state")
                await refresh_account_state(asof_time=scan_time, refresh_prices=True)
            except Exception as exc:
                logger.warning("Failed to refresh account state: %s", exc)

        if sources & TRIGGER_SOURCES:
            if not is_market_open(scan_time):
                logger.info(
                    "Skipping trigger scan — outside market hours (%s ET)",
                    scan_time.astimezone(EASTERN_TZ).strftime("%a %H:%M"),
                )
            else:
                logger.info(
                    "Trigger event received (%s); running scans",
                    ", ".join(sorted(sources)),
                )
                await self.pipeline.monitor_positions(scan_time)
                await self.pipeline.run_trigger_scan(scan_time)
        elif not (sources & WATCHLIST_SOURCES):
            logger.debug("Ignoring trigger sources: %s", sorted(sources))
