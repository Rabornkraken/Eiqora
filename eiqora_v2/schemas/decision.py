"""
Analog Planner and Decision output schemas.
"""

from typing import Literal
from pydantic import BaseModel, Field


class AnalogFilter(BaseModel):
    """Filters for analog selection."""
    sector_etf: str | None = Field(default=None, description="Sector ETF filter")
    vol_bucket: Literal["LOW", "MED", "HIGH"] | None = Field(default=None)
    trend_bucket: Literal["UP", "DOWN", "SIDEWAYS"] | None = Field(default=None)
    regime: str | None = Field(default=None, description="Market regime filter")


class AnalogPlan(BaseModel):
    """A single analog query plan."""
    plan_id: str = Field(description="Unique plan ID")
    idea_id: str = Field(description="ID of the idea this plan evaluates")
    event_type: str = Field(description="Setup/event type for analog matching")
    filters: AnalogFilter = Field(description="Filters to apply")
    lookback_years: int = Field(default=8, ge=1, le=20)
    min_samples: int = Field(default=30, ge=8, le=100)


class AnalogPlannerOutput(BaseModel):
    """Output schema for Analog Planner Agent."""
    
    plans: list[AnalogPlan] = Field(
        default_factory=list,
        description="Analog query plans to execute"
    )


class StatsResult(BaseModel):
    """Statistics from analog evaluation (from Stats Service, not LLM)."""
    plan_id: str = Field(description="Plan ID these stats apply to")
    status: Literal["OK", "INSUFFICIENT_DATA", "ERROR"] = Field(description="Query status")
    sample_size: int = Field(ge=0, description="Number of analogs found")
    win_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_return: float | None = Field(default=None)
    median_return: float | None = Field(default=None)
    p10: float | None = Field(default=None, description="10th percentile return")
    p90: float | None = Field(default=None, description="90th percentile return")
    avg_hold_days: float | None = Field(default=None)
    stability: float | None = Field(default=None, ge=0.0, le=1.0, description="Stats stability score")
    relaxations_applied: list[str] = Field(default_factory=list)


class TradeRule(BaseModel):
    """A complete trade rule for execution."""
    rule_id: str = Field(description="Unique rule ID")
    idea_id: str = Field(description="Parent idea ID")
    direction: Literal["LONG", "SHORT"] = Field(description="Trade direction")
    
    # Entry
    entry_trigger_type: str = Field(description="Entry trigger type")
    entry_level: float | None = Field(default=None)
    
    # Exit bracket
    tp_mult: float = Field(ge=1.0, le=10.0)
    sl_mult: float = Field(ge=0.5, le=5.0)
    time_stop_days: int = Field(ge=5, le=60)
    
    # Invalidation
    invalidation_type: str = Field(description="Invalidation trigger type")
    invalidation_level: float = Field(description="Invalidation price level")
    
    # Stats reference
    stats: StatsResult | None = Field(default=None)


class DecisionOutput(BaseModel):
    """Output schema for Decision Agent."""
    
    decision: Literal["GO", "NO_GO", "REVIEW"] = Field(
        description="Final decision"
    )
    
    rule: TradeRule | None = Field(
        default=None,
        description="Trade rule if GO"
    )
    
    gates_passed: list[str] = Field(
        default_factory=list,
        description="List of gates that passed"
    )
    
    gates_failed: list[str] = Field(
        default_factory=list,
        description="List of gates that failed"
    )
    
    reason: str = Field(
        max_length=300,
        description="Decision reasoning"
    )
    
    risk_score: float = Field(
        ge=0.0, le=1.0,
        description="Risk score (higher = riskier)"
    )


class VetoOutput(BaseModel):
    """Output schema for Sanity/Veto Agent."""
    
    veto: bool = Field(description="Whether to veto the trade")
    
    veto_reason: str | None = Field(
        default=None,
        max_length=200,
        description="Reason for veto if applicable"
    )
    
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking warnings"
    )
    
    sanity_checks: dict[str, bool] = Field(
        default_factory=dict,
        description="Individual sanity check results"
    )
