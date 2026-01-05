"""
Monitoring-related schemas for position tracking and alerts.
"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class OpenPosition(BaseModel):
    """An open position being monitored."""
    position_id: str = Field(description="Unique position ID")
    symbol: str = Field(description="Stock ticker")
    direction: Literal["LONG", "SHORT"] = Field(description="Trade direction")
    entry_date: datetime = Field(description="Entry timestamp")
    entry_price: float = Field(description="Entry price")
    current_price: float | None = Field(default=None, description="Current price")
    unrealized_pnl_pct: float | None = Field(default=None, description="Unrealized P&L %")
    
    # Exit levels
    tp_level: float = Field(description="Take profit level")
    sl_level: float = Field(description="Stop loss level")
    time_stop_date: datetime = Field(description="Time stop expiration")
    invalidation_level: float | None = Field(default=None, description="Invalidation level")
    
    # Status
    days_held: int = Field(default=0, description="Days since entry")
    status: Literal["ACTIVE", "AT_TP", "AT_SL", "AT_INVALIDATION", "TIME_WARNING", "CLOSED"] = Field(
        default="ACTIVE"
    )
    
    # Metadata
    setup_type: str = Field(description="Original setup type")
    conviction: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Original conviction")


class PositionAlert(BaseModel):
    """Alert for a position requiring attention."""
    alert_id: str = Field(description="Unique alert ID")
    position_id: str = Field(description="Related position ID")
    symbol: str = Field(description="Stock ticker")
    alert_type: Literal[
        "APPROACHING_TP",
        "APPROACHING_SL",
        "AT_INVALIDATION",
        "TIME_WARNING",
        "UNUSUAL_VOLUME",
        "GAP_DOWN",
        "GAP_UP",
    ] = Field(description="Type of alert")
    severity: Literal["INFO", "WARNING", "CRITICAL"] = Field(description="Alert severity")
    message: str = Field(max_length=200, description="Alert message")
    triggered_at: datetime = Field(description="When alert was triggered")


class MonitorOutput(BaseModel):
    """Output schema for Monitor Agent."""
    
    positions_checked: int = Field(description="Number of positions checked")
    
    position_updates: list[dict] = Field(
        default_factory=list,
        description="Updated position data"
    )
    
    alerts: list[PositionAlert] = Field(
        default_factory=list,
        description="Generated alerts"
    )
    
    summary: dict = Field(
        default_factory=dict,
        description="Portfolio summary (total P&L, etc.)"
    )


class InvalidationOutput(BaseModel):
    """Output schema for Invalidation Agent."""
    
    position_id: str = Field(description="Position being checked")
    symbol: str = Field(description="Stock ticker")
    
    is_invalidated: bool = Field(description="Whether position is invalidated")
    
    invalidation_type: Literal[
        "CLOSE_BELOW_LEVEL",
        "CLOSE_ABOVE_LEVEL",
        "TREND_REVERSAL",
        "TIME_STOP",
        "NOT_INVALIDATED",
    ] = Field(description="Type of invalidation")
    
    current_price: float = Field(description="Current price")
    invalidation_level: float | None = Field(default=None, description="Invalidation level")
    
    action: Literal["HOLD", "EXIT_NOW", "EXIT_NEXT_OPEN", "REDUCE"] = Field(
        description="Recommended action"
    )
    
    reason: str = Field(max_length=200, description="Explanation")


class NarrativeOutput(BaseModel):
    """Output schema for Narrative Agent."""
    
    symbol: str = Field(description="Stock ticker")
    
    headline: str = Field(max_length=100, description="One-line headline")
    
    thesis: str = Field(max_length=500, description="Trade thesis summary")
    
    setup_description: str = Field(max_length=300, description="Technical setup description")
    
    risk_factors: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Key risks to monitor"
    )
    
    entry_plan: str = Field(max_length=200, description="Entry execution plan")
    
    exit_plan: str = Field(max_length=200, description="Exit execution plan")
    
    stats_summary: str = Field(max_length=150, description="Historical stats summary")


class MarketContext(BaseModel):
    """Current market conditions for position monitoring."""
    vix_current: float | None = None
    vix_20d_avg: float | None = None
    vix_spike: bool = False
    days_to_fomc: int | None = None
    days_to_cpi: int | None = None
    ticker_has_earnings_soon: bool = False
    days_to_earnings: int | None = None
    price_vs_ma20: Literal["ABOVE", "BELOW"] | None = None
    daily_trend: Literal["UPTREND", "DOWNTREND", "SIDEWAYS"] | None = None


class PositionMonitorOutput(BaseModel):
    """Output from Position Monitor Agent for active position management."""
    
    action: Literal["HOLD", "TIGHTEN", "WIDEN", "EXIT"] = Field(
        description="Recommended action for the position"
    )
    
    reason: str = Field(
        max_length=300,
        description="Why this action is recommended"
    )
    
    new_stop_loss: float | None = Field(
        default=None,
        description="New stop loss level if TIGHTEN/WIDEN"
    )
    
    new_take_profit: float | None = Field(
        default=None,
        description="New take profit level if adjusting"
    )
    
    urgency: Literal["LOW", "MEDIUM", "HIGH"] = Field(
        default="LOW",
        description="How urgent the action is"
    )
    
    risk_factors: list[str] = Field(
        default_factory=list,
        description="Active risk factors (FOMC, VIX_SPIKE, EARNINGS, etc.)"
    )

