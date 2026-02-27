"""
Real-time quote utility using Finnhub API.

Provides current prices for trade execution with a short TTL cache
to avoid duplicate calls during batch position refreshes.
"""

import logging
import time as _time
from datetime import datetime, time
from zoneinfo import ZoneInfo

import httpx

from eiqora_v2.config.settings import get_settings

logger = logging.getLogger(__name__)

_EASTERN_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)

_CACHE_TTL = 5  # seconds
_TIMEOUT = 5  # seconds

# Simple TTL cache: {symbol: (price, timestamp)}
_quote_cache: dict[str, tuple[float, float]] = {}


def _is_market_hours() -> bool:
    """Return True if current time is within US equity market hours (Mon-Fri 9:30-16:00 ET)."""
    now_et = datetime.now(_EASTERN_TZ)
    if now_et.weekday() >= 5:
        return False
    return _MARKET_OPEN <= now_et.time() < _MARKET_CLOSE


async def get_finnhub_quote(symbol: str) -> float | None:
    """Fetch real-time quote from Finnhub.

    Returns the current price if available, or None on any failure
    (missing API key, outside market hours, network error, etc.).
    Callers should fall back to DB-based prices when None is returned.
    """
    if not _is_market_hours():
        return None

    api_key = get_settings().FINNHUB_API_KEY
    if not api_key:
        return None

    # Check cache
    now = _time.monotonic()
    cached = _quote_cache.get(symbol)
    if cached is not None:
        price, ts = cached
        if now - ts < _CACHE_TTL:
            return price

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": api_key},
            )
            resp.raise_for_status()
            data = resp.json()

        current_price = data.get("c")
        if current_price is not None and float(current_price) > 0:
            price = float(current_price)
            _quote_cache[symbol] = (price, now)
            logger.debug("Finnhub quote %s: $%.4f", symbol, price)
            return price

        logger.warning("Finnhub returned zero/null price for %s: %s", symbol, data)
        return None

    except Exception as exc:
        logger.warning("Finnhub quote failed for %s: %s", symbol, exc)
        return None
