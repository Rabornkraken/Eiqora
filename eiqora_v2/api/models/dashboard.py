"""
Dashboard API Request/Response Models
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Literal, List, Dict, Any


class WatchlistItem(BaseModel):
    """Single watchlist candidate"""

    symbol: str = Field(..., description="Stock ticker symbol")
    technical_score: float = Field(..., description="Technical analysis score (0-1)")
    profile_score: float = Field(..., description="Profile score (0-1)")
    total_score: float = Field(..., description="Combined score (0-1)")
    last_updated: datetime = Field(..., description="Last update timestamp")


class WatchlistResponse(BaseModel):
    """Response with watchlist data"""

    date: datetime = Field(..., description="Watchlist date")
    watchlist: List[WatchlistItem] = Field(..., description="List of watchlist candidates")
    total_candidates: int = Field(..., description="Total number of candidates")


class SignalItem(BaseModel):
    """Single trade signal"""

    symbol: str = Field(..., description="Stock ticker symbol")
    signal_date: datetime = Field(..., description="Signal generation date")
    entry_price: float = Field(..., description="Entry price")
    stop_loss: float = Field(..., description="Stop loss level")
    take_profit: float = Field(..., description="Take profit level")
    conviction: Literal["HIGH", "MEDIUM", "LOW"] = Field(..., description="Conviction level")
    reasoning: str = Field(..., description="Reasoning for the signal")
    direction: Literal["LONG", "SHORT"] = Field(..., description="Trade direction")


class SignalsResponse(BaseModel):
    """Response with recent signals"""

    signals: List[SignalItem] = Field(..., description="List of recent signals")
    total: int = Field(..., description="Total count")


class MarketRegime(BaseModel):
    """Market regime information"""

    spy_trend: Literal["UPTREND", "DOWNTREND", "SIDEWAYS"] = Field(
        ..., description="SPY trend status"
    )
    vix_level: Literal["LOW", "NORMAL", "ELEVATED", "HIGH"] = Field(
        ..., description="VIX level classification"
    )
    vix_value: float = Field(..., description="Current VIX value")
    spy_return_20d: float = Field(..., description="SPY 20-day return")
    regime: Literal["RISK_ON", "RISK_OFF", "HIGH_VOL", "TRANSITION"] = Field(
        ..., description="Overall market regime"
    )
    last_updated: datetime = Field(..., description="Last update timestamp")


class SystemStats(BaseModel):
    """System statistics"""

    total_analyses: int = Field(..., description="Total analyses run")
    active_positions: int = Field(..., description="Number of active positions")
    win_rate: float = Field(..., description="Overall win rate (0-1)")
    avg_return: float = Field(..., description="Average return per trade")
    last_updated: datetime = Field(..., description="Last update timestamp")