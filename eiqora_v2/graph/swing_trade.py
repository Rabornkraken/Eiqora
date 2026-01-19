"""
LangGraph DAG definition for swing trade analysis.
Supports optional agents and live/backtest config presets.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from langgraph.graph import StateGraph, END

from eiqora_v2.config.orchestrator import OrchestratorConfig
from eiqora_v2.config.universe import get_sector, get_sector_etf
from eiqora_v2.schemas.state import SwingTradeState

from eiqora_v2.agents.context import ContextAgent
from eiqora_v2.agents.chart import ChartAgent
from eiqora_v2.agents.idea_generator import IdeaGeneratorAgent
from eiqora_v2.agents.exit_policy import ExitPolicyAgent
from eiqora_v2.agents.red_team import RedTeamAgent
from eiqora_v2.agents.short_perspective import ShortPerspectiveAgent
from eiqora_v2.agents.decision import DecisionAgent
from eiqora_v2.agents.veto import VetoAgent
from eiqora_v2.agents.narrative import NarrativeAgent
from eiqora_v2.agents.topdown import TopDownAgent
from eiqora_v2.agents.fundamental import FundamentalAgent
from eiqora_v2.agents.supply_chain import SupplyChainAgent
from eiqora_v2.agents.position_manager import PositionManagerAgent

logger = logging.getLogger(__name__)

# Initialize agents
context_agent = ContextAgent()
chart_agent = ChartAgent()
idea_generator_agent = IdeaGeneratorAgent()
exit_policy_agent = ExitPolicyAgent()
red_team_agent = RedTeamAgent()
short_perspective_agent = ShortPerspectiveAgent()
decision_agent = DecisionAgent()
veto_agent = VetoAgent()
narrative_agent = NarrativeAgent()
topdown_agent = TopDownAgent()
fundamental_agent = FundamentalAgent()
supply_chain_agent = SupplyChainAgent()
position_manager_agent = PositionManagerAgent()


# ============================================================================
# Node functions
# ============================================================================

async def context_node(state: SwingTradeState) -> dict[str, Any]:
    return await context_agent.run(state)


async def chart_node(state: SwingTradeState) -> dict[str, Any]:
    return await chart_agent.run(state)


async def topdown_node(state: SwingTradeState) -> dict[str, Any]:
    return await topdown_agent.run(state)


async def fundamental_node(state: SwingTradeState) -> dict[str, Any]:
    return await fundamental_agent.run(state)


async def supply_chain_node(state: SwingTradeState) -> dict[str, Any]:
    return await supply_chain_agent.run(state)


async def idea_generator_node(state: SwingTradeState) -> dict[str, Any]:
    return await idea_generator_agent.run(state)


async def exit_policy_node(state: SwingTradeState) -> dict[str, Any]:
    return await exit_policy_agent.run(state)


async def red_team_node(state: SwingTradeState) -> dict[str, Any]:
    return await red_team_agent.run(state)


async def short_perspective_node(state: SwingTradeState) -> dict[str, Any]:
    return await short_perspective_agent.run(state)


async def decision_node(state: SwingTradeState) -> dict[str, Any]:
    return await decision_agent.run(state)


async def position_manager_node(state: SwingTradeState) -> dict[str, Any]:
    return await position_manager_agent.run(state)


async def veto_node(state: SwingTradeState) -> dict[str, Any]:
    return await veto_agent.run(state)


async def narrative_node(state: SwingTradeState) -> dict[str, Any]:
    return await narrative_agent.run(state)


async def early_filter_node(state: SwingTradeState) -> dict[str, Any]:
    """
    Early filter gate to skip tickers that don't meet criteria.
    """
    chart = state.get("chart", {}) or {}
    context = state.get("context", {}) or {}

    if context.get("error"):
        reason = "CONTEXT_ERROR"
        logger.info("Filtered %s: %s", state.get("symbol"), reason)
        return {"should_continue": False, "filter_reason": reason}

    if chart.get("error"):
        reason = "CHART_ERROR"
        logger.info("Filtered %s: %s", state.get("symbol"), reason)
        return {"should_continue": False, "filter_reason": reason}

    setup_type = chart.get("setup_type", "NO_SETUP")
    if setup_type == "NO_SETUP":
        logger.info("Filtered %s: NO_SETUP", state.get("symbol"))
        return {"should_continue": False, "filter_reason": "NO_SETUP"}

    state_tags = context.get("state_tags", [])
    vol_basis = context.get("vol_basis", {})
    rv20 = vol_basis.get("value", 0) if isinstance(vol_basis, dict) else 0

    if "DOWNTREND" in state_tags and rv20 > 0.04:
        logger.info("Filtered %s: DOWNTREND + HIGH_VOL", state.get("symbol"))
        return {"should_continue": False, "filter_reason": "DOWNTREND_HIGH_VOL"}

    logger.info("Passed filter %s: %s", state.get("symbol"), setup_type)
    return {"should_continue": True, "filter_reason": None}


async def idea_gate_node(state: SwingTradeState) -> dict[str, Any]:
    """Join node for idea generation gating."""
    return {}


# ============================================================================
# Conditional edge functions
# ============================================================================

def should_continue_after_filter(state: SwingTradeState) -> Literal["continue", "end"]:
    if state.get("should_continue", True):
        return "continue"
    return "end"


def has_ideas(state: SwingTradeState) -> Literal["has_ideas", "no_ideas"]:
    ideas = state.get("ideas", {})
    ideas_list = ideas.get("ideas", [])
    return "has_ideas" if ideas_list else "no_ideas"


def decision_outcome(state: SwingTradeState) -> Literal["go", "no_go"]:
    decision = state.get("decision", {}) or {}
    final_call = decision.get("final_call") or decision.get("decision")
    if isinstance(final_call, str) and final_call.upper() == "GO":
        return "go"
    return "no_go"


def position_manager_outcome(state: SwingTradeState) -> Literal["reject", "continue"]:
    pm = state.get("position_manager", {}) or {}
    if pm.get("decision") == "REJECT":
        return "reject"
    decision = state.get("decision", {}) or {}
    final_call = decision.get("final_call") or decision.get("decision")
    if isinstance(final_call, str) and final_call.upper() != "GO":
        return "reject"
    return "continue"


def veto_outcome(state: SwingTradeState) -> Literal["veto", "ok"]:
    veto = state.get("veto", {}) or {}
    if veto.get("veto"):
        return "veto"
    return "ok"


# ============================================================================
# Graph construction
# ============================================================================

def create_swing_trade_graph(config: OrchestratorConfig | None = None):
    """
    Create a LangGraph DAG for swing trade analysis.
    """
    cfg = config or OrchestratorConfig()
    graph = StateGraph(SwingTradeState)

    graph.add_node("context", context_node)
    graph.add_node("chart", chart_node)
    graph.add_node("early_filter", early_filter_node)
    graph.add_node("idea_gate", idea_gate_node)
    graph.add_node("idea_generator", idea_generator_node)
    graph.add_node("exit_policy", exit_policy_node)
    graph.add_node("red_team", red_team_node)
    graph.add_node("short_perspective", short_perspective_node)
    graph.add_node("decision", decision_node)
    graph.add_node("veto", veto_node)
    graph.add_node("narrative", narrative_node)

    if cfg.include_topdown:
        graph.add_node("topdown", topdown_node)
    if cfg.include_fundamental:
        graph.add_node("fundamental", fundamental_node)
    if cfg.include_supply_chain:
        graph.add_node("supply_chain", supply_chain_node)
    if cfg.enable_position_manager:
        graph.add_node("position_manager", position_manager_node)

    # Phase 1: Parallel start
    graph.add_edge("__start__", "context")
    if cfg.include_topdown:
        graph.add_edge("__start__", "topdown")
    if cfg.include_fundamental:
        graph.add_edge("__start__", "fundamental")
    if cfg.include_supply_chain:
        graph.add_edge("__start__", "supply_chain")

    # Context -> Chart
    graph.add_edge("context", "chart")

    # Early filter depends on context + chart
    graph.add_edge("context", "early_filter")
    graph.add_edge("chart", "early_filter")

    # Join gate waits for early filter + optional nodes
    graph.add_edge("early_filter", "idea_gate")
    if cfg.include_topdown:
        graph.add_edge("topdown", "idea_gate")
    if cfg.include_fundamental:
        graph.add_edge("fundamental", "idea_gate")
    if cfg.include_supply_chain:
        graph.add_edge("supply_chain", "idea_gate")

    graph.add_conditional_edges(
        "idea_gate",
        should_continue_after_filter,
        {"continue": "idea_generator", "end": END},
    )

    # Idea -> Exit Policy -> Red Team -> Short Perspective -> Decision
    graph.add_conditional_edges(
        "idea_generator",
        has_ideas,
        {"has_ideas": "exit_policy", "no_ideas": END},
    )
    graph.add_edge("exit_policy", "red_team")
    graph.add_edge("red_team", "short_perspective")
    graph.add_edge("short_perspective", "decision")

    if cfg.enable_position_manager:
        graph.add_conditional_edges(
            "decision",
            decision_outcome,
            {"go": "position_manager", "no_go": END},
        )
        graph.add_conditional_edges(
            "position_manager",
            position_manager_outcome,
            {"reject": END, "continue": "veto"},
        )
    else:
        graph.add_conditional_edges(
            "decision",
            decision_outcome,
            {"go": "veto", "no_go": END},
        )

    graph.add_conditional_edges(
        "veto",
        veto_outcome,
        {"veto": END, "ok": "narrative"},
    )

    graph.add_edge("narrative", END)

    return graph.compile()


_GRAPH_CACHE: dict[tuple[bool, bool, bool, bool], Any] = {}


def get_compiled_graph(config: OrchestratorConfig | None = None):
    cfg = config or OrchestratorConfig()
    key = (
        cfg.include_topdown,
        cfg.include_fundamental,
        cfg.include_supply_chain,
        cfg.enable_position_manager,
    )
    cached = _GRAPH_CACHE.get(key)
    if cached is not None:
        return cached
    compiled = create_swing_trade_graph(cfg)
    _GRAPH_CACHE[key] = compiled
    return compiled


def create_initial_state(
    symbol: str,
    asof_time: datetime | None = None,
    trigger_type: str = "CHART_SETUP",
    trigger_refs: list[str] | None = None,
    trigger_detail: dict[str, Any] | None = None,
    trigger_priority: str | None = None,
    state_overrides: dict[str, Any] | None = None,
) -> SwingTradeState:
    if asof_time is None:
        asof_time = datetime.utcnow()

    state = SwingTradeState(
        request_id=f"{symbol}_{asof_time.strftime('%Y%m%d_%H%M%S')}",
        symbol=symbol,
        sector=get_sector(symbol),
        sector_etf=get_sector_etf(symbol),
        asof_time=asof_time,
        trigger_type=trigger_type,
        trigger_refs=trigger_refs or [],
        trigger_detail=trigger_detail,
        trigger_priority=trigger_priority,
        triage=None,
        facts=None,
        context=None,
        topdown=None,
        chart=None,
        ideas=None,
        rules=None,
        exit_policy=None,
        red_team=None,
        short_perspective=None,
        decision=None,
        position_manager=None,
        veto=None,
        narrative=None,
        needs_extraction=False,
        should_continue=True,
        filter_reason=None,
        profile=None,
        market_data=None,
        data_freshness=None,
        enrichment_errors=None,
        errors=[],
    )

    if state_overrides:
        state.update(state_overrides)

    return state


async def run_analysis(
    symbol: str,
    asof_time: datetime | None = None,
    trigger_type: str = "CHART_SETUP",
    config: OrchestratorConfig | None = None,
    state_overrides: dict[str, Any] | None = None,
) -> SwingTradeState:
    initial_state = create_initial_state(
        symbol=symbol,
        asof_time=asof_time,
        trigger_type=trigger_type,
        state_overrides=state_overrides,
    )

    logger.info("Starting analysis for %s", symbol)
    graph = get_compiled_graph(config)
    final_state = await graph.ainvoke(initial_state)
    logger.info("Analysis complete for %s", symbol)
    return final_state
