"""
Quantitative Signal Aggregator.

Gathers structured quantitative signals from all data sources:
- Earnings: beats, misses, surprise magnitude, guidance
- Insider: transactions by role, dollar values
- Institutional: 13F holdings changes
- Sentiment: news sentiment scores
- Corporate Actions: dividends, splits

Outputs structured data for LLM to interpret contextually.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from eiqora_v2.tools.db import get_connection

_logger = logging.getLogger(__name__)


async def gather_quantitative_signals(
    symbol: str,
    asof_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Gather all quantitative signals for a symbol.
    
    Returns structured dict that can be passed to LLM.
    """
    if asof_date is None:
        asof_date = datetime.utcnow()
    
    async with get_connection() as conn:
        signals = {
            "symbol": symbol,
            "as_of": str(asof_date.date()),
        }
        
        # === EARNINGS ===
        signals["earnings"] = await _get_earnings_signals(conn, symbol, asof_date)
        
        # === INSIDER TRANSACTIONS ===
        signals["insider"] = await _get_insider_signals(conn, symbol, asof_date)
        
        # === INSTITUTIONAL HOLDINGS ===
        signals["institutional"] = await _get_institutional_signals(conn, symbol, asof_date)
        
        # === NEWS SENTIMENT ===
        signals["sentiment"] = await _get_sentiment_signals(conn, symbol, asof_date)
        
        # === CORPORATE ACTIONS ===
        signals["corporate_actions"] = await _get_corporate_signals(conn, symbol, asof_date)
        
        return signals


async def _get_earnings_signals(conn, symbol: str, asof: datetime) -> dict:
    """Aggregate earnings signals."""
    rows = await conn.fetch("""
        SELECT earnings_date, eps_actual, eps_est, 
               revenue_actual, revenue_est, revenue_growth_yoy, guidance
        FROM earnings_event
        WHERE symbol = $1 AND earnings_date <= $2
        ORDER BY earnings_date DESC
        LIMIT 8
    """, symbol, asof)
    
    if not rows:
        return {"available": False}
    
    beats = 0
    total = 0
    surprises = []
    latest_guidance = None
    
    for r in rows:
        eps_actual = r['eps_actual']
        eps_est = r['eps_est']
        
        if eps_actual is not None and eps_est is not None:
            total += 1
            if float(eps_actual) > float(eps_est):
                beats += 1
            
            if float(eps_est) != 0:
                surprise_pct = (float(eps_actual) - float(eps_est)) / abs(float(eps_est)) * 100
                surprises.append(surprise_pct)
        
        if latest_guidance is None and r['guidance']:
            latest_guidance = r['guidance']
    
    return {
        "available": True,
        "quarters_analyzed": total,
        "beats": beats,
        "misses": total - beats,
        "beat_rate": beats / total if total > 0 else None,
        "avg_surprise_pct": sum(surprises) / len(surprises) if surprises else None,
        "latest_guidance": latest_guidance,
        "revenue_growth_yoy": float(rows[0]['revenue_growth_yoy']) if rows[0]['revenue_growth_yoy'] else None,
    }


async def _get_insider_signals(conn, symbol: str, asof: datetime) -> dict:
    """Aggregate insider transaction signals with role-based breakdown."""
    start_date = asof - timedelta(days=90)
    
    rows = await conn.fetch("""
        SELECT i.owner_name, i.transaction_date, i.code, i.shares, i.price
        FROM insider_transaction i
        JOIN security s ON i.cik = s.cik
        WHERE s.ticker = $1 
          AND i.transaction_date BETWEEN $2 AND $3
        ORDER BY i.transaction_date DESC
    """, symbol, start_date, asof)
    
    if not rows:
        return {"available": False, "transactions_90d": 0}
    
    # Categorize by role (based on common patterns in names)
    ceo_value = 0.0
    cfo_value = 0.0
    director_value = 0.0
    other_value = 0.0
    
    total_buy_value = 0.0
    total_sell_value = 0.0
    buy_count = 0
    sell_count = 0
    
    for r in rows:
        shares = float(r['shares'] or 0)
        price = float(r['price'] or 0)
        value = shares * price
        
        name_lower = (r['owner_name'] or '').lower()
        code = r['code'] or ''
        
        is_buy = code in ('P', 'A')  # Purchase, Award
        is_sell = code in ('S', 'F')  # Sale, Tax withholding
        
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
        
        # Approximate role detection
        if 'ceo' in name_lower or 'chief executive' in name_lower:
            ceo_value += net_value
        elif 'cfo' in name_lower or 'chief financial' in name_lower:
            cfo_value += net_value
        elif 'director' in name_lower:
            director_value += net_value
        else:
            other_value += net_value
    
    return {
        "available": True,
        "transactions_90d": len(rows),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_buy_value": total_buy_value,
        "total_sell_value": total_sell_value,
        "net_value": total_buy_value - total_sell_value,
        "ceo_net_value": ceo_value,
        "cfo_net_value": cfo_value,
        "director_net_value": director_value,
        "other_net_value": other_value,
    }


async def _get_institutional_signals(conn, symbol: str, asof: datetime) -> dict:
    """Aggregate institutional holding signals from 13F filings."""
    # Get CIK for symbol
    cik_row = await conn.fetchrow(
        "SELECT cik FROM security WHERE ticker = $1 LIMIT 1", symbol
    )
    if not cik_row:
        return {"available": False}
    
    # 13F uses issuer_cusip - we need to find it via the cik
    # For now, simplified: just count how many 13F holders mention this CIK
    holdings = await conn.fetch("""
        SELECT manager_cik, SUM(shares) as total_shares, SUM(value) as total_value
        FROM sec_13f_holding
        WHERE issuer_cusip LIKE $1 || '%'
        GROUP BY manager_cik
        ORDER BY total_value DESC
        LIMIT 50
    """, cik_row['cik'][:6] if cik_row['cik'] else '')
    
    if not holdings:
        # Fallback: return aggregate stats
        return {"available": False, "note": "No 13F data found for this symbol"}
    
    total_shares = sum(float(r['total_shares'] or 0) for r in holdings)
    total_value = sum(float(r['total_value'] or 0) for r in holdings)
    holder_count = len(holdings)
    
    return {
        "available": True,
        "holder_count": holder_count,
        "total_shares_held": total_shares,
        "total_value_held": total_value,
    }


async def _get_sentiment_signals(conn, symbol: str, asof: datetime) -> dict:
    """Aggregate news sentiment signals."""
    start_date = asof - timedelta(days=90)
    
    # Note: document table doesn't have sentiment_score yet
    # Just count articles and return headlines for LLM to interpret
    rows = await conn.fetch("""
        SELECT published_at, title
        FROM document
        WHERE ticker = $1 
          AND published_at BETWEEN $2 AND $3
        ORDER BY published_at DESC
    """, symbol, start_date, asof)
    
    if not rows:
        return {"available": False, "article_count_90d": 0}
    
    return {
        "available": True,
        "article_count_90d": len(rows),
        "recent_headlines": [r['title'] for r in rows[:10]],
        "note": "Sentiment scoring not yet implemented - LLM should interpret headlines"
    }


async def _get_corporate_signals(conn, symbol: str, asof: datetime) -> dict:
    """Aggregate corporate action signals."""
    start_date = asof - timedelta(days=365)
    
    rows = await conn.fetch("""
        SELECT action_type, ex_date, cash_amount, ratio
        FROM corporate_action
        WHERE ticker = $1 
          AND ex_date BETWEEN $2 AND $3
        ORDER BY ex_date DESC
    """, symbol, start_date, asof)
    
    if not rows:
        return {"available": False}
    
    dividends = [r for r in rows if r['action_type'] == 'dividend']
    splits = [r for r in rows if r['action_type'] == 'split']
    
    total_dividend = sum(float(r['cash_amount'] or 0) for r in dividends)
    
    return {
        "available": True,
        "dividend_count_1y": len(dividends),
        "total_dividend_1y": total_dividend,
        "split_count_1y": len(splits),
        "latest_dividend": float(dividends[0]['cash_amount']) if dividends and dividends[0]['cash_amount'] else None,
    }


async def main():
    """Test signal gathering."""
    logging.basicConfig(level=logging.INFO)
    
    import json
    
    for symbol in ['AAPL', 'NVDA']:
        signals = await gather_quantitative_signals(symbol)
        print(f"\n{'='*60}")
        print(f"SIGNALS: {symbol}")
        print(f"{'='*60}")
        print(json.dumps(signals, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
