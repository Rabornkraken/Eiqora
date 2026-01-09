"""
Live Trading Pipeline orchestrator.

Connects:
- CandidateSelector (daily watchlist)
- TriggerMonitor (event detection)
- Orchestrator (10-agent LLM pipeline)
- SignalManager (trade signals)
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any

from eiqora_v2.live.candidate_selector import CandidateSelector
from eiqora_v2.live.trigger_monitor import TriggerMonitor, Trigger
from eiqora_v2.live.position_monitor import PositionMonitor, PositionTrigger
from eiqora_v2.live.signals import SignalManager
from eiqora_v2.live.orchestrator import LiveTradingOrchestrator
from eiqora_v2.services.profile_generator import ProfileGenerator
from eiqora_v2.agents.position_reassessment import PositionReassessmentAgent

_logger = logging.getLogger(__name__)
INGEST_CHANNEL = os.getenv("EIQORA_INGEST_CHANNEL", "eiqora_ingest")
TRIGGER_SOURCES = {"market_bar_hourly", "yfinance_news", "earnings_event", "sec_filing"}
WATCHLIST_SOURCES = {"market_bar_daily"}
TRIGGER_CONCURRENCY = max(1, int(os.getenv("TRIGGER_CONCURRENCY", "3")))


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
        self.reassessment_agent = PositionReassessmentAgent()  # NEW
    
    async def build_daily_watchlist(
        self,
        scan_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Build daily watchlist using candidate selector."""
        if scan_time is None:
            scan_time = datetime.now(timezone.utc)
        
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
        from eiqora_v2.live.analysis_logger import is_trigger_processed, log_analysis
        from eiqora_v2.tools.positions import has_open_position
        
        # Check if this trigger was already processed
        if await is_trigger_processed(trigger.details, trigger.symbol):
            _logger.info(f"⏭️  Skipping already processed trigger: {trigger.symbol} {trigger.trigger_type}")
            return None

        # Skip analysis if we already have an active position
        if await has_open_position(trigger.symbol):
            _logger.info(f"⏭️  Skipping trigger for {trigger.symbol} (active position)")
            return None
        
        _logger.info(f"Processing trigger: {trigger}")
        start_time = time.time()
        
        # Get profile for context
        profile_score = None
        try:
            profile = await self.profile_generator.get_profile(trigger.symbol)
            profile_dict = profile.model_dump()
            profile_score = profile.profile_score
        except Exception as e:
            _logger.warning(f"Could not get profile for {trigger.symbol}: {e}")
            profile_dict = None
        
        # Build initial state with trigger context
        initial_state = {
            "symbol": trigger.symbol,
            "asof_time": trigger.detected_at,
            "trigger_type": trigger.trigger_type,
            "trigger_priority": trigger.priority,
            "trigger_detail": trigger.details,
            "profile": profile_dict,
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
        await log_analysis(
            symbol=trigger.symbol,
            trigger_type=trigger.trigger_type,
            trigger_detail=trigger.details,
            final_decision=action,
            final_state=final_state,
            processing_time_ms=processing_time_ms,
            profile_score=profile_score or trigger.details.get("profile_score"),
            technical_score=trigger.details.get("technical_score"),
        )
        
        if action == "GO":
            # Get prices from various sources
            rule = decision.get("rule", {}) or {}
            context = final_state.get("context", {})
            
            # Entry price from rule or context
            entry_price = rule.get("entry_level") or context.get("current_price", 0)
            
            # Get ATR-based stops from rule (fallbacks)
            sl_mult = rule.get("sl_mult", 2.0)
            tp_mult = rule.get("tp_mult", 4.0)
            time_stop_days = rule.get("time_stop_days", 30)
            atr = context.get("atr14", entry_price * 0.02)  # Default to 2% if no ATR
            
            direction = rule.get("direction", "LONG")
            if direction == "SHORT":
                stop_loss = entry_price + (atr * sl_mult)
                take_profit = entry_price - (atr * tp_mult)
            else:
                stop_loss = entry_price - (atr * sl_mult)
                take_profit = entry_price + (atr * tp_mult)
            
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
            }
            
            # Store signal
            signal_id = await self.signal_manager.store_signal(signal)

            # Open position in database
            try:
                from eiqora_v2.tools.positions import open_position

                await open_position(
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
            except Exception as exc:
                _logger.warning("Failed to open position for %s: %s", trigger.symbol, exc)
            
            _logger.info(
                f"✅ GO: {trigger.symbol} @ ${signal['entry_price']:.2f} "
                f"(SL: ${signal['stop_loss']:.2f}, TP: ${signal['take_profit']:.2f})"
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
            scan_time = datetime.now(timezone.utc)
        
        _logger.info(f"\n{'='*60}")
        _logger.info(f"TRIGGER SCAN: {scan_time.strftime('%Y-%m-%d %H:%M %Z')}")
        _logger.info(f"{'='*60}")
        
        # Get triggers
        triggers = await self.trigger_monitor.scan_watchlist(scan_time)
        
        if not triggers:
            _logger.info("No triggers found")
            return []
        
        _logger.info(f"Found {len(triggers)} triggers")
        
        # Process triggers in parallel with a concurrency cap
        semaphore = asyncio.Semaphore(TRIGGER_CONCURRENCY)

        async def _run_trigger(trigger: Trigger) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    return await self.process_trigger(trigger)
                except Exception as exc:
                    _logger.warning("Trigger processing failed for %s: %s", trigger.symbol, exc)
                    return None

        tasks = [asyncio.create_task(_run_trigger(trigger)) for trigger in triggers]
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
            check_time = datetime.now(timezone.utc)
        
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
        """Listen for ingestion NOTIFY events and trigger scans."""
        from eiqora_v2.tools.db import get_connection

        listen_channel = channel or INGEST_CHANNEL
        queue: asyncio.Queue[str] = asyncio.Queue()

        def _listener(_connection, _pid, _channel, payload):
            if payload is not None:
                queue.put_nowait(payload)

        async with get_connection() as conn:
            await conn.add_listener(listen_channel, _listener)
            _logger.info(f"Listening for ingest notifications on '{listen_channel}'")

            try:
                while True:
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=idle_log_seconds)
                    except asyncio.TimeoutError:
                        _logger.info("No ingest notifications in the last %s seconds", idle_log_seconds)
                        continue

                    # Coalesce bursts of events into a single scan.
                    await asyncio.sleep(coalesce_seconds)
                    payloads = [payload]
                    while not queue.empty():
                        payloads.append(queue.get_nowait())

                    sources = set()
                    latest_at = None
                    for raw in payloads:
                        try:
                            data = json.loads(raw)
                        except Exception:
                            data = {}
                        source = data.get("source")
                        if source:
                            sources.add(source)
                        ts = data.get("latest_at")
                        if ts:
                            try:
                                parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                if parsed.tzinfo is None:
                                    parsed = parsed.replace(tzinfo=timezone.utc)
                                if latest_at is None or parsed > latest_at:
                                    latest_at = parsed
                            except ValueError:
                                pass

                    now = datetime.now(timezone.utc)
                    scan_time = min(latest_at, now) if latest_at else now

                    if sources & WATCHLIST_SOURCES:
                        try:
                            if await self._watchlist_exists(scan_time.date()):
                                _logger.info("Watchlist already built for %s; skipping rebuild", scan_time.date())
                            else:
                                _logger.info("Daily bars update received; building watchlist for %s", scan_time.date())
                                await self.build_daily_watchlist(scan_time)
                        except Exception as exc:
                            _logger.warning("Failed to build daily watchlist: %s", exc)

                    if sources & TRIGGER_SOURCES:
                        _logger.info("Ingest update received (%s); running scan", ", ".join(sorted(sources)))
                        await self.monitor_positions(scan_time)
                        await self.run_trigger_scan(scan_time)
                    elif not (sources & WATCHLIST_SOURCES):
                        _logger.debug("Ignoring ingest notification sources: %s", sorted(sources))
            finally:
                await conn.remove_listener(listen_channel, _listener)


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
    if mode == "notify":
        await pipeline.run_on_ingest_notifications()
        return

    # Run continuously (polling mode)
    while True:
        try:
            now = datetime.now(timezone.utc)

            # 1. Monitor Positions (Thesis Check)
            await pipeline.monitor_positions(now)

            # 2. Scan for Triggers
            await pipeline.run_trigger_scan(now)

            _logger.info("Sleeping for 15 minutes...")
            await asyncio.sleep(900)

        except KeyboardInterrupt:
            _logger.info("Service stopped by user")
            break
        except Exception as e:
            _logger.error(f"Pipeline loop error: {e}", exc_info=True)
            await asyncio.sleep(60)  # Retry after 1 min on error

if __name__ == "__main__":
    asyncio.run(main())
