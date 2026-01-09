"""
Multi-Agent Orchestrator for Swing Trading.

FULL AGENT CHAIN (10 agents using ALL database tables):

1. TopDownAgent      - Market regime (SPY, QQQ, VIX from market_bar_daily)
2. ContextAgent      - Stock technical context (market_bar_daily)
3. ChartAgent        - Setup classification (market_bar_daily)
4. FundamentalAgent  - News/SEC/Earnings (yfinance_news, sec_filing, earnings_event)
5. IdeaGeneratorAgent- Generate trade ideas
6. ExitPolicyAgent   - Define TP/SL/time stop
7. RedTeamAgent      - Stress test idea for risks
8. DecisionAgent     - Final GO/NO_GO decision
9. VetoAgent         - Sanity checks
10. NarrativeAgent   - Generate trade narrative

Each agent receives the state from previous agents and adds its output.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from eiqora_v2.schemas.state import SwingTradeState

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Orchestrates the FULL multi-agent pipeline for swing trade decisions.
    Uses ALL database tables for comprehensive analysis.
    """
    
    def __init__(self, include_topdown: bool = True, include_fundamental: bool = True):
        """
        Initialize orchestrator with configurable agents.
        
        Args:
            include_topdown: Include TopDownAgent (macro analysis)
            include_fundamental: Include FundamentalAgent (news/SEC/earnings)
        """
        self.include_topdown = include_topdown
        self.include_fundamental = include_fundamental
        self.agents = self._build_agent_chain()
    
    def _build_agent_chain(self) -> list:
        """Build the agent chain based on configuration."""
        agents = []
        
        # 1. TopDown (macro context) - uses SPY/QQQ/sector OHLCV + VIX
        if self.include_topdown:
            try:
                from eiqora_v2.agents.topdown import TopDownAgent
                agents.append(TopDownAgent())
            except Exception as e:
                logger.warning(f"Could not load TopDownAgent: {e}")
        
        # 2. Context (stock-level technicals) - uses market_bar_daily
        from eiqora_v2.agents.context import ContextAgent
        agents.append(ContextAgent())
        
        # 3. Chart (setup classification) - uses market_bar_daily
        from eiqora_v2.agents.chart import ChartAgent
        agents.append(ChartAgent())
        
        # 4. Fundamental (news/SEC/earnings) - uses yfinance_news, sec_filing, earnings_event
        if self.include_fundamental:
            try:
                from eiqora_v2.agents.fundamental import FundamentalAgent
                agents.append(FundamentalAgent())
            except Exception as e:
                logger.warning(f"Could not load FundamentalAgent: {e}")
        
        # 5. Idea Generator - synthesizes context/chart/fundamental
        from eiqora_v2.agents.idea_generator import IdeaGeneratorAgent
        agents.append(IdeaGeneratorAgent())
        
        # 6. Exit Policy - TP/SL/time stop definition
        from eiqora_v2.agents.exit_policy import ExitPolicyAgent
        agents.append(ExitPolicyAgent())
        
        # 7. Red Team - stress test ideas
        from eiqora_v2.agents.red_team import RedTeamAgent
        agents.append(RedTeamAgent())

        # 8. Decision - final GO/NO_GO
        from eiqora_v2.agents.decision import DecisionAgent
        agents.append(DecisionAgent())
        
        # 9. Veto - sanity checks
        from eiqora_v2.agents.veto import VetoAgent
        agents.append(VetoAgent())
        
        # 10. Narrative (for approved trades only) - generates trade story
        # NOTE: This runs conditionally after decision
        
        return agents
    
    async def run(self, initial_state: SwingTradeState) -> SwingTradeState:
        """
        Run the full agent pipeline.
        
        Args:
            initial_state: Initial state with symbol and asof_time
            
        Returns:
            Final state with all agent outputs
        """
        state = initial_state.copy()
        
        logger.info(f"Starting orchestrator for {state.get('symbol')}")
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
        if isinstance(decision, dict) and decision.get("decision") == "GO":
            try:
                from eiqora_v2.agents.narrative import NarrativeAgent
                narrative_agent = NarrativeAgent()
                logger.info("  Running narrative (trade approved)...")
                state_update = await narrative_agent.run(state)
                for key, value in state_update.items():
                    state[key] = value
            except Exception as e:
                logger.warning(f"  NarrativeAgent failed: {e}")
        
        logger.info(f"Orchestrator complete for {state.get('symbol')}")
        return state


class BacktestOrchestrator:
    """
    Orchestrator optimized for backtesting with:
    - Point-in-time data constraints
    - Date masking for LLM prompts
    - Full data integration
    """
    
    def __init__(self, use_cache: bool = True, include_fundamental: bool = True):
        """
        Args:
            use_cache: Cache orchestrator results
            include_fundamental: Include fundamental agent (may be slow due to data collection)
        """
        self.orchestrator = Orchestrator(
            include_topdown=True,
            include_fundamental=include_fundamental,
        )
        self.use_cache = use_cache
        self.cache: dict[str, Any] = {}
    
    async def run(
        self,
        symbol: str,
        asof_time: datetime,
        trigger: dict,
    ) -> dict:
        """
        Run orchestrator for backtest decision.
        
        Args:
            symbol: Ticker symbol
            asof_time: Point-in-time for the backtest
            trigger: Trigger event that caused this analysis
            
        Returns:
            Decision result with action, TP, SL, etc.
        """
        # Build initial state
        state = SwingTradeState(
            symbol=symbol,
            asof_time=asof_time,
            trigger=trigger,
        )
        
        # Run full pipeline
        final_state = await self.orchestrator.run(state)
        
        # Extract decision
        decision = final_state.get("decision", {})
        veto = final_state.get("veto", {})
        
        # If vetoed, return no action
        if isinstance(veto, dict) and veto.get("veto"):
            return {
                "action": "hold",
                "reason": veto.get("veto_reason", "Vetoed"),
                "confidence": 0.0,
                "agent_outputs": final_state,
            }
        
        # If no decision or NO_GO
        if not decision or decision.get("decision") != "GO":
            return {
                "action": "hold",
                "reason": decision.get("reason", "No trade") if decision else "No decision",
                "confidence": 0.0,
                "agent_outputs": final_state,
            }
        
        # Extract trade rule
        rule = decision.get("rule")
        if not rule:
            return {
                "action": "hold",
                "reason": "No trade rule generated",
                "confidence": 0.0,
                "agent_outputs": final_state,
            }
        
        # Get current price from context
        context = final_state.get("context", {})
        current_price = context.get("current_price", 0)
        
        if current_price == 0:
            # Fallback if context didn't get price
            return {
                "action": "hold",
                "reason": "Could not determine current price",
                "confidence": 0.0,
                "agent_outputs": final_state,
            }
        
        # Calculate TP/SL from rule multipliers
        vol_basis = context.get("vol_basis", {})
        rv20 = vol_basis.get("value", 0.02) if isinstance(vol_basis, dict) else 0.02
        
        tp_mult = rule.get("tp_mult", 4.0)
        sl_mult = rule.get("sl_mult", 2.0)
        
        sl_distance = current_price * rv20 * sl_mult
        tp_distance = current_price * rv20 * tp_mult
        
        direction = rule.get("direction", "LONG")
        
        result = {
            "confidence": 1.0 - decision.get("risk_score", 0.5),
            "entry_price": current_price,
            "max_holding_days": rule.get("time_stop_days", 30),
            "reasoning": decision.get("reason", ""),
            "agent_outputs": {
                "topdown": final_state.get("topdown", {}),
                "context": context,
                "chart": final_state.get("chart", {}),
                "fundamental": final_state.get("fundamental", {}),
                "ideas": final_state.get("ideas", {}),
                "red_team": final_state.get("red_team", {}),
                "decision": decision,
                "narrative": final_state.get("narrative", {}),
            },
        }
        
        if direction == "LONG":
            result["action"] = "enter_long"
            result["take_profit"] = current_price + tp_distance
            result["stop_loss"] = current_price - sl_distance
        else:
            result["action"] = "enter_short"
            result["take_profit"] = current_price - tp_distance
            result["stop_loss"] = current_price + sl_distance
        
        return result


async def main():
    """Test the orchestrator."""
    import os
    
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    
    print("="*60)
    print("FULL MULTI-AGENT ORCHESTRATOR TEST")
    print("="*60)
    print()
    print("Agent Chain (10 agents):")
    print("1. TopDownAgent      - SPY/QQQ/VIX (market_bar_daily)")
    print("2. ContextAgent      - Stock technicals (market_bar_daily)")
    print("3. ChartAgent        - Setup (market_bar_daily)")
    print("4. FundamentalAgent  - News/SEC/Earnings (yfinance_news, sec_filing, earnings_event)")
    print("5. IdeaGeneratorAgent")
    print("6. ExitPolicyAgent")
    print("7. RedTeamAgent")
    print("8. DecisionAgent")
    print("9. VetoAgent")
    print("10. NarrativeAgent   - (if trade approved)")
    print()
    
    orchestrator = BacktestOrchestrator(include_fundamental=False)  # Skip for quick test
    
    result = await orchestrator.run(
        symbol="NVDA",
        asof_time=datetime(2025, 12, 15, 9, 30),
        trigger={"type": "technical", "signal": "breakout_20d"},
    )
    
    print("="*60)
    print("ORCHESTRATOR RESULT")
    print("="*60)
    print(f"Action: {result.get('action')}")
    print(f"Entry: ${result.get('entry_price', 0):.2f}")
    print(f"TP: ${result.get('take_profit', 0):.2f}")
    print(f"SL: ${result.get('stop_loss', 0):.2f}")
    print(f"Confidence: {result.get('confidence', 0):.1%}")
    print(f"Reason: {result.get('reasoning')}")


if __name__ == "__main__":
    asyncio.run(main())
