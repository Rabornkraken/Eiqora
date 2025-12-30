"""
Idea Generator and Exit Policy output schemas.
"""

from typing import Literal
from pydantic import BaseModel, Field


class TradeIdea(BaseModel):
    """A single trade idea."""
    idea_id: str = Field(description="Unique ID for this idea")
    direction: Literal["LONG", "SHORT"] = Field(description="Trade direction")
    thesis: str = Field(max_length=300, description="Brief thesis for the trade")
    setup_type: str = Field(description="Setup type from Chart Agent")
    catalyst: str | None = Field(default=None, description="Catalyst if event-driven")
    time_horizon: Literal["SWING_SHORT", "SWING_MEDIUM", "SWING_LONG"] = Field(
        description="Expected holding period (5-15d, 15-30d, 30-45d)"
    )
    conviction: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="Conviction level"
    )


class IdeaGeneratorOutput(BaseModel):
    """Output schema for Idea Generator Agent."""
    
    ideas: list[TradeIdea] = Field(
        default_factory=list,
        max_length=3,
        description="Generated trade ideas (max 3)"
    )
    
    primary_idea_id: str | None = Field(
        default=None,
        description="ID of the primary/best idea"
    )
    
    skip_reason: str | None = Field(
        default=None,
        description="Reason if no ideas generated"
    )


class ExitBracket(BaseModel):
    """Exit bracket specification."""
    tp_mult: float = Field(ge=1.0, le=10.0, description="Take profit multiplier of vol basis")
    sl_mult: float = Field(ge=0.5, le=5.0, description="Stop loss multiplier of vol basis")
    time_stop_days: int = Field(ge=5, le=60, description="Time stop in trading days")


class TrailingStop(BaseModel):
    """Trailing stop specification."""
    activation_pct: float = Field(ge=0.0, le=1.0, description="Profit % to activate trailing")
    trail_pct: float = Field(ge=0.01, le=0.2, description="Trail percentage from high")


class ExitPolicyOutput(BaseModel):
    """Output schema for Exit Policy Agent."""
    
    idea_id: str = Field(description="ID of the idea this exit policy applies to")
    
    bracket: ExitBracket = Field(description="Exit bracket (TP/SL/time stop)")
    
    vol_basis_type: Literal["RV20", "ATR14_PROXY"] = Field(
        description="Volatility basis for bracket calculation"
    )
    
    trailing_stop: TrailingStop | None = Field(
        default=None,
        description="Optional trailing stop"
    )
    
    scale_out_levels: list[float] = Field(
        default_factory=list,
        max_length=2,
        description="Optional scale-out levels as profit multiples"
    )
    
    notes: str = Field(
        default="",
        max_length=200,
        description="Exit strategy notes"
    )
