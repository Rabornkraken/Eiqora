"""
Deterministic Setup Labeler - Rule-based setup detection.

No LLM needed - pure Python pattern matching on price data.
Fast and free to run on historical data.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Literal

import numpy as np

from eiqora_v2.tools.db import get_connection, close_pool
from eiqora_v2.config.universe import MEGA50_TICKERS, get_sector_etf

logger = logging.getLogger(__name__)

# Setup type taxonomy
SetupType = Literal[
    "PULLBACK_MA20",
    "PULLBACK_MA50",
    "BREAKOUT_20D",
    "BREAKDOWN_20D",
    "RANGE_LOW_BOUNCE",
    "RANGE_HIGH_REJECTION",
    "MA_CROSSOVER_BULLISH",
    "MA_CROSSOVER_BEARISH",
    "COMPRESSION_BREAKOUT",
    "GAP_AND_GO",
    "NO_SETUP",
]


async def detect_setup(
    symbol: str,
    asof_date: date,
    lookback: int = 60,
) -> tuple[SetupType, dict]:
    """
    Detect setup type using deterministic rules.
    
    Returns:
        Tuple of (setup_type, metadata dict with entry_price, vol_bucket, etc.)
    """
    async with get_connection() as conn:
        # Fetch price data
        rows = await conn.fetch("""
            SELECT date, open, high, low, close, volume
            FROM market_bar_daily
            WHERE symbol = $1
              AND date <= $2
            ORDER BY date DESC
            LIMIT $3
        """, symbol, asof_date, lookback)
        
        if len(rows) < 50:
            return "NO_SETUP", {}
        
        # Convert to arrays (most recent first)
        closes = np.array([float(r["close"]) for r in rows])
        highs = np.array([float(r["high"]) for r in rows])
        lows = np.array([float(r["low"]) for r in rows])
        volumes = np.array([r["volume"] or 0 for r in rows])
        
        # Reverse to chronological order
        closes = closes[::-1]
        highs = highs[::-1]
        lows = lows[::-1]
        volumes = volumes[::-1]
        
        current_price = closes[-1]
        
        # Calculate indicators
        ma20 = np.mean(closes[-20:])
        ma50 = np.mean(closes[-50:])
        
        # Volatility (RV20)
        log_returns = np.diff(np.log(closes[-21:]))
        rv20 = float(np.std(log_returns))
        
        # 20-day high/low
        high_20d = np.max(highs[-21:-1])  # Exclude today
        low_20d = np.min(lows[-21:-1])
        
        # Trend determination
        if current_price > ma20 > ma50:
            trend_bucket = "UP"
        elif current_price < ma20 < ma50:
            trend_bucket = "DOWN"
        else:
            trend_bucket = "SIDEWAYS"
        
        # Volatility bucket
        if rv20 < 0.015:
            vol_bucket = "LOW"
        elif rv20 > 0.03:
            vol_bucket = "HIGH"
        else:
            vol_bucket = "MED"
        
        # Volume confirmation
        avg_volume = np.mean(volumes[-20:])
        volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 1
        
        metadata = {
            "entry_price": current_price,
            "vol_bucket": vol_bucket,
            "trend_bucket": trend_bucket,
            "rv20": rv20,
            "ma20": ma20,
            "ma50": ma50,
        }
        
        # ==========================================
        # SETUP DETECTION RULES
        # ==========================================
        
        # 1. Pullback to MA50 (in uptrend)
        if trend_bucket == "UP":
            ma50_distance = abs(current_price - ma50) / ma50
            was_above = closes[-10] > ma50 * 1.03  # Was 3% above MA50 recently
            
            if ma50_distance < 0.02 and was_above:  # Within 2% of MA50
                metadata["invalidation_level"] = ma50 * 0.97
                return "PULLBACK_MA50", metadata
        
        # 2. Pullback to MA20 (in uptrend)
        if trend_bucket == "UP":
            ma20_distance = abs(current_price - ma20) / ma20
            was_above = closes[-5] > ma20 * 1.02
            
            if ma20_distance < 0.015 and was_above:  # Within 1.5% of MA20
                metadata["invalidation_level"] = ma20 * 0.98
                return "PULLBACK_MA20", metadata
        
        # 3. Breakout above 20-day high
        if current_price > high_20d:
            breakout_pct = (current_price - high_20d) / high_20d
            if breakout_pct < 0.03 and volume_ratio > 1.2:  # Fresh breakout with volume
                metadata["invalidation_level"] = high_20d * 0.98
                return "BREAKOUT_20D", metadata
        
        # 4. Breakdown below 20-day low
        if current_price < low_20d:
            breakdown_pct = (low_20d - current_price) / low_20d
            if breakdown_pct < 0.03 and volume_ratio > 1.2:
                metadata["invalidation_level"] = low_20d * 1.02
                return "BREAKDOWN_20D", metadata
        
        # 5. Range low bounce
        if trend_bucket == "SIDEWAYS":
            range_position = (current_price - low_20d) / (high_20d - low_20d) if high_20d > low_20d else 0.5
            if range_position < 0.15:  # Near bottom of range
                yesterday_close = closes[-2]
                if current_price > yesterday_close:  # Bouncing
                    metadata["invalidation_level"] = low_20d * 0.98
                    return "RANGE_LOW_BOUNCE", metadata
        
        # 6. Range high rejection
        if trend_bucket == "SIDEWAYS":
            range_position = (current_price - low_20d) / (high_20d - low_20d) if high_20d > low_20d else 0.5
            if range_position > 0.85:  # Near top of range
                yesterday_close = closes[-2]
                if current_price < yesterday_close:  # Rejecting
                    metadata["invalidation_level"] = high_20d * 1.02
                    return "RANGE_HIGH_REJECTION", metadata
        
        # 7. MA crossover bullish
        prev_ma20 = np.mean(closes[-21:-1])
        prev_ma50 = np.mean(closes[-51:-1])
        if prev_ma20 <= prev_ma50 and ma20 > ma50:
            metadata["invalidation_level"] = ma50 * 0.97
            return "MA_CROSSOVER_BULLISH", metadata
        
        # 8. MA crossover bearish
        if prev_ma20 >= prev_ma50 and ma20 < ma50:
            metadata["invalidation_level"] = ma50 * 1.03
            return "MA_CROSSOVER_BEARISH", metadata
        
        # 9. Compression breakout (low volatility expanding)
        if vol_bucket == "LOW":
            prev_rv = float(np.std(np.diff(np.log(closes[-31:-10]))))
            if rv20 > prev_rv * 1.5:  # Vol expanding
                if current_price > ma20:
                    metadata["invalidation_level"] = ma20 * 0.98
                    return "COMPRESSION_BREAKOUT", metadata
        
        # 10. Gap and go
        if len(rows) >= 2:
            yesterday_close = closes[-2]
            today_open = float(rows[0]["open"])  # rows is reversed, so [0] is most recent
            gap_pct = (today_open - yesterday_close) / yesterday_close
            if abs(gap_pct) > 0.02:  # 2% gap
                if gap_pct > 0 and current_price > today_open:  # Gap up, continuing
                    metadata["invalidation_level"] = today_open * 0.98
                    return "GAP_AND_GO", metadata
        
        return "NO_SETUP", metadata


async def backfill_analog_events(
    symbols: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    interval_days: int = 5,
) -> dict[str, int]:
    """
    Backfill analog_event table using deterministic rules.
    
    Args:
        symbols: Tickers to process (defaults to MEGA50)
        start_date: Start date (defaults to 5 years ago)
        end_date: End date (defaults to today)
        interval_days: Days between samples
    
    Returns:
        Dict of {symbol: setups_found}
    """
    symbols = symbols or MEGA50_TICKERS
    end_date = end_date or date.today()
    start_date = start_date or (end_date - timedelta(days=365 * 5))
    
    logger.info(f"Backfilling {len(symbols)} symbols from {start_date} to {end_date}")
    
    results = {}
    total_setups = 0
    
    for symbol in symbols:
        setups = 0
        current = start_date
        
        while current <= end_date:
            try:
                setup_type, metadata = await detect_setup(symbol, current)
                
                if setup_type != "NO_SETUP":
                    await _insert_analog(symbol, current, setup_type, metadata)
                    setups += 1
                    
            except Exception as e:
                logger.debug(f"Skip {symbol} {current}: {e}")
            
            current += timedelta(days=interval_days)
        
        results[symbol] = setups
        total_setups += setups
        logger.info(f"{symbol}: {setups} setups found")
    
    logger.info(f"Total: {total_setups} setups across {len(symbols)} symbols")
    return results


async def _insert_analog(
    symbol: str,
    event_date: date,
    setup_type: str,
    metadata: dict,
) -> None:
    """Insert analog event into database."""
    async with get_connection() as conn:
        await conn.execute("""
            INSERT INTO analog_event (
                symbol, event_date, event_type, sector_etf,
                vol_bucket, trend_bucket, entry_price,
                invalidation_price, setup_quality_score,
                labeled_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (symbol, event_date, event_type) DO UPDATE SET
                vol_bucket = EXCLUDED.vol_bucket,
                trend_bucket = EXCLUDED.trend_bucket,
                entry_price = EXCLUDED.entry_price,
                invalidation_price = EXCLUDED.invalidation_price
        """,
            symbol,
            event_date,
            setup_type,
            get_sector_etf(symbol),
            metadata.get("vol_bucket"),
            metadata.get("trend_bucket"),
            metadata.get("entry_price"),
            metadata.get("invalidation_level"),
            0.7,  # Default quality score for rule-based
            "deterministic_v1",
        )


async def main():
    """CLI entry point."""
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    
    parser = argparse.ArgumentParser(description="Backfill analog events (deterministic)")
    parser.add_argument("--symbols", help="Comma-separated symbols")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--interval", type=int, default=5, help="Sample interval days")
    
    args = parser.parse_args()
    
    symbols = args.symbols.split(",") if args.symbols else None
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else None
    
    try:
        results = await backfill_analog_events(
            symbols=symbols,
            start_date=start,
            end_date=end,
            interval_days=args.interval,
        )
        
        print(f"\n✓ Backfill complete!")
        print(f"Total setups: {sum(results.values())}")
        
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
