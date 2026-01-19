"""
Fundamental Agent implementation.
Provides ticker-level sentiment and fundamental overview.

READ-ONLY: Does NOT trigger data collection. Reads from pre-populated database tables:
- document (news articles)
- earnings_event
- sec_filing (via CIK join)
"""

from datetime import datetime, timedelta
from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.fundamental import (
    FundamentalOutput,
    SentimentSummary,
    SECFilingSummary,
    DataStatus,
    EarningsSnapshot,
    InsiderSummary,
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
    - yfinance_news table (news with FinBERT sentiment)
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

            # Step 3b: Get latest earnings snapshot
            earnings_snapshot = await self._get_latest_earnings(symbol, asof_time)

            # Step 3c: Get insider summary
            insider_summary = await self._get_insider_summary(symbol, asof_time)
            
            # Step 3d: Get influential statements
            influential_statements = await self._get_influential_statements(symbol, asof_time)
            
            # Step 4: Analyze sentiment from news headlines
            sentiment_overall, pos_count, neg_count, neutral_count = await self._analyze_sentiment(news_docs)
            
            # Step 5: Check data freshness (but don't trigger collection)
            news_fresh = news_count > 0
            news_last_at = news_docs[0]["published_at"] if news_docs else None
            sec_fresh = len(sec_filings) > 0
            sec_last_at = sec_filings[0]["filed_at"] if sec_filings else None
            earnings_fresh = earnings_snapshot is not None
            
            # Build output (combine profile baseline + fresh data)
            output = FundamentalOutput(
                symbol=symbol,
                sentiment=SentimentSummary(
                    overall=sentiment_overall,
                    news_count=news_count,
                    positive_count=pos_count,
                    negative_count=neg_count,
                    neutral_count=neutral_count,
                    key_topics=self._extract_topics(news_docs),
                    notable_headlines=[d["title"] for d in news_docs[:3]],
                    baseline_risks=baseline_risks,  # From profile
                    baseline_catalysts=baseline_catalysts,  # From profile
                ),
                earnings=earnings_snapshot,
                sec_filings=SECFilingSummary(
                    recent_filings=[
                        {"form_type": f["form_type"], "filed_at": str(f["filed_at"])}
                        for f in sec_filings[:5]
                    ],
                    has_8k=any(f["form_type"] == "8-K" for f in sec_filings),
                    has_10q=any(f["form_type"] == "10-Q" for f in sec_filings),
                    has_10k=any(f["form_type"] == "10-K" for f in sec_filings),
                ),
                insider=insider_summary,
                data_status=DataStatus(
                    news_fresh=news_fresh,
                    news_last_at=news_last_at,
                    sec_fresh=sec_fresh,
                    sec_last_at=sec_last_at,
                    earnings_fresh=earnings_fresh,
                    collections_triggered=[],  # NO collections triggered
                    collection_errors=[],
                ),
                analysis_timestamp=datetime.utcnow(),
                error=None,
            )
            
            self.logger.info(f"{self.name} completed successfully (news={news_count}, sec={len(sec_filings)}, statements={len(influential_statements)})")
            return {
                "fundamental": output.model_dump(),
                "influential_statements": influential_statements,  # Add to state for other agents
            }
            
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
    
    async def _analyze_sentiment(self, news_docs: list[dict]) -> tuple[str, int, int, int]:
        """Simple keyword-based sentiment analysis with counts."""
        if not news_docs:
            return "NEUTRAL", 0, 0, 0
        
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
        
        neutral_count = max(0, len(news_docs) - positive_count - negative_count)

        if positive_count > negative_count + 2:
            return "POSITIVE", positive_count, negative_count, neutral_count
        if negative_count > positive_count + 2:
            return "NEGATIVE", positive_count, negative_count, neutral_count
        return "NEUTRAL", positive_count, negative_count, neutral_count
    
    def _extract_topics(self, news_docs: list[dict]) -> list[str]:
        """Extract key topics from news titles."""
        topics = []
        for doc in news_docs[:5]:
            title = doc.get("title", "")[:50]
            if title:
                topics.append(title)
        return topics

    async def _get_latest_earnings(
        self,
        symbol: str,
        asof_time: datetime,
    ) -> EarningsSnapshot | None:
        """Fetch latest earnings snapshot from earnings_event, if available."""
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM earnings_event
                WHERE symbol = $1
                  AND earnings_date <= $2::date
                ORDER BY earnings_date DESC
                LIMIT 1
                """,
                symbol,
                asof_time,
            )
            if not row:
                return None
            data = dict(row)

        eps_actual = data.get("eps_actual")
        eps_est = data.get("eps_est")
        revenue_actual = data.get("revenue_actual")
        revenue_est = data.get("revenue_est")

        eps_surprise_pct = None
        if eps_actual is not None and eps_est not in (None, 0):
            eps_surprise_pct = (float(eps_actual) - float(eps_est)) / abs(float(eps_est)) * 100

        revenue_growth_yoy = data.get("revenue_growth_yoy")
        if revenue_growth_yoy is not None:
            revenue_growth_yoy = float(revenue_growth_yoy)

        return EarningsSnapshot(
            fiscal_quarter=data.get("fiscal_quarter"),
            eps_actual=float(eps_actual) if eps_actual is not None else None,
            eps_estimate=float(eps_est) if eps_est is not None else None,
            eps_surprise_pct=eps_surprise_pct,
            revenue_actual=float(revenue_actual) if revenue_actual is not None else None,
            revenue_growth_yoy=revenue_growth_yoy,
            guidance=data.get("guidance"),
        )

    async def _get_insider_summary(
        self,
        symbol: str,
        asof_time: datetime,
        window_days: int = 90,
    ) -> InsiderSummary | None:
        """Aggregate insider transactions by role and direction."""
        start_date = asof_time - timedelta(days=window_days)
        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT i.owner_name, i.transaction_date, i.code, i.shares, i.price
                FROM insider_transaction i
                JOIN security s ON i.cik = s.cik
                WHERE s.ticker = $1
                  AND i.transaction_date BETWEEN $2 AND $3
                ORDER BY i.transaction_date DESC
                """,
                symbol,
                start_date,
                asof_time,
            )

        if not rows:
            return InsiderSummary(available=False, transactions_90d=0)

        ceo_value = 0.0
        cfo_value = 0.0
        director_value = 0.0
        other_value = 0.0

        total_buy_value = 0.0
        total_sell_value = 0.0
        buy_count = 0
        sell_count = 0

        for r in rows:
            shares = float(r["shares"] or 0)
            price = float(r["price"] or 0)
            value = shares * price

            name_lower = (r["owner_name"] or "").lower()
            code = r["code"] or ""

            is_buy = code in ("P", "A")
            is_sell = code in ("S", "F")

            if is_buy:
                total_buy_value += value
                buy_count += 1
                net_value = value
            elif is_sell:
                total_sell_value += value
                sell_count += 1
                net_value = -value
            else:
                continue

            if "ceo" in name_lower or "chief executive" in name_lower:
                ceo_value += net_value
            elif "cfo" in name_lower or "chief financial" in name_lower:
                cfo_value += net_value
            elif "director" in name_lower:
                director_value += net_value
            else:
                other_value += net_value

        return InsiderSummary(
            available=True,
            transactions_90d=len(rows),
            buy_count=buy_count,
            sell_count=sell_count,
            total_buy_value=total_buy_value,
            total_sell_value=total_sell_value,
            net_value=total_buy_value - total_sell_value,
            ceo_net_value=ceo_value,
            cfo_net_value=cfo_value,
            director_net_value=director_value,
            other_net_value=other_value,
        )
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Not used - run() is overridden."""
        return {}
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Not used - run() is overridden."""
        return ""
    
    def _build_state_update(self, state: SwingTradeState, result: FundamentalOutput) -> dict[str, Any]:
        """Build state update."""
        return {"fundamental": result.model_dump()}

    async def _get_influential_statements(
        self,
        symbol: str,
        asof_time: datetime,
        window_days: int = 7
    ) -> list[dict]:
        """Get recent statements from influential figures mentioning this symbol."""
        try:
            async with get_connection() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        f.name,
                        f.category,
                        f.organization,
                        f.influence_score,
                        s.statement_text,
                        s.statement_source,
                        s.sentiment,
                        s.statement_date
                    FROM influential_statements s
                    JOIN influential_figures f ON s.figure_id = f.figure_id
                    WHERE $1 = ANY(s.mentioned_symbols)
                      AND s.statement_date >= $2::timestamp - make_interval(days => $3)
                    ORDER BY f.influence_score DESC, s.statement_date DESC
                    LIMIT 5
                """, symbol, asof_time, window_days)
                
                if not rows:
                    return []
                
                return [
                    {
                        "figure": r['name'],
                        "category": r['category'],
                        "organization": r['organization'],
                        "influence": float(r['influence_score']),
                        "statement": r['statement_text'],
                        "source": r['statement_source'],
                        "sentiment": r['sentiment'],
                        "date": r['statement_date'].isoformat() if r['statement_date'] else None,
                    }
                    for r in rows
                ]
        
        except Exception as e:
            self.logger.warning(f"{symbol}: Influential statements fetch failed - {e}")
            return []
