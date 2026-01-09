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
            date: Date for watchlist (defaults to today)

        Returns:
            WatchlistResponse with watchlist data
        """
        # Default to today if date not provided
        if date is None:
            date = datetime.utcnow()

        try:
            # Import database connection
            from eiqora_v2.tools.db import get_connection

            async with get_connection() as conn:
                # Query watchlist from database
                rows = await conn.fetch(
                    """
                    SELECT
                        symbol,
                        technical_score,
                        profile_score,
                        total_score,
                        updated_at
                    FROM watchlist
                    WHERE scan_date = $1::date
                    ORDER BY total_score DESC
                    LIMIT 20
                    """,
                    date.date(),
                )

                # Convert to WatchlistItem objects
                watchlist = [
                    WatchlistItem(
                        symbol=row["symbol"],
                        technical_score=float(row["technical_score"]),
                        profile_score=float(row["profile_score"]),
                        total_score=float(row["total_score"]),
                        last_updated=row["updated_at"],
                    )
                    for row in rows
                ]

                return WatchlistResponse(
                    date=date,
                    watchlist=watchlist,
                    total_candidates=len(watchlist),
                )

        except Exception as e:
            # If database query fails, return empty watchlist
            print(f"Error fetching watchlist: {e}")
            return WatchlistResponse(
                date=date,
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
                        direction
                    FROM signal
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
        Get system statistics

        Returns:
            SystemStats with system metrics
        """
        try:
            from eiqora_v2.tools.db import get_connection

            async with get_connection() as conn:
                # Query active positions
                    positions_count = await conn.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM position
                        WHERE status = 'ACTIVE'
                        """
                    )

                # Query recent signals for win rate
                rows = await conn.fetch(
                    """
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN actual_return > 0 THEN 1 ELSE 0 END) as wins,
                        AVG(actual_return) as avg_return
                    FROM signal
                    WHERE signal_date >= NOW() - INTERVAL '90 days'
                        AND actual_return IS NOT NULL
                    """
                )

                if rows and rows[0]["total"] > 0:
                    total = rows[0]["total"]
                    wins = rows[0]["wins"] or 0
                    avg_return = rows[0]["avg_return"] or 0.0
                    win_rate = wins / total
                else:
                    total = 0
                    win_rate = 0.0
                    avg_return = 0.0

                # Get total analyses from analysis service
                from ..services.analysis_service import analysis_service

                total_analyses = len(analysis_service.running_analyses) + len(
                    analysis_service.completed_analyses
                )

                return SystemStats(
                    total_analyses=total_analyses,
                    active_positions=positions_count or 0,
                    win_rate=float(win_rate),
                    avg_return=float(avg_return),
                    last_updated=datetime.utcnow(),
                )

        except Exception as e:
            # If database query fails, return default stats
            print(f"Error fetching system stats: {e}")
            return SystemStats(
                total_analyses=0,
                active_positions=0,
                win_rate=0.0,
                avg_return=0.0,
                last_updated=datetime.utcnow(),
            )


# Singleton instance
dashboard_service = DashboardService()
