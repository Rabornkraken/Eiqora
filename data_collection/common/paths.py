"""Helpers for building raw object keys."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_date_str(dt: datetime | None = None) -> str:
    target = dt or datetime.now(timezone.utc)
    return target.strftime("%Y-%m-%d")


def utc_datetime_compact(dt: datetime | None = None) -> str:
    target = dt or datetime.now(timezone.utc)
    return target.strftime("%Y%m%dT%H%M%SZ")
