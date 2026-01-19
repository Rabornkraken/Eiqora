"""
Smart Trigger Analysis Cache.

Prevents redundant LLM analyses by caching decisions with context snapshots.
Only re-analyzes when market conditions improve significantly.

Strategy:
- Cache analysis decisions (BUY/PASS/SELL) with full context
- Detect significant changes (score jumps, volume surges, trend reversals)
- Invalidate cache only when conditions warrant re-analysis
- Expire all caches at end of trading day
"""

import logging
from datetime import datetime, time, timedelta
from typing import Any, Optional
import pytz

from data_collection.db.connection import get_connection

logger = logging.getLogger(__name__)

# Eastern timezone for market hours
ET = pytz.timezone('America/New_York')


# Invalidation thresholds for cache
INVALIDATION_RULES = {
    # Common rules (apply to all triggers)
    'common': {
        'hourly_score_improvement': 0.15,  # Score jumped +0.15 or more
        'price_movement_pct': 2.0,         # Price moved >2%
        'volume_surge': 2.0,               # Volume Z-score increased by 2+
        'trend_reversal': True,            # Trend changed direction
    },
    
    # Trigger-specific rules
    'vwap_reclaim': {
        'vwap_distance_change': 1.0,       # Crossed from below to +1% above
        'cmf_improvement': 0.20,           # CMF jumped significantly
    },
    
    'hourly_rsi_divergence': {
        'rsi_recovery': 15,                # RSI improved by 15+ points
        'support_break': True,             # Broke support/resistance
    },
    
    'hourly_money_flow_surge': {
        'cmf_spike': 0.25,                 # CMF jumped by 0.25+
        'mfi_spike': 20,                   # MFI jumped by 20+ points
    },
    
    'intraday_consolidation_break': {
        'breakout_continuation': 1.5,      # Price moved another 1.5%+
        'volume_acceleration': 1.5,        # Volume increased further
    },
    
    'opening_range_breakout': {
        'range_expansion': 1.0,            # Breakout extended by 1%+
    },
}


def get_end_of_trading_day(dt: datetime) -> datetime:
    """Get 4:00 PM ET on the given date."""
    dt_et = dt.astimezone(ET)
    end_time = datetime.combine(dt_et.date(), time(16, 0), tzinfo=ET)
    return end_time.astimezone(pytz.UTC)


def get_cached_analysis(
    symbol: str,
    trigger_type: str,
    check_date: datetime.date
) -> Optional[dict]:
    """
    Retrieve cached analysis for symbol+trigger on given date.
    
    Returns:
        dict with decision, context, analyzed_at, expires_at or None
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT decision, context, analyzed_at, expires_at
                FROM trigger_analysis_cache
                WHERE symbol = %s
                  AND trigger_type = %s
                  AND DATE(analyzed_at AT TIME ZONE 'America/New_York') = %s
                ORDER BY analyzed_at DESC
                LIMIT 1
            """, (symbol, trigger_type, check_date))
            
            row = cur.fetchone()
            if not row:
                return None
            
            return {
                'decision': row[0],
                'context': row[1],
                'analyzed_at': row[2],
                'expires_at': row[3],
            }


def cache_analysis(
    symbol: str,
    trigger_type: str,
    decision: str,
    context: dict,
    analyzed_at: datetime,
    expires_at: Optional[datetime] = None
) -> None:
    """
    Cache trigger analysis decision with context.
    
    Args:
        symbol: Stock symbol
        trigger_type: Type of trigger
        decision: Analysis decision ('BUY', 'PASS', 'SELL')
        context: Market conditions snapshot
        analyzed_at: Analysis timestamp
        expires_at: Optional expiration (defaults to end of trading day)
    """
    if expires_at is None:
        expires_at = get_end_of_trading_day(analyzed_at)
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Use INSERT ... ON CONFLICT to handle duplicates
            cur.execute("""
                INSERT INTO trigger_analysis_cache 
                (symbol, trigger_type, decision, analyzed_at, expires_at, context)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, trigger_type, DATE(analyzed_at AT TIME ZONE 'America/New_York'))
                DO UPDATE SET
                    decision = EXCLUDED.decision,
                    analyzed_at = EXCLUDED.analyzed_at,
                    expires_at = EXCLUDED.expires_at,
                    context = EXCLUDED.context
            """, (symbol, trigger_type, decision, analyzed_at, expires_at, context))
            
            conn.commit()
    
    logger.debug(f"Cached {decision} for {symbol} {trigger_type}")


def context_significantly_changed(
    cached_context: dict,
    current_context: dict,
    trigger_type: str
) -> tuple[bool, str]:
    """
    Detect if current context differs significantly from cached context.
    
    Returns:
        (should_invalidate, reason)
    """
    rules = INVALIDATION_RULES['common']
    trigger_rules = INVALIDATION_RULES.get(trigger_type, {})
    
    # Helper to safely get values with defaults
    def safe_get(ctx, key, default=0):
        return ctx.get(key, default) if ctx.get(key) is not None else default
    
    # Check common rules
    
    # 1. Hourly score improvement
    cached_score = safe_get(cached_context, 'hourly_score', 0)
    current_score = safe_get(current_context, 'hourly_score', 0)
    score_delta = current_score - cached_score
    
    if score_delta >= rules['hourly_score_improvement']:
        return True, f"hourly_score_improved_+{score_delta:.2f}"
    
    # 2. Price movement
    cached_price = safe_get(cached_context, 'price', 0)
    current_price = safe_get(current_context, 'price', 0)
    
    if cached_price > 0:
        price_change_pct = abs((current_price - cached_price) / cached_price * 100)
        if price_change_pct >= rules['price_movement_pct']:
            return True, f"price_moved_{price_change_pct:.1f}%"
    
    # 3. Volume surge
    cached_vol_z = safe_get(cached_context, 'volume_z', 0)
    current_vol_z = safe_get(current_context, 'volume_z', 0)
    vol_delta = current_vol_z - cached_vol_z
    
    if vol_delta >= rules['volume_surge']:
        return True, f"volume_surge_+{vol_delta:.1f}"
    
    # 4. Trend reversal
    if rules['trend_reversal']:
        cached_trend = cached_context.get('trend_direction', '')
        current_trend = current_context.get('trend_direction', '')
        
        # Favorable reversals
        if cached_trend in ['DOWNTREND', 'SIDEWAYS'] and current_trend == 'UPTREND':
            return True, f"trend_{cached_trend.lower()}_to_uptrend"
    
    # Check trigger-specific rules
    if trigger_type == 'vwap_reclaim' and trigger_rules:
        # VWAP distance improvement (crossing from below to above)
        cached_vwap_dist = safe_get(cached_context, 'vwap_distance_pct', 0)
        current_vwap_dist = safe_get(current_context, 'vwap_distance_pct', 0)
        
        threshold = trigger_rules.get('vwap_distance_change', 1.0)
        if cached_vwap_dist < 0 and current_vwap_dist > threshold:
            return True, f"vwap_breakout_strengthened_{current_vwap_dist:.2f}%"
        
        # CMF improvement
        cached_cmf = safe_get(cached_context, 'cmf', 0)
        current_cmf = safe_get(current_context, 'cmf', 0)
        cmf_delta = current_cmf - cached_cmf
        
        if cmf_delta >= trigger_rules.get('cmf_improvement', 0.20):
            return True, f"cmf_improved_+{cmf_delta:.2f}"
    
    elif trigger_type == 'hourly_rsi_divergence' and trigger_rules:
        # RSI recovery
        cached_rsi = safe_get(cached_context, 'hourly_rsi', 50)
        current_rsi = safe_get(current_context, 'hourly_rsi', 50)
        rsi_delta = current_rsi - cached_rsi
        
        if rsi_delta >= trigger_rules.get('rsi_recovery', 15):
            return True, f"rsi_recovered_+{rsi_delta:.0f}"
    
    elif trigger_type == 'hourly_money_flow_surge' and trigger_rules:
        # CMF spike
        cached_cmf = safe_get(cached_context, 'cmf', 0)
        current_cmf = safe_get(current_context, 'cmf', 0)
        cmf_delta = current_cmf - cached_cmf
        
        if cmf_delta >= trigger_rules.get('cmf_spike', 0.25):
            return True, f"cmf_spiked_+{cmf_delta:.2f}"
        
        # MFI spike
        cached_mfi = safe_get(cached_context, 'mfi_14', 50)
        current_mfi = safe_get(current_context, 'mfi_14', 50)
        mfi_delta = current_mfi - cached_mfi
        
        if mfi_delta >= trigger_rules.get('mfi_spike', 20):
            return True, f"mfi_spiked_+{mfi_delta:.0f}"
    
    elif trigger_type == 'intraday_consolidation_break' and trigger_rules:
        # Check if breakout continued
        cached_price_change = safe_get(cached_context, 'price_change_pct', 0)
        current_price_change = safe_get(current_context, 'price_change_pct', 0)
        breakout_continuation = abs(current_price_change) - abs(cached_price_change)
        
        if breakout_continuation >= trigger_rules.get('breakout_continuation', 1.5):
            return True, f"breakout_continued_+{breakout_continuation:.1f}%"
        
        # Volume acceleration
        vol_delta = current_vol_z - cached_vol_z
        if vol_delta >= trigger_rules.get('volume_acceleration', 1.5):
            return True, f"volume_accelerated_+{vol_delta:.1f}"
    
    # No significant change detected
    return False, "no_significant_change"


def cleanup_expired_cache() -> int:
    """Remove expired cache entries. Returns count of deleted rows."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM trigger_analysis_cache
                WHERE expires_at < NOW()
            """)
            
            deleted = cur.rowcount
            conn.commit()
    
    if deleted > 0:
        logger.info(f"Cleaned up {deleted} expired cache entries")
    
    return deleted
