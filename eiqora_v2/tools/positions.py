"""
Position management utilities backed by the database.

Provides helpers to open/close positions and query active positions.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
import os
from typing import Any

from zoneinfo import ZoneInfo

from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.prices import get_indicators
from eiqora_v2.tools.broker import (
    submit_market_order,
    wait_for_fill,
    close_alpaca_position,
    sync_account_from_alpaca,
    _is_alpaca_configured,
)

logger = logging.getLogger(__name__)

_EASTERN_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)


def _is_market_open(dt: datetime) -> bool:
    et = dt.astimezone(_EASTERN_TZ)
    if et.weekday() >= 5:
        return False
    return _MARKET_OPEN <= et.time() < _MARKET_CLOSE


DEFAULT_ACCOUNT_ID = os.getenv("EIQORA_ACCOUNT_ID", "main")
DEFAULT_BASE_CURRENCY = os.getenv("ACCOUNT_BASE_CURRENCY", "USD")
DEFAULT_STARTING_EQUITY = float(os.getenv("ACCOUNT_STARTING_EQUITY", "10000"))
DEFAULT_CASH_BALANCE = float(os.getenv("ACCOUNT_CASH_BALANCE", str(DEFAULT_STARTING_EQUITY)))


MAX_POSITION_PCT = 0.25  # Hard cap: no single position > 25% of equity


def _normalize_position_pct(value: Any) -> float | None:
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return None
    if pct <= 0:
        return None
    # Values >= 1.0 are treated as percentages (e.g. 5 → 0.05, 1 → 0.01).
    # No realistic single position should be 100% of equity.
    if pct >= 1.0:
        pct = pct / 100.0
    # Hard cap to prevent oversized positions
    if pct > MAX_POSITION_PCT:
        pct = MAX_POSITION_PCT
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
    """
    Refresh account_state and append a new row to account_snapshot.

    Source-of-truth rules (in priority order):
      1. Alpaca (when configured): cash, equity, positions_count, unrealized_pnl,
         gross/net_exposure all come directly from the broker. The DB position
         table is used only for trade-management metadata (stops, conviction),
         not for accounting numbers.
      2. DB fallback: sum the DB's active positions. Used only when Alpaca is
         not configured or the broker call fails.

    This prevents the equity chart from drifting when the DB and Alpaca
    disagree about which positions are open (e.g. DB-only "ghost" rows or
    Alpaca-only positions opened outside the system).
    """
    asof_time = asof_time or datetime.now(timezone.utc)
    state = await _ensure_account_state(conn, account_id)
    realized_pnl = state.get("realized_pnl") or 0.0

    # Self-heal DB/Alpaca drift before computing account state. This closes
    # ghost DB rows and reinstates Alpaca-only positions so the monitoring
    # loop's stop-loss/take-profit/time-stop checks operate on accurate data.
    try:
        await reconcile_positions_with_alpaca(conn, asof_time=asof_time)
    except Exception as exc:
        logger.warning("Position reconciliation failed (non-fatal): %s", exc)

    # --- Path 1: Alpaca is the source of truth ---------------------------
    if _is_alpaca_configured():
        try:
            from eiqora_v2.tools.broker import get_alpaca_account, get_alpaca_positions

            alpaca_account = await get_alpaca_account()
            alpaca_positions = await get_alpaca_positions()
        except Exception as exc:
            logger.warning("Alpaca fetch for account refresh failed: %s", exc)
            alpaca_account = None
            alpaca_positions = None

        if alpaca_account is not None and alpaca_positions is not None:
            cash_balance = float(alpaca_account.get("cash", 0))
            equity = float(alpaca_account.get("equity", 0))

            unrealized_pnl = 0.0
            gross_exposure = 0.0
            net_exposure = 0.0
            for p in alpaca_positions:
                mv = float(p.get("market_value", 0))
                pl = float(p.get("unrealized_pl", 0))
                side = str(p.get("side", "long")).lower()
                unrealized_pnl += pl
                gross_exposure += abs(mv)
                net_exposure += mv if side == "long" else -mv

            positions_count = len(alpaca_positions)

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
                positions_count,
            )

            await conn.execute(
                """
                INSERT INTO account_snapshot (
                    account_id, asof_ts, cash_balance, equity, realized_pnl,
                    unrealized_pnl, gross_exposure, net_exposure, positions_count
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
                positions_count,
            )

            # Reconciliation log (non-mutating): warn when DB positions diverge
            # from Alpaca. A dedicated reconciliation job can auto-close DB-only
            # "ghost" rows later; for now we just surface the drift.
            alpaca_symbols = {str(p.get("symbol", "")).upper() for p in alpaca_positions}
            db_rows = await conn.fetch(
                "SELECT symbol, alpaca_order_id FROM position WHERE status = 'ACTIVE'"
            )
            db_with_broker = {r["symbol"] for r in db_rows if r["alpaca_order_id"] is not None}
            ghost_db = db_with_broker - alpaca_symbols
            orphan_alpaca = alpaca_symbols - {r["symbol"] for r in db_rows}
            if ghost_db:
                logger.warning(
                    "Position drift: %d symbol(s) ACTIVE in DB but missing from Alpaca: %s",
                    len(ghost_db), sorted(ghost_db),
                )
            if orphan_alpaca:
                logger.info(
                    "Position drift: %d symbol(s) on Alpaca but not tracked in DB: %s",
                    len(orphan_alpaca), sorted(orphan_alpaca),
                )

            return

    # --- Path 2: DB fallback (no Alpaca) ---------------------------------
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

        if row["unrealized_pnl"] is not None:
            position_pnl = float(row["unrealized_pnl"])
        else:
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
            account_id, asof_ts, cash_balance, equity, realized_pnl,
            unrealized_pnl, gross_exposure, net_exposure, positions_count
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


async def _get_latest_minute_price(symbol: str, asof_time: datetime | None) -> float | None:
    """Get latest 1-minute bar close price."""
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
                FROM market_bar_1min
                WHERE symbol = $1
                  AND datetime <= $2
                ORDER BY datetime DESC
                LIMIT 1
                """,
                symbol,
                asof_ts,
            )
        except Exception as exc:
            logger.debug("Failed to fetch minute price for %s: %s", symbol, exc)
            return None
    if row and row["close"] is not None:
        return float(row["close"])
    return None


async def _get_latest_price(symbol: str, asof_time: datetime | None) -> float | None:
    try:
        # 1. Try 1-minute bar (freshest, updated every minute during market hours)
        minute_price = await _get_latest_minute_price(symbol, asof_time)
        if minute_price is not None:
            return minute_price

        # 2. Fall back to hourly bar close
        hourly_price = await _get_latest_hourly_price(symbol, asof_time)
        if hourly_price is not None:
            return hourly_price

        # 3. Fall back to daily indicators
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

    # When Alpaca is configured, use broker equity/cash for sizing
    from eiqora_v2.tools.broker import get_alpaca_account as _get_alpaca_acct

    alpaca_equity = None
    alpaca_cash = None
    if _is_alpaca_configured():
        try:
            _acct = await _get_alpaca_acct()
            if _acct:
                alpaca_equity = float(_acct.get("equity", 0))
                alpaca_cash = float(_acct.get("cash", 0))
                logger.info(
                    "Using Alpaca account for sizing: equity=$%.2f, cash=$%.2f",
                    alpaca_equity, alpaca_cash,
                )
        except Exception as exc:
            logger.warning("Alpaca account fetch failed, falling back to DB: %s", exc)

    async with get_connection() as conn:
        account_state = await _ensure_account_state(conn)
        base_equity = alpaca_equity or account_state.get("equity") or account_state.get("starting_equity") or DEFAULT_STARTING_EQUITY
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

        # --- Cash check BEFORE inserting position row ---
        if notional_value is not None:
            cash_balance = alpaca_cash if alpaca_cash is not None else account_state.get("cash_balance")
            if cash_balance is None:
                cash_balance = base_equity
            cash_balance = float(cash_balance)

            # Guard: don't let cash go negative
            if float(notional_value) > cash_balance:
                logger.warning(
                    "Position for %s would exceed cash balance "
                    "(notional=$%.2f, cash=$%.2f); capping to available cash",
                    symbol, float(notional_value), cash_balance,
                )
                notional_value = max(cash_balance, 0.0)
                if notional_value <= 0 or entry_price <= 0:
                    logger.warning("No cash available for %s; skipping position", symbol)
                    return None
                shares = float(notional_value) / float(entry_price)

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

        # Deduct cash after successful insert
        if notional_value is not None:
            cash_balance -= float(notional_value)
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

        # --- Alpaca paper trade ---
        if position_id and shares and _is_alpaca_configured():
            side = "SELL" if direction == "SHORT" else "BUY"
            # Round shares to int for whole-share orders
            order_qty = int(shares)
            if order_qty > 0:
                order = await submit_market_order(symbol, order_qty, side)
                if order and order.get("order_id"):
                    fill = await wait_for_fill(order["order_id"])
                    alpaca_order_id = order["order_id"]
                    fill_price = None
                    if fill and fill.get("filled_avg_price"):
                        fill_price = fill["filled_avg_price"]
                    await conn.execute(
                        """
                        UPDATE position
                        SET alpaca_order_id = $2,
                            entry_price = COALESCE($3, entry_price),
                            current_price = COALESCE($3, current_price),
                            last_updated = NOW()
                        WHERE position_id = $1
                        """,
                        position_id,
                        alpaca_order_id,
                        fill_price,
                    )
                    logger.info(
                        "Alpaca order %s for %s: fill_price=%s",
                        alpaca_order_id, symbol, fill_price,
                    )
                else:
                    logger.warning("Alpaca order submission failed for %s; DB position kept", symbol)

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

        # --- Alpaca: close on broker FIRST (atomic close) ------------------
        # If the broker rejects/errors, we must NOT mark the DB CLOSED —
        # that's how ghost positions were created. Only proceed with DB
        # bookkeeping after broker confirmation.
        # NOTE: asyncpg Records don't support .get() — use _row_value to
        # handle both dicts and Records correctly. Previously this always
        # returned None, silently skipping the broker close.
        alpaca_order_id = _row_value(row, "alpaca_order_id")
        if alpaca_order_id and _is_alpaca_configured():
            # Reconciled synthetic ids are OK — they still correspond to a
            # real broker holding (the reconciler only assigns them when
            # Alpaca actually shows the symbol).
            close_result = await close_alpaca_position(row["symbol"])
            if not close_result:
                logger.error(
                    "Alpaca close FAILED for %s; leaving DB ACTIVE to avoid drift",
                    row["symbol"],
                )
                return None
            logger.info("Alpaca position closed for %s: %s", row["symbol"], close_result)
            # Prefer Alpaca's fill price over computed exit_price when available
            fill_price = close_result.get("filled_avg_price") if isinstance(close_result, dict) else None
            if fill_price:
                exit_price = float(fill_price)

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

        # NOTE: do NOT call _refresh_account_state here. This function is called
        # by the read-only API endpoint /api/v1/positions/open every ~30s from
        # the frontend. Writing a snapshot every poll pollutes the equity
        # time-series and, when the API container's view of positions diverges
        # from the DB (e.g. asyncpg snapshot isolation holding an old visibility
        # window), produces misleading account_snapshot rows (e.g. showing
        # 14 positions / $102K alongside 9 positions / $63K within the same
        # minute).
        # Account_state + account_snapshot refreshes are owned by the live
        # scheduler's run_account_refresh_loop + trigger_dispatcher, which
        # refresh on a single cadence tied to market events.
        return positions


async def _decide_time_stop_action(
    position: dict[str, Any],
    asof_time: datetime,
    *,
    current_price: float,
    direction: str,
) -> str:
    """Run the LLM time-stop decision agent and apply any non-exit action.

    Returns:
        "EXIT" to signal the caller to close the position normally, or
        "HELD" if the agent chose TRAIL_TIGHT / EXTEND_HOLD and the
        position has been updated in place (no exit needed).

    The LLM call is wrapped in a broad try/except: if anything fails
    (network, schema, validation, LLM downtime), we fall back to EXIT —
    the historical default — so a broken agent never traps a position
    past its deadline.
    """
    symbol = position.get("symbol")
    try:
        # Lazy import: TimeStopDecisionAgent pulls the LLM client which
        # transitively imports heavy modules; positions.py is loaded by
        # almost everything in the codebase and we don't want every
        # consumer (CLI tools, backtests) paying that cost.
        from eiqora_v2.agents.time_stop_decision import TimeStopDecisionAgent

        # Profile is best-effort; the agent works without it.
        profile_dict: dict[str, Any] = {}
        try:
            from eiqora_v2.services.profile_generator import ProfileGenerator

            profile = await ProfileGenerator().get_profile(symbol, allow_stale=True)
            profile_dict = profile.model_dump() if profile else {}
        except Exception as exc:  # noqa: BLE001
            logger.info("time_stop %s: profile unavailable (%s); proceeding without", symbol, exc)

        state = {
            "symbol": symbol,
            "asof_time": asof_time,
            "position": position,
            "profile": profile_dict,
        }
        result = await TimeStopDecisionAgent().run(state)
        decision = (result or {}).get("time_stop_decision") or {}
        action = str(decision.get("action") or "EXIT").upper()
        reason = decision.get("reasoning") or "(no reason given)"

        if action == "TRAIL_TIGHT":
            new_sl = decision.get("new_stop_loss")
            if new_sl is None:
                logger.warning(
                    "time_stop %s: TRAIL_TIGHT chosen without new_stop_loss; falling back to EXIT",
                    symbol,
                )
                return "EXIT"
            new_sl = float(new_sl)
            current_sl = float(position.get("stop_loss") or 0)
            # LONG: new SL must be tighter (higher) and below current price.
            # SHORT: new SL must be tighter (lower) and above current price.
            if direction == "LONG":
                if not (current_sl < new_sl < current_price):
                    logger.warning(
                        "time_stop %s: invalid LONG new_sl=%.4f (need %.4f < x < %.4f); EXIT",
                        symbol, new_sl, current_sl, current_price,
                    )
                    return "EXIT"
            else:  # SHORT
                if not (current_price < new_sl < current_sl):
                    logger.warning(
                        "time_stop %s: invalid SHORT new_sl=%.4f (need %.4f < x < %.4f); EXIT",
                        symbol, new_sl, current_price, current_sl,
                    )
                    return "EXIT"
            async with get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE position
                       SET stop_loss = $1,
                           time_stop_date = NULL
                     WHERE symbol = $2 AND status = 'ACTIVE'
                    """,
                    new_sl, symbol,
                )
            logger.info(
                "time_stop %s: TRAIL_TIGHT applied — SL %.4f → %.4f, time stop cleared. %s",
                symbol, current_sl, new_sl, reason,
            )
            return "HELD"

        if action == "EXTEND_HOLD":
            ext = decision.get("extension_days")
            try:
                ext_int = int(ext) if ext is not None else 0
            except (TypeError, ValueError):
                ext_int = 0
            if ext_int < 1 or ext_int > 30:
                logger.warning(
                    "time_stop %s: EXTEND_HOLD with invalid extension_days=%r; EXIT",
                    symbol, ext,
                )
                return "EXIT"
            async with get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE position
                       SET time_stop_date = time_stop_date + ($1 || ' days')::interval
                     WHERE symbol = $2 AND status = 'ACTIVE'
                    """,
                    str(ext_int), symbol,
                )
            logger.info(
                "time_stop %s: EXTEND_HOLD applied — pushed deadline %d more days. %s",
                symbol, ext_int, reason,
            )
            return "HELD"

        # Default / explicit EXIT
        logger.info("time_stop %s: EXIT — %s", symbol, reason)
        return "EXIT"

    except Exception as exc:  # noqa: BLE001 — never trap a position
        logger.warning(
            "time_stop %s: decision agent failed (%s); falling back to EXIT",
            symbol, exc,
        )
        return "EXIT"


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
            decision = await _decide_time_stop_action(
                position, asof_time, current_price=current_price, direction=direction
            )
            if decision == "EXIT":
                auto_exits.append((symbol, "TIME_STOP", "time_stop_hit", current_price))
            # TRAIL_TIGHT and EXTEND_HOLD have already been applied to the
            # database by _decide_time_stop_action — nothing to enqueue here.

    if auto_exits and not _is_market_open(asof_time):
        logger.info(
            "Skipping %d auto-exit(s) — market closed (%s ET)",
            len(auto_exits),
            asof_time.astimezone(_EASTERN_TZ).strftime("%a %H:%M"),
        )
        auto_exits = []

    if auto_exits:
        for symbol, exit_type, reason, exit_price in auto_exits:
            try:
                result = await close_position(
                    symbol=symbol,
                    exit_price=exit_price,
                    exit_reason=reason,
                    exit_type=exit_type,
                    exit_time=asof_time,
                )
                if result is None:
                    logger.warning(
                        "Auto-exit %s for %s at %.4f — broker close failed; will retry next refresh",
                        exit_type, symbol, exit_price,
                    )
                else:
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


# ---------------------------------------------------------------------------
# Position <-> Alpaca reconciliation
# ---------------------------------------------------------------------------

async def reconcile_positions_with_alpaca(conn, *, asof_time: datetime | None = None) -> dict[str, int]:
    """
    Reconcile the DB position table against Alpaca's live positions.

    Two kinds of drift are healed:

    1. **Ghost DB positions** — ACTIVE in DB but missing from Alpaca. The
       DB close never reached the broker (e.g. sell order rejected), or
       the position was closed on Alpaca outside the system. We mark the
       DB row CLOSED with exit_reason='ghost_reconciled' so the
       monitoring and capacity counts stay consistent.

    2. **Alpaca orphans** — held on Alpaca but NOT ACTIVE in DB. Could
       be a prior DB-only close that didn't propagate, a trade opened
       outside the system, or a broker-only position. We reinstate an
       ACTIVE DB row mirroring Alpaca's (qty, avg_entry_price, current_price)
       and — critically — preserve TP/SL/time-stop metadata from any
       prior CLOSED row for the same symbol so the auto-exit loop works.

    This runs on every ``_refresh_account_state`` call and makes the
    system self-healing.

    Returns a summary dict: {'ghosts_closed': N, 'orphans_reinstated': N}.
    """
    asof_time = asof_time or datetime.now(timezone.utc)
    asof_naive = _to_naive_utc(asof_time)
    summary = {"ghosts_closed": 0, "orphans_reinstated": 0}

    if not _is_alpaca_configured():
        return summary

    try:
        from eiqora_v2.tools.broker import get_alpaca_positions
        alpaca_positions = await get_alpaca_positions()
    except Exception as exc:
        logger.warning("Reconciler: Alpaca fetch failed, skipping: %s", exc)
        return summary

    if alpaca_positions is None:
        # Network/auth issue — don't touch DB
        return summary

    alpaca_by_symbol: dict[str, dict[str, Any]] = {
        str(p["symbol"]).upper(): p for p in alpaca_positions
    }
    alpaca_symbols = set(alpaca_by_symbol.keys())

    db_active = await conn.fetch(
        """
        SELECT position_id, symbol, alpaca_order_id
        FROM position
        WHERE status = 'ACTIVE'
        """
    )
    db_active_symbols = {r["symbol"] for r in db_active}

    # --- 1. Close ghost DB rows (ACTIVE in DB, not on Alpaca) ---------------
    ghost_symbols = db_active_symbols - alpaca_symbols
    for row in db_active:
        sym = row["symbol"]
        if sym not in ghost_symbols:
            continue
        # Safety: only auto-close rows that WERE broker-managed (have alpaca_order_id).
        # Legacy manual positions (no alpaca_order_id) are left alone — the
        # operator can reconcile them explicitly.
        if not row["alpaca_order_id"]:
            logger.info(
                "Reconciler: leaving legacy ACTIVE %s untouched (no alpaca_order_id)",
                sym,
            )
            continue
        # Fetch entry_price / shares so exit math is clean
        detail = await conn.fetchrow(
            "SELECT entry_price, shares, direction FROM position WHERE position_id = $1",
            row["position_id"],
        )
        if not detail:
            continue
        entry_price = float(detail["entry_price"] or 0)
        shares = float(detail["shares"] or 0)
        # We don't know the real exit price; use entry (zero P&L). Operator
        # or Alpaca history can correct later.
        await conn.execute(
            """
            UPDATE position
            SET status = 'CLOSED',
                exit_date = $2,
                exit_price = $3,
                realized_pnl = 0,
                realized_pnl_pct = 0,
                exit_reason = 'ghost_reconciled',
                exit_type = 'RECONCILE',
                last_updated = NOW()
            WHERE position_id = $1
            """,
            row["position_id"],
            asof_naive,
            entry_price,
        )
        summary["ghosts_closed"] += 1
        logger.warning(
            "Reconciler: closed ghost DB position %s (broker no longer holds it)",
            sym,
        )

    # --- 2. Reinstate Alpaca orphans ----------------------------------------
    # Skip symbols that were just CLOSED on our side — the corresponding
    # Alpaca sell may still be pending fill, and we'd otherwise loop:
    # close → broker pending → reinstate → close again. 5-minute window
    # is generous enough for paper-account fills while still letting
    # genuine drift heal on the next cycle.
    recent_closed = await conn.fetch(
        """
        SELECT DISTINCT symbol
        FROM position
        WHERE status = 'CLOSED'
          AND exit_date >= NOW() - INTERVAL '5 minutes'
        """
    )
    recently_closed_symbols = {r["symbol"] for r in recent_closed}

    orphan_symbols = alpaca_symbols - db_active_symbols - recently_closed_symbols
    if recently_closed_symbols & alpaca_symbols:
        logger.info(
            "Reconciler: deferring reinstate for %s — recent close still pending broker fill",
            sorted(recently_closed_symbols & alpaca_symbols),
        )
    for sym in sorted(orphan_symbols):
        ap = alpaca_by_symbol[sym]
        qty = float(ap.get("qty", 0))
        avg_entry = float(ap.get("avg_entry_price", 0))
        current_price = float(ap.get("current_price", avg_entry))
        unrealized_pl = float(ap.get("unrealized_pl", 0))
        side = str(ap.get("side", "long")).lower()
        direction = "SHORT" if side == "short" else "LONG"
        notional = qty * avg_entry if qty and avg_entry else None

        if qty <= 0 or avg_entry <= 0:
            continue

        # Preserve TP / SL / time-stop from any prior CLOSED row so the
        # monitoring loop's auto-exit rules can fire.
        prior = await conn.fetchrow(
            """
            SELECT take_profit, stop_loss, time_stop_date,
                   position_size_pct, conviction, reasoning, entry_date
            FROM position
            WHERE symbol = $1
            ORDER BY entry_date DESC
            LIMIT 1
            """,
            sym,
        )
        if prior:
            take_profit = prior["take_profit"]
            stop_loss = prior["stop_loss"]
            time_stop_date = prior["time_stop_date"]
            position_size_pct = prior["position_size_pct"]
            conviction = prior["conviction"] or "MEDIUM"
            reasoning = prior["reasoning"] or "Reinstated from Alpaca drift"
            entry_date = prior["entry_date"] or asof_naive
        else:
            # Conservative fallback when there's no prior metadata.
            # +15% TP, -10% SL, 30-day time stop. The operator can edit.
            take_profit = avg_entry * 1.15
            stop_loss = avg_entry * 0.90
            time_stop_date = asof_naive + timedelta(days=30)
            position_size_pct = None
            conviction = "MEDIUM"
            reasoning = "Reinstated from Alpaca drift (no prior metadata)"
            entry_date = asof_naive

        unrealized_pnl_pct = ((current_price - avg_entry) / avg_entry * 100.0) if avg_entry else 0.0

        # Synthesize an alpaca_order_id so future reconcilers know this
        # is broker-managed (and so ghost-close logic can still trigger
        # if Alpaca drops the position later).
        alpaca_order_id = f"reconciled:{sym}"

        await conn.execute(
            """
            INSERT INTO position (
                symbol, direction, entry_date, entry_price, shares, notional_value,
                current_price, take_profit, stop_loss, time_stop_date, status,
                conviction, reasoning, position_size_pct, unrealized_pnl,
                unrealized_pnl_pct, alpaca_order_id, last_updated
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'ACTIVE',
                $11, $12, $13, $14, $15, $16, NOW()
            )
            """,
            sym,
            direction,
            _to_naive_utc(entry_date) if isinstance(entry_date, datetime) else entry_date,
            avg_entry,
            qty,
            notional,
            current_price,
            take_profit,
            stop_loss,
            _to_naive_utc(time_stop_date) if isinstance(time_stop_date, datetime) else time_stop_date,
            conviction,
            reasoning,
            position_size_pct,
            unrealized_pl,
            unrealized_pnl_pct,
            alpaca_order_id,
        )
        summary["orphans_reinstated"] += 1
        logger.warning(
            "Reconciler: reinstated Alpaca position %s qty=%s entry=%s tp=%s sl=%s",
            sym, qty, avg_entry, take_profit, stop_loss,
        )

    if summary["ghosts_closed"] or summary["orphans_reinstated"]:
        logger.info(
            "Reconciler summary: %d ghosts closed, %d orphans reinstated",
            summary["ghosts_closed"],
            summary["orphans_reinstated"],
        )

    return summary
