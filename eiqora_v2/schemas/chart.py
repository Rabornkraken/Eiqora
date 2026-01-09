"""
Chart Agent output schema.
"""

from typing import Literal
from pydantic import BaseModel, Field

from eiqora_v2.schemas.context import RelativeStrengthMetrics

class EntryTrigger(BaseModel):
    """Entry trigger specification."""
    type: Literal["BREAK_YDAY_HIGH", "CLOSE_ABOVE_LEVEL", "NEXT_OPEN"] = Field(
        description="Type of entry trigger"
    )
    level: float | None = Field(default=None, description="Price level if applicable")


class Invalidation(BaseModel):
    """Invalidation level specification."""
    type: Literal["CLOSE_BELOW_LEVEL", "CLOSE_BELOW_20D_LOW", "CLOSE_BELOW_50DMA"] = Field(
        description="Type of invalidation trigger"
    )
    level: float = Field(description="Price level for invalidation")
    days_confirm: int = Field(default=1, ge=1, description="Days required to confirm")


class SetupQuality(BaseModel):
    """Setup quality assessment."""
    volume_confirm: bool = Field(description="Whether volume confirms the setup")
    compression: bool = Field(description="Whether price is in compression")
    score: float = Field(ge=0.0, le=1.0, description="Overall setup quality score")


class ChartOutput(BaseModel):
    """Output schema for Chart Agent."""
    
    setup_type: Literal[
        "BREAKOUT_20D",
        "BREAKOUT_60D",
        "PULLBACK_MA20",
        "PULLBACK_MA50",
        "BASE_BREAKOUT",
        "REVERSAL_AFTER_SELL_OFF",
        "RANGE_FADE_HIGH",
        "RANGE_FADE_LOW",
        "NO_SETUP",
    ] = Field(description="Chart setup classification from finite taxonomy")
    
    direction: Literal["LONG", "SHORT", "NEUTRAL"] = Field(
        default="NEUTRAL",
        description="Directional bias from chart analysis"
    )
    
    entry_trigger: EntryTrigger | None = Field(
        default=None,
        description="Entry trigger if setup is actionable"
    )
    
    invalidation: Invalidation | None = Field(
        default=None,
        description="Invalidation level for the setup"
    )
    
    setup_quality: SetupQuality = Field(description="Quality assessment of the setup")
    
    key_levels: dict[str, float] = Field(
        default_factory=dict,
        description="Key price levels (support, resistance, pivots)"
    )
    
    notes: str = Field(
        default="",
        max_length=300,
        description="Brief technical notes"
    )

    relative_strength: RelativeStrengthMetrics | None = Field(
        default=None,
        description="Relative strength vs benchmarks (copied from context)"
    )
