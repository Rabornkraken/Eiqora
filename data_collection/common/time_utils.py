"""Time utilities for timezone-aware dates."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def today_in_timezone(tz_name: str) -> date:
    tz = ZoneInfo(tz_name)
    return datetime.now(tz).date()
