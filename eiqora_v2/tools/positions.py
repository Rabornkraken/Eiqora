"""
Position management utilities backed by the database.

Provides helpers to open/close positions and query active positions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
import os
from typing import Any

from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.prices import get_indicators

logger = logging.getLogger(__name__)

DEFAULT_ACCOUNT_ID = os.getenv("EIQORA_ACCOUNT_ID", "main")
DEFAULT_BASE_CURRENCY = os.getenv("ACCOUNT_BASE_CURRENCY", "USD")
DEFAULT_STARTING_EQUITY = float(os.getenv("ACCOUNT_STARTING_EQUITY", "10000"))
DEFAULT_CASH_BALANCE = float(os.getenv("ACCOUNT_CASH_BALANCE", str(DEFAULT_STARTING_EQUITY)))


def _normalize_position_pct(value: Any) -> float | None:
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return None
    if pct <= 0:
        return pct
    if pct > 1.0:
        pct = pct / 100.0
    if pct > 1.0:
        pct = 1.0
    return pct


async def _ensure_account_state(conn, account_id: str = DEFAULT_ACCOUNT_ID) -> dict[str, Any]:
    row = await conn.fetchrow(
        "SELECT * FROM account_state WHERE account_id = $1",
        account_id,
    )
    if row:
        return dict(row)

    starting_equity = DEFAULT_STARTING_EQUITY
    cash_balance = DEFAULT_CASH_BALANCE
    equity = starting_equity

    await conn.execute(
        """
        INSERT INTO account_state (
            account_id,
            base_currency,
            starting_equity,
            cash_balance,
            equity,
            realized_pnl,
            unrealized_pnl,
            gross_exposure,
            net_exposure,
            positions_count
        ) VALUES ($1, $2, $3, $4, $5, 0, 0, 0, 0, 0)
        """,
        account_id,
        DEFAULT_BASE_CURRENCY,
        starting_equity,
        cash_balance,
        equity,
    )

    return {
        "account_id": account_id,
        "base_currency": DEFAULT_BASE_CURRENCY,
        "starting_equity": starting_equity,
        "cash_balance": cash_balance,
        "equity": equity,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "gross_exposure": 0.0,
        "net_exposure": 0.0,
        "positions_count": 0,
    }


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        return getattr(row, key, None)


def _resolve_notional_and_shares(
    row: Any,
    *,
    base_equity: float,
    entry_price: float,
) -> tuple[float | None, float | None]:
    notional_value = _row_value(row, "notional_value")
    shares = _row_value(row, "shares")
    size_pct = _normalize_position_pct(_row_value(row, "position_size_pct"))

    if notional_value is None and size_pct is not None:
        try:
            notional_value = float(base_equity) * float(size_pct)
        except (TypeError, ValueError):
            notional_value = None

    if shares is None and notional_value is not None and entry_price > 0:
        shares = float(notional_value) / float(entry_price)

    return (
        float(notional_value) if notional_value is not None else None,
        float(shares) if shares is not None else None,
    )


async def _refresh_account_state(
    conn,
    *,
    account_id: str = DEFAULT_ACCOUNT_ID,
    asof_time: datetime | None = None,
) -> None:
    asof_time = asof_time or datetime.now(timezone.utc)
    state = await _ensure_account_state(conn, account_id)

    cash_balance = state.get("cash_balance")
    if cash_balance is None:
        cash_balance = state.get("starting_equity", DEFAULT_STARTING_EQUITY)

    base_equity = state.get("starting_equity") or DEFAULT_STARTING_EQUITY

    rows = await conn.fetch(
        """
        SELECT position_id, symbol, direction, entry_price, current_price,
               position_size_pct, shares, notional_value, unrealized_pnl
        FROM position
        WHERE status = 'ACTIVE'
        """
    )

    unrealized_pnl = 0.0
    gross_exposure = 0.0
    net_exposure = 0.0
    current_value_total = 0.0

    for row in rows:
        entry_price = float(row["entry_price"])
        notional_value, shares = _resolve_notional_and_shares(
            row,
            base_equity=base_equity,
            entry_price=entry_price,
        )
        if notional_value is None or shares is None:
            continue

        # Use the unrealized_pnl from the position table if available
        # This is more accurate than recalculating since positions may have been
        # refreshed with latest prices just before this function is called
        if row["unrealized_pnl"] is not None:
            position_pnl = float(row["unrealized_pnl"])
        else:
            # Fallback: calculate from current_price
            current_price = row["current_price"]
            if current_price is None:
                current_price = entry_price
            current_price = float(current_price)
            
            direction = _normalize_direction(row["direction"])
            pnl_per_share, _ = _calculate_pnl(entry_price, current_price, direction)
            position_pnl = pnl_per_share * shares
        
        unrealized_pnl += position_pnl

        current_value = notional_value + position_pnl
        current_value_total += current_value

        gross_exposure += abs(notional_value)
        net_exposure += notional_value if _normalize_direction(row["direction"]) != "SHORT" else -notional_value

    equity = float(cash_balance) + current_value_total
    realized_pnl = state.get("realized_pnl") or 0.0

    await conn.execute(
        """
        UPDATE account_state
        SET cash_balance = $2,
            equity = $3,
            realized_pnl = $4,
            unrealized_pnl = $5,
            gross_exposure = $6,
            net_exposure = $7,
            positions_count = $8,
            updated_at = NOW()
        WHERE account_id = $1
        """,
        account_id,
        cash_balance,
        equity,
        realized_pnl,
        unrealized_pnl,
        gross_exposure,
        net_exposure,
        len(rows),
    )

    await conn.execute(
        """
        INSERT INTO account_snapshot (
            account_id,
            asof_ts,
            cash_balance,
            equity,
            realized_pnl,
            unrealized_pnl,
            gross_exposure,
            net_exposure,
            positions_count
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        account_id,
        asof_time,
        cash_balance,
        equity,
        realized_pnl,
        unrealized_pnl,
        gross_exposure,
        net_exposure,
        len(rows),
    )


def _normalize_direction(direction: str | None) -> str:
    if not direction:
        return "LONG"
    return direction.upper()


def _to_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _calculate_pnl(entry_price: float, current_price: float, direction: str) -> tuple[float, float]:
    """Return (pnl_per_share, pnl_pct) with pnl_pct signed by direction."""
    if entry_price <= 0:
        return 0.0, 0.0
    if direction == "SHORT":
        pnl_per_share = entry_price - current_price
    else:
        pnl_per_share = current_price - entry_price
    pnl_pct = pnl_per_share / entry_price
    return pnl_per_share, pnl_pct


def _days_held(entry_date: Any, asof_time: datetime) -> int:
    entry_day = entry_date.date() if isinstance(entry_date, datetime) else entry_date
    asof_day = asof_time.date() if isinstance(asof_time, datetime) else asof_time
    if entry_day is None or asof_day is None:
        return 0
    return (asof_day - entry_day).days


async def _get_latest_hourly_price(symbol: str, asof_time: datetime | None) -> float | None:
    asof_time = asof_time or datetime.now(timezone.utc)
    if asof_time.tzinfo is None:
        asof_ts = asof_time.replace(tzinfo=timezone.utc)
    else:
        asof_ts = asof_time.astimezone(timezone.utc)
    async with get_connection() as conn:
        try:
            row = await conn.fetchrow(
                """
                SELECT close
                FROM market_bar_hourly
                WHERE symbol = $1
                  AND datetime <= $2
                ORDER BY datetime DESC
                LIMIT 1
                """,
                symbol,
                asof_ts,
            )
        except Exception as exc:
            logger.debug("Failed to fetch hourly price for %s: %s", symbol, exc)
            return None
    if row and row["close"] is not None:
        return float(row["close"])
    return None


async def _get_latest_price(symbol: str, asof_time: datetime | None) -> float | None:
    try:
        hourly_price = await _get_latest_hourly_price(symbol, asof_time)
        if hourly_price is not None:
            return hourly_price
        indicators = await get_indicators(symbol, 30, asof_time)
        if not indicators or indicators.get("error"):
            return None
        return indicators.get("current_price")
    except Exception as exc:
        logger.debug("Failed to fetch latest price for %s: %s", symbol, exc)
        return None


async def open_position(
    *,
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float | None,
    take_profit: float | None,
    entry_time: datetime | None = None,
    time_stop_days: int | None = None,
    conviction: str | None = None,
    reasoning: str | None = None,
    position_size_pct: float | None = None,
    signal_id: str | None = None,
) -> str | None:
    """Open a new position if one isn't already active for the symbol."""
    entry_time = entry_time or datetime.now(timezone.utc)
    entry_time_db = _to_naive_utc(entry_time)
    direction = _normalize_direction(direction)
    time_stop_date = None
    if time_stop_days is None:
        time_stop_days = 30
    time_stop_date = entry_time + timedelta(days=time_stop_days)
    time_stop_date_db = _to_naive_utc(time_stop_date)

    async with get_connection() as conn:
        account_state = await _ensure_account_state(conn)
        base_equity = account_state.get("starting_equity") or DEFAULT_STARTING_EQUITY
        notional_value = None
        shares = None
        position_size_pct = _normalize_position_pct(position_size_pct)
        if position_size_pct is not None and entry_price > 0:
            try:
                notional_value = float(base_equity) * float(position_size_pct)
                shares = float(notional_value) / float(entry_price)
            except (TypeError, ValueError):
                notional_value = None
                shares = None

        existing = await conn.fetchrow(
            """
            SELECT position_id
            FROM position
            WHERE symbol = $1 AND status = 'ACTIVE'
            ORDER BY entry_date DESC
            LIMIT 1
            """,
            symbol,
        )
        if existing:
            logger.info("Position already active for %s; skipping open", symbol)
            return str(existing["position_id"])

        position_id = await conn.fetchval(
            """
            INSERT INTO position (
                symbol,
                direction,
                entry_date,
                entry_price,
                shares,
                notional_value,
                current_price,
                stop_loss,
                take_profit,
                time_stop_date,
                status,
                conviction,
                reasoning,
                position_size_pct,
                signal_id,
                last_updated
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'ACTIVE', $11, $12, $13, $14, NOW())
            RETURNING position_id
            """,
            symbol,
            direction,
            entry_time_db,
            entry_price,
            shares,
            notional_value,
            entry_price,
            stop_loss,
            take_profit,
            time_stop_date_db,
            conviction,
            reasoning,
            position_size_pct,
            signal_id,
        )
        if notional_value is not None:
            cash_balance = account_state.get("cash_balance")
            if cash_balance is None:
                cash_balance = base_equity
            cash_balance = float(cash_balance) - float(notional_value)
            await conn.execute(
                """
                UPDATE account_state
                SET cash_balance = $2,
                    updated_at = NOW()
                WHERE account_id = $1
                """,
                DEFAULT_ACCOUNT_ID,
                cash_balance,
            )
            await _refresh_account_state(conn, asof_time=entry_time)
        logger.info("Opened position %s for %s", position_id, symbol)
        return str(position_id) if position_id else None


async def close_position(
    *,
    symbol: str | None = None,
    position_id: str | None = None,
    exit_price: float | None = None,
    exit_reason: str | None = None,
    exit_type: str = "MANUAL",
    exit_time: datetime | None = None,
) -> dict[str, Any] | None:
    """Close an active position and record exit metrics."""
    exit_time = exit_time or datetime.now(timezone.utc)
    exit_time_db = _to_naive_utc(exit_time)

    async with get_connection() as conn:
        if position_id:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM position
                WHERE position_id = $1 AND status = 'ACTIVE'
                """,
                position_id,
            )
        elif symbol:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM position
                WHERE symbol = $1 AND status = 'ACTIVE'
                ORDER BY entry_date DESC
                LIMIT 1
                """,
                symbol,
            )
        else:
            return None

        if not row:
            return None

        direction = _normalize_direction(row["direction"])
        entry_price = float(row["entry_price"])
        stop_loss = float(row["stop_loss"]) if row["stop_loss"] is not None else None
        account_state = await _ensure_account_state(conn)
        base_equity = account_state.get("starting_equity") or DEFAULT_STARTING_EQUITY
        notional_value, shares = _resolve_notional_and_shares(
            row,
            base_equity=base_equity,
            entry_price=entry_price,
        )
        if _row_value(row, "shares") is None and shares is not None:
            await conn.execute(
                """
                UPDATE position
                SET shares = $2,
                    notional_value = $3,
                    last_updated = NOW()
                WHERE position_id = $1
                """,
                row["position_id"],
                shares,
                notional_value,
            )

        if exit_price is None:
            latest = await _get_latest_price(row["symbol"], exit_time)
            exit_price = latest if latest is not None else float(row["current_price"] or entry_price)

        pnl_per_share, pnl_pct = _calculate_pnl(entry_price, float(exit_price), direction)
        if shares is None:
            realized_pnl = pnl_per_share
        else:
            realized_pnl = pnl_per_share * float(shares)
        realized_pnl_pct = pnl_pct * 100

        r_multiple = None
        if stop_loss is not None and entry_price:
            risk_per_share = abs(entry_price - stop_loss)
            if risk_per_share > 0:
                r_multiple = pnl_per_share / risk_per_share

        max_profit_pct = row["max_profit_pct"] if row["max_profit_pct"] is not None else 0
        max_loss_pct = row["max_loss_pct"] if row["max_loss_pct"] is not None else 0
        max_profit = _row_value(row, "max_profit") if _row_value(row, "max_profit") is not None else 0
        max_drawdown = _row_value(row, "max_drawdown") if _row_value(row, "max_drawdown") is not None else 0
        max_profit_pct = max(max_profit_pct, realized_pnl_pct)
        max_loss_pct = min(max_loss_pct, realized_pnl_pct)
        if realized_pnl >= 0:
            max_profit = max(max_profit, realized_pnl)
        else:
            max_drawdown = min(max_drawdown, realized_pnl)

        await conn.execute(
            """
            UPDATE position
            SET status = 'CLOSED',
                current_price = $2,
                exit_price = $3,
                exit_date = $4,
                realized_pnl_pct = $5,
                realized_pnl = $6,
                r_multiple = $7,
                exit_reason = $8,
                exit_type = $9,
                max_profit_pct = $10,
                max_loss_pct = $11,
                max_profit = $12,
                max_drawdown = $13,
                last_updated = NOW()
            WHERE position_id = $1
            """,
            row["position_id"],
            exit_price,
            exit_price,
            exit_time_db,
            realized_pnl_pct,
            realized_pnl,
            r_multiple,
            exit_reason,
            exit_type,
            max_profit_pct,
            max_loss_pct,
            max_profit,
            max_drawdown,
        )

        if notional_value is not None:
            cash_balance = account_state.get("cash_balance")
            if cash_balance is None:
                cash_balance = account_state.get("equity") or base_equity
            cash_balance = float(cash_balance) + float(notional_value) + float(realized_pnl)
            realized_total = float(account_state.get("realized_pnl") or 0) + float(realized_pnl)
            await conn.execute(
                """
                UPDATE account_state
                SET cash_balance = $2,
                    realized_pnl = $3,
                    updated_at = NOW()
                WHERE account_id = $1
                """,
                DEFAULT_ACCOUNT_ID,
                cash_balance,
                realized_total,
            )
            await _refresh_account_state(conn, asof_time=exit_time)

        return {
            "position_id": str(row["position_id"]),
            "symbol": row["symbol"],
            "exit_price": float(exit_price),
            "exit_date": exit_time,
            "realized_pnl_pct": realized_pnl_pct,
            "realized_pnl": realized_pnl,
            "r_multiple": r_multiple,
            "exit_reason": exit_reason,
            "exit_type": exit_type,
            "max_profit": max_profit,
            "max_drawdown": max_drawdown,
        }


async def _refresh_position_row(
    conn,
    row,
    asof_time: datetime | None,
    *,
    base_equity: float | None = None,
) -> dict[str, Any]:
    symbol = row["symbol"]
    entry_price = float(row["entry_price"])
    direction = _normalize_direction(row["direction"])
    current_price = row["current_price"]

    latest = await _get_latest_price(symbol, asof_time)
    if latest is not None:
        current_price = latest

    if current_price is None:
        current_price = entry_price

    pnl_per_share, pnl_pct = _calculate_pnl(entry_price, float(current_price), direction)
    base_equity = base_equity or DEFAULT_STARTING_EQUITY
    notional_value, shares = _resolve_notional_and_shares(
        row,
        base_equity=base_equity,
        entry_price=entry_price,
    )
    if _row_value(row, "shares") is None and shares is not None:
        await conn.execute(
            """
            UPDATE position
            SET shares = $2,
                notional_value = $3,
                last_updated = NOW()
            WHERE position_id = $1
            """,
            row["position_id"],
            shares,
            notional_value,
        )
    if shares is None:
        unrealized_pnl = pnl_per_share
    else:
        unrealized_pnl = pnl_per_share * float(shares)
    unrealized_pnl_pct = pnl_pct * 100

    max_profit_pct = row["max_profit_pct"] if row["max_profit_pct"] is not None else 0
    max_loss_pct = row["max_loss_pct"] if row["max_loss_pct"] is not None else 0
    max_profit = _row_value(row, "max_profit") if _row_value(row, "max_profit") is not None else 0
    max_drawdown = _row_value(row, "max_drawdown") if _row_value(row, "max_drawdown") is not None else 0

    max_profit_pct = max(max_profit_pct, unrealized_pnl_pct)
    max_loss_pct = min(max_loss_pct, unrealized_pnl_pct)
    if unrealized_pnl >= 0:
        max_profit = max(max_profit, unrealized_pnl)
    else:
        max_drawdown = min(max_drawdown, unrealized_pnl)

    await conn.execute(
        """
        UPDATE position
        SET current_price = $2,
            unrealized_pnl_pct = $3,
            unrealized_pnl = $4,
            max_profit_pct = $5,
            max_loss_pct = $6,
            max_profit = $7,
            max_drawdown = $8,
            last_updated = NOW()
        WHERE position_id = $1
        """,
        row["position_id"],
        current_price,
        unrealized_pnl_pct,
        unrealized_pnl,
        max_profit_pct,
        max_loss_pct,
        max_profit,
        max_drawdown,
    )

    days_held = _days_held(row["entry_date"], asof_time) if asof_time else 0
    position = {
        "position_id": str(row["position_id"]),
        "symbol": symbol,
        "direction": direction,
        "side": "short" if direction == "SHORT" else "long",
        "entry_date": row["entry_date"],
        "entry_price": entry_price,
        "current_price": float(current_price),
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "unrealized_pl": unrealized_pnl,
        "unrealized_plpc": pnl_pct,
        "take_profit": float(row["take_profit"]) if row["take_profit"] is not None else None,
        "stop_loss": float(row["stop_loss"]) if row["stop_loss"] is not None else None,
        "time_stop_date": row["time_stop_date"],
        "days_held": days_held,
        "status": row["status"],
        "conviction": row["conviction"],
        "reasoning": row["reasoning"],
        "size_pct": float(row["position_size_pct"]) if row["position_size_pct"] is not None else None,
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "max_profit": max_profit,
        "max_drawdown": max_drawdown,
        "shares": float(shares) if shares is not None else None,
    }
    return position


async def get_open_positions(
    asof_time: datetime | None = None,
    refresh_prices: bool = True,
) -> list[dict[str, Any]]:
    """
    Get active positions from the database.
    Optionally refresh current prices and unrealized PnL.
    """
    if asof_time is None:
        asof_time = datetime.now(timezone.utc)

    async with get_connection() as conn:
        account_state = await _ensure_account_state(conn)
        base_equity = account_state.get("equity") or account_state.get("starting_equity") or DEFAULT_STARTING_EQUITY
        rows = await conn.fetch(
            """
            SELECT *
            FROM position
            WHERE status = 'ACTIVE'
            ORDER BY entry_date DESC
            """
        )

        if not rows:
            return []

        positions = []
        for row in rows:
            if refresh_prices:
                position = await _refresh_position_row(conn, row, asof_time, base_equity=base_equity)
            else:
                position = {
                    "position_id": str(row["position_id"]),
                    "symbol": row["symbol"],
                    "direction": _normalize_direction(row["direction"]),
                    "side": "short" if _normalize_direction(row["direction"]) == "SHORT" else "long",
                    "entry_date": row["entry_date"],
                    "entry_price": float(row["entry_price"]),
                    "current_price": float(row["current_price"]) if row["current_price"] is not None else None,
                    "unrealized_pnl": float(row["unrealized_pnl"]) if row["unrealized_pnl"] is not None else 0.0,
                    "unrealized_pnl_pct": float(row["unrealized_pnl_pct"]) if row["unrealized_pnl_pct"] is not None else 0.0,
                    "unrealized_pl": float(row["unrealized_pnl"]) if row["unrealized_pnl"] is not None else 0.0,
                    "unrealized_plpc": float(row["unrealized_pnl_pct"]) / 100 if row["unrealized_pnl_pct"] is not None else 0.0,
                    "take_profit": float(row["take_profit"]) if row["take_profit"] is not None else None,
                    "stop_loss": float(row["stop_loss"]) if row["stop_loss"] is not None else None,
                    "time_stop_date": row["time_stop_date"],
                    "days_held": _days_held(row["entry_date"], asof_time) if asof_time else 0,
                    "status": row["status"],
                    "conviction": row["conviction"],
                    "reasoning": row["reasoning"],
                    "size_pct": float(row["position_size_pct"]) if row["position_size_pct"] is not None else None,
                    "max_profit_pct": float(row["max_profit_pct"]) if row["max_profit_pct"] is not None else 0.0,
                    "max_loss_pct": float(row["max_loss_pct"]) if row["max_loss_pct"] is not None else 0.0,
                    "max_profit": float(row["max_profit"]) if row["max_profit"] is not None else 0.0,
                    "max_drawdown": float(row["max_drawdown"]) if row["max_drawdown"] is not None else 0.0,
                    "shares": float(row["shares"]) if row["shares"] is not None else None,
                }
            positions.append(position)
        
        # Refresh account state after updating all positions to ensure
        # account_state table and account_snapshot have current values
        if refresh_prices and rows:
            await _refresh_account_state(conn, asof_time=asof_time)
        
        return positions


async def refresh_account_state(
    asof_time: datetime | None = None,
    refresh_prices: bool = True,
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> None:
    """Refresh account_state and account_snapshot, optionally updating prices."""
    if asof_time is None:
        asof_time = datetime.now(timezone.utc)

    updated_positions: list[dict[str, Any]] = []
    asof_time_db = _to_naive_utc(asof_time)

    async with get_connection() as conn:
        account_state = await _ensure_account_state(conn, account_id)
        rows = await conn.fetch(
            """
            SELECT *
            FROM position
            WHERE status = 'ACTIVE'
            ORDER BY entry_date DESC
            """
        )

        if refresh_prices and rows:
            base_equity = (
                account_state.get("equity")
                or account_state.get("starting_equity")
                or DEFAULT_STARTING_EQUITY
            )
            for row in rows:
                position = await _refresh_position_row(conn, row, asof_time, base_equity=base_equity)
                updated_positions.append(position)

        await _refresh_account_state(conn, account_id=account_id, asof_time=asof_time)

    if not updated_positions:
        return

    auto_exits = []
    for position in updated_positions:
        symbol = position.get("symbol")
        direction = _normalize_direction(position.get("direction"))
        current_price = position.get("current_price")
        stop_loss = position.get("stop_loss")
        take_profit = position.get("take_profit")
        time_stop_date = position.get("time_stop_date")
        time_stop_date = _to_naive_utc(time_stop_date) if isinstance(time_stop_date, datetime) else time_stop_date
        if not symbol or current_price is None:
            continue

        if stop_loss is not None:
            stop_loss = float(stop_loss)
            if (direction == "LONG" and current_price <= stop_loss) or (
                direction == "SHORT" and current_price >= stop_loss
            ):
                auto_exits.append((symbol, "SL", "stop_loss_hit", current_price))
                continue

        if take_profit is not None:
            take_profit = float(take_profit)
            if (direction == "LONG" and current_price >= take_profit) or (
                direction == "SHORT" and current_price <= take_profit
            ):
                auto_exits.append((symbol, "TP", "take_profit_hit", current_price))
                continue

        if time_stop_date and asof_time_db and asof_time_db >= time_stop_date:
            auto_exits.append((symbol, "TIME_STOP", "time_stop_hit", current_price))

    if auto_exits:
        for symbol, exit_type, reason, exit_price in auto_exits:
            try:
                await close_position(
                    symbol=symbol,
                    exit_price=exit_price,
                    exit_reason=reason,
                    exit_type=exit_type,
                    exit_time=asof_time,
                )
                logger.info("Auto-exit %s for %s at %.4f", exit_type, symbol, exit_price)
            except Exception as exc:
                logger.warning("Auto-exit failed for %s: %s", symbol, exc)

        async with get_connection() as conn:
            await _refresh_account_state(conn, account_id=account_id, asof_time=asof_time)


async def get_position_by_symbol(
    symbol: str,
    asof_time: datetime | None = None,
    refresh_prices: bool = True,
) -> dict[str, Any] | None:
    """Return the latest active position for a symbol, if any."""
    if asof_time is None:
        asof_time = datetime.now(timezone.utc)

    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM position
            WHERE symbol = $1 AND status = 'ACTIVE'
            ORDER BY entry_date DESC
            LIMIT 1
            """,
            symbol,
        )

        if not row:
            return None

        if refresh_prices:
            account_state = await _ensure_account_state(conn)
            base_equity = account_state.get("starting_equity") or DEFAULT_STARTING_EQUITY
            return await _refresh_position_row(conn, row, asof_time, base_equity=base_equity)

        return {
            "position_id": str(row["position_id"]),
            "symbol": row["symbol"],
            "direction": _normalize_direction(row["direction"]),
            "side": "short" if _normalize_direction(row["direction"]) == "SHORT" else "long",
            "entry_date": row["entry_date"],
            "entry_price": float(row["entry_price"]),
            "current_price": float(row["current_price"]) if row["current_price"] is not None else None,
            "unrealized_pnl": float(row["unrealized_pnl"]) if row["unrealized_pnl"] is not None else 0.0,
            "unrealized_pnl_pct": float(row["unrealized_pnl_pct"]) if row["unrealized_pnl_pct"] is not None else 0.0,
            "unrealized_pl": float(row["unrealized_pnl"]) if row["unrealized_pnl"] is not None else 0.0,
            "unrealized_plpc": float(row["unrealized_pnl_pct"]) / 100 if row["unrealized_pnl_pct"] is not None else 0.0,
            "take_profit": float(row["take_profit"]) if row["take_profit"] is not None else None,
            "stop_loss": float(row["stop_loss"]) if row["stop_loss"] is not None else None,
            "time_stop_date": row["time_stop_date"],
            "days_held": _days_held(row["entry_date"], asof_time) if asof_time else 0,
            "status": row["status"],
            "conviction": row["conviction"],
            "reasoning": row["reasoning"],
            "size_pct": float(row["position_size_pct"]) if row["position_size_pct"] is not None else None,
            "max_profit_pct": float(row["max_profit_pct"]) if row["max_profit_pct"] is not None else 0.0,
            "max_loss_pct": float(row["max_loss_pct"]) if row["max_loss_pct"] is not None else 0.0,
            "max_profit": float(row["max_profit"]) if row["max_profit"] is not None else 0.0,
            "max_drawdown": float(row["max_drawdown"]) if row["max_drawdown"] is not None else 0.0,
            "shares": float(row["shares"]) if row["shares"] is not None else None,
        }


async def has_open_position(symbol: str) -> bool:
    """Return True if there is an active position for the symbol."""
    position = await get_position_by_symbol(symbol, refresh_prices=False)
    return position is not None
