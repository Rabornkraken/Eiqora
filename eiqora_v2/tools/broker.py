"""
Alpaca broker integration for paper trading.

Thin wrapper around alpaca-py with graceful degradation:
- If API keys are missing, all functions return None (DB-only mode).
- If alpaca-py is not installed, all functions return None.
- All blocking alpaca-py calls are wrapped in asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Lazy import alpaca-py — not a hard dependency
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, ClosePositionRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus

    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False

_client: Any | None = None


def _is_alpaca_configured() -> bool:
    if not _ALPACA_AVAILABLE:
        return False
    from eiqora_v2.config.settings import get_settings

    settings = get_settings()
    return bool(settings.ALPACA_API_KEY and settings.ALPACA_API_SECRET)


def get_alpaca_client() -> Any | None:
    """Return a singleton TradingClient, or None if not configured."""
    global _client
    if _client is not None:
        return _client
    if not _is_alpaca_configured():
        return None
    from eiqora_v2.config.settings import get_settings

    settings = get_settings()
    _client = TradingClient(
        api_key=settings.ALPACA_API_KEY,
        secret_key=settings.ALPACA_API_SECRET,
        paper=settings.ALPACA_PAPER_TRADING,
    )
    return _client


# ---------------------------------------------------------------------------
# Order submission
# ---------------------------------------------------------------------------

def _submit_market_order_sync(
    symbol: str, qty: float, side: str
) -> dict[str, Any] | None:
    client = get_alpaca_client()
    if client is None:
        return None
    order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
    request = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=order_side,
        time_in_force=TimeInForce.DAY,
    )
    order = client.submit_order(request)
    return {
        "order_id": str(order.id),
        "status": str(order.status),
        "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
        "filled_qty": float(order.filled_qty) if order.filled_qty else None,
    }


async def submit_market_order(
    symbol: str, qty: float, side: str
) -> dict[str, Any] | None:
    """Submit a market order via Alpaca. Returns order dict or None."""
    if not _is_alpaca_configured():
        return None
    try:
        return await asyncio.to_thread(_submit_market_order_sync, symbol, qty, side)
    except Exception as exc:
        logger.warning("Alpaca submit_market_order failed for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Fill polling
# ---------------------------------------------------------------------------

def _wait_for_fill_sync(order_id: str, timeout: float = 10.0, interval: float = 0.5) -> dict[str, Any] | None:
    client = get_alpaca_client()
    if client is None:
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        order = client.get_order_by_id(order_id)
        if order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            return {
                "order_id": str(order.id),
                "status": str(order.status),
                "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
                "filled_qty": float(order.filled_qty) if order.filled_qty else None,
            }
        if order.status in (OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED):
            logger.warning("Alpaca order %s ended with status %s", order_id, order.status)
            return {
                "order_id": str(order.id),
                "status": str(order.status),
                "filled_avg_price": None,
                "filled_qty": None,
            }
        time.sleep(interval)
    logger.warning("Alpaca order %s fill poll timed out after %.1fs", order_id, timeout)
    return None


async def wait_for_fill(order_id: str, timeout: float = 10.0) -> dict[str, Any] | None:
    """Poll until order is filled or timeout. Returns fill dict or None."""
    if not _is_alpaca_configured():
        return None
    try:
        return await asyncio.to_thread(_wait_for_fill_sync, order_id, timeout)
    except Exception as exc:
        logger.warning("Alpaca wait_for_fill failed for %s: %s", order_id, exc)
        return None


# ---------------------------------------------------------------------------
# Close position
# ---------------------------------------------------------------------------

def _close_alpaca_position_sync(symbol: str) -> dict[str, Any] | None:
    client = get_alpaca_client()
    if client is None:
        return None
    order = client.close_position(symbol)
    return {
        "order_id": str(order.id) if hasattr(order, "id") else None,
        "status": str(order.status) if hasattr(order, "status") else "closed",
    }


async def close_alpaca_position(symbol: str) -> dict[str, Any] | None:
    """Close a full position on Alpaca by symbol. Returns order dict or None."""
    if not _is_alpaca_configured():
        return None
    try:
        return await asyncio.to_thread(_close_alpaca_position_sync, symbol)
    except Exception as exc:
        logger.warning("Alpaca close_position failed for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Account info
# ---------------------------------------------------------------------------

def _get_alpaca_account_sync() -> dict[str, Any] | None:
    client = get_alpaca_client()
    if client is None:
        return None
    account = client.get_account()
    return {
        "cash": float(account.cash),
        "equity": float(account.equity),
        "buying_power": float(account.buying_power),
        "portfolio_value": float(account.portfolio_value) if account.portfolio_value else None,
        "currency": str(account.currency),
        "status": str(account.status),
        "pattern_day_trader": bool(account.pattern_day_trader),
    }


async def get_alpaca_account() -> dict[str, Any] | None:
    """Return Alpaca account info or None."""
    if not _is_alpaca_configured():
        return None
    try:
        return await asyncio.to_thread(_get_alpaca_account_sync)
    except Exception as exc:
        logger.warning("Alpaca get_account failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Positions list
# ---------------------------------------------------------------------------

def _get_alpaca_positions_sync() -> list[dict[str, Any]] | None:
    client = get_alpaca_client()
    if client is None:
        return None
    positions = client.get_all_positions()
    return [
        {
            "symbol": str(p.symbol),
            "qty": float(p.qty),
            "side": str(p.side),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "market_value": float(p.market_value),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
        }
        for p in positions
    ]


async def get_alpaca_positions() -> list[dict[str, Any]] | None:
    """Return all Alpaca positions or None."""
    if not _is_alpaca_configured():
        return None
    try:
        return await asyncio.to_thread(_get_alpaca_positions_sync)
    except Exception as exc:
        logger.warning("Alpaca get_positions failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Account sync helper
# ---------------------------------------------------------------------------

async def sync_account_from_alpaca(conn, account_id: str) -> bool:
    """
    Update DB account_state with Alpaca cash/equity values.
    Only call this when ALL positions have alpaca_order_id (no legacy positions).
    Returns True if sync succeeded.
    """
    account = await get_alpaca_account()
    if account is None:
        return False
    try:
        await conn.execute(
            """
            UPDATE account_state
            SET cash_balance = $2,
                equity = $3,
                updated_at = NOW()
            WHERE account_id = $1
            """,
            account_id,
            account["cash"],
            account["equity"],
        )
        return True
    except Exception as exc:
        logger.warning("sync_account_from_alpaca failed: %s", exc)
        return False
