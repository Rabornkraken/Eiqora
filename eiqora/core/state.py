import operator
from typing import TypedDict, Annotated, List, Optional, Dict, Any, Union
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    """
    The central shared state of the Eiqora system.
    Acts as the 'Forum' where all agents publish their findings.
    """
    
    # --- Input ---
    ticker: str
    
    # --- Scout Data (The "Deep Research" Layer) ---
    market_data: Optional[Dict[str, Any]]  # Prices, ratios, volume
    technical_indicators: Optional[Dict[str, Any]] # RSI, MACD, etc.
    earnings_reports: Optional[Dict[str, Any]] # Upcoming dates, surprises
    news_summary: Optional[str]            # Synthesized news briefing
    news_raw_sources: Optional[List[Dict[str, str]]]  # Raw news URLs and snippets
    sentiment_analysis: Optional[Dict[str, float]] # e.g. {"bullish_score": 0.8}
    sentiment_raw_sources: Optional[List[Dict[str, str]]]  # Raw Reddit snippets
    
    # --- The Arena (The Reasoning Layer) ---
    # We use 'operator.add' to append new messages to the history automatically
    debate_history: Annotated[List[BaseMessage], operator.add]
    
    # --- Decisions ---
    investment_plan: Optional[str]         # The Trader's proposed plan
    risk_assessment: Optional[str]         # The Risk Manager's critique
    final_decision: Optional[str]          # BUY/SELL/HOLD + Rationale
    
    # --- Memory ---
    relevant_memories: Optional[List[str]] # Insights from past similar situations
    
    # --- Metadata ---
    iteration_count: int                   # For tracking debate loops
