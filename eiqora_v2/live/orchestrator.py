"""
Live Trading Orchestrator - includes Position Manager.

Extends base Orchestrator with portfolio position management for live trading.
"""

import logging
from eiqora_v2.orchestrator import Orchestrator
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.config.orchestrator import OrchestratorConfig

logger = logging.getLogger(__name__)


class LiveTradingOrchestrator(Orchestrator):
    """
    Orchestrator for live trading with Position Manager integration.
    
    Agent sequence:
    1-6: Standard agents (TopDown, Context, Chart, Fundamental, Idea, Exit)
    7. Red Team
    8. Decision
    9. Position Manager  ← NEW: checks portfolio, can override/adjust
    10. Veto
    11. Narrative (if approved)
    """
    
    def __init__(self, config: OrchestratorConfig | None = None):
        super().__init__(config=config or OrchestratorConfig.live())

    def _build_registry(self):
        """Build agent registry with Position Manager."""
        registry, order = super()._build_registry()

        # Insert Position Manager after Decision and before Veto.
        if self.config.enable_position_manager:
            try:
                from eiqora_v2.agents.position_manager import PositionManagerAgent
                registry.register("position_manager", PositionManagerAgent(), depends_on=["decision"])

                insert_at = order.index("veto") if "veto" in order else len(order)
                if "position_manager" not in order:
                    order.insert(insert_at, "position_manager")

                if registry.has("veto"):
                    registry.add_dependency("veto", "position_manager")

                logger.info("Position Manager enabled for live trading")
            except Exception as e:
                logger.warning(f"Could not load PositionManagerAgent: {e}")

        return registry, registry.execution_order(order)
    
    async def run(self, initial_state: SwingTradeState) -> SwingTradeState:
        """
        Run the full agent pipeline with Position Manager.
        
        Position Manager runs AFTER decision but can override it.
        """
        logger.info("Starting LIVE orchestrator for %s", initial_state.get("symbol"))
        final_state = await super().run(initial_state)
        logger.info("Live orchestrator complete for %s", initial_state.get("symbol"))
        return final_state
