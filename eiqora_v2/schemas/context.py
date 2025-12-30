"""
Context Agent output schema.
"""

from typing import Literal
from pydantic import BaseModel, Field


class VolBasis(BaseModel):
    """Volatility measurement."""
    type: Literal["RV20", "ATR14_PROXY"] = Field(description="Volatility calculation method")
    value: float = Field(ge=0, description="Volatility value (daily)")


class TrendStatus(BaseModel):
    """Trend status relative to moving averages."""
    ma20: Literal["ABOVE", "BELOW"] = Field(description="Price vs 20-day MA")
    ma50: Literal["ABOVE", "BELOW"] | None = Field(default=None, description="Price vs 50-day MA")
    ma200: Literal["ABOVE", "BELOW"] | None = Field(default=None, description="Price vs 200-day MA")


class MomentumMetrics(BaseModel):
    """Momentum measurements."""
    ret_20d: float | None = Field(default=None, description="20-day return")
    ret_60d: float | None = Field(default=None, description="60-day return")


class ContextOutput(BaseModel):
    """Output schema for Context Agent."""
    
    vol_basis: VolBasis = Field(description="Volatility basis for position sizing")
    
    trend: TrendStatus = Field(description="Trend classification vs moving averages")
    
    momentum: MomentumMetrics = Field(description="Recent momentum metrics")
    
    volume_z_20d: float = Field(description="Volume z-score vs 20-day average")
    
    state_tags: list[str] = Field(
        default_factory=list,
        description="State classifications (e.g., UPTREND, HIGH_VOL)"
    )
    
    current_price: float = Field(gt=0, description="Current stock price")
    
    data_quality: Literal["GOOD", "SPARSE", "STALE"] = Field(
        default="GOOD",
        description="Data quality flag"
    )
