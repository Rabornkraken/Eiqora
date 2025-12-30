"""
Historical Setup Labeler - Backfills analog_event table.

Runs Chart Agent on historical data to label setups.
These labeled events are used by the Stats Service for backtesting.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from eiqora_v2.tools.db import get_connection, close_pool
from eiqora_v2.tools.prices import get_prices, get_indicators
from eiqora_v2.agents.chart import ChartAgent
from eiqora_v2.config.universe import MEGA50_TICKERS, get_sector_etf

logger = logging.getLogger(__name__)

# Chart agent for labeling
chart_agent = ChartAgent()


async def label_historical_setups(
    symbols: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    sample_interval_days: int = 5,  # Check every N trading days
) -> dict[str, int]:
    """
    Run Chart Agent on historical dates to label setups.
    
    Args:
        symbols: Symbols to process (defaults to MEGA50)
        start_date: Start of backfill period
        end_date: End of backfill period
        sample_interval_days: Days between samples (5 = weekly)
    
    Returns:
        Dict of {symbol: setups_labeled}
    """
    symbols = symbols or MEGA50_TICKERS
    end_date = end_date or date.today()
    start_date = start_date or (end_date - timedelta(days=365 * 5))  # 5 years
    
    logger.info(f"Labeling setups for {len(symbols)} symbols from {start_date} to {end_date}")
    
    results = {}
    
    for symbol in symbols:
        setups_found = await _label_symbol(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            sample_interval_days=sample_interval_days,
        )
        results[symbol] = setups_found
        logger.info(f"Labeled {setups_found} setups for {symbol}")
    
    total = sum(results.values())
    logger.info(f"Total setups labeled: {total}")
    
    return results


async def _label_symbol(
    symbol: str,
    start_date: date,
    end_date: date,
    sample_interval_days: int,
) -> int:
    """Label setups for a single symbol over date range."""
    setups_found = 0
    current_date = start_date
    
    while current_date <= end_date:
        try:
            setup = await _check_date_for_setup(symbol, current_date)
            if setup:
                await _insert_analog_event(setup)
                setups_found += 1
        except Exception as e:
            logger.debug(f"Skip {symbol} {current_date}: {e}")
        
        # Move to next sample date
        current_date += timedelta(days=sample_interval_days)
    
    return setups_found


async def _check_date_for_setup(symbol: str, as_of_date: date) -> dict | None:
    """Check if there's a valid setup on this date."""
    asof_time = datetime.combine(as_of_date, datetime.max.time())
    
    # Get indicators
    indicators = await get_indicators(symbol, 60, asof_time)
    
    if indicators.get("error"):
        return None
    
    # Build minimal state for Chart Agent
    state = {
        "symbol": symbol,
        "asof_time": asof_time,
        "sector": "",
        "sector_etf": get_sector_etf(symbol),
    }
    
    # Run Chart Agent
    result = await chart_agent.run(state)
    chart = result.get("chart", {})
    
    setup_type = chart.get("setup_type", "NO_SETUP")
    if setup_type == "NO_SETUP":
        return None
    
    # Determine vol bucket
    rv20 = indicators.get("rv20", 0.02)
    if rv20 < 0.015:
        vol_bucket = "LOW"
    elif rv20 > 0.03:
        vol_bucket = "HIGH"
    else:
        vol_bucket = "MED"
    
    # Determine trend bucket
    state_tags = indicators.get("state_tags", [])
    if "UPTREND" in state_tags:
        trend_bucket = "UP"
    elif "DOWNTREND" in state_tags:
        trend_bucket = "DOWN"
    else:
        trend_bucket = "SIDEWAYS"
    
    # Get entry price
    entry_price = indicators.get("current_price", 0)
    
    # Get invalidation level
    invalidation = chart.get("invalidation", {})
    invalidation_price = invalidation.get("level") if invalidation else None
    
    return {
        "symbol": symbol,
        "event_date": as_of_date,
        "event_type": setup_type,
        "sector_etf": get_sector_etf(symbol),
        "vol_bucket": vol_bucket,
        "trend_bucket": trend_bucket,
        "regime": None,  # Would come from TopDown agent
        "entry_price": entry_price,
        "invalidation_price": invalidation_price,
        "setup_quality_score": chart.get("setup_quality", {}).get("score", 0),
        "volume_confirm": chart.get("setup_quality", {}).get("volume_confirm", False),
    }


async def _insert_analog_event(setup: dict) -> None:
    """Insert or update analog event in database."""
    async with get_connection() as conn:
        await conn.execute("""
            INSERT INTO analog_event (
                symbol, event_date, event_type, sector_etf,
                vol_bucket, trend_bucket, regime,
                entry_price, invalidation_price,
                setup_quality_score, volume_confirm
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (symbol, event_date, event_type) 
            DO UPDATE SET
                sector_etf = EXCLUDED.sector_etf,
                vol_bucket = EXCLUDED.vol_bucket,
                trend_bucket = EXCLUDED.trend_bucket,
                entry_price = EXCLUDED.entry_price,
                invalidation_price = EXCLUDED.invalidation_price,
                setup_quality_score = EXCLUDED.setup_quality_score,
                volume_confirm = EXCLUDED.volume_confirm
        """,
            setup["symbol"],
            setup["event_date"],
            setup["event_type"],
            setup["sector_etf"],
            setup["vol_bucket"],
            setup["trend_bucket"],
            setup["regime"],
            setup["entry_price"],
            setup["invalidation_price"],
            setup["setup_quality_score"],
            setup["volume_confirm"],
        )


async def main():
    """CLI entry point for backfill."""
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    parser = argparse.ArgumentParser(description="Backfill analog_event table")
    parser.add_argument("--symbols", help="Comma-separated symbols (default: MEGA50)")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: 5 years ago)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--interval", type=int, default=5, help="Sample interval in days")
    
    args = parser.parse_args()
    
    symbols = args.symbols.split(",") if args.symbols else None
    start_date = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else None
    
    try:
        results = await label_historical_setups(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            sample_interval_days=args.interval,
        )
        
        print(f"\nBackfill complete!")
        print(f"Total setups: {sum(results.values())}")
        
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
