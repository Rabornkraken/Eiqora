"""
Fundamental Agent implementation.
Provides ticker-level sentiment and fundamental overview.

READ-ONLY: Does NOT trigger data collection. Reads from pre-populated database tables:
- document (news articles)
- earnings_event
- sec_filing (via CIK join)
"""

from datetime import datetime
from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.fundamental import (
    FundamentalOutput,
    SentimentSummary,
    SECFilingSummary,
    DataStatus,
)
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.documents import get_documents, count_recent_documents
from eiqora_v2.tools.events import get_sec_filings
from eiqora_v2.tools.db import get_connection


class FundamentalAgent(BaseAgent[FundamentalOutput]):
    """
    Fundamental Agent: provides sentiment and fundamental overview.
    
    READ-ONLY - does NOT trigger data collection.
    Assumes data is pre-populated by BacktestDataCollector or scheduled pipelines.
    
    Reads from:
    - document table (news)
    - earnings_event table
    - sec_filing table
    
    Returns structured output with available data, or indicates data is missing.
    """
    
    name = "fundamental"
    output_schema = FundamentalOutput
    
    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """Read fundamental data from database (no collection)."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        # Get profile for baseline context (updated weekly)
        profile = state.get("profile", {})
        baseline_risks = profile.get("risks", [])
        baseline_catalysts = profile.get("catalysts", [])
        
        self.logger.info(f"Running {self.name} for {symbol}")
        if profile:
            self.logger.info(f"  Using profile baseline: {len(baseline_risks)} known risks, {len(baseline_catalysts)} catalysts")
        
        try:
            # Step 1: Check what data is available (READ-ONLY)
            doc_counts = await count_recent_documents(symbol, 168, asof_time)  # 7 days
            news_count = sum(doc_counts.values())
            
            # Step 2: Get recent news for sentiment
            news_docs = await get_documents(symbol, 72, asof_time, limit=20)  # 3 days
            
            # Step 3: Get SEC filings
            sec_filings = await get_sec_filings(symbol, 30, asof_time)
            
            # Step 4: Analyze sentiment from news headlines
            sentiment_overall = await self._analyze_sentiment(news_docs)
            
            # Step 5: Check data freshness (but don't trigger collection)
            news_fresh = news_count > 0
            news_last_at = news_docs[0]["published_at"] if news_docs else None
            sec_fresh = len(sec_filings) > 0
            sec_last_at = sec_filings[0]["filed_at"] if sec_filings else None
            
            # Build output (combine profile baseline + fresh data)
            output = FundamentalOutput(
                symbol=symbol,
                sentiment=SentimentSummary(
                    overall=sentiment_overall,
                    news_count=news_count,
                    key_topics=self._extract_topics(news_docs),
                    notable_headlines=[d["title"] for d in news_docs[:3]],
                    baseline_risks=baseline_risks,  # From profile
                    baseline_catalysts=baseline_catalysts,  # From profile
                ),
                earnings=None,  # Would need earnings_event query
                sec_filings=SECFilingSummary(
                    recent_filings=[
                        {"form_type": f["form_type"], "filed_at": str(f["filed_at"])}
                        for f in sec_filings[:5]
                    ],
                    has_8k=any(f["form_type"] == "8-K" for f in sec_filings),
                    has_10q=any(f["form_type"] == "10-Q" for f in sec_filings),
                    has_10k=any(f["form_type"] == "10-K" for f in sec_filings),
                ),
                data_status=DataStatus(
                    news_fresh=news_fresh,
                    news_last_at=news_last_at,
                    sec_fresh=sec_fresh,
                    sec_last_at=sec_last_at,
                    earnings_fresh=False,
                    collections_triggered=[],  # NO collections triggered
                    collection_errors=[],
                ),
                analysis_timestamp=datetime.utcnow(),
                error=None,
            )
            
            self.logger.info(f"{self.name} completed successfully (news={news_count}, sec={len(sec_filings)})")
            return {"fundamental": output.model_dump()}
            
        except Exception as e:
            self.logger.error(f"{self.name} failed: {e}")
            return {
                "fundamental": FundamentalOutput(
                    symbol=symbol,
                    sentiment=SentimentSummary(overall="NEUTRAL", news_count=0),
                    data_status=DataStatus(
                        news_fresh=False,
                        collections_triggered=[],
                        collection_errors=[str(e)],
                    ),
                    analysis_timestamp=datetime.utcnow(),
                    error=str(e),
                ).model_dump()
            }
    
    async def _analyze_sentiment(self, news_docs: list[dict]) -> str:
        """Simple keyword-based sentiment analysis."""
        if not news_docs:
            return "NEUTRAL"
        
        positive_keywords = ["beat", "surge", "jump", "upgrade", "bullish", "strong", "growth", "record"]
        negative_keywords = ["miss", "drop", "fall", "downgrade", "bearish", "weak", "decline", "cut"]
        
        positive_count = 0
        negative_count = 0
        
        for doc in news_docs:
            title = (doc.get("title") or "").lower()
            for kw in positive_keywords:
                if kw in title:
                    positive_count += 1
            for kw in negative_keywords:
                if kw in title:
                    negative_count += 1
        
        if positive_count > negative_count + 2:
            return "POSITIVE"
        elif negative_count > positive_count + 2:
            return "NEGATIVE"
        return "NEUTRAL"
    
    def _extract_topics(self, news_docs: list[dict]) -> list[str]:
        """Extract key topics from news titles."""
        topics = []
        for doc in news_docs[:5]:
            title = doc.get("title", "")[:50]
            if title:
                topics.append(title)
        return topics
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Not used - run() is overridden."""
        return {}
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Not used - run() is overridden."""
        return ""
    
    def _build_state_update(self, state: SwingTradeState, result: FundamentalOutput) -> dict[str, Any]:
        """Build state update."""
        return {"fundamental": result.model_dump()}
