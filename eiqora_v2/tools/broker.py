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
    from alpaca.trading.requests import MarketOrderRequest, ClosePositionRequest, GetOrdersRequest
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


# ---------------------------------------------------------------------------
# Closed orders / trades (for Trading History)
# ---------------------------------------------------------------------------

def _get_alpaca_closed_orders_sync(limit: int = 200) -> list[dict[str, Any]] | None:
    client = get_alpaca_client()
    if client is None:
        return None
    request = GetOrdersRequest(status="closed", limit=limit)
    orders = client.get_orders(request)
    logger.info("Alpaca returned %d closed orders", len(orders))
    return [
        {
            "order_id": str(o.id),
            "symbol": str(o.symbol),
            "side": o.side.value,
            "qty": float(o.filled_qty) if o.filled_qty else 0,
            "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
            "filled_at": o.filled_at.isoformat() if o.filled_at else None,
            "status": o.status.value,
        }
        for o in orders
        if o.status.value == "filled"
    ]


async def get_alpaca_closed_orders(limit: int = 200) -> list[dict[str, Any]] | None:
    """Return filled orders from Alpaca."""
    if not _is_alpaca_configured():
        return None
    try:
        return await asyncio.to_thread(_get_alpaca_closed_orders_sync, limit)
    except Exception as exc:
        logger.warning("Alpaca get_closed_orders failed: %s", exc, exc_info=True)
        return None


async def get_alpaca_closed_trades() -> list[dict[str, Any]]:
    """Match buy/sell order pairs into closed trade records."""
    orders = await get_alpaca_closed_orders()
    if not orders:
        return []

    buys: dict[str, list] = {}
    sells: dict[str, list] = {}
    for o in orders:
        bucket = buys if o["side"] == "buy" else sells
        bucket.setdefault(o["symbol"], []).append(o)

    for sym in buys:
        buys[sym].sort(key=lambda x: x["filled_at"] or "")
    for sym in sells:
        sells[sym].sort(key=lambda x: x["filled_at"] or "")

    trades = []
    for sym, sell_list in sells.items():
        buy_list = buys.get(sym, [])
        buy_idx = 0
        for sell in sell_list:
            if buy_idx >= len(buy_list):
                break
            buy = buy_list[buy_idx]
            buy_idx += 1
            entry_price = buy["filled_avg_price"] or 0
            exit_price = sell["filled_avg_price"] or 0
            qty = sell["qty"]
            pnl = (exit_price - entry_price) * qty
            pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0
            trades.append({
                "symbol": sym,
                "direction": "LONG",
                "shares": qty,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "entry_date": buy["filled_at"],
                "exit_date": sell["filled_at"],
                "realized_pnl": round(pnl, 2),
                "realized_pnl_pct": round(pnl_pct, 2),
            })
    trades.sort(key=lambda t: t["exit_date"] or "", reverse=True)
    return trades


# ---------------------------------------------------------------------------
# Portfolio history (for equity chart)
# ---------------------------------------------------------------------------

def _get_alpaca_portfolio_history_sync(period: str = "1M", timeframe: str = "1D") -> list[dict[str, Any]] | None:
    """Fetch Alpaca's native portfolio equity history."""
    from eiqora_v2.config.settings import get_settings
    import requests
    from datetime import datetime as _dt, timezone as _tz

    settings = get_settings()
    if not (settings.ALPACA_API_KEY and settings.ALPACA_API_SECRET):
        return None
    base_url = settings.ALPACA_BASE_URL.rstrip("/")
    url = f"{base_url}/v2/account/portfolio/history"
    headers = {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET,
    }
    params = {"period": period, "timeframe": timeframe}
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    timestamps = data.get("timestamp", []) or []
    equities = data.get("equity", []) or []
    pls = data.get("profit_loss", []) or []
    out: list[dict[str, Any]] = []
    for i, ts in enumerate(timestamps):
        if i >= len(equities) or equities[i] is None:
            continue
        dt = _dt.fromtimestamp(ts, tz=_tz.utc)
        out.append({
            "timestamp": dt.isoformat(),
            "equity": float(equities[i]),
            "profit_loss": float(pls[i]) if i < len(pls) and pls[i] is not None else 0.0,
        })
    return out


async def get_alpaca_portfolio_history(period: str = "1M", timeframe: str = "1D") -> list[dict[str, Any]] | None:
    """Return Alpaca's own daily equity history or None."""
    if not _is_alpaca_configured():
        return None
    try:
        return await asyncio.to_thread(_get_alpaca_portfolio_history_sync, period, timeframe)
    except Exception as exc:
        logger.warning("Alpaca portfolio_history failed: %s", exc)
        return None
