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

import json
import logging
from datetime import datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from eiqora_v2.tools.db import get_connection

logger = logging.getLogger(__name__)

# Eastern timezone for market hours
ET = ZoneInfo("America/New_York")


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
    end_time = dt_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return end_time


async def get_cached_analysis(
    symbol: str,
    trigger_type: str,
    check_date,
) -> Optional[dict]:
    """
    Retrieve cached analysis for symbol+trigger on given date.

    Returns:
        dict with decision, context, analyzed_at, expires_at or None
    """
    async with get_connection() as conn:
        row = await conn.fetchrow("""
            SELECT decision, context, analyzed_at, expires_at
            FROM trigger_analysis_cache
            WHERE symbol = $1
              AND trigger_type = $2
              AND DATE(analyzed_at AT TIME ZONE 'America/New_York') = $3
            ORDER BY analyzed_at DESC
            LIMIT 1
        """, symbol, trigger_type, check_date)

        if not row:
            return None

        context = row['context']
        if isinstance(context, str):
            try:
                context = json.loads(context)
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            'decision': row['decision'],
            'context': context,
            'analyzed_at': row['analyzed_at'],
            'expires_at': row['expires_at'],
        }


async def cache_analysis(
    symbol: str,
    trigger_type: str,
    decision: str,
    context: dict,
    analyzed_at: datetime,
    expires_at: Optional[datetime] = None
) -> None:
    """
    Cache trigger analysis decision with context.
    """
    if expires_at is None:
        expires_at = get_end_of_trading_day(analyzed_at)

    context_json = json.dumps(context, default=str)

    async with get_connection() as conn:
        await conn.execute("""
            INSERT INTO trigger_analysis_cache
            (symbol, trigger_type, decision, analyzed_at, expires_at, context)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (symbol, trigger_type, DATE(analyzed_at AT TIME ZONE 'America/New_York'))
            DO UPDATE SET
                decision = EXCLUDED.decision,
                analyzed_at = EXCLUDED.analyzed_at,
                expires_at = EXCLUDED.expires_at,
                context = EXCLUDED.context
        """, symbol, trigger_type, decision, analyzed_at, expires_at, context_json)

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


async def cleanup_expired_cache() -> int:
    """Remove expired cache entries. Returns count of deleted rows."""
    async with get_connection() as conn:
        result = await conn.execute("""
            DELETE FROM trigger_analysis_cache
            WHERE expires_at < NOW()
        """)
        deleted = int(result.split()[-1]) if result else 0

    if deleted > 0:
        logger.info(f"Cleaned up {deleted} expired cache entries")

    return deleted
