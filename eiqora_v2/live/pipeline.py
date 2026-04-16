"""
Live Trading Pipeline orchestrator.

Connects:
- CandidateSelector (daily watchlist)
- TriggerMonitor (event detection)
- Orchestrator (10-agent LLM pipeline)
- SignalManager (trade signals)
"""

import asyncio
import logging
import os
import contextlib
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from eiqora_v2.live.candidate_selector import CandidateSelector
from eiqora_v2.live.trigger_monitor import TriggerMonitor, Trigger
from eiqora_v2.live.position_monitor import PositionMonitor, PositionTrigger
from eiqora_v2.live.signals import SignalManager
from eiqora_v2.live.orchestrator import LiveTradingOrchestrator
from eiqora_v2.live.trigger_dispatcher import (
    TriggerDispatcher,
    TriggerEvent,
    TRIGGER_SOURCES,
    WATCHLIST_SOURCES,
    build_event_from_payloads,
)
from eiqora_v2.services.profile_generator import ProfileGenerator
from eiqora_v2.services.context_enrichment import ContextEnricher
from eiqora_v2.agents.position_reassessment import PositionReassessmentAgent

_logger = logging.getLogger(__name__)
EASTERN_TZ = ZoneInfo("America/New_York")
ACCOUNT_REFRESH_SECONDS = max(0, int(os.getenv("ACCOUNT_REFRESH_SECONDS", "900")))
INGEST_CHANNEL = os.getenv("EIQORA_INGEST_CHANNEL", "eiqora_ingest")
TRIGGER_CONCURRENCY = max(1, int(os.getenv("TRIGGER_CONCURRENCY", "3")))
POLL_INTERVAL_SECONDS = max(60, int(os.getenv("POLL_INTERVAL_SECONDS", "900")))


class LiveTradingPipeline:
    """
    Orchestrates the full trading pipeline:
    1. Daily: Build watchlist (candidates)
    2. Continuous: Monitor for triggers
    3. On trigger: Run LLM pipeline (with Position Manager)
    4. On GO: Store signal, execute trade
    """
    
    def __init__(self):
        self.candidate_selector = CandidateSelector()
        self.trigger_monitor = TriggerMonitor()
        self.position_monitor = PositionMonitor()  # NEW
        self.signal_manager = SignalManager()
        self.orchestrator = LiveTradingOrchestrator()  # Uses Position Manager
        self.profile_generator = ProfileGenerator()
        self.context_enricher = ContextEnricher(self.profile_generator)
        self.reassessment_agent = PositionReassessmentAgent()  # NEW
        self.trigger_dispatcher = TriggerDispatcher(self)
    
    async def build_daily_watchlist(
        self,
        scan_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Build daily watchlist using candidate selector."""
        if scan_time is None:
            scan_time = datetime.now(EASTERN_TZ)
        
        _logger.info(f"Building daily watchlist for {scan_time.date()}")
        
        watchlist = await self.candidate_selector.build_watchlist(scan_time)
        
        # Save to database
        await self.candidate_selector.save_watchlist(watchlist, scan_time.date())
        
        _logger.info(f"Watchlist: {len(watchlist)} candidates saved")
        return watchlist

    async def _watchlist_exists(self, scan_date: date) -> bool:
        """Check if a watchlist already exists for the given date."""
        from eiqora_v2.tools.db import get_connection

        async with get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM daily_watchlist WHERE scan_date = $1 LIMIT 1",
                scan_date,
            )
            return row is not None

    
    async def process_trigger(
        self,
        trigger: Trigger,
    ) -> dict[str, Any] | None:
        """
        Process a trigger through the LLM pipeline.
        
        Returns:
            Trade signal if GO, None otherwise
        """
        import time
        from eiqora_v2.live.analysis_logger import (
            is_trigger_processed,
            log_analysis,
            log_suppressed_trigger,
        )
        from eiqora_v2.tools.positions import has_open_position
        
        # Check if this trigger was already processed
        if await is_trigger_processed(trigger.details, trigger.symbol):
            _logger.info(f"⏭️  Skipping already processed trigger: {trigger.symbol} {trigger.trigger_type}")
            return None

        # Skip analysis if we already have an active position (DB or Alpaca)
        if await has_open_position(trigger.symbol):
            _logger.info(f"⏭️  Skipping trigger for {trigger.symbol} (active position in DB)")
            return None

        from eiqora_v2.tools.broker import _is_alpaca_configured, get_alpaca_positions, get_alpaca_account
        if _is_alpaca_configured():
            try:
                alpaca_positions = await get_alpaca_positions()
                if alpaca_positions:
                    alpaca_symbols = {p["symbol"] for p in alpaca_positions}
                    if trigger.symbol in alpaca_symbols:
                        _logger.info(f"⏭️  Skipping trigger for {trigger.symbol} (active position on Alpaca)")
                        return None
            except Exception:
                pass  # Fallback: DB check above is sufficient

        # Skip analysis if no cash available (avoids wasting 10-agent LLM pipeline)
        # Prefer Alpaca account when configured (real trading capital)
        cash = 0.0
        equity = 0.0
        if _is_alpaca_configured():
            try:
                alpaca_acct = await get_alpaca_account()
                if alpaca_acct:
                    cash = float(alpaca_acct.get("cash", 0))
                    equity = float(alpaca_acct.get("equity", 0))
            except Exception:
                alpaca_acct = None
            if not alpaca_acct:
                from eiqora_v2.tools.db import get_connection
                async with get_connection() as conn:
                    acct = await conn.fetchrow("SELECT cash_balance, equity FROM account_state LIMIT 1")
                if acct:
                    cash = float(acct["cash_balance"] or 0)
                    equity = float(acct["equity"] or 0)
        else:
            from eiqora_v2.tools.db import get_connection
            async with get_connection() as conn:
                acct = await conn.fetchrow("SELECT cash_balance, equity FROM account_state LIMIT 1")
            if acct:
                cash = float(acct["cash_balance"] or 0)
                equity = float(acct["equity"] or 0)

        if equity > 0:
            min_cash = max(equity * 0.005, 50)  # 0.5% of equity or $50, whichever is larger
            if cash < min_cash:
                _logger.info("⏭️  Skipping %s — insufficient cash ($%.0f < $%.0f min)", trigger.symbol, cash, min_cash)
                await log_suppressed_trigger(
                    symbol=trigger.symbol,
                    trigger_type=trigger.trigger_type,
                    trigger_detail=trigger.details,
                    detected_at=trigger.detected_at,
                    suppressed_reason=f"insufficient_cash cash=${cash:.0f}",
                    technical_score=trigger.details.get("technical_score"),
                    profile_score=trigger.details.get("profile_score"),
                )
                return None

        _logger.info(f"Processing trigger: {trigger}")
        start_time = time.time()
        
        # Enrich with deterministic context (profile + market data)
        enrichment = await self.context_enricher.enrich(
            symbol=trigger.symbol,
            asof_time=trigger.detected_at,
            include_profile=True,
        )
        profile_dict = enrichment.get("profile")
        profile_score = enrichment.get("profile_score")
        market_data = enrichment.get("market_data") or {}
        freshness = enrichment.get("data_freshness") or {}
        enrichment_errors = enrichment.get("errors") or []

        if not freshness.get("daily_ok", True):
            reason = f"stale_daily_data gap_days={freshness.get('daily_gap_days')}"
            _logger.info("⏭️  Skipping %s due to %s", trigger.symbol, reason)
            await log_suppressed_trigger(
                symbol=trigger.symbol,
                trigger_type=trigger.trigger_type,
                trigger_detail=trigger.details,
                detected_at=trigger.detected_at,
                suppressed_reason=reason,
                technical_score=trigger.details.get("technical_score"),
                profile_score=profile_score or trigger.details.get("profile_score"),
            )
            return None

        if not freshness.get("hourly_ok", True):
            reason = f"stale_hourly_data gap_hours={freshness.get('hourly_gap_hours')}"
            _logger.info("⏭️  Skipping %s due to %s", trigger.symbol, reason)
            await log_suppressed_trigger(
                symbol=trigger.symbol,
                trigger_type=trigger.trigger_type,
                trigger_detail=trigger.details,
                detected_at=trigger.detected_at,
                suppressed_reason=reason,
                technical_score=trigger.details.get("technical_score"),
                profile_score=profile_score or trigger.details.get("profile_score"),
            )
            return None
        
        # Assess signal quality based on confirmations
        from eiqora_v2.services.signal_quality import assess_full_signal_quality
        
        signal_quality = assess_full_signal_quality(trigger.details)
        _logger.info(
            f"  Signal quality: {signal_quality['quality_score']:.3f} "
            f"({signal_quality['quality_level']}) - {', '.join(signal_quality['quality_flags'])}"
        )
        
        # Enrich trigger details with signal quality
        enriched_trigger_details = {
            **trigger.details,
            **signal_quality,
        }
        
        # Build initial state with trigger context
        initial_state = {
            "symbol": trigger.symbol,
            "asof_time": trigger.detected_at,
            "trigger_type": trigger.trigger_type,
            "trigger_priority": trigger.priority,
            "trigger_detail": enriched_trigger_details,  # Now includes signal quality
            "profile": profile_dict,
            "market_data": market_data,
            "data_freshness": freshness,
            "enrichment_errors": enrichment_errors,
        }
        
        # Run 10-agent pipeline
        _logger.info(f"Running LLM pipeline for {trigger.symbol} ({trigger.trigger_type})")
        final_state = await self.orchestrator.run(initial_state)
        
        processing_time_ms = int((time.time() - start_time) * 1000)
        
        # Check decision (respect overrides and veto)
        decision = final_state.get("decision", {})
        action = decision.get("final_call") or decision.get("decision") or "NO_GO"
        veto = final_state.get("veto", {})
        if isinstance(veto, dict) and veto.get("veto"):
            action = "NO_GO"
        
        # Log analysis regardless of outcome (GO or NO_GO)
        analysis_id = await log_analysis(
            symbol=trigger.symbol,
            trigger_type=trigger.trigger_type,
            trigger_detail=trigger.details,
            final_decision=action,
            final_state=final_state,
            processing_time_ms=processing_time_ms,
            profile_score=profile_score or trigger.details.get("profile_score"),
            technical_score=trigger.details.get("technical_score"),
        )
        
        # Store analysis_id for backtest cleanup
        final_state["analysis_id"] = analysis_id
        
        if action == "GO":
            # Get prices from various sources
            rule = decision.get("rule", {}) or {}
            context = final_state.get("context", {})
            direction = rule.get("direction", "LONG")
            time_stop_days = rule.get("time_stop_days", 30)
            
            # Entry price: prefer real-time Finnhub quote, then hourly bar
            entry_price = rule.get("entry_level") or context.get("current_price") or 0

            from eiqora_v2.tools.quote import get_finnhub_quote

            quote_price = await get_finnhub_quote(trigger.symbol)
            if quote_price is not None:
                entry_price = quote_price
            else:
                hourly_data = market_data.get("hourly_indicators") or {}
                hourly_price = hourly_data.get("current_price")
                if hourly_price and not hourly_data.get("error"):
                    try:
                        entry_price = float(hourly_price)
                    except (TypeError, ValueError):
                        pass

            if not entry_price or entry_price <= 0:
                _logger.warning("No valid entry price for %s; skipping GO signal", trigger.symbol)
                return None
            
            # Get exit policy from agent (may have explicit levels)
            exit_policy = final_state.get("exit_policy", {}) or {}
            bracket = exit_policy.get("bracket", {}) if isinstance(exit_policy, dict) else {}
            
            # ADAPTIVE: Prefer explicit price levels over ATR multiples
            stop_loss = bracket.get("sl_level")
            take_profit = bracket.get("tp_level")
            
            # Fallback to ATR-based if no explicit levels
            if stop_loss is None or take_profit is None:
                # Get multipliers from exit policy or fallback defaults
                sl_mult = bracket.get("sl_mult") or rule.get("sl_mult", 3.0)  # Wide catastrophic stop
                tp_mult = bracket.get("tp_mult") or rule.get("tp_mult")  # Often null for decision-based
                atr = context.get("atr14", entry_price * 0.02)  # Default to 2% if no ATR

                if direction == "SHORT":
                    stop_loss = stop_loss or (entry_price + (atr * sl_mult))
                    if tp_mult:
                        take_profit = take_profit or (entry_price - (atr * tp_mult))
                else:
                    stop_loss = stop_loss or (entry_price - (atr * sl_mult))
                    if tp_mult:
                        take_profit = take_profit or (entry_price + (atr * tp_mult))
            
            # Log the exit policy decision
            sl_rationale = bracket.get("sl_rationale", "ATR-based")
            tp_rationale = bracket.get("tp_rationale", "decision-based")
            sl_str = f"${stop_loss:.2f}" if stop_loss is not None else "None"
            tp_str = f"${take_profit:.2f}" if take_profit is not None else "None (decision-based)"
            _logger.info(f"Exit policy - SL: {sl_str} ({sl_rationale}), TP: {tp_str} ({tp_rationale})")
            
            # Get conviction from idea generator or decision
            ideas = final_state.get("ideas", {})
            primary_id = ideas.get("primary_idea_id")
            conviction = "MEDIUM"
            if ideas.get("ideas"):
                primary = next((i for i in ideas["ideas"] if i.get("idea_id") == primary_id), ideas["ideas"][0])
                conviction = primary.get("conviction", "MEDIUM")
            
            conviction_score = {"HIGH": 0.8, "MEDIUM": 0.6, "LOW": 0.4}.get(conviction, 0.5)

            # Deterministic position sizing
            position_size_pct = None
            try:
                from eiqora_v2.services.risk_model import size_position

                risk_result = await size_position(
                    symbol=trigger.symbol,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    conviction=conviction,
                )
                position_size_pct = risk_result.get("position_size_pct")
                final_state["risk_model"] = risk_result
            except Exception as exc:
                _logger.warning("Risk model sizing failed for %s: %s", trigger.symbol, exc)

            # Apply Position Manager sizing if provided
            pm_result = final_state.get("position_manager", {})
            pm_size = pm_result.get("approved_size_pct") if isinstance(pm_result, dict) else None
            if pm_size is not None:
                position_size_pct = (
                    min(position_size_pct, pm_size)
                    if position_size_pct is not None
                    else pm_size
                )

            if position_size_pct is not None:
                try:
                    size_val = float(position_size_pct)
                    # Values >= 1.0 are percentages (e.g. 5 → 0.05, 1 → 0.01)
                    if size_val >= 1.0:
                        size_val = size_val / 100.0
                    # Hard cap: no single position > 25% of equity
                    if size_val > 0.25:
                        size_val = 0.25
                    position_size_pct = size_val
                except (TypeError, ValueError):
                    position_size_pct = None

            # Apply market regime position size adjustment
            if position_size_pct is not None:
                regime_multiplier = trigger.details.get("position_size_multiplier", 1.0)
                if regime_multiplier < 1.0:
                    original = position_size_pct
                    position_size_pct = position_size_pct * regime_multiplier
                    _logger.info(f"Regime adjustment: {original:.4f} * {regime_multiplier:.2f} = {position_size_pct:.4f}")

            signal = {
                "symbol": trigger.symbol,
                "signal_date": trigger.detected_at.date(),
                "signal_time": trigger.detected_at,
                "trigger_type": trigger.trigger_type,
                "action": "GO",
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "conviction": conviction_score,
                "reasoning": decision.get("reason", ""),
                "agent_outputs": final_state,
                "trigger_detail": trigger.details,
                "analysis_id": final_state.get("analysis_id"),  # For backtest cleanup
            }
            
            # Store signal
            signal_id = await self.signal_manager.store_signal(signal)

            # Open position in database
            position_opened = False
            try:
                from eiqora_v2.tools.positions import open_position

                pos_id = await open_position(
                    symbol=trigger.symbol,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    entry_time=trigger.detected_at,
                    time_stop_days=time_stop_days,
                    conviction=conviction,
                    reasoning=decision.get("reason", ""),
                    position_size_pct=position_size_pct,
                    signal_id=signal_id,
                )
                position_opened = pos_id is not None
            except Exception as exc:
                _logger.error("Failed to open position for %s: %s", trigger.symbol, exc, exc_info=True)

            if not position_opened:
                _logger.warning("GO signal for %s but position was NOT opened (no cash or error)", trigger.symbol)
                return None

            sl_fmt = f"${signal['stop_loss']:.2f}" if signal['stop_loss'] is not None else "None"
            tp_fmt = f"${signal['take_profit']:.2f}" if signal['take_profit'] is not None else "None"
            _logger.info(
                f"GO: {trigger.symbol} @ ${signal['entry_price']:.2f} "
                f"(SL: {sl_fmt}, TP: {tp_fmt})"
            )
            return signal
        else:
            _logger.info(f"❌ NO_GO: {trigger.symbol} ({trigger.trigger_type}) - {decision.get('reason', '')[:100]}")
            return None
    
    async def run_trigger_scan(
        self,
        scan_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """
        Scan for triggers and process through LLM.
        
        Returns:
            List of GO signals
        """
        if scan_time is None:
            scan_time = datetime.now(EASTERN_TZ)
        
        _logger.info(f"\n{'='*60}")
        _logger.info(f"TRIGGER SCAN: {scan_time.strftime('%Y-%m-%d %H:%M %Z')}")
        _logger.info(f"{'='*60}")
        
        # Get triggers
        triggers = await self.trigger_monitor.scan_watchlist(scan_time)
        
        if not triggers:
            _logger.info("No triggers found")
            return []
        
        _logger.info(f"Found {len(triggers)} triggers")
        
        # GROUP TRIGGERS BY SYMBOL (consolidation)
        # This prevents multiple analyses for the same symbol
        triggers_by_symbol: dict[str, list[Trigger]] = {}
        for trigger in triggers:
            if trigger.symbol not in triggers_by_symbol:
                triggers_by_symbol[trigger.symbol] = []
            triggers_by_symbol[trigger.symbol].append(trigger)
        
        _logger.info(
            f"Consolidated into {len(triggers_by_symbol)} symbols "
            f"(avg {len(triggers)/len(triggers_by_symbol):.1f} triggers/symbol)"
        )
        
        # Log consolidation details
        for symbol, symbol_triggers in triggers_by_symbol.items():
            if len(symbol_triggers) > 1:
                trigger_types = [t.trigger_type for t in symbol_triggers]
                _logger.info(
                    f"  {symbol}: {len(symbol_triggers)} triggers ({', '.join(trigger_types)})"
                )
        
        # Process triggers in parallel with a concurrency cap
        semaphore = asyncio.Semaphore(TRIGGER_CONCURRENCY)

        async def _run_trigger(symbol: str, symbol_triggers: list[Trigger]) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    # Select primary trigger (highest priority)
                    PRIORITY_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
                    primary_trigger = max(
                        symbol_triggers,
                        key=lambda t: PRIORITY_ORDER.get(t.priority, 0)
                    )
                    
                    # Add consolidation context to primary trigger
                    if len(symbol_triggers) > 1:
                        primary_trigger.details["consolidated_triggers"] = [
                            {
                                "type": t.trigger_type,
                                "priority": t.priority,
                                "details": t.details,
                            }
                            for t in symbol_triggers
                        ]
                        primary_trigger.details["trigger_count"] = len(symbol_triggers)
                        _logger.info(
                            f"  {symbol}: Processing consolidated triggers via {primary_trigger.trigger_type} "
                            f"({len(symbol_triggers)} total)"
                        )
                    
                    return await self.process_trigger(primary_trigger)
                except Exception as exc:
                    _logger.warning("Trigger processing failed for %s: %s", symbol, exc)
                    return None

        tasks = [
            asyncio.create_task(_run_trigger(symbol, symbol_triggers))
            for symbol, symbol_triggers in triggers_by_symbol.items()
        ]
        results = await asyncio.gather(*tasks)
        signals = [signal for signal in results if signal]
        
        _logger.info(f"\n{'='*60}")
        _logger.info(f"SCAN COMPLETE: {len(signals)} GO signal(s)")
        _logger.info(f"{'='*60}\n")
        
        return signals
    
    async def monitor_positions(
        self,
        check_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """
        Monitor open positions for thesis-breaking events.
        Separate workflow from entry scanning.
        
        Returns:
            List of exit decisions
        """
        if check_time is None:
            check_time = datetime.now(EASTERN_TZ)
        
        _logger.info(f"=== POSITION MONITORING: {check_time} ===")
        
        # Detect thesis-breaking triggers
        triggers = await self.position_monitor.scan_positions(check_time)
        
        if not triggers:
            _logger.info("No thesis-breaking triggers detected")
            return []
        
        exit_decisions = []
        
        for trigger in triggers:
            _logger.info(f"\n⚠️  Reassessing position: {trigger}")
            
            # Get position details and profile
            from eiqora_v2.tools.positions import get_position_by_symbol, close_position
            position = await get_position_by_symbol(trigger.symbol, asof_time=check_time)
            
            if not position:
                _logger.warning(f"Position not found for {trigger.symbol}, skipping")
                continue

            if trigger.trigger_type in {"stop_loss_hit", "take_profit_hit", "time_stop_hit"}:
                exit_type = {
                    "stop_loss_hit": "SL",
                    "take_profit_hit": "TP",
                    "time_stop_hit": "TIME_STOP",
                }.get(trigger.trigger_type, "AUTO_EXIT")
                reason = f"{trigger.trigger_type}: {trigger.details}"
                exit_price = None
                if isinstance(trigger.details, dict):
                    exit_price = trigger.details.get("current_price")
                try:
                    await close_position(
                        symbol=trigger.symbol,
                        exit_price=exit_price,
                        exit_reason=reason,
                        exit_type=exit_type,
                        exit_time=check_time,
                    )
                    exit_decisions.append({
                        "symbol": trigger.symbol,
                        "action": "EXIT",
                        "urgency": "IMMEDIATE",
                        "reasoning": reason,
                        "trigger": trigger.trigger_type,
                    })
                except Exception as exc:
                    _logger.warning("Failed to auto-exit position for %s: %s", trigger.symbol, exc)
                continue
            
            # Get profile for original thesis
            try:
                profile = await self.profile_generator.get_profile(trigger.symbol)
                profile_dict = profile.model_dump()
            except Exception as e:
                _logger.warning(f"Could not get profile for {trigger.symbol}: {e}")
                profile_dict = {}
            
            # Build state for reassessment
            reassessment_state = {
                "symbol": trigger.symbol,
                "position": position,
                "trigger": {
                    "trigger_type": trigger.trigger_type,
                    "severity": trigger.severity,
                    "details": trigger.details,
                },
                "profile": profile_dict,
            }
            
            # Run LLM reassessment
            decision = await self.reassessment_agent.run(reassessment_state)
            
            _logger.info(
                f"Decision: {decision['position_reassessment']['decision']} "
                f"(confidence: {decision['position_reassessment']['confidence']:.2f})"
            )
            _logger.info(f"Reasoning: {decision['position_reassessment']['reasoning']}")
            
            # If EXIT or REDUCE_SIZE, add to exit decisions
            if decision["position_reassessment"]["decision"] in ["EXIT", "REDUCE_SIZE"]:
                exit_action = decision["position_reassessment"]["decision"]
                exit_decisions.append({
                    "symbol": trigger.symbol,
                    "action": exit_action,
                    "urgency": decision["position_reassessment"]["urgency"],
                    "reasoning": decision["position_reassessment"]["reasoning"],
                    "trigger": trigger.trigger_type,
                })

                if exit_action == "EXIT":
                    try:
                        await close_position(
                            symbol=trigger.symbol,
                            exit_reason=f"{trigger.trigger_type}: {decision['position_reassessment']['reasoning']}",
                            exit_type="REASSESSMENT",
                            exit_time=check_time,
                        )
                    except Exception as exc:
                        _logger.warning("Failed to close position for %s: %s", trigger.symbol, exc)
                elif exit_action == "REDUCE_SIZE":
                    _logger.info(
                        "REDUCE_SIZE not implemented for %s; leaving position active",
                        trigger.symbol,
                    )
        
        _logger.info(f"\n=== Position Monitoring: {len(exit_decisions)} exit decision(s) ===\n")
        
        return exit_decisions

    async def run_on_ingest_notifications(
        self,
        channel: str | None = None,
        idle_log_seconds: int = 900,
        coalesce_seconds: float = 3.0,
    ) -> None:
        """Listen for ingestion NOTIFY events and trigger scans.

        Uses a dedicated connection (not from the pool) so that pool
        lifecycle events don't kill the long-lived LISTEN.  Automatically
        reconnects on connection loss.
        """
        import asyncpg
        from eiqora_v2.tools.db import _get_database_dsn

        listen_channel = channel or INGEST_CHANNEL
        reconnect_delay = 5  # seconds between reconnect attempts

        while True:
            conn: asyncpg.Connection | None = None
            try:
                # Dedicated connection — NOT from the pool
                dsn = _get_database_dsn()
                conn = await asyncpg.connect(dsn)

                queue: asyncio.Queue[str] = asyncio.Queue()

                def _listener(_connection, _pid, _channel, payload):
                    if payload is not None:
                        queue.put_nowait(payload)

                await conn.add_listener(listen_channel, _listener)
                _logger.info("Listening for ingest notifications on '%s' (dedicated connection)", listen_channel)

                while True:
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=idle_log_seconds)
                    except asyncio.TimeoutError:
                        # Keepalive: run a cheap query to detect dead connections
                        try:
                            await conn.execute("SELECT 1")
                        except Exception:
                            _logger.warning("LISTEN connection dead (keepalive failed); reconnecting")
                            break
                        _logger.info("No ingest notifications in the last %s seconds", idle_log_seconds)
                        continue

                    # Coalesce bursts of events into a single scan.
                    await asyncio.sleep(coalesce_seconds)
                    payloads = [payload]
                    while not queue.empty():
                        payloads.append(queue.get_nowait())

                    now = datetime.now(EASTERN_TZ)
                    event = build_event_from_payloads(payloads, now=now, tz=EASTERN_TZ)
                    try:
                        await self.trigger_dispatcher.dispatch(event)
                    except Exception as exc:
                        _logger.error("Live trigger dispatch failed: %s", exc, exc_info=True)

            except asyncio.CancelledError:
                _logger.info("LISTEN task cancelled; shutting down")
                raise
            except Exception as exc:
                _logger.error(
                    "LISTEN connection lost (%s: %s); reconnecting in %ss",
                    type(exc).__name__, exc, reconnect_delay,
                )
            finally:
                if conn is not None:
                    try:
                        await conn.close()
                    except Exception:
                        pass

            await asyncio.sleep(reconnect_delay)

    async def run_polling_loop(
        self,
        interval_seconds: int | None = None,
        skip_initial_delay: bool = False,
    ) -> None:
        """Periodically poll for new data as a fallback to notifications.

        Dispatches a full trigger event (all sources) every ``interval_seconds``
        so that even if DB NOTIFY events are missed, the pipeline still picks up
        new data.
        """
        interval = POLL_INTERVAL_SECONDS if interval_seconds is None else interval_seconds
        _logger.info("Polling loop every %s seconds", interval)
        if not skip_initial_delay:
            # Wait one full interval before the first poll so that the notify
            # listener has a chance to react first.
            await asyncio.sleep(interval)
        from eiqora_v2.live.trigger_dispatcher import is_market_open

        while True:
            try:
                now = datetime.now(EASTERN_TZ)
                if not is_market_open(now):
                    _logger.debug("Polling skipped — outside market hours (%s)", now.strftime("%a %H:%M %Z"))
                else:
                    _logger.info("Polling fallback: dispatching full scan at %s", now.strftime("%H:%M %Z"))
                    event = TriggerEvent(
                        sources=set(TRIGGER_SOURCES) | set(WATCHLIST_SOURCES),
                        scan_time=now,
                        reason="polling",
                    )
                    await self.trigger_dispatcher.dispatch(event)
            except Exception as exc:
                _logger.error("Polling dispatch failed: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def run_profile_refresh_loop(
        self,
        check_interval_seconds: int = 3600,
        max_age_days: int = 7,
        concurrency: int = 5,
    ) -> None:
        """Refresh stale ticker profiles (>max_age_days old).

        Checks once per hour; only regenerates profiles that are actually stale.
        """
        from eiqora_v2.services.profile_generator import ProfileGenerator
        from eiqora_v2.tools.db import get_connection

        generator = ProfileGenerator()
        _logger.info(
            "Profile refresh loop: check every %ss, max_age=%sd, concurrency=%s",
            check_interval_seconds, max_age_days, concurrency,
        )

        while True:
            try:
                async with get_connection() as conn:
                    rows = await conn.fetch(
                        "SELECT symbol FROM universe_member WHERE active = true ORDER BY symbol"
                    )
                symbols = [r["symbol"] for r in rows]
                if not symbols:
                    _logger.debug("No active universe symbols; skipping profile refresh")
                    await asyncio.sleep(check_interval_seconds)
                    continue

                sem = asyncio.Semaphore(concurrency)
                refreshed, fresh, failed = 0, 0, 0
                cutoff = datetime.utcnow() - __import__("datetime").timedelta(days=max_age_days)

                async def _refresh(sym: str) -> None:
                    nonlocal refreshed, fresh, failed
                    async with sem:
                        try:
                            profile = await generator._load_profile(sym)
                            if profile and profile.last_updated and profile.last_updated.replace(tzinfo=None) >= cutoff:
                                fresh += 1
                                return
                            age_label = "missing" if profile is None else f"{(datetime.utcnow() - profile.last_updated.replace(tzinfo=None)).days}d"
                            _logger.info("Refreshing profile: %s (age=%s)", sym, age_label)
                            await generator.generate_profile(sym)
                            refreshed += 1
                        except Exception as exc:
                            failed += 1
                            _logger.warning("Profile refresh failed for %s: %s", sym, exc)

                await asyncio.gather(*(_refresh(s) for s in symbols))
                _logger.info(
                    "Profile refresh: refreshed=%s fresh=%s failed=%s total=%s",
                    refreshed, fresh, failed, len(symbols),
                )
            except Exception as exc:
                _logger.warning("Profile refresh loop error: %s", exc)

            await asyncio.sleep(check_interval_seconds)

    async def run_account_refresh_loop(self, interval_seconds: int | None = None) -> None:
        """Periodically refresh account_state and account_snapshot."""
        interval = ACCOUNT_REFRESH_SECONDS if interval_seconds is None else interval_seconds
        if interval <= 0:
            _logger.info("Account refresh loop disabled (ACCOUNT_REFRESH_SECONDS=%s)", interval)
            return

        from eiqora_v2.tools.positions import refresh_account_state

        _logger.info("Account refresh loop every %s seconds", interval)
        while True:
            try:
                now = datetime.now(EASTERN_TZ)
                await refresh_account_state(asof_time=now, refresh_prices=True)
            except Exception as exc:
                _logger.warning("Account refresh failed: %s", exc)
            await asyncio.sleep(interval)


async def main():
    """Run the live trading pipeline as a service."""
    log_dir = Path(__file__).resolve().parents[2] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "live_pipeline.log"

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )

    if not any(isinstance(h, logging.FileHandler) for h in root_logger.handlers):
        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        root_logger.addHandler(file_handler)
    
    pipeline = LiveTradingPipeline()
    _logger.info("Starting Live Pipeline Service")

    mode = os.getenv("LIVE_TRIGGER_MODE", "notify").lower()

    # Background tasks that run in every mode
    background_tasks: list[asyncio.Task] = []

    refresh_task = asyncio.create_task(pipeline.run_account_refresh_loop())
    background_tasks.append(refresh_task)

    profile_task = asyncio.create_task(pipeline.run_profile_refresh_loop())
    background_tasks.append(profile_task)

    if mode == "notify":
        # Hybrid: listen for DB notifications + periodic polling fallback
        poll_task = asyncio.create_task(pipeline.run_polling_loop())
        background_tasks.append(poll_task)
        _logger.info("Mode: notify + polling fallback (every %ss)", POLL_INTERVAL_SECONDS)
        try:
            await pipeline.run_on_ingest_notifications()
        finally:
            for t in background_tasks:
                t.cancel()
            await asyncio.gather(*background_tasks, return_exceptions=True)
        return

    # Polling-only mode
    _logger.info("Mode: polling only (every %ss)", POLL_INTERVAL_SECONDS)
    poll_task = asyncio.create_task(pipeline.run_polling_loop(skip_initial_delay=True))
    background_tasks.append(poll_task)
    try:
        # Block until a background task fails (shouldn't happen)
        await asyncio.gather(*background_tasks)
    except KeyboardInterrupt:
        _logger.info("Service stopped by user")
    finally:
        for t in background_tasks:
            t.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
