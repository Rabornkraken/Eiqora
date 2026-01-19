"""
Services for generating and managing Ticker Profiles.
"""

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from eiqora_v2.schemas.profile import TickerProfile, MaterialEvent
from eiqora_v2.schemas.fundamental import EarningsSnapshot
from eiqora_v2.tools.db import get_connection
from eiqora_v2.llm.client import call_llm

logger = logging.getLogger(__name__)


class ProfileGenerator:
    """Generates Ticker Profiles using multi-layered historical data."""
    
    async def get_profile(self, symbol: str, use_cache: bool = True) -> TickerProfile:
        """
        Get Ticker Profile, using cache if fresh (< 7 days).
        If stale or use_cache=False, generates new profile.
        """
        if use_cache:
            profile = await self._load_profile(symbol)
            if profile:
                # Check freshness - weekly refresh
                age = datetime.utcnow().replace(tzinfo=None) - profile.last_updated.replace(tzinfo=None)
                if age < timedelta(days=7):
                    logger.info(f"Using cached profile for {symbol} (age: {age.days}d)")
                    return profile
                else:
                    logger.info(f"Cached profile for {symbol} is stale (age: {age.days}d, refreshing...)")
            else:
                logger.info(f"No cached profile found for {symbol}")

        return await self.generate_profile(symbol)

    async def generate_profile(self, symbol: str) -> TickerProfile:
        """
        Generate a fresh Ticker Profile for the symbol.
        Queries deep history (3y) for fundamentals and 1y for events.
        """
        from eiqora_v2.services.signal_aggregator import gather_quantitative_signals
        
        logger.info(f"Generating Ticker Profile for {symbol}")
        
        # 1. Gather Layered Data (text for narrative)
        data = await self._gather_layered_data(symbol)
        
        # 2. Gather Quantitative Signals (structured for scoring)
        signals = await gather_quantitative_signals(symbol)
        
        # 3. Synthesize with LLM (includes signals for contextual scoring)
        profile = await self._synthesize_profile(symbol, data, signals)
        
        # 4. Save to DB
        await self._save_profile(profile)
        
        return profile

    async def _load_profile(self, symbol: str) -> TickerProfile | None:
        """Load profile from DB."""
        try:
            async with get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT profile_data, updated_at FROM ticker_profile WHERE symbol = $1", 
                    symbol
                )
                if row:
                    import json
                    data = json.loads(row["profile_data"])
                    profile = TickerProfile.model_validate(data)
                    # Override last_updated with DB timestamp to ensure freshness check is accurate
                    if row["updated_at"]:
                        profile.last_updated = row["updated_at"]
                    return profile
        except Exception as e:
            logger.error(f"Error loading profile for {symbol}: {e}")
        return None

    async def _save_profile(self, profile: TickerProfile) -> None:
        """Save profile to DB."""
        try:
            import json
            # Ensure last_updated is current before saving
            profile.last_updated = datetime.utcnow()
            data_json = profile.model_dump_json()
            
            async with get_connection() as conn:
                await conn.execute("""
                    INSERT INTO ticker_profile (symbol, profile_data, updated_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (symbol) 
                    DO UPDATE SET profile_data = EXCLUDED.profile_data, updated_at = EXCLUDED.updated_at
                """, profile.symbol, data_json, profile.last_updated)
        except Exception as e:
            logger.error(f"Error saving profile for {profile.symbol}: {e}")
        
    async def _gather_layered_data(self, symbol: str) -> dict[str, Any]:
        """Gather data across different time horizons + new data sources."""
        async with get_connection() as conn:
            # A. Fundamentals (Last 3 Years / 12 Quarters)
            earnings_rows = await conn.fetch("""
                SELECT earnings_date as report_date, fiscal_quarter, eps_actual, eps_est as eps_estimate, 
                       revenue_actual, revenue_growth_yoy, guidance
                FROM earnings_event
                WHERE symbol = $1
                ORDER BY earnings_date DESC
                LIMIT 12
            """, symbol)
            
            # B. News (Last 1 Year for Major Events)
            # Filter for high-impact keywords to find events
            major_event_keywords = [
                "investigation", "lawsuit", "SEC", "DOJ", "regulatory",
                "acquisition", "merger", "spinoff", "restructuring",
                "CEO", "CFO", "resignation", "activist", "patent"
            ]
            keywords_pattern = "|".join(major_event_keywords)
            
            event_news_rows = await conn.fetch("""
                SELECT yn.published_at, yn.title, LEFT(yn.text, 500) as text_preview,
                       nr.score as sentiment
                FROM yfinance_news yn
                LEFT JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
                WHERE yn.ticker = $1 
                  AND yn.published_at >= NOW() - interval '1 year'
                  AND (yn.title ~* $2 OR yn.text ~* $2)
                ORDER BY yn.published_at DESC
                LIMIT 50
            """, symbol, keywords_pattern)
            
            # C. Recent News (Last 90 Days for Narrative)
            # Include sentiment scores for context
            recent_news_rows = await conn.fetch("""
                SELECT yn.published_at, yn.title, nr.score as sentiment
                FROM yfinance_news yn
                LEFT JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
                WHERE yn.ticker = $1
                  AND yn.published_at >= NOW() - interval '90 days'
                ORDER BY yn.published_at DESC
                LIMIT 30
            """, symbol)
            
            # D. SEC Filings (Last 2 Years)
            sec_rows = await conn.fetch("""
                SELECT s.filed_at, s.form_type, s.description
                FROM sec_filing s
                JOIN security sec ON s.cik = sec.cik
                WHERE sec.ticker = $1 
                  AND s.filed_at >= NOW() - interval '2 years'
                  AND s.form_type IN ('10-K', '10-Q', '8-K')
                ORDER BY s.filed_at DESC
                LIMIT 20
            """, symbol)
            
            # E. NEW: Options Data (Last 30 Days)
            options_rows = await conn.fetch("""
                SELECT date, put_call_ratio_volume, put_call_ratio_oi, atm_iv
                FROM options_daily_summary
                WHERE symbol = $1
                  AND date >= CURRENT_DATE - 30
                ORDER BY date DESC
            """, symbol)
            
            # F. NEW: Stock Relationships (Supply Chain Context)
            relationships_rows = await conn.fetch("""
                SELECT symbol_to, relationship_type, description, strength
                FROM stock_relationships
                WHERE symbol_from = $1
                ORDER BY strength DESC
                LIMIT 10
            """, symbol)
            
            # G. NEW: Influential Statements (Last 30 Days)
            statements_rows = await conn.fetch("""
                SELECT 
                    f.name as figure_name,
                    f.category,
                    f.influence_score,
                    s.statement_text,
                    s.sentiment,
                    s.statement_date
                FROM influential_statements s
                JOIN influential_figures f ON s.figure_id = f.figure_id
                WHERE $1 = ANY(s.mentioned_symbols)
                  AND s.statement_date >= CURRENT_DATE - 30
                ORDER BY f.influence_score DESC, s.statement_date DESC
                LIMIT 5
            """, symbol)
            
            # H. NEW: Money Flow Trend (Last 30 Days)
            money_flow_rows = await conn.fetch("""
                SELECT date, cmf_20, mfi_14, obv
                FROM market_bar_daily
                WHERE symbol = $1
                  AND date >= CURRENT_DATE - 30
                  AND cmf_20 IS NOT NULL
                ORDER BY date DESC
            """, symbol)
            
            return {
                "earnings": [dict(r) for r in earnings_rows],
                "event_news": [dict(r) for r in event_news_rows],
                "recent_news": [dict(r) for r in recent_news_rows],
                "sec_filings": [dict(r) for r in sec_rows],
                "options_data": [dict(r) for r in options_rows],
                "relationships": [dict(r) for r in relationships_rows],
                "influential_statements": [dict(r) for r in statements_rows],
                "money_flow_data": [dict(r) for r in money_flow_rows],
            }

    async def _synthesize_profile(self, symbol: str, data: dict[str, Any], signals: dict[str, Any] | None = None) -> TickerProfile:
        """Call LLM to synthesize data into a profile with HYBRID scoring."""
        from eiqora_v2.services.quantitative_scoring import calculate_quantitative_base_score
        
        # STEP 1: Calculate quantitative base score
        base_score = 0.5
        quant_breakdown = {}
        
        if signals:
            options_data = data.get('options_data', [])
            money_flow_data = data.get('money_flow_data', [])
            base_score, quant_breakdown = calculate_quantitative_base_score(
                signals, 
                options_data, 
                money_flow_data
            )
            logger.info(f"{symbol}: Quantitative base score = {base_score:.3f} (incl. money flow)")
        
        # STEP 2: Build enhanced prompt with new data sources
        prompt = self._build_synthesis_prompt(symbol, data, signals, base_score, quant_breakdown)
        
        import json
        schema_json = json.dumps(TickerProfile.model_json_schema(), indent=2)
        
        system_prompt = f"""You are an Expert Equity Analyst. 
        Your goal is to build a "Deep Context Profile" for a stock.
        
        HYBRID SCORING APPROACH:
        - A quantitative base score has been calculated from earnings, insider, sentiment, and options data
        - Your job is to ADJUST this score +/- 0.20 based on qualitative factors
        - Adjustment factors: material events, competitive threats, regulatory risks, management changes
        
        Focus on:
        1. **The Structural Story:** What drives this business over 3-5 years?
        2. **Material Events:** Ongoing sagas (lawsuits, turnarounds, supply chain issues)
        3. **Market Context:** Options flow, influential statements, supply chain signals
        4. **Current Narrative:** What is the market focused on RIGHT NOW (last 90 days)?
        5. **Qualitative Adjustment:** How should material events modify the quantitative score?
        
        IMPORTANT SCORING RULES:
        - Base quantitative_score is provided
        - Your llm_adjustment should be between -0.20 and +0.20
        - Final profile_score = quantitative_score + llm_adjustment (clamped to 0.0-1.0)
        - Explain adjustment in score_breakdown
        
        Be concise, objective, and professional.
        
        CRITICAL: Return ONLY valid JSON matching this schema:
        {schema_json}
        """
        
        profile = await call_llm(
            prompt=prompt,
            schema=TickerProfile,
            system_prompt=system_prompt
        )
        
        # STEP 3: Apply hybrid scoring (override LLM's score if it's way off)
        # Get LLM's suggested score
        llm_score = profile.profile_score
        
        # Calculate adjustment from base
        llm_adjustment = llm_score - base_score
        
        # Clamp adjustment to reasonable range
        llm_adjustment_clamped = max(-0.20, min(0.20, llm_adjustment))
        
        # Final hybrid score
        hybrid_score = base_score + llm_adjustment_clamped
        hybrid_score = max(0.0, min(1.0, hybrid_score))
        
        # Log for debugging
        if abs(llm_adjustment) > 0.20:
            logger.warning(
                f"{symbol}: LLM adjustment {llm_adjustment:+.3f} clamped to {llm_adjustment_clamped:+.3f}"
            )
        
        # Update profile with hybrid score
        profile.profile_score = hybrid_score
        
        # Enhance score_breakdown with hybrid details
        if not profile.score_breakdown:
            profile.score_breakdown = {}
        
        profile.score_breakdown.update({
            'quantitative_base': round(base_score, 3),
            'llm_adjustment': round(llm_adjustment_clamped, 3),
            'final_hybrid_score': round(hybrid_score, 3),
            **quant_breakdown,  # Include detailed quantitative breakdown
        })
        
        return profile

    def _build_synthesis_prompt(self, symbol: str, data: dict[str, Any], signals: dict[str, Any] | None = None, base_score: float = 0.5, quant_breakdown: dict[str, Any] | None = None) -> str:
        import json
        
        earnings = data.get("earnings", [])
        events = data.get("event_news", [])
        recent = data.get("recent_news", [])
        
        # Format Earnings
        earnings_txt = "No earnings data."
        if earnings:
            earnings_txt = "\n".join([
                f"- {e['report_date']} ({e['fiscal_quarter']}): EPS {e['eps_actual']} vs {e['eps_estimate']}, Rev Growth {e.get('revenue_growth_yoy')}%"
                for e in earnings
            ])
            
        # Format Major Events News with sentiment
        events_txt = "\n".join([
            f"- {n['published_at']}: {n['title']}" + 
            (f" (sentiment: {n['sentiment']:.1f})" if n.get('sentiment') else "")
            for n in events[:15]
        ])
        
        # Format Recent Narrative News with sentiment
        recent_txt = "\n".join([
            f"- {n['published_at']}: {n['title']}" + 
            (f" (sentiment: {n['sentiment']:.1f})" if n.get('sentiment') else "")
            for n in recent[:10]
        ])
        
        # Format Quantitative Signals
        signals_txt = ""
        if signals:
            sentiment_signals = signals.get('sentiment', {})
            signals_txt = f"""
        ### 4. Quantitative Signals (interpret contextually for scoring)
        
        **Earnings:**
        - Quarters analyzed: {signals.get('earnings', {}).get('quarters_analyzed', 0)}
        - Beat rate: {signals.get('earnings', {}).get('beat_rate', 'N/A')}
        - Avg surprise %: {signals.get('earnings', {}).get('avg_surprise_pct', 'N/A')}
        
        **Insider Transactions (90 days):**
        - Total transactions: {signals.get('insider', {}).get('transactions_90d', 0)}
        - Buy count: {signals.get('insider', {}).get('buy_count', 0)}, Sell count: {signals.get('insider', {}).get('sell_count', 0)}
        - Net value: ${signals.get('insider', {}).get('net_value', 0):,.0f}
        - CEO net: ${signals.get('insider', {}).get('ceo_net_value', 0):,.0f}
        - CFO net: ${signals.get('insider', {}).get('cfo_net_value', 0):,.0f}
        - Directors net: ${signals.get('insider', {}).get('director_net_value', 0):,.0f}
        
        **Corporate Actions (1 year):**
        - Dividends: {signals.get('corporate_actions', {}).get('dividend_count_1y', 0)}
        - Total dividend: ${signals.get('corporate_actions', {}).get('total_dividend_1y', 0):,.2f}
        
        **News Sentiment (FinBERT, 90 days):**
        - Total articles: {sentiment_signals.get('article_count_90d', 0)}
        - Scored articles: {sentiment_signals.get('scored_count', 0)}
        - Average sentiment: {sentiment_signals.get('avg_sentiment', 'N/A')} (scale: -10 to +10)
        - Positive articles (>2.0): {sentiment_signals.get('positive_count', 0)}
        - Negative articles (<-2.0): {sentiment_signals.get('negative_count', 0)}
        - Neutral articles: {sentiment_signals.get('neutral_count', 0)}
        """
        
        # NEW: Format Options Data
        options_txt = ""
        options_data = data.get('options_data', [])
        if options_data:
            avg_pcr = sum(o.get('put_call_ratio_volume', 0) for o in options_data if o.get('put_call_ratio_volume')) / len(options_data)
            options_txt = f"""
        
        **Options Flow (30 days):**
        - Average Put/Call Ratio: {avg_pcr:.2f} {"(Bullish - more calls)" if avg_pcr < 0.8 else "(Bearish - more puts)" if avg_pcr > 1.2 else "(Neutral)"}
        - Recent data points: {len(options_data)}
        """
            
        # NEW: Format Relationships
        relationships_txt = ""
        relationships = data.get('relationships', [])
        if relationships:
            relationships_txt = "\n\n**Supply Chain Relationships:**\n"
            for rel in relationships[:5]:
                relationships_txt += f"- {rel['relationship_type']}: {rel['symbol_to']} - {rel['description']}\n"
        
        # NEW: Format Influential Statements
        statements_txt = ""
        statements = data.get('influential_statements', [])
        if statements:
            statements_txt = "\n\n**Influential Statements (30 days):**\n"
            for stmt in statements:
                statements_txt += f"- {stmt['figure_name']} ({stmt['category']}, influence {stmt['influence_score']:.2f}): {stmt['sentiment']} - \"{stmt['statement_text'][:100]}...\"\n"
        
        # Format Quantitative Breakdown
        quant_txt = ""
        if quant_breakdown:
            quant_txt = f"""
        
        ### QUANTITATIVE BASE SCORE = {base_score:.3f}
        
        **Component Scores:**
        - Earnings Quality: {quant_breakdown.get('earnings_score', 0):.2f} - {quant_breakdown.get('earnings_explain', 'N/A')}
        - Insider Sentiment: {quant_breakdown.get('insider_score', 0):.2f} - {quant_breakdown.get('insider_explain', 'N/A')}
        - News Sentiment: {quant_breakdown.get('sentiment_score', 0):.2f} - {quant_breakdown.get('sentiment_explain', 'N/A')}
        - Options Flow: {quant_breakdown.get('options_score', 0):.2f} - {quant_breakdown.get('options_explain', 'N/A')}
        - Money Flow Trend: {quant_breakdown.get('money_flow_score', 0):.2f} - {quant_breakdown.get('money_flow_explain', 'N/A')}
        
        **Your Task**: Adjust this base score by -0.20 to +0.20 based on qualitative factors below.
        """
        
        return f"""
        Generate a Ticker Profile for {symbol}.
        {quant_txt}
        
        ### 1. Fundamentals (Last 3 Years)
        {earnings_txt}
        
        ### 2. Potential Material Events (Last 1 Year)
        (Look for lawsuits, M&A, regulatory, executive changes)
        {events_txt}
        
        ### 3. Recent News / Narrative (Last 90 Days)
        {recent_txt}
        {signals_txt}{options_txt}{relationships_txt}{statements_txt}
        
        Task:
        - Summarize the multi-year business and profitability trend.
        - Identify persistent "Material Events".
        - Describe the current 90-day narrative.
        - Consider supply chain context and influential opinions.
        - Define the Bull/Bear thesis.
        - **ADJUSTMENT**: Based on material events and qualitative factors, how much should we adjust the quantitative base score ({base_score:.3f})?
          - Lawsuits/regulatory issues: -0.10 to -0.20
          - Strong management/strategic moves: +0.05 to +0.15
          - Supply chain threats: -0.05 to -0.10
          - Influential bearish statements: -0.05
        - Set profile_score = base_score + your_adjustment (must be 0.0-1.0)
        - Provide score_breakdown dict explaining the adjustment.
        """
