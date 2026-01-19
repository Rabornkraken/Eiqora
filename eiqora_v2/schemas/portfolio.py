"""
Portfolio-level schemas for coordination and batch processing.
"""

from typing import Any, Literal
from pydantic import BaseModel, Field


class SignalSummary(BaseModel):
    """Summary of a GO signal from an individual ticker."""
    symbol: str = Field(description="Stock ticker")
    sector: str = Field(description="Sector classification")
    sector_etf: str = Field(description="Sector ETF")
    direction: Literal["LONG", "SHORT"] = Field(description="Trade direction")
    conviction: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Conviction level")
    setup_type: str = Field(description="Chart setup type")
    risk_score: float = Field(ge=0.0, le=1.0, description="Risk score from Decision Agent")
    entry_level: float | None = Field(default=None, description="Entry price level")
    clusters: list[str] = Field(default_factory=list, description="Correlation clusters this symbol belongs to")


class PortfolioConstraints(BaseModel):
    """Portfolio constraint configuration."""
    max_positions: int = Field(default=6, description="Maximum simultaneous positions")
    max_sector_pct: float = Field(default=0.30, description="Max % in single sector")
    max_cluster_positions: dict[str, int] = Field(
        default_factory=lambda: {
            "SEMIS": 2,
            "FANMAG": 3,
            "BANKS": 2,
            "ENERGY": 2,
        },
        description="Max positions per correlation cluster"
    )


class ApprovedSignal(BaseModel):
    """A signal approved by Portfolio Coordinator."""
    symbol: str
    direction: Literal["LONG", "SHORT"]
    priority: int = Field(description="Execution priority (1 = highest)")
    allocation_pct: float | None = Field(default=None, description="Suggested allocation %")
    notes: str = Field(default="", max_length=200)


class RejectedSignal(BaseModel):
    """A signal rejected by Portfolio Coordinator."""
    symbol: str
    reason: str = Field(description="Reason for rejection")
    constraint: str = Field(description="Which constraint caused rejection")


class PortfolioCoordinatorOutput(BaseModel):
    """Output schema for Portfolio Coordinator."""
    
    approved: list[ApprovedSignal] = Field(
        default_factory=list,
        description="Approved signals in priority order"
    )
    
    rejected: list[RejectedSignal] = Field(
        default_factory=list,
        description="Rejected signals with reasons"
    )
    
    portfolio_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of approved portfolio (sector breakdown, etc.)"
    )
    
    constraints_applied: list[str] = Field(
        default_factory=list,
        description="List of constraints that were binding"
    )


class TopDownOutput(BaseModel):
    """Output schema for TopDown Agent."""
    
    regime: Literal[
        "RISK_ON",
        "RISK_OFF",
        "HIGH_VOL",
        "LOW_VOL",
        "TRANSITION",
        "UNKNOWN",
    ] = Field(description="Current market regime")
    
    spy_trend: Literal["UP", "DOWN", "SIDEWAYS"] = Field(
        description="SPY trend status"
    )
    
    vix_level: Literal["LOW", "NORMAL", "ELEVATED", "HIGH"] = Field(
        description="VIX regime classification"
    )
    
    sector_rotation: dict[str, Literal["LEADING", "LAGGING", "NEUTRAL"]] = Field(
        default_factory=dict,
        description="Sector rotation status"
    )
    
    policy_hints: dict[str, str] = Field(
        default_factory=dict,
        description="Policy implications (Fed, rates, etc.)"
    )
    
    bias: Literal["BULLISH", "BEARISH", "NEUTRAL"] = Field(
        description="Overall market bias"
    )
    
    notes: str = Field(default="", max_length=300)


class ShortPerspectiveOutput(BaseModel):
    """Output schema for Short Perspective Agent (Contrarian Rebuttal)."""
    
    short_case_strength: Literal["STRONG", "MODERATE", "WEAK", "NONE"] = Field(
        description="Strength of the short case"
    )
    
    short_score: float = Field(
        ge=0.0,
        le=10.0,
        description="Quantitative short score (0-10)"
    )
    
    reasoning: str = Field(
        max_length=500,
        description="Why would/wouldn't you short this stock?"
    )
    
    red_flags: list[str] = Field(
        default_factory=list,
        description="Specific bearish signals detected"
    )
    
    recommend_reject_long: bool = Field(
        description="Should the long trade be rejected?"
    )
