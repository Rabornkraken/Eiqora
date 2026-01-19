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


class RelativeStrengthPair(BaseModel):
    """Relative performance vs a benchmark."""

    benchmark: str = Field(description="Benchmark symbol")
    symbol_ret_20d: float | None = Field(default=None, description="Symbol 20-day return")
    benchmark_ret_20d: float | None = Field(default=None, description="Benchmark 20-day return")
    rel_ret_20d: float | None = Field(default=None, description="Symbol - benchmark 20-day return")
    symbol_ret_60d: float | None = Field(default=None, description="Symbol 60-day return")
    benchmark_ret_60d: float | None = Field(default=None, description="Benchmark 60-day return")
    rel_ret_60d: float | None = Field(default=None, description="Symbol - benchmark 60-day return")
    data_quality: Literal["OK", "STALE", "MISSING"] = Field(default="OK")


class RelativeStrengthMetrics(BaseModel):
    """Relative strength metrics vs benchmarks."""

    sector_etf: str | None = Field(default=None, description="Sector ETF benchmark")
    vs_spy: RelativeStrengthPair | None = None
    vs_sector: RelativeStrengthPair | None = None
    vs_qqq: RelativeStrengthPair | None = None
    missing: list[str] = Field(default_factory=list)


class OptionsSentiment(BaseModel):
    """Options market sentiment indicators."""
    
    available: bool = Field(description="Whether options data is available")
    pcr_volume: float | None = Field(default=None, description="Put/call ratio by volume")
    pcr_oi: float | None = Field(default=None, description="Put/call ratio by open interest")
    sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"] | None = Field(default=None)
    atm_iv: float | None = Field(default=None, description="At-the-money implied volatility")
    max_pain: float | None = Field(default=None, description="Max pain strike price")
    spot_price: float | None = Field(default=None, description="Spot price at options data collection")


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
    
    atr14: float = Field(gt=0, description="14-period Average True Range for stop loss calculation")

    relative_strength: RelativeStrengthMetrics | None = Field(
        default=None,
        description="Relative strength vs benchmarks"
    )
    
    data_quality: Literal["GOOD", "SPARSE", "STALE"] = Field(
        default="GOOD",
        description="Data quality flag"
    )
    
    options: OptionsSentiment | None = Field(
        default=None,
        description="Options market sentiment (if available)"
    )
