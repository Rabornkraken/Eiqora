"""
Dashboard Router - REST endpoints for dashboard data
"""
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime
from typing import Optional

from ..models.dashboard import (
    WatchlistResponse,
    SignalsResponse,
    MarketRegime,
    SystemStats,
)
from ..services.dashboard_service import dashboard_service

router = APIRouter()


@router.get("/watchlist", response_model=WatchlistResponse)
async def get_watchlist(date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")):
    """
    Get watchlist for a specific date

    Retrieve the daily watchlist with candidate scores.
    If no date is provided, returns today's watchlist.

    Args:
        date: Date for watchlist (YYYY-MM-DD format, optional)

    Returns:
        WatchlistResponse with watchlist data
    """
    try:
        # Parse date if provided
        parsed_date = None
        if date:
            parsed_date = datetime.strptime(date, "%Y-%m-%d")

        watchlist = await dashboard_service.get_watchlist(date=parsed_date)
        return watchlist

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch watchlist: {str(e)}")


@router.get("/signals", response_model=SignalsResponse)
async def get_recent_signals(
    limit: int = Query(10, ge=1, le=50, description="Maximum number of signals to return")
):
    """
    Get recent GO signals

    Retrieve recent trade signals with entry/exit levels.

    Args:
        limit: Maximum number of signals to return (1-50)

    Returns:
        SignalsResponse with recent signals
    """
    try:
        signals = await dashboard_service.get_recent_signals(limit=limit)
        return signals

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch signals: {str(e)}")


@router.get("/regime", response_model=MarketRegime)
async def get_market_regime():
    """
    Get current market regime

    Retrieve current market regime information including SPY trend, VIX level, and overall regime.

    Returns:
        MarketRegime with market status
    """
    try:
        regime = await dashboard_service.get_market_regime()
        return regime

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch market regime: {str(e)}")


@router.get("/stats", response_model=SystemStats)
async def get_system_stats():
    """
    Get system statistics

    Retrieve system-wide statistics including total analyses, active positions, win rate, and average return.

    Returns:
        SystemStats with system metrics
    """
    try:
        stats = await dashboard_service.get_system_stats()
        return stats

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch system stats: {str(e)}")