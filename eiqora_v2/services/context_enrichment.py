"""
Context enrichment bridge for live/backtest analysis.

Preloads deterministic data (profile + market data) and computes freshness
metadata so the LLM pipeline can make consistent decisions without redundant
DB calls.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any

from eiqora_v2.services.profile_generator import ProfileGenerator
from eiqora_v2.tools.prices import (
    get_hourly_indicators,
    get_indicators,
    get_price_levels,
    get_prices,
)

logger = logging.getLogger(__name__)

DAILY_LOOKBACK_DAYS = int(os.getenv("CONTEXT_DAILY_LOOKBACK_DAYS", "60"))
PRICE_LOOKBACK_DAYS = int(os.getenv("CONTEXT_PRICE_LOOKBACK_DAYS", "60"))
MAX_DAILY_GAP_DAYS = int(os.getenv("CONTEXT_MAX_DAILY_GAP_DAYS", "4"))
MAX_HOURLY_GAP_HOURS = int(os.getenv("CONTEXT_MAX_HOURLY_GAP_HOURS", "3"))


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _compute_freshness(
    *,
    daily: dict[str, Any] | None,
    hourly: dict[str, Any] | None,
    asof_time: datetime,
) -> dict[str, Any]:
    asof_date = asof_time.date()
    asof_ts = _as_utc(asof_time)

    daily_last = _parse_date(daily.get("last_date") if daily else None)
    daily_gap_days = (asof_date - daily_last).days if daily_last else None
    daily_ok = daily_gap_days is not None and daily_gap_days <= MAX_DAILY_GAP_DAYS

    hourly_last = _parse_datetime(hourly.get("bar_time") if hourly else None)
    hourly_last = _as_utc(hourly_last)
    hourly_gap_hours = None
    if hourly_last and asof_ts:
        hourly_gap_hours = (asof_ts - hourly_last).total_seconds() / 3600.0
    hourly_ok = hourly_gap_hours is not None and hourly_gap_hours <= MAX_HOURLY_GAP_HOURS

    return {
        "daily_last_date": daily_last.isoformat() if daily_last else None,
        "daily_gap_days": daily_gap_days,
        "daily_ok": daily_ok,
        "hourly_last_time": hourly_last.isoformat() if hourly_last else None,
        "hourly_gap_hours": hourly_gap_hours,
        "hourly_ok": hourly_ok,
        "daily_error": daily.get("error") if daily else None,
        "hourly_error": hourly.get("error") if hourly else None,
    }


class ContextEnricher:
    """Preload profile + market data for downstream agents."""

    def __init__(self, profile_generator: ProfileGenerator | None = None):
        self.profile_generator = profile_generator or ProfileGenerator()

    async def enrich(
        self,
        *,
        symbol: str,
        asof_time: datetime,
        include_profile: bool = True,
    ) -> dict[str, Any]:
        profile_dict = None
        profile_score = None
        errors: list[str] = []

        if include_profile:
            try:
                profile = await self.profile_generator.get_profile(symbol)
                profile_dict = profile.model_dump()
                profile_score = profile.profile_score
            except Exception as exc:
                logger.warning("Context enrichment profile failed for %s: %s", symbol, exc)
                errors.append(f"profile: {exc}")

        try:
            daily = await get_indicators(symbol, DAILY_LOOKBACK_DAYS, asof_time)
        except Exception as exc:
            logger.warning("Context enrichment indicators failed for %s: %s", symbol, exc)
            daily = {"error": str(exc)}
            errors.append(f"daily_indicators: {exc}")

        try:
            prices = await get_prices(symbol, PRICE_LOOKBACK_DAYS, asof_time)
        except Exception as exc:
            logger.warning("Context enrichment prices failed for %s: %s", symbol, exc)
            prices = []
            errors.append(f"prices: {exc}")

        try:
            levels = await get_price_levels(symbol, DAILY_LOOKBACK_DAYS, asof_time)
        except Exception as exc:
            logger.warning("Context enrichment levels failed for %s: %s", symbol, exc)
            levels = {"error": str(exc)}
            errors.append(f"price_levels: {exc}")

        try:
            hourly = await get_hourly_indicators(symbol, asof_time.date(), asof_time)
        except Exception as exc:
            logger.warning("Context enrichment hourly failed for %s: %s", symbol, exc)
            hourly = {"error": str(exc)}
            errors.append(f"hourly_indicators: {exc}")

        freshness = _compute_freshness(
            daily=daily,
            hourly=hourly,
            asof_time=asof_time,
        )

        return {
            "profile": profile_dict,
            "profile_score": profile_score,
            "market_data": {
                "daily_indicators": daily,
                "prices": prices,
                "hourly_indicators": hourly,
                "price_levels": levels,
            },
            "data_freshness": freshness,
            "errors": errors,
        }
