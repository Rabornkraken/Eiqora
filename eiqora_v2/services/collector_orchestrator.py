"""
Collector Orchestrator - Bridge between eiqora_v2 agents and data_collection pipelines.

Triggers data collection on-demand when DB data is stale or missing.
"""

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from eiqora_v2.tools.db import get_connection

logger = logging.getLogger(__name__)

# Project root for pipeline imports
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Staleness thresholds
STALENESS_HOURS = {
    "news": 24,       # News older than 24 hours is stale
    "sec": 168,       # SEC filings older than 7 days is stale
    "earnings": 2160, # Earnings older than 90 days is stale
}

# Collection timeout
COLLECTION_TIMEOUT_SECONDS = 60


async def check_data_freshness(symbol: str) -> dict:
    """
    Check when we last collected data for this symbol.
    
    Returns:
        {
            "news_fresh": bool,
            "news_last_at": datetime | None,
            "news_count_24h": int,
            "sec_fresh": bool,
            "sec_last_at": datetime | None,
            "earnings_fresh": bool,
            "earnings_last_at": datetime | None,
        }
    """
    async with get_connection() as conn:
        now = datetime.utcnow()
        
        # Check news (document table)
        news_row = await conn.fetchrow("""
            SELECT MAX(published_at) as last_at, 
                   COUNT(*) FILTER (WHERE published_at >= NOW() - interval '24 hours') as count_24h
            FROM document
            WHERE ticker = $1
        """, symbol)
        
        news_last = news_row["last_at"] if news_row else None
        news_count = news_row["count_24h"] if news_row else 0
        news_fresh = news_count > 0
        
        # Check SEC filings (use cik since ticker not in table)
        # Need to map ticker to CIK first, or just check if table has recent data
        sec_row = await conn.fetchrow("""
            SELECT MAX(filed_at) as last_at
            FROM sec_filing
        """)
        
        sec_last = sec_row["last_at"] if sec_row else None
        if sec_last:
            # sec_last is a date, convert to datetime for comparison
            if hasattr(sec_last, 'replace') and hasattr(sec_last, 'hour'):
                # It's a datetime with tzinfo
                sec_last_dt = sec_last.replace(tzinfo=None) if sec_last.tzinfo else sec_last
            else:
                # It's a date object
                sec_last_dt = datetime.combine(sec_last, datetime.min.time())
            sec_fresh = (now - sec_last_dt) < timedelta(hours=STALENESS_HOURS["sec"])
        else:
            sec_fresh = False
        
        # Check earnings (if table exists)
        earnings_fresh = False
        earnings_last = None
        try:
            earnings_row = await conn.fetchrow("""
                SELECT MAX(earnings_date) as last_at
                FROM earnings_event
                WHERE symbol = $1
            """, symbol)
            earnings_last = earnings_row["last_at"] if earnings_row else None
            if earnings_last:
                # earnings_date is a date, convert to datetime
                earnings_last_dt = datetime.combine(earnings_last, datetime.min.time())
                earnings_fresh = (now - earnings_last_dt) < timedelta(hours=STALENESS_HOURS["earnings"])
        except Exception:
            # Table might not exist
            pass
        
        return {
            "news_fresh": news_fresh,
            "news_last_at": news_last,
            "news_count_24h": news_count,
            "sec_fresh": sec_fresh,
            "sec_last_at": sec_last,
            "earnings_fresh": earnings_fresh,
            "earnings_last_at": earnings_last,
        }


async def trigger_news_collection(symbol: str, sources: list[str] | None = None) -> tuple[bool, str | None]:
    """
    Trigger news pipelines for a symbol.
    
    Args:
        symbol: Stock ticker
        sources: List of sources to trigger ["yfinance"]. Defaults to ["yfinance"].
    
    Returns:
        Tuple of (success, error_message)
    """
    sources = sources or ["yfinance"]
    errors = []
    
    for source in sources:
        if source == "yfinance":
            success, error = await _trigger_yfinance_news(symbol)
        else:
            logger.warning(f"Unknown news source: {source}")
            continue
        
        if not success:
            errors.append(f"{source}: {error}")
    
    if errors:
        return False, "; ".join(errors)
    return True, None


async def _trigger_yfinance_news(symbol: str) -> tuple[bool, str | None]:
    """Trigger yfinance news pipeline."""
    logger.info(f"Triggering yfinance news for {symbol}")
    
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", 
                "data_collection.pipelines.yfinance_news",
                "run",
                "--symbols", symbol,
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=COLLECTION_TIMEOUT_SECONDS,
        )
        
        if result.returncode != 0:
            error = result.stderr[:500] if result.stderr else "Unknown error"
            logger.error(f"yfinance news failed: {error}")
            return False, error
        
        logger.info(f"yfinance news completed for {symbol}")
        return True, None
        
    except subprocess.TimeoutExpired:
        return False, "yfinance timed out"
    except Exception as e:
        return False, str(e)





async def trigger_sec_collection(symbol: str) -> tuple[bool, str | None]:
    """
    Trigger SEC RSS pipeline for a symbol.
    
    Returns:
        Tuple of (success, error_message)
    """
    logger.info(f"Triggering SEC collection for {symbol}")
    
    try:
        result = subprocess.run(
            [
                sys.executable, "-m",
                "data_collection.pipelines.sec_rss",
                "--tickers", symbol,
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=COLLECTION_TIMEOUT_SECONDS,
        )
        
        if result.returncode != 0:
            error = result.stderr[:500] if result.stderr else "Unknown error"
            logger.error(f"SEC collection failed: {error}")
            return False, f"SEC pipeline failed: {error}"
        
        logger.info(f"SEC collection completed for {symbol}")
        return True, None
        
    except subprocess.TimeoutExpired:
        logger.error(f"SEC collection timed out for {symbol}")
        return False, "SEC collection timed out"
    except Exception as e:
        logger.error(f"SEC collection error: {e}")
        return False, str(e)


async def trigger_earnings_collection(symbol: str) -> tuple[bool, str | None]:
    """
    Trigger earnings pipeline for a symbol.
    
    Returns:
        Tuple of (success, error_message)
    """
    logger.info(f"Triggering earnings collection for {symbol}")
    
    try:
        result = subprocess.run(
            [
                sys.executable, "-m",
                "data_collection.pipelines.earnings",
                "--symbols", symbol,
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=COLLECTION_TIMEOUT_SECONDS,
        )
        
        if result.returncode != 0:
            error = result.stderr[:500] if result.stderr else "Unknown error"
            logger.error(f"Earnings collection failed: {error}")
            return False, f"Earnings pipeline failed: {error}"
        
        logger.info(f"Earnings collection completed for {symbol}")
        return True, None
        
    except subprocess.TimeoutExpired:
        logger.error(f"Earnings collection timed out for {symbol}")
        return False, "Earnings collection timed out"
    except Exception as e:
        logger.error(f"Earnings collection error: {e}")
        return False, str(e)


async def ensure_fresh_data(
    symbol: str,
    require_news: bool = True,
    require_sec: bool = False,
    require_earnings: bool = False,
) -> tuple[dict, list[str]]:
    """
    Ensure fresh data is available, triggering collection if needed.
    
    Args:
        symbol: Stock ticker
        require_news: Require fresh news data
        require_sec: Require fresh SEC data
        require_earnings: Require fresh earnings data
    
    Returns:
        Tuple of (freshness_status, errors)
        If errors is not empty, data collection failed.
    """
    freshness = await check_data_freshness(symbol)
    errors = []
    collections_triggered = []
    
    # Check and trigger news
    if require_news and not freshness["news_fresh"]:
        collections_triggered.append("yfinance_news")
        success, error = await trigger_news_collection(symbol)
        if not success:
            errors.append(error)
    
    # Check and trigger SEC
    if require_sec and not freshness["sec_fresh"]:
        collections_triggered.append("sec_rss")
        success, error = await trigger_sec_collection(symbol)
        if not success:
            errors.append(error)
    
    # Check and trigger earnings
    if require_earnings and not freshness["earnings_fresh"]:
        collections_triggered.append("earnings")
        success, error = await trigger_earnings_collection(symbol)
        if not success:
            errors.append(error)
    
    # Re-check freshness after collection
    if collections_triggered and not errors:
        freshness = await check_data_freshness(symbol)
    
    freshness["collections_triggered"] = collections_triggered
    
    return freshness, errors
