"""
Dashboard Service - Business logic for dashboard data
Integrates with database queries and existing tools
"""
from datetime import datetime, timedelta
from typing import List, Optional
import asyncio

from ..models.dashboard import (
    WatchlistResponse,
    WatchlistItem,
    SignalsResponse,
    SignalItem,
    MarketRegime,
    SystemStats,
)


class DashboardService:
    """Service for dashboard data aggregation"""

    def __init__(self):
        pass

    async def get_watchlist(self, date: Optional[datetime] = None) -> WatchlistResponse:
        """
        Get watchlist for a specific date

        Args:
            date: Date for watchlist (defaults to most recent available)

        Returns:
            WatchlistResponse with watchlist data
        """
        try:
            # Import database connection
            from eiqora_v2.tools.db import get_connection

            async with get_connection() as conn:
                # If no date provided, find the most recent scan date
                if date is None:
                    latest = await conn.fetchval(
                        "SELECT MAX(scan_date) FROM daily_watchlist"
                    )
                    if latest is None:
                        return WatchlistResponse(
                            date=datetime.utcnow(),
                            watchlist=[],
                            total_candidates=0,
                        )
                    query_date = latest
                else:
                    query_date = date.date()

                # Query watchlist from database
                rows = await conn.fetch(
                    """
                    SELECT
                        symbol,
                        technical_score,
                        profile_score,
                        total_score,
                        created_at
                    FROM daily_watchlist
                    WHERE scan_date = $1::date
                    ORDER BY total_score DESC
                    LIMIT 20
                    """,
                    query_date,
                )

                # Convert to WatchlistItem objects
                watchlist = [
                    WatchlistItem(
                        symbol=row["symbol"],
                        technical_score=float(row["technical_score"]),
                        profile_score=float(row["profile_score"]),
                        total_score=float(row["total_score"]),
                        last_updated=row["created_at"],
                    )
                    for row in rows
                ]

                return WatchlistResponse(
                    date=datetime.combine(query_date, datetime.min.time()) if not isinstance(query_date, datetime) else query_date,
                    watchlist=watchlist,
                    total_candidates=len(watchlist),
                )

        except Exception as e:
            # If database query fails, return empty watchlist
            print(f"Error fetching watchlist: {e}")
            return WatchlistResponse(
                date=date or datetime.utcnow(),
                watchlist=[],
                total_candidates=0,
            )

    async def get_recent_signals(self, limit: int = 10) -> SignalsResponse:
        """
        Get recent GO signals

        Args:
            limit: Maximum number of signals to return

        Returns:
            SignalsResponse with recent signals
        """
        try:
            from eiqora_v2.tools.db import get_connection

            async with get_connection() as conn:
                # Query recent signals from database
                rows = await conn.fetch(
                    """
                    SELECT
                        symbol,
                        signal_date,
                        entry_price,
                        stop_loss,
                        take_profit,
                        conviction,
                        reasoning,
                        trigger_type as direction
                    FROM trade_signal
                    WHERE action = 'GO'
                    ORDER BY signal_date DESC
                    LIMIT $1
                    """,
                    limit,
                )

                # Convert to SignalItem objects
                signals = [
                    SignalItem(
                        symbol=row["symbol"],
                        signal_date=row["signal_date"],
                        entry_price=float(row["entry_price"]),
                        stop_loss=float(row["stop_loss"]),
                        take_profit=float(row["take_profit"]),
                        conviction=row["conviction"],
                        reasoning=row["reasoning"],
                        direction=row["direction"],
                    )
                    for row in rows
                ]

                return SignalsResponse(
                    signals=signals,
                    total=len(signals),
                )

        except Exception as e:
            # If database query fails, return empty signals
            print(f"Error fetching signals: {e}")
            return SignalsResponse(
                signals=[],
                total=0,
            )

    async def get_market_regime(self) -> MarketRegime:
        """
        Get current market regime

        Returns:
            MarketRegime with market status
        """
        try:
            from eiqora_v2.tools.market_regime import get_market_regime

            # Use existing market_regime tool
            regime_data = await get_market_regime()

            return MarketRegime(
                spy_trend=regime_data.get("spy_trend", "SIDEWAYS"),
                vix_level=regime_data.get("vix_level", "NORMAL"),
                vix_value=float(regime_data.get("vix_value", 15.0)),
                spy_return_20d=float(regime_data.get("spy_return_20d", 0.0)),
                regime=regime_data.get("regime", "TRANSITION"),
                last_updated=datetime.utcnow(),
            )

        except Exception as e:
            # If tool fails, return default regime
            print(f"Error fetching market regime: {e}")
            return MarketRegime(
                spy_trend="SIDEWAYS",
                vix_level="NORMAL",
                vix_value=15.0,
                spy_return_20d=0.0,
                regime="TRANSITION",
                last_updated=datetime.utcnow(),
            )

    async def get_system_stats(self) -> SystemStats:
        """
        Get system statistics.

        Uses Alpaca broker data (when configured) for live equity/P&L,
        falls back to DB account_state otherwise.
        """
        try:
            from eiqora_v2.tools.db import get_connection
            from eiqora_v2.tools.broker import _is_alpaca_configured, get_alpaca_account, get_alpaca_positions

            async with get_connection() as conn:
                # Only count Alpaca-managed closed trades (live system)
                closed_stats = await conn.fetchrow(
                    """
                    SELECT
                        COUNT(*) as total_closed,
                        COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) as wins,
                        SUM(realized_pnl) as total_pnl
                    FROM position
                    WHERE status = 'CLOSED'
                      AND alpaca_order_id IS NOT NULL
                    """
                )

                total_trades = closed_stats["total_closed"] or 0
                wins = closed_stats["wins"] or 0

                # Calculate win rate
                win_rate_str = None
                if total_trades > 0:
                    win_rate_pct = (wins / total_trades) * 100
                    win_rate_str = f"{win_rate_pct:.1f}%"

                # Get starting_equity from DB
                account = await conn.fetchrow(
                    "SELECT starting_equity FROM account_state LIMIT 1"
                )
                starting = float(account["starting_equity"] or 100000) if account else 100000

            # Prefer Alpaca for live data
            equity = 0.0
            unrealized = 0.0
            positions_count = 0

            if _is_alpaca_configured():
                try:
                    alpaca_acct = await get_alpaca_account()
                    if alpaca_acct:
                        equity = float(alpaca_acct.get("equity", 0))
                except Exception:
                    alpaca_acct = None

                try:
                    alpaca_positions = await get_alpaca_positions()
                    if alpaca_positions:
                        positions_count = len(alpaca_positions)
                        unrealized = sum(
                            float(p.get("unrealized_pl", 0)) for p in alpaca_positions
                        )
                except Exception:
                    pass

            # Fallback to DB if Alpaca not configured or failed
            if not equity:
                async with get_connection() as conn:
                    db_acct = await conn.fetchrow(
                        "SELECT equity, unrealized_pnl FROM account_state LIMIT 1"
                    )
                    if db_acct:
                        equity = float(db_acct["equity"] or 0)
                        unrealized = float(db_acct["unrealized_pnl"] or 0)
                    positions_count = await conn.fetchval(
                        "SELECT COUNT(*) FROM position WHERE status = 'ACTIVE'"
                    ) or 0

            current_equity_str = f"${equity:,.2f}" if equity else None
            total_return_str = None
            if starting > 0 and equity:
                total_return_pct = ((equity - starting) / starting) * 100
                sign = "+" if total_return_pct >= 0 else ""
                total_return_str = f"{sign}{total_return_pct:.2f}%"

            return SystemStats(
                total_trades=total_trades,
                active_positions=positions_count,
                win_rate=win_rate_str,
                total_return=total_return_str,
                current_equity=current_equity_str,
                unrealized_pnl=unrealized,
                sharpe_ratio=None,
                last_updated=datetime.utcnow(),
            )

        except Exception as e:
            print(f"Error fetching system stats: {e}")
            return SystemStats(
                total_trades=0,
                active_positions=0,
                win_rate=None,
                total_return=None,
                current_equity=None,
                sharpe_ratio=None,
                last_updated=datetime.utcnow(),
            )


    async def get_equity_history(self, days: int = 30) -> List[dict]:
        """
        Get equity history for chart

        Args:
            days: Number of days of history to return

        Returns:
            List of equity snapshots
        """
        try:
            from eiqora_v2.tools.db import get_connection

            async with get_connection() as conn:
                # Query daily equity snapshots.
                # Use the MAX equity per day to avoid anomalous low-equity
                # snapshots that occur when a refresh transiently sees a
                # partial position count (e.g. during a close transaction).
                # This makes the equity curve continuous across days.
                rows = await conn.fetch(
                    """
                    SELECT
                        DATE(asof_ts) AS day,
                        MAX(asof_ts) AS timestamp,
                        MAX(equity) AS equity,
                        MAX(cash_balance) AS cash_balance,
                        MAX(unrealized_pnl) AS unrealized_pnl,
                        MAX(realized_pnl) AS realized_pnl,
                        MAX(positions_count) AS positions_count
                    FROM account_snapshot
                    WHERE asof_ts >= NOW() - INTERVAL '%s days'
                    GROUP BY DATE(asof_ts)
                    ORDER BY DATE(asof_ts)
                    """ % days
                )

                return [
                    {
                        "timestamp": row["timestamp"].isoformat(),
                        "equity": float(row["equity"] or 0),
                        "cash_balance": float(row["cash_balance"] or 0),
                        "unrealized_pnl": float(row["unrealized_pnl"] or 0),
                        "realized_pnl": float(row["realized_pnl"] or 0),
                        "positions_count": row["positions_count"] or 0,
                    }
                    for row in rows
                ]

        except Exception as e:
            print(f"Error fetching equity history: {e}")
            return []

    async def get_trading_history(self, limit: int = 50, source: str = "alpaca") -> List[dict]:
        """
        Get trading history (closed positions).

        Args:
            limit: Maximum number of trades to return.
            source: ``'alpaca'`` for broker-matched buy/sell pairs (live trades),
                ``'legacy'`` for closed rows in the DB ``position`` table that
                predate Alpaca integration. Defaults to ``'alpaca'`` so the
                main Trading History view surfaces real broker activity.

        Returns:
            List of closed trades.
        """
        if source == "alpaca":
            try:
                from eiqora_v2.tools.broker import _is_alpaca_configured, get_alpaca_closed_trades

                if not _is_alpaca_configured():
                    return []

                trades = await get_alpaca_closed_trades()
                if not trades:
                    return []

                # Shape to match legacy response so the frontend renders both
                # sources identically (pnl, pnl_pct, direction, dates).
                return [
                    {
                        "position_id": t.get("order_id") or f"{t.get('symbol')}-{t.get('exit_date', '')}",
                        "symbol": t.get("symbol"),
                        "direction": t.get("direction", "LONG"),
                        "entry_date": t.get("entry_date"),
                        "exit_date": t.get("exit_date"),
                        "entry_price": float(t.get("entry_price") or 0),
                        "exit_price": float(t.get("exit_price") or 0),
                        "shares": float(t.get("shares") or 0) or None,
                        "position_size_pct": 0.0,
                        "realized_pnl": float(t.get("realized_pnl") or 0),
                        "realized_pnl_pct": float(t.get("realized_pnl_pct") or 0),
                        "exit_reason": "alpaca_close",
                        "exit_type": None,
                    }
                    for t in trades[:limit]
                ]
            except Exception as e:
                print(f"Error fetching Alpaca trading history: {e}")
                return []

        # source == 'legacy': closed DB positions with no alpaca_order_id
        try:
            from eiqora_v2.tools.db import get_connection

            async with get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        position_id, symbol, direction, entry_date, exit_date,
                        entry_price, exit_price, shares, position_size_pct,
                        realized_pnl, realized_pnl_pct, exit_reason, exit_type
                    FROM position
                    WHERE status = 'CLOSED'
                      AND alpaca_order_id IS NULL
                    ORDER BY exit_date DESC
                    LIMIT $1
                    """,
                    limit,
                )

                return [
                    {
                        "position_id": str(row["position_id"]),
                        "symbol": row["symbol"],
                        "direction": row["direction"],
                        "entry_date": row["entry_date"].isoformat() if row["entry_date"] else None,
                        "exit_date": row["exit_date"].isoformat() if row["exit_date"] else None,
                        "entry_price": float(row["entry_price"] or 0),
                        "exit_price": float(row["exit_price"] or 0),
                        "shares": float(row["shares"]) if row["shares"] else None,
                        "position_size_pct": float(row["position_size_pct"] or 0),
                        "realized_pnl": float(row["realized_pnl"] or 0),
                        "realized_pnl_pct": float(row["realized_pnl_pct"] or 0),
                        "exit_reason": row["exit_reason"],
                        "exit_type": row["exit_type"],
                    }
                    for row in rows
                ]

        except Exception as e:
            print(f"Error fetching trading history: {e}")
            return []

    async def get_account(self) -> dict:
        """
        Get account summary

        Returns:
            Account summary with cash_balance, equity, unrealized_pnl, realized_pnl
        """
        try:
            from eiqora_v2.tools.db import get_connection

            async with get_connection() as conn:
                # Query account_state
                row = await conn.fetchrow(
                    """
                    SELECT
                        cash_balance,
                        equity,
                        unrealized_pnl,
                        realized_pnl,
                        starting_equity,
                        positions_count
                    FROM account_state
                    LIMIT 1
                    """
                )

                if row:
                    return {
                        "cash_balance": float(row["cash_balance"] or 0),
                        "equity": float(row["equity"] or 0),
                        "unrealized_pnl": float(row["unrealized_pnl"] or 0),
                        "realized_pnl": float(row["realized_pnl"] or 0),
                        "starting_equity": float(row["starting_equity"] or 100000),
                        "positions_count": row["positions_count"] or 0,
                    }
                else:
                    return {
                        "cash_balance": 0,
                        "equity": 0,
                        "unrealized_pnl": 0,
                        "realized_pnl": 0,
                        "starting_equity": 100000,
                        "positions_count": 0,
                    }

        except Exception as e:
            print(f"Error fetching account: {e}")
            return {
                "cash_balance": 0,
                "equity": 0,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "starting_equity": 100000,
                "positions_count": 0,
            }


# Singleton instance
dashboard_service = DashboardService()
