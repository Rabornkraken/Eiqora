"""
Event-related tools for accessing economic calendar and SEC filings.
"""

from datetime import datetime, timedelta
from typing import Any
import logging

from eiqora_v2.tools.db import get_connection

logger = logging.getLogger(__name__)

# Critical macro events that warrant trading blackout
BLACKOUT_EVENTS = [
    'FOMC',
    'Fed Interest Rate Decision',
    'Non-Farm Payrolls',
    'NFP',
    'CPI',
    'Core CPI',
    'PCE Price Index',
]


async def check_macro_blackout(asof_time: datetime, hours_ahead: int = 6) -> tuple[bool, str | None]:
    """
    Check if there's a high-impact macro event in the next N hours.
    
    Returns:
        (is_blackout, event_name)
    """
    end_time = asof_time + timedelta(hours=hours_ahead)
    
    try:
        async with get_connection() as conn:
            # Check if table has any data
            count = await conn.fetchval("SELECT COUNT(*) FROM economic_event")
            if count == 0:
                # Table exists but empty - not a blackout
                return False, None
            
            events = await conn.fetch("""
                SELECT event_name, event_date, event_time, impact
                FROM economic_event
                WHERE event_date = $1::date
                  AND impact = 'HIGH'
                ORDER BY event_date, event_time
            """, asof_time.date())
            
            for event in events:
                event_name = event['event_name']
                # Check if this is a blackout event
                for blackout_trigger in BLACKOUT_EVENTS:
                    if blackout_trigger.lower() in event_name.lower():
                        logger.warning(
                            f"MACRO BLACKOUT: {event_name} today (impact={event['impact']})"
                        )
                        return True, event_name
            
            return False, None
            
    except Exception as e:
        # Table doesn't exist or error - log at debug level and continue
        logger.debug(f"Economic event check skipped: {e}")
        return False, None


async def get_sec_filings(
    symbol: str,
    window_days: int,
    asof_time: datetime,
    form_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch SEC filings for a symbol.
    
    Args:
        symbol: Stock ticker symbol
        window_days: Days to look back
        asof_time: Point-in-time reference
        form_types: Optional filter (e.g., ["8-K", "10-K", "4"])
    
    Returns:
        List of filing dicts with accession, form_type, filed_at, etc.
    """
    async with get_connection() as conn:
        # First get CIK for symbol
        cik_row = await conn.fetchrow("""
            SELECT cik FROM security WHERE ticker = $1 AND is_primary = true
        """, symbol)
        
        if not cik_row:
            # Try direct lookup in sec_filing if security table doesn't have it
            return []
        
        cik = cik_row["cik"]
        
        if form_types:
            rows = await conn.fetch("""
                SELECT accession, cik, form_type, filed_at, report_period,
                       is_amendment, primary_doc_url
                FROM sec_filing
                WHERE cik = $1
                  AND filed_at <= $2::date
                  AND filed_at >= $2::date - interval '1 day' * $3
                  AND form_type = ANY($4)
                ORDER BY filed_at DESC
            """, cik, asof_time, window_days, form_types)
        else:
            rows = await conn.fetch("""
                SELECT accession, cik, form_type, filed_at, report_period,
                       is_amendment, primary_doc_url
                FROM sec_filing
                WHERE cik = $1
                  AND filed_at <= $2::date
                  AND filed_at >= $2::date - interval '1 day' * $3
                ORDER BY filed_at DESC
            """, cik, asof_time, window_days)
        
        return [dict(r) for r in rows]


async def get_macro_indicators(
    series_ids: list[str],
    asof_time: datetime,
) -> dict[str, dict[str, Any]]:
    """
    Fetch FRED macro indicators.
    
    Args:
        series_ids: List of FRED series IDs (e.g., ["DGS10", "FEDFUNDS"])
        asof_time: Point-in-time reference
    
    Returns:
        Dict mapping series_id to {value, date}
    """
    async with get_connection() as conn:
        # Try fred_observation table first, then fall back to other possible names
        try:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (series_id) series_id, date, value
                FROM fred_observation
                WHERE series_id = ANY($1)
                  AND date <= $2::date
                ORDER BY series_id, date DESC
            """, series_ids, asof_time)
        except Exception:
            # If table doesn't exist, return empty
            return {}
        
        result = {}
        for r in rows:
            result[r["series_id"]] = {
                "value": float(r["value"]) if r["value"] else None,
                "date": r["date"],
            }
        
        return result


async def get_corporate_actions(
    symbol: str,
    window_days: int,
    asof_time: datetime,
) -> list[dict[str, Any]]:
    """
    Fetch corporate actions (splits, dividends) for a symbol.
    
    Args:
        symbol: Stock ticker symbol
        window_days: Days to look back
        asof_time: Point-in-time reference
    
    Returns:
        List of corporate action dicts
    """
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT action_id, ticker, action_type, ex_date, pay_date,
                   ratio, cash_amount, currency
            FROM corporate_action
            WHERE ticker = $1
              AND ex_date <= $2::date
              AND ex_date >= $2::date - interval '1 day' * $3
            ORDER BY ex_date DESC
        """, symbol, asof_time, window_days)
        
        return [dict(r) for r in rows]


async def get_insider_transactions(
    symbol: str,
    window_days: int,
    asof_time: datetime,
) -> list[dict[str, Any]]:
    """
    Fetch insider transactions (Form 4) for a symbol.
    
    Note: Requires insider_transaction table to be populated by SEC Form 4 parser.
    """
    async with get_connection() as conn:
        try:
            rows = await conn.fetch("""
                SELECT *
                FROM insider_transaction
                WHERE ticker = $1
                  AND transaction_date <= $2::date
                  AND transaction_date >= $2::date - interval '1 day' * $3
                ORDER BY transaction_date DESC
            """, symbol, asof_time, window_days)
            return [dict(r) for r in rows]
        except Exception:
            # Table may not exist yet
            return []


async def get_economic_calendar(
    asof_time: datetime,
    window_days_back: int = 7,
    window_days_forward: int = 7,
) -> dict[str, Any]:
    """
    Fetch upcoming and recent economic events from economic_event.
    
    Used by TopDownAgent to understand macro calendar context:
    - FOMC meeting dates (Fed policy)
    - CPI releases (inflation)
    - NFP releases (employment)
    - GDP releases (growth)
    
    Args:
        asof_time: Point-in-time reference
        window_days_back: Days to look back for recent events
        window_days_forward: Days to look forward for upcoming events
    
    Returns:
        {
            "upcoming_events": [...],
            "recent_events": [...],
            "next_fomc": date or None,
            "next_cpi": date or None,
            "days_to_fomc": int or None,
        }
    """
    async with get_connection() as conn:
        result = {
            "upcoming_events": [],
            "recent_events": [],
            "next_fomc": None,
            "next_cpi": None,
            "next_nfp": None,
            "days_to_fomc": None,
        }
        
        try:
            # Get upcoming events
            upcoming = await conn.fetch("""
                SELECT event_name, event_date, event_time, actual, forecast, previous, impact, source
                FROM economic_event
                WHERE event_date > $1::date
                  AND event_date <= $1::date + interval '1 day' * $2
                ORDER BY event_date ASC, event_time ASC NULLS LAST
            """, asof_time, window_days_forward)
            
            result["upcoming_events"] = [
                {
                    "indicator_name": r["event_name"],
                    "event_date": r["event_date"],
                    "event_time": r["event_time"],
                    "impact": r["impact"],
                    "value": r["actual"],
                    "forecast": r["forecast"],
                    "previous_value": r["previous"],
                    "source": r["source"],
                }
                for r in upcoming
            ]
            
            # Get recent events (for context on what just happened)
            recent = await conn.fetch("""
                SELECT event_name, event_date, event_time, actual, forecast, previous, impact, source
                FROM economic_event
                WHERE event_date <= $1::date
                  AND event_date >= $1::date - interval '1 day' * $2
                ORDER BY event_date DESC, event_time DESC NULLS LAST
            """, asof_time, window_days_back)
            
            result["recent_events"] = [
                {
                    "indicator_name": r["event_name"],
                    "event_date": r["event_date"],
                    "event_time": r["event_time"],
                    "impact": r["impact"],
                    "value": r["actual"],
                    "forecast": r["forecast"],
                    "previous_value": r["previous"],
                    "source": r["source"],
                }
                for r in recent
            ]
            
            # Find next FOMC
            fomc = await conn.fetchrow("""
                SELECT event_date FROM economic_event
                WHERE event_name ILIKE '%FOMC%'
                  AND event_date > $1::date
                ORDER BY event_date ASC
                LIMIT 1
            """, asof_time)
            if fomc:
                result["next_fomc"] = fomc["event_date"]
                asof_date = asof_time.date() if hasattr(asof_time, "date") else asof_time
                result["days_to_fomc"] = (fomc["event_date"] - asof_date).days
            
            # Find next CPI
            cpi = await conn.fetchrow("""
                SELECT event_date FROM economic_event
                WHERE event_name ILIKE '%CPI%'
                  AND event_date > $1::date
                ORDER BY event_date ASC
                LIMIT 1
            """, asof_time)
            if cpi:
                result["next_cpi"] = cpi["event_date"]
            
            # Find next NFP
            nfp = await conn.fetchrow("""
                SELECT event_date FROM economic_event
                WHERE event_name ILIKE '%NFP%' OR event_name ILIKE '%payroll%'
                  AND event_date > $1::date
                ORDER BY event_date ASC
                LIMIT 1
            """, asof_time)
            if nfp:
                result["next_nfp"] = nfp["event_date"]
            
        except Exception as e:
            # Table may not exist or have different schema
            result["error"] = str(e)
        
        return result
