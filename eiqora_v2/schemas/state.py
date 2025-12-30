"""
LangGraph state definition for swing trade analysis.
"""

from datetime import datetime
from typing import TypedDict, Any


class SwingTradeState(TypedDict, total=False):
    """
    State object passed through the LangGraph workflow.
    
    All agent outputs are accumulated here as the graph executes.
    Uses total=False to allow partial updates.
    """
    # Request context
    request_id: str
    symbol: str
    sector: str
    sector_etf: str
    asof_time: datetime
    
    # Trigger information
    trigger_type: str  # SEC_8K, NEWS, CHART_SETUP, etc.
    trigger_refs: list[str]
    
    # Agent outputs (accumulated as graph executes)
    triage: dict[str, Any] | None
    facts: dict[str, Any] | None  # From Event Extractor
    context: dict[str, Any] | None
    topdown: dict[str, Any] | None
    chart: dict[str, Any] | None
    ideas: dict[str, Any] | None
    rules: list[dict[str, Any]] | None
    stats: list[dict[str, Any]] | None
    decision: dict[str, Any] | None
    veto: dict[str, Any] | None
    narrative: dict[str, Any] | None
    
    # Control flow
    needs_extraction: bool
    should_continue: bool
    filter_reason: str | None
    
    # Error tracking
    errors: list[str]
