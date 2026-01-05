"""
Economic data tool for agents.
Provides access to macro indicators, release calendars, and economic context.
"""

from datetime import date, timedelta
from typing import Any

from eiqora_v2.tools.db import get_connection


async def get_upcoming_releases(days_ahead: int = 14) -> list[dict[str, Any]]:
    """
    Get upcoming economic release dates.
    
    Returns:
        List of upcoming releases with date, indicator, and impact level.
    """
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT indicator, indicator_date, impact
            FROM economic_indicator
            WHERE is_release_day = TRUE
              AND indicator_date >= CURRENT_DATE
              AND indicator_date <= CURRENT_DATE + $1 * interval '1 day'
            ORDER BY indicator_date
        """, days_ahead)
        
        return [
            {
                'date': str(r['indicator_date']),
                'indicator': r['indicator'],
                'impact': r['impact'],
            }
            for r in rows
        ]


async def get_latest_indicators() -> dict[str, Any]:
    """
    Get the latest values for key economic indicators.
    
    Returns:
        Dict with indicator names as keys and their latest values.
    """
    key_indicators = [
        'DGS2', 'DGS10', 'T10Y2Y',  # Yields
        'FEDFUNDS', 'SOFR',         # Rates
        'CPIAUCSL', 'UNRATE',       # Inflation & Employment
        'BAA10Y',                    # Credit spread
    ]
    
    async with get_connection() as conn:
        result = {}
        for indicator in key_indicators:
            row = await conn.fetchrow("""
                SELECT actual, indicator_date
                FROM economic_indicator
                WHERE indicator = $1 AND actual IS NOT NULL
                ORDER BY indicator_date DESC
                LIMIT 1
            """, indicator)
            
            if row:
                result[indicator] = {
                    'value': float(row['actual']),
                    'date': str(row['indicator_date']),
                }
        
        return result


async def get_indicator_history(indicator: str, days: int = 90) -> list[dict]:
    """
    Get historical values for a specific indicator.
    
    Args:
        indicator: Indicator code (e.g., 'DGS10', 'CPIAUCSL')
        days: Number of days of history
        
    Returns:
        List of {date, value} dicts
    """
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT indicator_date, actual
            FROM economic_indicator
            WHERE indicator = $1 
              AND actual IS NOT NULL
              AND indicator_date >= CURRENT_DATE - $2 * interval '1 day'
            ORDER BY indicator_date DESC
        """, indicator, days)
        
        return [
            {'date': str(r['indicator_date']), 'value': float(r['actual'])}
            for r in rows
        ]


async def get_economic_context() -> dict[str, Any]:
    """
    Get a comprehensive economic context for LLM agents.
    Combines latest indicators, upcoming releases, and key metrics.
    
    Returns:
        Dict with 'latest_values', 'upcoming_releases', and 'summary'
    """
    latest = await get_latest_indicators()
    upcoming = await get_upcoming_releases(days_ahead=14)
    
    # Calculate key metrics
    yield_curve = None
    if 'T10Y2Y' in latest:
        yield_curve = latest['T10Y2Y']['value']
    
    fed_rate = None
    if 'FEDFUNDS' in latest:
        fed_rate = latest['FEDFUNDS']['value']
    
    # Build summary
    summary_parts = []
    
    if yield_curve is not None:
        if yield_curve < 0:
            summary_parts.append(f"Yield curve INVERTED ({yield_curve:.2f}%)")
        else:
            summary_parts.append(f"Yield curve normal ({yield_curve:.2f}%)")
    
    if fed_rate is not None:
        summary_parts.append(f"Fed Funds at {fed_rate:.2f}%")
    
    if 'DGS10' in latest:
        summary_parts.append(f"10Y yield at {latest['DGS10']['value']:.2f}%")
    
    high_impact_upcoming = [e for e in upcoming if e['impact'] == 'high']
    if high_impact_upcoming:
        next_event = high_impact_upcoming[0]
        summary_parts.append(f"Next high-impact: {next_event['indicator']} on {next_event['date']}")
    
    return {
        'latest_values': latest,
        'upcoming_releases': upcoming[:10],  # Next 10 releases
        'summary': ' | '.join(summary_parts),
    }
