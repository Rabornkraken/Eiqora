"""
Ticker Profile schemas.
Provides deep fundamental and narrative context with layered lookbacks.
"""

from datetime import datetime
from typing import Literal, Any
from pydantic import BaseModel, Field

from eiqora_v2.schemas.fundamental import EarningsSnapshot


class MaterialEvent(BaseModel):
    """Significant event (merger, lawsuit, regulatory) with persistent impact."""
    date: str = Field(description="Date of event (YYYY-MM-DD)")
    event_type: str = Field(description="Type: LAWSUIT, REGULATORY, M_AND_A, PRODUCT, EXECUTIVE")
    description: str = Field(description="Brief description")
    status: Literal["ONGOING", "RESOLVED", "UNCERTAIN"] = Field(description="Current status")
    impact: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"] = Field(description="Long-term impact")


class TickerProfile(BaseModel):
    """
    Rich fundamental dossier for a stock.
    Aggregates data across layered time horizons:
    - Fundamentals: 3 years
    - Material Events: 1-2 years
    - Narrative: 90 days
    """
    symbol: str
    last_updated: datetime
    
    # --- LONG-TERM CONTEXT (3 Years) ---
    business_summary: str = Field(description="Core business model and revenue drivers")
    valuation_summary: str = Field(description="Current valuation vs history and sector")
    profitability_trend: str = Field(description="Trend in margins and ROE over last 3 years")
    revenue_growth_3y: float | None = Field(description="3-year Revenue CAGR (%)")
    
    # --- MEDIUM-TERM CONTEXT (1 Year) ---
    earnings_trajectory: list[EarningsSnapshot] = Field(description="Last 4-8 quarters of earnings")
    guidance_history: str = Field(description="Management track record on guidance")
    material_events: list[MaterialEvent] = Field(description="Major events with ongoing impact")
    
    # --- SHORT-TERM CONTEXT (90 Days) ---
    recent_narrative: str = Field(description="Current market narrative and dominant themes")
    analyst_sentiment: str = Field(description="Recent analyst actions and consensus shifts")
    
    # --- THESIS ---
    bull_case: list[str] = Field(description="Key arguments for LONG thesis")
    bear_case: list[str] = Field(description="Key arguments for SHORT thesis")
    catalysts: list[str] = Field(description="Specific upcoming events to watch")
    risks: list[str] = Field(description="Structural or event-driven risks")
    
    # --- PRE-COMPUTED SCORE (stored to avoid recalculation) ---
    profile_score: float = Field(default=0.0, description="Pre-computed P score (0-0.50)")
    score_breakdown: dict[str, Any] = Field(default_factory=dict, description="Individual score components and explanations")
