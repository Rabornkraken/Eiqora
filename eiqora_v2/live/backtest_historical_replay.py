"""
Historical backtest that replays the exact live pipeline on past data.

Uses the complete production flow:
1. Scan historical triggers
2. Load existing profiles (current knowledge)
3. Run complete orchestrator with all 8 agents
4. Track actual GO/NO_GO decisions
5. Measure outcomes

This is NOT a simulation - it runs the real production code on historical data.
"""

import asyncio
import argparse
import logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from pathlib import Path

from eiqora_v2.live.pipeline import LiveTradingPipeline
from eiqora_v2.live.trigger_monitor import TriggerMonitor
from eiqora_v2.tools.db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

EASTERN_TZ = ZoneInfo("America/New_York")

# Realistic data collection delays (based on actual scheduler.py)
# These prevent look-ahead bias by simulating real collection lag
REALISTIC_DATA_DELAYS = {
    'sec_filing': timedelta(minutes=20),    # Every 15min + 5min processing
    'earnings': timedelta(hours=14),         # 4pm close → 6am collection
    'news': timedelta(hours=1, minutes=30), # Hourly at :20 + processing
    'sector_data': timedelta(hours=2, minutes=30),  # Daily 6:30pm (2.5h after close)
    'hourly_data': timedelta(minutes=10),   # :05 past hour + processing
}


async def build_historical_watchlist(pipeline, scan_date: datetime):
    """Build watchlist for a historical date using production candidate selector."""
    
    logger.info(f"\nBuilding watchlist for {scan_date.date()}...")
    
    # Check if watchlist already exists
    if await pipeline._watchlist_exists(scan_date.date()):
        logger.info(f"  ✓ Watchlist already exists for {scan_date.date()}")
        
        # Load existing watchlist
        async with get_connection() as conn:
            rows = await conn.fetch("""
                SELECT symbol, technical_score, profile_score
                FROM daily_watchlist
                WHERE scan_date = $1
                ORDER BY technical_score DESC
            """, scan_date.date())
            
            watchlist_symbols = [row['symbol'] for row in rows]
            logger.info(f"  Loaded {len(watchlist_symbols)} symbols from existing watchlist")
            return watchlist_symbols
    
    # Build new watchlist
    watchlist = await pipeline.build_daily_watchlist(scan_date)
    watchlist_symbols = [c['symbol'] for c in watchlist]
    
    logger.info(f"  ✓ Built watchlist: {len(watchlist_symbols)} symbols")
    
    return watchlist_symbols


async def get_historical_triggers(pipeline, start_date: str, end_date: str, limit: int = None):
    """
    Scan historical data for triggers using production flow:
    1. Build daily watchlist
    2. Scan ONLY watchlist symbols for triggers
    """
    
    start = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=EASTERN_TZ)
    end = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=EASTERN_TZ)
    
    all_triggers = []
    current = start
    
    while current <= end:
        # Skip weekends
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing {current.date()}")
        logger.info(f"{'='*60}")
        
        try:
            # Step 1: Build daily watchlist (EXACT production flow)
            watchlist_symbols = await build_historical_watchlist(pipeline, current)
            
            if not watchlist_symbols:
                logger.info("  No symbols on watchlist, skipping day")
                current += timedelta(days=1)
                continue
            
            # Step 2: Scan for triggers on watchlist symbols only
            # Hourly during market hours (10:30 AM - 3:30 PM ET)
            scan_times = [
                current.replace(hour=10, minute=30),  # Post-opening
                current.replace(hour=11, minute=30),  # Mid-morning
                current.replace(hour=12, minute=30),  # Lunch hour
                current.replace(hour=13, minute=30),  # Early afternoon
                current.replace(hour=14, minute=30),  # Mid-afternoon
                current.replace(hour=15, minute=30),  # Near close
            ]
            
            day_triggers = []
            for scan_time in scan_times:
                logger.info(f"\n  Scanning at {scan_time.strftime('%H:%M')}...")
                
                # Use production trigger monitor
                triggers = await pipeline.trigger_monitor.scan_watchlist(scan_time)
                
                # Filter to only watchlist symbols (double-check)
                triggers = [t for t in triggers if t.symbol in watchlist_symbols]
                
                if triggers:
                    logger.info(f"    Found {len(triggers)} triggers")
                    day_triggers.extend(triggers)
            
            if day_triggers:
                logger.info(f"\n  Day total: {len(day_triggers)} triggers")
                all_triggers.extend(day_triggers)
                
                if limit and len(all_triggers) >= limit:
                    logger.info(f"\n✓ Reached limit of {limit} triggers")
                    break
            else:
                logger.info(f"  No triggers found for {current.date()}")
        
        except Exception as e:
            logger.error(f"  Error processing {current.date()}: {e}")
        
        current += timedelta(days=1)
    
    if limit:
        all_triggers = all_triggers[:limit]
    
    return all_triggers


async def track_trade_outcome(trigger, entry_price, stop_loss, take_profit, entry_time):
    """Track hypothetical trade outcome with detailed exit logging."""
    
    symbol = trigger.symbol
    
    logger.info(f"\n📊 Tracking {symbol} trade:")
    logger.info(f"   Entry: ${entry_price:.2f} at {entry_time}")
    logger.info(f"   SL: ${stop_loss:.2f} ({((stop_loss - entry_price) / entry_price * 100):+.2f}%)")
    logger.info(f"   TP: ${take_profit:.2f} ({((take_profit - entry_price) / entry_price * 100):+.2f}%)")
    
    # Look forward up to 30 days for outcome
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT date as datetime, close, high, low
            FROM market_bar_daily
            WHERE symbol = $1
            AND date > $2::date
            AND date <= ($2::date + INTERVAL '30 days')
            ORDER BY date
        """, symbol, entry_time)
        
        if not rows:
            logger.warning(f"   ⚠️  No price data available after entry")
            return {
                'outcome': 'NO_DATA',
                'pnl': 0,
                'exit_price': None,
                'bars_held': 0,
            }
        
        # Check each bar for TP/SL hit
        for i, row in enumerate(rows):
            bar_time, close, high, low = row
            
            # Check stop loss
            if low <= stop_loss:
                pnl_pct = ((stop_loss - entry_price) / entry_price) * 100
                logger.info(f"   🛑 STOP LOSS HIT at {bar_time}")
                logger.info(f"      Bar low: ${low:.2f}, SL: ${stop_loss:.2f}")
                logger.info(f"      Exit: ${stop_loss:.2f} ({pnl_pct:+.2f}%)")
                from datetime import datetime as dt
                days_held = (dt.combine(bar_time, dt.min.time()) - entry_time).days if isinstance(bar_time, date) else (bar_time - entry_time).days
                logger.info(f"      Held: {i + 1} bars ({days_held} days)")
                return {
                    'outcome': 'SL',
                    'pnl': pnl_pct,
                    'exit_price': stop_loss,
                    'exit_time': bar_time,
                    'bars_held': i + 1,
                }
            
            # Check take profit
            if high >= take_profit:
                pnl_pct = ((take_profit - entry_price) / entry_price) * 100
                logger.info(f"   🎯 TAKE PROFIT HIT at {bar_time}")
                logger.info(f"      Bar high: ${high:.2f}, TP: ${take_profit:.2f}")
                logger.info(f"      Exit: ${take_profit:.2f} ({pnl_pct:+.2f}%)")
                from datetime import datetime as dt
                days_held = (dt.combine(bar_time, dt.min.time()) - entry_time).days if isinstance(bar_time, date) else (bar_time - entry_time).days
                logger.info(f"      Held: {i + 1} bars ({days_held} days)")
                return {
                    'outcome': 'TP',
                    'pnl': pnl_pct,
                    'exit_price': take_profit,
                    'exit_time': bar_time,
                    'bars_held': i + 1,
                }
        
        # Expired - use last close
        final_close = float(rows[-1][1])
        final_time = rows[-1][0]
        pnl_pct = ((final_close - entry_price) / entry_price) * 100
        
        logger.info(f"   ⏱️  TIME EXPIRED at {final_time}")
        logger.info(f"      Final close: ${final_close:.2f}")
        logger.info(f"      Exit: ${final_close:.2f} ({pnl_pct:+.2f}%)")
        from datetime import datetime as dt
        days_held = (dt.combine(final_time, dt.min.time()) - entry_time).days if isinstance(final_time, date) else (final_time - entry_time).days
        logger.info(f"      Held: {len(rows)} bars ({days_held} days)")
        
        return {
            'outcome': 'EXPIRED',
            'pnl': pnl_pct,
            'exit_price': final_close,
            'exit_time': rows[-1][0],
            'bars_held': len(rows),
        }


def safe_json_dumps(obj):
    """Safely convert object to JSON, handling Decimal and datetime types."""
    import json
    from decimal import Decimal
    from datetime import datetime, date
    
    def default_handler(o):
        if isinstance(o, Decimal):
            return float(o)
        elif isinstance(o, (datetime, date)):
            return o.isoformat()
        elif isinstance(o, dict):
            return {k: default_handler(v) for k, v in o.items()}
        elif isinstance(o, list):
            return [default_handler(item) for item in o]
        else:
            return o
    
    try:
        # First pass: convert Decimal/datetime objects
        cleaned_obj = default_handler(obj)
        # Second pass: serialize to JSON
        return json.dumps(cleaned_obj)
    except Exception as e:
        logger.error(f"JSON serialization error: {e}")
        return None


async def create_backtest_run(start_date: str, end_date: str, run_name: str = None, parameters: dict = None):
    """Create a new backtest run record and return run_id."""
    from datetime import datetime
    import json
    
    # Convert string dates to date objects
    start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
    
    async with get_connection() as conn:
        row = await conn.fetchrow("""
            INSERT INTO backtest_run (
                run_name, start_date, end_date, parameters, status
            ) VALUES ($1, $2, $3, $4, 'RUNNING')
            RETURNING run_id
        """, run_name, start_date_obj, end_date_obj, json.dumps(parameters) if parameters else None)
        
        run_id = row['run_id']
        logger.info(f"Created backtest run: {run_id}")
        return run_id


async def save_backtest_analysis(run_id, trigger, final_state, decision: str, processing_time_ms: int):
    """Save agent analysis to backtest_analysis table."""
    
    # Extract agent outputs from final_state and convert to JSON using safe serializer
    agent_outputs = {
        'topdown_output': safe_json_dumps(final_state.get('topdown')) if final_state.get('topdown') else None,
        'context_output': safe_json_dumps(final_state.get('context')) if final_state.get('context') else None,
        'chart_output': safe_json_dumps(final_state.get('chart')) if final_state.get('chart') else None,
        'supply_chain_output': safe_json_dumps(final_state.get('supply_chain')) if final_state.get('supply_chain') else None,
        'fundamental_output': safe_json_dumps(final_state.get('fundamental')) if final_state.get('fundamental') else None,
        'idea_generator_output': safe_json_dumps(final_state.get('ideas')) if final_state.get('ideas') else None,
        'exit_policy_output': safe_json_dumps(final_state.get('exit_policy')) if final_state.get('exit_policy') else None,
        'red_team_output': safe_json_dumps(final_state.get('red_team')) if final_state.get('red_team') else None,
        'short_perspective_output': safe_json_dumps(final_state.get('short_perspective')) if final_state.get('short_perspective') else None,
        'decision_output': safe_json_dumps(final_state.get('decision')) if final_state.get('decision') else None,
        'position_manager_output': safe_json_dumps(final_state.get('position_manager')) if final_state.get('position_manager') else None,
        'veto_output': safe_json_dumps(final_state.get('veto')) if final_state.get('veto') else None,
        'risk_model_output': safe_json_dumps(final_state.get('risk_model')) if final_state.get('risk_model') else None,
    }
    
    decision_obj = final_state.get('decision', {})
    decision_reason = decision_obj.get('reason') if isinstance(decision_obj, dict) else None
    
    # Convert trigger_detail to JSON string using safe serializer
    trigger_detail_json = safe_json_dumps(trigger.details) if trigger.details else None
    
    async with get_connection() as conn:
        row = await conn.fetchrow("""
            INSERT INTO backtest_analysis (
                run_id, symbol, analysis_date, analysis_time,
                trigger_type, trigger_detail, trigger_priority,
                final_decision, decision_reason,
                topdown_output, context_output, chart_output, supply_chain_output,
                fundamental_output, idea_generator_output, exit_policy_output,
                red_team_output, short_perspective_output, decision_output, position_manager_output,
                veto_output, risk_model_output,
                profile_score, technical_score, processing_time_ms
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22,
                $23, $24, $25
            )
            RETURNING analysis_id
        """,
            run_id,
            trigger.symbol,
            trigger.detected_at.date(),
            trigger.detected_at,
            trigger.trigger_type,
            trigger_detail_json,
            trigger.priority,
            decision,
            decision_reason,
            agent_outputs['topdown_output'],
            agent_outputs['context_output'],
            agent_outputs['chart_output'],
            agent_outputs['supply_chain_output'],
            agent_outputs['fundamental_output'],
            agent_outputs['idea_generator_output'],
            agent_outputs['exit_policy_output'],
            agent_outputs['red_team_output'],
            agent_outputs['short_perspective_output'],
            agent_outputs['decision_output'],
            agent_outputs['position_manager_output'],
            agent_outputs['veto_output'],
            agent_outputs['risk_model_output'],
            trigger.details.get('profile_score') if trigger.details else None,
            trigger.details.get('technical_score') if trigger.details else None,
            processing_time_ms
        )
        
        return row['analysis_id']


async def save_backtest_position(run_id, analysis_id, trigger, signal, outcome):
    """Save trade position with outcome to backtest_position table."""
    
    async with get_connection() as conn:
        await conn.execute("""
            INSERT INTO backtest_position (
                run_id, analysis_id, symbol, direction,
                entry_time, entry_price,
                stop_loss, take_profit, time_stop_days,
                exit_time, exit_price, exit_reason, exit_type,
                bars_held, realized_pnl_pct,
                conviction, position_size_pct, trigger_type,
                status
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19
            )
        """,
            run_id,
            analysis_id,
            trigger.symbol,
            'LONG',  # Default direction
            trigger.detected_at,
            signal['entry_price'],
            signal['stop_loss'],
            signal['take_profit'],
            30,  # Default time stop
            outcome.get('exit_time'),
            outcome.get('exit_price'),
            f"{outcome['outcome']} - backtest simulation",
            outcome['outcome'],  # TP, SL, EXPIRED, NO_DATA
            outcome.get('bars_held', 0),
            outcome.get('pnl', 0),
            str(signal.get('conviction', 'MEDIUM')),  # Convert to string
            None,  # position_size_pct not in signal
            trigger.trigger_type,
            'CLOSED'
        )


async def finalize_backtest_run(run_id, stats: dict):
    """Update backtest run with final statistics."""
    async with get_connection() as conn:
        await conn.execute("""
            UPDATE backtest_run
            SET total_triggers = $2,
                go_count = $3,
                no_go_count = $4,
                error_count = $5,
                completed_at = NOW(),
                status = 'COMPLETED'
            WHERE run_id = $1
        """,
            run_id,
            stats.get('total_triggers', 0),
            stats.get('go_count', 0),
            stats.get('no_go_count', 0),
            stats.get('error_count', 0)
        )
        
        logger.info(f"Finalized backtest run: {run_id}")


async def run_historical_backtest(
    start_date: str,
    end_date: str,
    max_triggers: int = None,
    save_to_db: bool = True,
    run_name: str = None,
):
    """
    Run historical backtest using exact production pipeline.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        max_triggers: Max triggers to process (for testing)
        save_to_db: Save results to database
        run_name: Optional name for this backtest run
    """
    
    logger.info("=" * 80)
    logger.info("HISTORICAL BACKTEST - PRODUCTION PIPELINE REPLAY")
    logger.info("=" * 80)
    logger.info(f"Period: {start_date} to {end_date}")
    if max_triggers:
        logger.info(f"Max triggers: {max_triggers}")
    if save_to_db:
        logger.info(f"Saving results to database: {run_name or 'auto-generated name'}")
    logger.info("")
    
    # Create backtest run record if saving
    run_id = None
    if save_to_db:
        import json
        run_id = await create_backtest_run(
            start_date=start_date,
            end_date=end_date,
            run_name=run_name or f"backtest_{start_date}_to_{end_date}",
            parameters={'max_triggers': max_triggers}
        )
    
    # Step 1: Get historical triggers (with watchlist building)
    logger.info("Step 1: Building daily watchlists and scanning for triggers...")
    logger.info("(This replicates exact production flow)")
    logger.info("")
    logger.info("🔬 BACKTEST MODE: Applying realistic data collection delays:")
    logger.info(f"  - SEC 8-K: {REALISTIC_DATA_DELAYS['sec_filing']}")
    logger.info(f"  - Earnings: {REALISTIC_DATA_DELAYS['earnings']}")
    logger.info(f"  - Hourly data: {REALISTIC_DATA_DELAYS['hourly_data']}")
    logger.info("")
    
    # Create pipeline with backtest-enabled trigger monitor
    pipeline = LiveTradingPipeline()
    pipeline.trigger_monitor = TriggerMonitor(
        backtest_mode=True,
        data_delays=REALISTIC_DATA_DELAYS
    )
    triggers = await get_historical_triggers(pipeline, start_date, end_date, limit=max_triggers)
    
    logger.info(f"\n✓ Found {len(triggers)} total triggers")
    
    # Group by type for summary
    by_type = {}
    for t in triggers:
        by_type[t.trigger_type] = by_type.get(t.trigger_type, 0) + 1
    
    logger.info("\nTrigger breakdown:")
    for ttype, count in sorted(by_type.items(), key=lambda x: -x[1]):
        logger.info(f"  {ttype}: {count}")
    
    # Step 2: Process through production pipeline
    logger.info(f"\n\nStep 2: Processing triggers through production pipeline...")
    logger.info("(This will make real LLM calls)")
    
    results = []
    go_count = 0
    no_go_count = 0
    error_count = 0
    
    # Track positions and analyses created during backtest (to clean up later)
    backtest_position_ids = []
    backtest_analysis_ids = []
    
    # Track active backtest positions (in-memory, for position management)
    active_backtest_positions = {}  # symbol -> {'entry_time': datetime, 'entry_price': float}
    
    for i, trigger in enumerate(triggers, 1):
        logger.info(f"\n{'='*80}")
        logger.info(f"Processing {i}/{len(triggers)}: {trigger.symbol} ({trigger.trigger_type})")
        logger.info(f"{'='*80}")
        
        # REALISTIC BACKTEST: Check trigger cache to prevent duplicate analysis
        # This mirrors live pipeline behavior where triggers are cached per day
        try:
            from eiqora_v2.live.trigger_cache import get_cached_analysis
            
            cached = get_cached_analysis(
                trigger.symbol,
                trigger.trigger_type,
                trigger.detected_at.date()
            )
            
            if cached and cached.get('expires_at') and cached['expires_at'].replace(tzinfo=None) > trigger.detected_at.replace(tzinfo=None):
                logger.info(f"⏭️  Skipping already processed trigger: {trigger.symbol} {trigger.trigger_type}")
                no_go_count += 1
                result = {
                    'trigger': trigger,
                    'decision': 'NO_GO',
                    'reason': 'Cached - already analyzed today',
                    'signal': None,
                    'outcome': None,
                }
                results.append(result)
                continue
        except Exception as e:
            logger.debug(f"Cache check failed (continuing anyway): {e}")
        
        # BACKTEST POSITION CHECK: Skip if we already have an active position in this symbol
        if trigger.symbol in active_backtest_positions:
            logger.info(f"⏭️  Skipping {trigger.symbol} (active backtest position from {active_backtest_positions[trigger.symbol]['entry_time']})")
            no_go_count += 1
            result = {
                'trigger': trigger,
                'decision': 'NO_GO',
                'reason': 'Active backtest position exists',
                'signal': None,
                'outcome': None,
            }
            results.append(result)
            continue
        
        try:
            import time
            start_time = time.time()
            
            # Run through EXACT production pipeline
            signal = await pipeline.process_trigger(trigger)
            
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            # Get final state from pipeline's last analysis
            # Note: process_trigger doesn't return state, need to get it differently
            # For now, we'll save what we have
            
            if signal:
                # GO decision - track outcome
                go_count += 1
                
                # Track IDs for cleanup
                if 'position_id' in signal:
                    backtest_position_ids.append(signal['position_id'])
                if 'analysis_id' in signal:
                    backtest_analysis_ids.append(signal['analysis_id'])
                
                entry_price = signal['entry_price']
                stop_loss = signal['stop_loss']
                take_profit = signal['take_profit']
                
                outcome = await track_trade_outcome(
                    trigger,
                    entry_price,
                    stop_loss,
                    take_profit,
                    trigger.detected_at
                )
                
                # Track position open in backtest
                active_backtest_positions[trigger.symbol] = {
                    'entry_time': trigger.detected_at,
                    'entry_price': entry_price,
                }
                logger.info(f"📍 Opened backtest position for {trigger.symbol}")
                
                # Close position after outcome (SL/TP/EXPIRED)
                if outcome['outcome'] in ['SL', 'TP', 'EXPIRED']:
                    # CRITICAL FIX: Close in database, not just memory
                    # Without this, position stays in DB and blocks all future triggers!
                    try:
                        from eiqora_v2.tools.positions import close_position
                        
                        await close_position(
                            symbol=trigger.symbol,
                            exit_price=outcome.get('exit_price'),
                            exit_reason=f"Backtest {outcome['outcome']}: {outcome.get('pnl', 0):+.2f}%",
                            exit_type=outcome['outcome'],
                            exit_time=outcome.get('exit_time') or trigger.detected_at
                        )
                        logger.info(f"🗄️  Closed position in database for {trigger.symbol}")
                    except Exception as e:
                        logger.warning(f"Failed to close position in database: {e}")
                    
                    # Also close in memory tracker
                    if trigger.symbol in active_backtest_positions:
                        del active_backtest_positions[trigger.symbol]
                        logger.info(f"🔓 Closed backtest position for {trigger.symbol} ({outcome['outcome']})")
                
                result = {
                    'trigger': trigger,
                    'decision': 'GO',
                    'signal': signal,
                    'outcome': outcome,
                }
                
                logger.info(f"✅ GO: {trigger.symbol}")
                logger.info(f"   Entry: ${entry_price:.2f}, SL: ${stop_loss:.2f}, TP: ${take_profit:.2f}")
                logger.info(f"   Outcome: {outcome['outcome']}, P&L: {outcome['pnl']:+.2f}%")
                
                # Save to database if enabled
                if save_to_db and run_id:
                    try:
                        analysis_id = await save_backtest_analysis(
                            run_id, trigger, signal.get('agent_outputs', {}), 'GO', processing_time_ms
                        )
                        await save_backtest_position(run_id, analysis_id, trigger, signal, outcome)
                    except Exception as e:
                        logger.warning(f"Failed to save to database: {e}")
                
            else:
                # NO_GO decision
                no_go_count += 1
                
                # Track analysis_id for NO_GO (need to get it from pipeline state)
                # For now, we'll track by querying the latest analysis_log entry
                # This is a workaround since pipeline returns None for NO_GO
                
                result = {
                    'trigger': trigger,
                    'decision': 'NO_GO',
                    'signal': None,
                    'outcome': None,
                }
                logger.info(f"❌ NO_GO: {trigger.symbol}")
                
                # Save NO_GO analysis to database if enabled
                if save_to_db and run_id:
                    try:
                        await save_backtest_analysis(
                            run_id, trigger, {}, 'NO_GO', processing_time_ms
                        )
                    except Exception as e:
                        logger.warning(f"Failed to save to database: {e}")
            
            results.append(result)
            
            # Progress summary
            if i % 10 == 0:
                logger.info(f"\n--- Progress: {i}/{len(triggers)} ---")
                logger.info(f"GO: {go_count}, NO_GO: {no_go_count}")
        
        except Exception as e:
            logger.error(f"Error processing {trigger.symbol}: {e}", exc_info=True)
            error_count += 1
            results.append({
                'trigger': trigger,
                'decision': 'ERROR',
                'error': str(e),
            })
    
    # Finalize backtest run if saving
    if save_to_db and run_id:
        await finalize_backtest_run(run_id, {
            'total_triggers': len(triggers),
            'go_count': go_count,
            'no_go_count': no_go_count,
            'error_count': error_count,
        })
    
    # CRITICAL: Clean up live tables polluted by backtest
    async with get_connection() as conn:
        # 1. Clean up positions
        if backtest_position_ids:
            logger.info(f"\n🧹 Cleaning up {len(backtest_position_ids)} live positions created during backtest...")
            for pos_id in backtest_position_ids:
                await conn.execute("DELETE FROM position WHERE position_id = $1", pos_id)
            logger.info(f"✅ Cleaned up {len(backtest_position_ids)} positions from live table")
        
        # 2. Clean up analysis_log entries by ID
        if backtest_analysis_ids:
            logger.info(f"🧹 Cleaning up {len(backtest_analysis_ids)} analysis_log entries created during backtest...")
            for analysis_id in backtest_analysis_ids:
                await conn.execute("DELETE FROM analysis_log WHERE analysis_id = $1", analysis_id)
            logger.info(f"✅ Cleaned up {len(backtest_analysis_ids)} analysis_log entries from live table")
    
    # Step 3: Generate report
    logger.info(f"\n\n{'='*80}")
    logger.info("BACKTEST RESULTS")
    logger.info(f"{'='*80}")
    
    logger.info(f"\n📊 Decision Statistics:")
    logger.info(f"  Total triggers: {len(triggers)}")
    
    if len(triggers) > 0:
        logger.info(f"  GO decisions: {go_count} ({go_count/len(triggers)*100:.1f}%)")
        logger.info(f"  NO_GO decisions: {no_go_count} ({no_go_count/len(triggers)*100:.1f}%)")
    else:
        logger.info(f"  No triggers found - check date range and watchlist criteria")
        return []
    
    # Analyze GO trades
    go_results = [r for r in results if r['decision'] == 'GO' and r['outcome']]
    
    if go_results:
        logger.info(f"\n💰 Trade Outcomes (GO decisions only):")
        
        outcomes_count = {}
        for r in go_results:
            outcome_type = r['outcome']['outcome']
            outcomes_count[outcome_type] = outcomes_count.get(outcome_type, 0) + 1
        
        for outcome_type, count in outcomes_count.items():
            logger.info(f"  {outcome_type}: {count} ({count/len(go_results)*100:.1f}%)")
        
        # P&L stats
        total_pnl = sum(r['outcome']['pnl'] for r in go_results)
        avg_pnl = total_pnl / len(go_results)
        
        wins = [r for r in go_results if r['outcome']['pnl'] > 0]
        losses = [r for r in go_results if r['outcome']['pnl'] < 0]
        
        logger.info(f"\n📈 Performance:")
        logger.info(f"  Total P&L: {total_pnl:+.2f}%")
        logger.info(f"  Average P&L: {avg_pnl:+.2f}%")
        if wins:
            logger.info(f"  Wins: {len(wins)} (avg: +{sum(r['outcome']['pnl'] for r in wins)/len(wins):.2f}%)")
        if losses:
            logger.info(f"  Losses: {len(losses)} (avg: {sum(r['outcome']['pnl'] for r in losses)/len(losses):.2f}%)")
        
        # Top trades
        sorted_by_pnl = sorted(go_results, key=lambda r: r['outcome']['pnl'], reverse=True)
        
        logger.info(f"\n🏆 Top 5 Trades:")
        for r in sorted_by_pnl[:5]:
            logger.info(
                f"  {r['trigger'].symbol} ({r['trigger'].trigger_type}): "
                f"{r['outcome']['outcome']} {r['outcome']['pnl']:+.2f}%"
            )
        
        logger.info(f"\n❌ Worst 5 Trades:")
        for r in sorted_by_pnl[-5:]:
            logger.info(
                f"  {r['trigger'].symbol} ({r['trigger'].trigger_type}): "
                f"{r['outcome']['outcome']} {r['outcome']['pnl']:+.2f}%"
            )
    
    logger.info(f"\n{'='*80}")
    logger.info("Backtest complete!")
    logger.info(f"{'='*80}\n")
    
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Historical backtest using production pipeline")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--max-triggers", type=int, help="Max triggers to process")
    parser.add_argument("--save-to-db", action="store_true", default=True, help="Save results to database (default: True)")
    parser.add_argument("--no-save", action="store_true", help="Don't save results to database")
    parser.add_argument("--run-name", type=str, help="Name for this backtest run")
    
    args = parser.parse_args()
    
    save_to_db = args.save_to_db and not args.no_save
    
    asyncio.run(run_historical_backtest(
        start_date=args.start_date,
        end_date=args.end_date,
        max_triggers=args.max_triggers,
        save_to_db=save_to_db,
        run_name=args.run_name,
    ))
