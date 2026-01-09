"""
Live Trading Orchestrator - includes Position Manager.

Extends base Orchestrator with portfolio position management for live trading.
"""

import logging
from eiqora_v2.orchestrator import Orchestrator
from eiqora_v2.schemas.state import SwingTradeState

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
    
    def _build_agent_chain(self) -> list:
        """Build agent chain with Position Manager."""
        # Get standard agents (TopDown through ExitPolicy)
        agents = []
        
        # 1. TopDown
        if self.include_topdown:
            try:
                from eiqora_v2.agents.topdown import TopDownAgent
                agents.append(TopDownAgent())
            except Exception as e:
                logger.warning(f"Could not load TopDownAgent: {e}")
        
        # 2-6: Standard agents
        from eiqora_v2.agents.context import ContextAgent
        from eiqora_v2.agents.chart import ChartAgent
        from eiqora_v2.agents.idea_generator import IdeaGeneratorAgent
        from eiqora_v2.agents.exit_policy import ExitPolicyAgent
        
        agents.append(ContextAgent())
        agents.append(ChartAgent())
        
        if self.include_fundamental:
            try:
                from eiqora_v2.agents.fundamental import FundamentalAgent
                agents.append(FundamentalAgent())
            except Exception as e:
                logger.warning(f"Could not load FundamentalAgent: {e}")
        
        agents.append(IdeaGeneratorAgent())
        agents.append(ExitPolicyAgent())
        
        # 7. Red Team
        from eiqora_v2.agents.red_team import RedTeamAgent
        agents.append(RedTeamAgent())

        # 8. Decision
        from eiqora_v2.agents.decision import DecisionAgent
        agents.append(DecisionAgent())
        
        # 9. Position Manager (NEW - live trading only)
        try:
            from eiqora_v2.agents.position_manager import PositionManagerAgent
            agents.append(PositionManagerAgent())
            logger.info("Position Manager enabled for live trading")
        except Exception as e:
            logger.warning(f"Could not load PositionManagerAgent: {e}")
        
        # 10. Veto
        from eiqora_v2.agents.veto import VetoAgent
        agents.append(VetoAgent())
        
        return agents
    
    async def run(self, initial_state: SwingTradeState) -> SwingTradeState:
        """
        Run the full agent pipeline with Position Manager.
        
        Position Manager runs AFTER decision but can override it.
        """
        state = initial_state.copy()
        
        logger.info(f"Starting LIVE orchestrator for {state.get('symbol')}")
        logger.info(f"Agent chain: {[a.name for a in self.agents]}")
        
        for agent in self.agents:
            try:
                logger.info(f"  Running {agent.name}...")
                state_update = await agent.run(state)
                
                # Merge state update
                for key, value in state_update.items():
                    state[key] = value
                
                # Check for errors
                if state.get("errors"):
                    last_error = state["errors"][-1]
                    logger.warning(f"  Agent error: {last_error}")
                
                # Check for Position Manager override
                if agent.name == "position_manager":
                    pm_result = state.get("position_manager", {})
                    if pm_result.get("decision") == "REJECT":
                        logger.info(f"  Position Manager REJECTED: {pm_result.get('reasoning')}")
                        # Decision already overridden in position_manager agent
                        break
                    elif pm_result.get("decision") == "REDUCE_SIZE":
                        new_size = pm_result.get("approved_size_pct")
                        logger.info(f"  Position Manager reduced size to {new_size}%")
                
                # Check for veto
                veto_result = state.get("veto", {})
                if isinstance(veto_result, dict) and veto_result.get("veto"):
                    logger.info(f"  Trade vetoed: {veto_result.get('veto_reason')}")
                    break
                    
            except Exception as e:
                logger.error(f"  Agent {agent.name} failed: {e}")
                state.setdefault("errors", []).append(f"{agent.name}: {str(e)}")
        
        # Run NarrativeAgent for approved trades
        decision = state.get("decision", {})
        if isinstance(decision, dict) and decision.get("final_call") == "GO":
            try:
                from eiqora_v2.agents.narrative import NarrativeAgent
                narrative_agent = NarrativeAgent()
                logger.info("  Running narrative (trade approved)...")
                state_update = await narrative_agent.run(state)
                for key, value in state_update.items():
                    state[key] = value
            except Exception as e:
                logger.warning(f"  NarrativeAgent failed: {e}")
        
        logger.info(f"Live orchestrator complete for {state.get('symbol')}")
        return state
