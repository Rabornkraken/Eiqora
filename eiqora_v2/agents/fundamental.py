"""
Fundamental Agent implementation.
Provides ticker-level sentiment and fundamental overview.
Triggers data collection pipelines when data is stale or missing.
"""

from datetime import datetime
from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.fundamental import (
    FundamentalOutput,
    SentimentSummary,
    EarningsSnapshot,
    SECFilingSummary,
    DataStatus,
)
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.documents import get_documents
from eiqora_v2.services.collector_orchestrator import ensure_fresh_data
from eiqora_v2.tools.db import get_connection


class FundamentalAgent(BaseAgent[FundamentalOutput]):
    """
    Fundamental Agent: provides sentiment and fundamental overview.
    
    1. Checks DB for existing data
    2. Triggers data collection if data is stale/missing
    3. Aggregates sentiment from news
    4. Summarizes SEC filings and earnings
    5. Returns structured output
    
    Returns error if required data cannot be collected.
    """
    
    name = "fundamental"
    output_schema = FundamentalOutput
    
    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """
        Override run to handle data collection before LLM call.
        """
        symbol = state["symbol"]
        
        self.logger.info(f"Running {self.name} for {symbol}")
        
        # Step 1: Ensure fresh data
        freshness, errors = await ensure_fresh_data(
            symbol=symbol,
            require_news=True,
            require_sec=False,
            require_earnings=False,
        )
        
        # Step 2: Generate Deep Profile
        # This aggregates 3y fundamentals, 1y events, 90d narrative
        from eiqora_v2.services.profile_generator import ProfileGenerator
        generator = ProfileGenerator()
        
        try:
            # Use get_profile which handles caching (24h validity)
            profile = await generator.get_profile(symbol)
            
            # Step 3: Construct Output
            # Map TickerProfile to FundamentalOutput
            sentiment_overall = "NEUTRAL"
            if "bullish" in profile.analyst_sentiment.lower() or "positive" in profile.recent_narrative.lower():
                sentiment_overall = "POSITIVE"
            elif "bearish" in profile.analyst_sentiment.lower() or "negative" in profile.recent_narrative.lower():
                sentiment_overall = "NEGATIVE"
                
            output = FundamentalOutput(
                symbol=symbol,
                sentiment=SentimentSummary(
                    overall=sentiment_overall,
                    news_count=30, # from profile generator limit
                    key_topics=[profile.recent_narrative[:50]],
                    notable_headlines=profile.catalysts
                ),
                earnings=profile.earnings_trajectory[0] if profile.earnings_trajectory else None,
                sec_filings=SECFilingSummary(
                    recent_filings=[], # TODO: map from profile data
                    has_8k=any("8-K" in str(e) for e in profile.material_events),
                    has_10q=any("10-Q" in str(e) for e in profile.material_events),
                    has_10k=any("10-K" in str(e) for e in profile.material_events)
                ),
                data_status=DataStatus(
                    news_fresh=freshness.get("news_fresh", False),
                    news_last_at=freshness.get("news_last_at"),
                    sec_fresh=freshness.get("sec_fresh", False),
                    sec_last_at=freshness.get("sec_last_at"),
                    earnings_fresh=freshness.get("earnings_fresh", False),
                    collections_triggered=freshness.get("collections_triggered", []),
                    collection_errors=errors
                ),
                analysis_timestamp=datetime.utcnow(),
                error=None
            )
            
            self.logger.info(f"{self.name} completed successfully")
            return {"fundamental": output.model_dump()}
            
        except Exception as e:
            self.logger.error(f"{self.name} failed: {e}")
            return {
                "fundamental": FundamentalOutput(
                    symbol=symbol,
                    sentiment=SentimentSummary(overall="NEUTRAL", news_count=0),
                    data_status=DataStatus(
                        news_fresh=freshness.get("news_fresh", False), 
                        collections_triggered=freshness.get("collections_triggered", []),
                        collection_errors=errors
                    ),
                    analysis_timestamp=datetime.utcnow(),
                    error=str(e),
                ).model_dump()
            }

    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Unused as run() is overridden."""
        return {}
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Unused as run() is overridden."""
        return ""
    
    def _build_state_update(self, state: SwingTradeState, result: FundamentalOutput) -> dict[str, Any]:
        """Build state update."""
        return {"fundamental": result.model_dump()}
