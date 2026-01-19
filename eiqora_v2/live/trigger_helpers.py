"""
Trigger helpers for integrating advanced data sources.
Provides functions to check options sentiment, supply chain signals, and influential statements.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from eiqora_v2.tools.db import get_connection

logger = logging.getLogger(__name__)


async def get_options_sentiment(symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
    """
    Get latest options sentiment from options_daily_summary.
    
    Returns:
        {
            'available': bool,
            'pcr_volume': float | None,
            'pcr_oi': float | None,
            'sentiment': 'BULLISH' | 'BEARISH' | 'NEUTRAL' | None,
            'atm_iv': float | None,
        }
    """
    try:
        if as_of is None:
            as_of = datetime.now()
        
        async with get_connection() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    put_call_ratio_volume,
                    put_call_ratio_oi,
                    atm_iv,
                    date
                FROM options_daily_summary
                WHERE symbol = $1 AND date <= $2
                ORDER BY date DESC LIMIT 1
            """, symbol, as_of.date())
            
            if not row:
                return {'available': False}
            
            pcr_vol = float(row['put_call_ratio_volume']) if row['put_call_ratio_volume'] else None
            
            # Interpret sentiment
            sentiment = "NEUTRAL"
            if pcr_vol:
                if pcr_vol > 1.2:  # Very bearish
                    sentiment = "BEARISH"
                elif pcr_vol < 0.7:  # Very bullish
                    sentiment = "BULLISH"
            
            return {
                'available': True,
                'pcr_volume': pcr_vol,
                'pcr_oi': float(row['put_call_ratio_oi']) if row['put_call_ratio_oi'] else None,
                'sentiment': sentiment,
                'atm_iv': float(row['atm_iv']) if row['atm_iv'] else None,
                'data_date': row['date'],
            }
    
    except Exception as e:
        logger.warning(f"Options sentiment fetch failed for {symbol}: {e}")
        return {'available': False}


async def check_supply_chain_catalysts(
    symbol: str,
    check_time: datetime,
    lookback_hours: int = 48
) -> list[dict[str, Any]]:
    """
    Check if any suppliers/customers have recent earnings or 8-K filings.
    
    Returns list of catalyst events from related stocks.
    """
    try:
        async with get_connection() as conn:
            # Get relationships
            relationships = await conn.fetch("""
                SELECT symbol_to, relationship_type, description, strength
                FROM stock_relationships
                WHERE symbol_from = $1
                  AND relationship_type IN ('SUPPLIER', 'CUSTOMER')
                ORDER BY strength DESC
                LIMIT 5
            """, symbol)
            
            if not relationships:
                return []
            
            catalysts = []
            start_time = check_time - timedelta(hours=lookback_hours)
            
            for rel in relationships:
                related_symbol = rel['symbol_to']
                rel_type = rel['relationship_type']
                
                # Check for earnings
                earnings_row = await conn.fetchrow("""
                    SELECT earnings_date, fiscal_quarter
                    FROM earnings_event
                    WHERE symbol = $1
                      AND earnings_date BETWEEN $2 AND $3
                    ORDER BY earnings_date DESC LIMIT 1
                """, related_symbol, start_time, check_time)
                
                if earnings_row:
                    catalysts.append({
                        'related_symbol': related_symbol,
                        'relationship': rel_type,
                        'catalyst_type': 'earnings',
                        'event_date': earnings_row['earnings_date'],
                        'description': rel['description'],
                    })
                
                # Check for 8-K filings
                sec_row = await conn.fetchrow("""
                    SELECT s.filed_at, s.form_type
                    FROM sec_filing s
                    JOIN security sec ON s.cik = sec.cik
                    WHERE sec.ticker = $1
                      AND s.form_type = '8-K'
                      AND s.filed_at BETWEEN $2 AND $3
                    ORDER BY s.filed_at DESC LIMIT 1
                """, related_symbol, start_time, check_time)
                
                if sec_row:
                    catalysts.append({
                        'related_symbol': related_symbol,
                        'relationship': rel_type,
                        'catalyst_type': '8k',
                        'event_date': sec_row['filed_at'],
                        'description': rel['description'],
                    })
            
            return catalysts
    
    except Exception as e:
        logger.warning(f"Supply chain catalyst check failed for {symbol}: {e}")
        return []


async def check_influential_suppressors(symbol: str, check_time: datetime) -> list[dict[str:  Any]]:
    """
    Check if any high-influence figures made bearish statements recently.
    
    Returns list of suppressor signals (statements that should pause triggers).
    """
    try:
        async with get_connection() as conn:
            rows = await conn.fetch("""
                SELECT 
                    f.name,
                    f.category,
                    f.influence_score,
                    s.statement_text,
                    s.sentiment,
                    s.statement_date
                FROM influential_statements s
                JOIN influential_figures f ON s.figure_id = f.figure_id
                WHERE s.statement_date >= $1 - INTERVAL '24 hours'
                  AND f.influence_score >= 0.85
                  AND s.sentiment IN ('BEARISH', 'NEGATIVE')
                ORDER BY f.influence_score DESC
                LIMIT 3
            """, check_time)
            
            return [
                {
                    'figure': r['name'],
                    'influence': float(r['influence_score']),
                    'sentiment': r['sentiment'],
                    'statement': r['statement_text'],
                    'date': r['statement_date'],
                }
                for r in rows
            ]
    
    except Exception as e:
        logger.debug(f"Influential suppressor check failed: {e}")
        return []


async def get_intraday_relative_strength(
    symbol: str,
    check_time: datetime,
) -> dict[str, Any]:
    """
    Get TODAY's relative strength vs SPY and sector.
    More responsive than 20-day trailing metrics.
   
    Returns:
        {
            'symbol_pct': float,  # Today's % change
            'spy_pct': float,
            'sector_pct': float,
            'vs_spy': float,  # symbol_pct - spy_pct
            'vs_sector': float,
        }
    """
    from eiqora_v2.config.universe import get_sector_etf
    from eiqora_v2.tools.prices import get_prices
    
    try:
        today_start = check_time.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Get today's data
        async def get_today_change(sym: str) -> float | None:
            bars = await get_prices(sym, 2, check_time)
            if len(bars) < 2:
                return None
            prev_close = float(bars[-2].get('close', 0))
            current = float(bars[-1].get('close', 0))
            if prev_close <= 0:
                return None
            return (current - prev_close) / prev_close
        
        symbol_pct = await get_today_change(symbol)
        spy_pct = await get_today_change('SPY')
        
        sector_etf = get_sector_etf(symbol)
        sector_pct = await get_today_change(sector_etf)
        
        if symbol_pct is None:
            return {'available': False}
        
        return {
            'available': True,
            'symbol_pct': symbol_pct,
            'spy_pct': spy_pct,
            'sector_pct': sector_pct,
            'vs_spy': symbol_pct - spy_pct if spy_pct else None,
            'vs_sector': symbol_pct - sector_pct if sector_pct else None,
        }
    
    except Exception as e:
        logger.debug(f"Intraday relative strength failed for {symbol}: {e}")
        return {'available': False}
