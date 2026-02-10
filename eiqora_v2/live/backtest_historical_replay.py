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

    from datetime import datetime as dt, date as dt_date, timezone as dt_timezone

    def _as_aware_utc(value):
        if isinstance(value, dt):
            if value.tzinfo is None:
                return value.replace(tzinfo=dt_timezone.utc)
            return value.astimezone(dt_timezone.utc)
        if isinstance(value, dt_date):
            return dt.combine(value, dt.min.time()).replace(tzinfo=dt_timezone.utc)
        return None

    entry_time = _as_aware_utc(entry_time) or entry_time
    
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
                bar_time_dt = _as_aware_utc(bar_time)
                days_held = (bar_time_dt - entry_time).days if bar_time_dt and entry_time else 0
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
                bar_time_dt = _as_aware_utc(bar_time)
                days_held = (bar_time_dt - entry_time).days if bar_time_dt and entry_time else 0
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
        final_time_dt = _as_aware_utc(final_time)
        days_held = (final_time_dt - entry_time).days if final_time_dt and entry_time else 0
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
        'short_perspective_output': None,  # now merged into red_team
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
    
    # Step 2: Process through production agent pipeline (NO live table writes)
    logger.info(f"\n\nStep 2: Processing triggers through agent pipeline...")
    logger.info("(Orchestrator-only mode: NO writes to live position/signal/analysis_log tables)")

    results = []
    go_count = 0
    no_go_count = 0
    error_count = 0

    # Track active backtest positions IN-MEMORY ONLY (never touches live DB)
    active_backtest_positions = {}  # symbol -> {'entry_time': datetime, 'entry_price': float}

    # Set up orchestrator + enricher directly (bypass LiveTradingPipeline)
    from eiqora_v2.live.orchestrator import LiveTradingOrchestrator
    from eiqora_v2.services.context_enrichment import ContextEnricher
    from eiqora_v2.services.profile_generator import ProfileGenerator
    from eiqora_v2.services.signal_quality import assess_full_signal_quality

    profile_generator = ProfileGenerator()
    context_enricher = ContextEnricher(profile_generator)
    orchestrator = LiveTradingOrchestrator()

    # Track symbols already analyzed today (in-memory dedup)
    analyzed_today = set()  # (symbol, date)

    for i, trigger in enumerate(triggers, 1):
        logger.info(f"\n{'='*80}")
        logger.info(f"Processing {i}/{len(triggers)}: {trigger.symbol} ({trigger.trigger_type})")
        logger.info(f"{'='*80}")

        # In-memory dedup: skip if same symbol already analyzed same day
        dedup_key = (trigger.symbol, trigger.detected_at.date())
        if dedup_key in analyzed_today:
            logger.info(f"⏭️  Skipping {trigger.symbol} (already analyzed on {trigger.detected_at.date()})")
            no_go_count += 1
            results.append({
                'trigger': trigger,
                'decision': 'NO_GO',
                'reason': 'Already analyzed today',
                'signal': None,
                'outcome': None,
            })
            continue

        # In-memory position check (no DB query)
        if trigger.symbol in active_backtest_positions:
            logger.info(f"⏭️  Skipping {trigger.symbol} (active backtest position from {active_backtest_positions[trigger.symbol]['entry_time']})")
            no_go_count += 1
            results.append({
                'trigger': trigger,
                'decision': 'NO_GO',
                'reason': 'Active backtest position exists',
                'signal': None,
                'outcome': None,
            })
            continue

        try:
            import time
            start_time = time.time()

            # Enrich context (read-only DB queries, no writes)
            enrichment = await context_enricher.enrich(
                symbol=trigger.symbol,
                asof_time=trigger.detected_at,
                include_profile=True,
            )
            profile_dict = enrichment.get("profile")
            market_data = enrichment.get("market_data") or {}
            freshness = enrichment.get("data_freshness") or {}
            enrichment_errors = enrichment.get("errors") or []

            # Signal quality assessment (pure function, no DB)
            signal_quality = assess_full_signal_quality(trigger.details)
            enriched_trigger_details = {**trigger.details, **signal_quality}

            # Build initial state (same as live pipeline)
            initial_state = {
                "symbol": trigger.symbol,
                "asof_time": trigger.detected_at,
                "trigger_type": trigger.trigger_type,
                "trigger_priority": trigger.priority,
                "trigger_detail": enriched_trigger_details,
                "profile": profile_dict,
                "market_data": market_data,
                "data_freshness": freshness,
                "enrichment_errors": enrichment_errors,
            }

            # Run agent pipeline directly (no live table writes)
            final_state = await orchestrator.run(initial_state)

            processing_time_ms = int((time.time() - start_time) * 1000)
            analyzed_today.add(dedup_key)

            # Extract decision
            decision = final_state.get("decision", {})
            action = decision.get("final_call") or decision.get("decision") or "NO_GO"
            veto = final_state.get("veto", {})
            if isinstance(veto, dict) and veto.get("veto"):
                action = "NO_GO"

            if action == "GO":
                go_count += 1

                rule = decision.get("rule", {}) or {}
                context = final_state.get("context", {})
                direction = rule.get("direction", "LONG")

                entry_price = rule.get("entry_level") or context.get("current_price", 0)

                # Prefer hourly price if available
                hourly_data = market_data.get("hourly_indicators") or {}
                hourly_price = hourly_data.get("current_price")
                if hourly_price and not hourly_data.get("error"):
                    try:
                        entry_price = float(hourly_price)
                    except (TypeError, ValueError):
                        pass

                # Get exit levels from exit policy or ATR fallback
                exit_policy = final_state.get("exit_policy", {}) or {}
                bracket = exit_policy.get("bracket", {}) if isinstance(exit_policy, dict) else {}

                stop_loss = bracket.get("sl_level")
                take_profit = bracket.get("tp_level")

                if stop_loss is None or take_profit is None:
                    sl_mult = bracket.get("sl_mult") or rule.get("sl_mult", 3.0)
                    tp_mult = bracket.get("tp_mult") or rule.get("tp_mult")
                    atr = context.get("atr14", entry_price * 0.02)

                    if direction == "SHORT":
                        stop_loss = stop_loss or (entry_price + (atr * sl_mult))
                        if tp_mult:
                            take_profit = take_profit or (entry_price - (atr * tp_mult))
                    else:
                        stop_loss = stop_loss or (entry_price - (atr * sl_mult))
                        if tp_mult:
                            take_profit = take_profit or (entry_price + (atr * tp_mult))

                signal = {
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'agent_outputs': final_state,
                    'conviction': decision.get('conviction', 'MEDIUM'),
                }

                outcome = await track_trade_outcome(
                    trigger, entry_price, stop_loss, take_profit, trigger.detected_at
                )

                # Track position in-memory only
                active_backtest_positions[trigger.symbol] = {
                    'entry_time': trigger.detected_at,
                    'entry_price': entry_price,
                }
                logger.info(f"📍 Opened backtest position for {trigger.symbol} (in-memory only)")

                # Close in-memory position on outcome
                if outcome['outcome'] in ['SL', 'TP', 'EXPIRED']:
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

                # Save to backtest-specific tables only
                if save_to_db and run_id:
                    try:
                        analysis_id = await save_backtest_analysis(
                            run_id, trigger, final_state, 'GO', processing_time_ms
                        )
                        await save_backtest_position(run_id, analysis_id, trigger, signal, outcome)
                    except Exception as e:
                        logger.warning(f"Failed to save to backtest DB: {e}")

            else:
                no_go_count += 1

                result = {
                    'trigger': trigger,
                    'decision': 'NO_GO',
                    'signal': None,
                    'outcome': None,
                }
                logger.info(f"❌ NO_GO: {trigger.symbol}")

                # Save to backtest-specific tables only
                if save_to_db and run_id:
                    try:
                        await save_backtest_analysis(
                            run_id, trigger, final_state, 'NO_GO', processing_time_ms
                        )
                    except Exception as e:
                        logger.warning(f"Failed to save to backtest DB: {e}")

            results.append(result)

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
