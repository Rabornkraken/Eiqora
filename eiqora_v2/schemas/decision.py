"""
Decision output schemas.
"""

from typing import Literal
from pydantic import BaseModel, Field


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
        max_length=1000,
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
