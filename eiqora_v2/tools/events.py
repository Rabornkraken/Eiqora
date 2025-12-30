"""
Event and SEC filing tools.
Fetches SEC filings, macro indicators, and earnings data.
"""

from datetime import datetime
from typing import Any

from eiqora_v2.tools.db import get_connection


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
