"""
Live Trading Pipeline orchestrator.

Connects:
- CandidateSelector (daily watchlist)
- TriggerMonitor (event detection)
- Orchestrator (8-agent LLM pipeline)
- SignalManager (trade signals)
"""

import asyncio
import logging
from datetime import datetime, timezone, date
from typing import Any

from eiqora_v2.live.candidate_selector import CandidateSelector
from eiqora_v2.live.trigger_monitor import TriggerMonitor, Trigger
from eiqora_v2.live.position_monitor import PositionMonitor, PositionTrigger
from eiqora_v2.live.signals import SignalManager
from eiqora_v2.live.orchestrator import LiveTradingOrchestrator
from eiqora_v2.services.profile_generator import ProfileGenerator
from eiqora_v2.agents.position_reassessment import PositionReassessmentAgent

_logger = logging.getLogger(__name__)


class LiveTradingPipeline:
    """
    Orchestrates the full trading pipeline:
    1. Daily: Build watchlist (candidates)
    2. Continuous: Monitor for triggers
    3. On trigger: Run LLM pipeline (with Position Manager)
    4. On GO: Store signal, execute trade
    """
    
    def __init__(self):
        self.candidate_selector = CandidateSelector(threshold=0.50)
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
    
    async def process_trigger(
        self,
        trigger: Trigger,
    ) -> dict[str, Any] | None:
        """
        Process a trigger through the LLM pipeline.
        
        Returns:
            Trade signal if GO, None otherwise
        """
        _logger.info(f"Processing trigger: {trigger}")
        
        # Get profile for context
        try:
            profile = await self.profile_generator.get_profile(trigger.symbol)
            profile_dict = profile.model_dump()
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
        
        # Run 8-agent pipeline
        _logger.info(f"Running LLM pipeline for {trigger.symbol} ({trigger.trigger_type})")
        final_state = await self.orchestrator.run(initial_state)
        
        # Check decision
        decision = final_state.get("decision", {})
        action = decision.get("final_call", "NO_GO")
        
        if action == "GO":
            signal = {
                "symbol": trigger.symbol,
                "signal_date": trigger.detected_at.date(),
                "signal_time": trigger.detected_at,
                "trigger_type": trigger.trigger_type,
                "action": "GO",
                "entry_price": decision.get("entry_price"),
                "stop_loss": decision.get("stop_loss"),
                "take_profit": decision.get("take_profit"),
                "conviction": decision.get("conviction", 0.5),
                "reasoning": decision.get("reasoning", ""),
                "agent_outputs": final_state,
                "trigger_detail": trigger.details,
            }
            
            # Store signal
            await self.signal_manager.store_signal(signal)
            
            _logger.info(
                f"✅ GO: {trigger.symbol} @ ${signal['entry_price']:.2f} "
                f"(SL: ${signal['stop_loss']:.2f}, TP: ${signal['take_profit']:.2f})"
            )
            return signal
        else:
            _logger.info(f"❌ NO_GO: {trigger.symbol} ({trigger.trigger_type})")
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
        
        # Process each trigger
        signals = []
        for trigger in triggers:
            signal = await self.process_trigger(trigger)
            if signal:
                signals.append(signal)
        
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
            from eiqora_v2.tools.positions import get_position_by_symbol
            position = await get_position_by_symbol(trigger.symbol)
            
            if not position:
                _logger.warning(f"Position not found for {trigger.symbol}, skipping")
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
                exit_decisions.append({
                    "symbol": trigger.symbol,
                    "action": decision["position_reassessment"]["decision"],
                    "urgency": decision["position_reassessment"]["urgency"],
                    "reasoning": decision["position_reassessment"]["reasoning"],
                    "trigger": trigger.trigger_type,
                })
        
        _logger.info(f"\n=== Position Monitoring: {len(exit_decisions)} exit decision(s) ===\n")
        
        return exit_decisions


async def main():
    """Test the live trading pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    
    pipeline = LiveTradingPipeline()
    
    test_time = datetime(2024, 12, 17, 14, 0, tzinfo=timezone.utc)
    
    print(f"\n{'='*60}")
    print(f"Testing Live Trading Pipeline")
    print(f"{'='*60}\n")
    
    # 1. Build watchlist
    print("Step 1: Building watchlist...")
    watchlist = await pipeline.build_daily_watchlist(test_time)
    print(f"  → {len(watchlist)} candidates\n")
    
    # 2. Scan for triggers (on existing watchlist)
    print("Step 2: Scanning for triggers...")
    signals = await pipeline.run_trigger_scan(test_time)
    print(f"  → {len(signals)} GO signals\n")


if __name__ == "__main__":
    asyncio.run(main())
