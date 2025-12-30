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
        Get Ticker Profile, using cache if fresh (< 24h).
        If stale or use_cache=False, generates new profile.
        """
        if use_cache:
            profile = await self._load_profile(symbol)
            if profile:
                # Check freshness
                age = datetime.utcnow().replace(tzinfo=None) - profile.last_updated.replace(tzinfo=None)
                if age < timedelta(hours=24):
                    logger.info(f"Using cached profile for {symbol} (age: {age})")
                    return profile
                else:
                    logger.info(f"Cached profile for {symbol} is stale (age: {age})")
            else:
                logger.info(f"No cached profile found for {symbol}")

        return await self.generate_profile(symbol)

    async def generate_profile(self, symbol: str) -> TickerProfile:
        """
        Generate a fresh Ticker Profile for the symbol.
        Queries deep history (3y) for fundamentals and 1y for events.
        """
        logger.info(f"Generating Ticker Profile for {symbol}")
        
        # 1. Gather Layered Data
        data = await self._gather_layered_data(symbol)
        
        # 2. Synthesize with LLM
        profile = await self._synthesize_profile(symbol, data)
        
        # 3. Save to DB
        await self._save_profile(profile)
        
        return profile

    async def _load_profile(self, symbol: str) -> TickerProfile | None:
        """Load profile from DB."""
        try:
            async with get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT profile_data FROM ticker_profile WHERE symbol = $1", 
                    symbol
                )
                if row:
                    import json
                    data = json.loads(row["profile_data"])
                    return TickerProfile.model_validate(data)
        except Exception as e:
            logger.error(f"Error loading profile for {symbol}: {e}")
        return None

    async def _save_profile(self, profile: TickerProfile) -> None:
        """Save profile to DB."""
        try:
            import json
            data_json = profile.model_dump_json()
            
            async with get_connection() as conn:
                await conn.execute("""
                    INSERT INTO ticker_profile (symbol, profile_data, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (symbol) 
                    DO UPDATE SET profile_data = EXCLUDED.profile_data, updated_at = NOW()
                """, profile.symbol, data_json)
        except Exception as e:
            logger.error(f"Error saving profile for {profile.symbol}: {e}")
        
    async def _gather_layered_data(self, symbol: str) -> dict[str, Any]:
        """Gather data across different time horizons."""
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
                "investigation", "lawsuit", "SEC", "doj", "regulatory",
                "acquisition", "merger", "spinoff", "restructuring",
                "CEO", "CFO", "resignation", "activist", "patent"
            ]
            keywords_pattern = "|".join(major_event_keywords)
            
            event_news_rows = await conn.fetch("""
                SELECT published_at, title, text
                FROM document
                WHERE ticker = $1 
                  AND published_at >= NOW() - interval '1 year'
                  AND (title ~* $2 OR text ~* $2)
                ORDER BY published_at DESC
                LIMIT 50
            """, symbol, keywords_pattern)
            
            # C. Recent News (Last 90 Days for Narrative)
            # Use smaller limit for recent news to fit in context context
            recent_news_rows = await conn.fetch("""
                SELECT published_at, title
                FROM document
                WHERE ticker = $1
                  AND published_at >= NOW() - interval '90 days'
                ORDER BY published_at DESC
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
            
            return {
                "earnings": [dict(r) for r in earnings_rows],
                "event_news": [dict(r) for r in event_news_rows],
                "recent_news": [dict(r) for r in recent_news_rows],
                "sec_filings": [dict(r) for r in sec_rows],
            }

    async def _synthesize_profile(self, symbol: str, data: dict[str, Any]) -> TickerProfile:
        """Call LLM to synthesize data into a profile."""
        
        prompt = self._build_synthesis_prompt(symbol, data)
        
        import json
        schema_json = json.dumps(TickerProfile.model_json_schema(), indent=2)
        
        system_prompt = f"""You are an Expert Equity Analyst. 
        Your goal is to build a "Deep Context Profile" for a stock.
        
        Focus on:
        1. **The Structural Story:** What drives this business over 3-5 years?
        2. **Consistency:** Review the 3-year earnings history. Are they growing? Margins expanding?
        3. **Ongoing Sagas:** Identify "Material Events" that span months/years (lawsuits, turnarounds).
        4. **Current Narrative:** What is the market focused on RIGHT NOW (last 90 days)?
        
        Be concise, objective, and professional.
        
        CRITICAL: Return ONLY valid JSON matching this schema:
        {schema_json}
        """
        
        return await call_llm(
            prompt=prompt,
            schema=TickerProfile,
            system_prompt=system_prompt
        )

    def _build_synthesis_prompt(self, symbol: str, data: dict[str, Any]) -> str:
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
            
        # Format Major Events News
        events_txt = "\n".join([f"- {n['published_at']}: {n['title']}" for n in events[:15]])
        
        # Format Recent Narrative News
        recent_txt = "\n".join([f"- {n['published_at']}: {n['title']}" for n in recent[:10]])
        
        return f"""
        Generate a Ticker Profile for {symbol}.
        
        ### 1. Fundamentals (Last 3 Years)
        {earnings_txt}
        
        ### 2. Potential Material Events (Last 1 Year)
        (Look for lawsuits, M&A, regulatory, executive changes)
        {events_txt}
        
        ### 3. Recent News / Narrative (Last 90 Days)
        {recent_txt}
        
        Task:
        - Summarize the multi-year business and profitability trend.
        - Identify persistent "Material Events".
        - Describe the current 90-day narrative.
        - Define the Bull/Bear thesis.
        """
