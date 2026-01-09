"""
Positions API Request/Response Models
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Literal, List, Dict, Any


class Position(BaseModel):
    """Single position"""

    position_id: str = Field(..., description="Position identifier")
    symbol: str = Field(..., description="Stock ticker symbol")
    direction: Literal["LONG", "SHORT"] = Field(..., description="Trade direction")
    entry_date: datetime = Field(..., description="Position entry date")
    entry_price: float = Field(..., description="Entry price")
    current_price: float = Field(..., description="Current price")
    unrealized_pnl_pct: float = Field(..., description="Unrealized P&L percentage")
    unrealized_pnl: float = Field(..., description="Unrealized P&L in dollars")
    take_profit: float = Field(..., description="Take profit level")
    stop_loss: float = Field(..., description="Stop loss level")
    time_stop_date: datetime = Field(..., description="Time stop date")
    days_held: int = Field(..., description="Days position has been held")
    status: Literal["ACTIVE", "AT_TP", "AT_SL", "CLOSED"] = Field(
        ..., description="Position status"
    )
    conviction: Literal["HIGH", "MEDIUM", "LOW"] = Field(..., description="Conviction level")
    reasoning: str = Field(..., description="Original reasoning for position")


class PositionsResponse(BaseModel):
    """Response with positions list"""

    positions: List[Position] = Field(..., description="List of positions")
    total: int = Field(..., description="Total count")
    total_unrealized_pnl: float = Field(..., description="Total unrealized P&L")


class PositionDetail(Position):
    """Detailed position information"""

    sector: Optional[str] = Field(None, description="Sector")
    shares: int = Field(..., description="Number of shares")
    cost_basis: float = Field(..., description="Cost basis")
    market_value: float = Field(..., description="Current market value")
    max_profit_pct: float = Field(..., description="Maximum profit percentage achieved")
    max_loss_pct: float = Field(..., description="Maximum loss percentage achieved")
    alerts: List[str] = Field(..., description="Active alerts for this position")
    last_updated: datetime = Field(..., description="Last update timestamp")


class ExitRequest(BaseModel):
    """Request to exit a position"""

    reason: str = Field(..., description="Reason for exit")
    exit_type: Optional[Literal["MANUAL", "TP", "SL", "TIME_STOP", "REASSESSMENT"]] = Field(
        "MANUAL", description="Type of exit"
    )


class ExitResponse(BaseModel):
    """Response for position exit"""

    position_id: str = Field(..., description="Position identifier")
    symbol: str = Field(..., description="Stock ticker symbol")
    exit_price: float = Field(..., description="Exit price")
    realized_pnl: float = Field(..., description="Realized P&L")
    realized_pnl_pct: float = Field(..., description="Realized P&L percentage")
    exit_date: datetime = Field(..., description="Exit date")
    reason: str = Field(..., description="Reason for exit")
    exit_type: str = Field(..., description="Type of exit")