"""
LangGraph StateGraph definition for swing trade analysis.
Orchestrates the agent workflow with parallel execution and conditional routing.

Week 2: Full pipeline including Idea Generator, Exit Policy, Decision, and Veto.
Stats Service integrated for historical backtesting.
"""

import logging
from datetime import datetime
from typing import Any, Literal

from langgraph.graph import StateGraph, END

from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.agents.event_triage import EventTriageAgent
from eiqora_v2.agents.event_extractor import EventExtractorAgent
from eiqora_v2.agents.context import ContextAgent
from eiqora_v2.agents.chart import ChartAgent
from eiqora_v2.agents.idea_generator import IdeaGeneratorAgent
from eiqora_v2.agents.exit_policy import ExitPolicyAgent
from eiqora_v2.agents.analog_planner import AnalogPlannerAgent
from eiqora_v2.agents.decision import DecisionAgent
from eiqora_v2.agents.veto import VetoAgent
from eiqora_v2.config.universe import get_sector, get_sector_etf
from eiqora_v2.services.stats import run_analog_stats

logger = logging.getLogger(__name__)

# Initialize agents
event_triage_agent = EventTriageAgent()
event_extractor_agent = EventExtractorAgent()
context_agent = ContextAgent()
chart_agent = ChartAgent()
idea_generator_agent = IdeaGeneratorAgent()
exit_policy_agent = ExitPolicyAgent()
analog_planner_agent = AnalogPlannerAgent()
decision_agent = DecisionAgent()
veto_agent = VetoAgent()


# ============================================================================
# Node functions
# ============================================================================

async def event_triage_node(state: SwingTradeState) -> dict[str, Any]:
    """Run Event Triage Agent."""
    return await event_triage_agent.run(state)


async def event_extractor_node(state: SwingTradeState) -> dict[str, Any]:
    """Run Event Extractor Agent."""
    return await event_extractor_agent.run(state)


async def context_node(state: SwingTradeState) -> dict[str, Any]:
    """Run Context Agent."""
    return await context_agent.run(state)


async def chart_node(state: SwingTradeState) -> dict[str, Any]:
    """Run Chart Agent."""
    return await chart_agent.run(state)


async def idea_generator_node(state: SwingTradeState) -> dict[str, Any]:
    """Run Idea Generator Agent."""
    return await idea_generator_agent.run(state)


async def exit_policy_node(state: SwingTradeState) -> dict[str, Any]:
    """Run Exit Policy Agent."""
    return await exit_policy_agent.run(state)


async def analog_planner_node(state: SwingTradeState) -> dict[str, Any]:
    """Run Analog Planner Agent."""
    return await analog_planner_agent.run(state)


async def stats_node(state: SwingTradeState) -> dict[str, Any]:
    """
    Run Stats Service on analog plans.
    
    This is a deterministic node (NO LLM) that backtests
    trade rules against historical analogs.
    """
    rules = state.get("rules", [])
    asof_time = state["asof_time"]
    
    stats_results = []
    
    for rule in rules:
        if "analog_plan" in rule:
            analog_plan = rule["analog_plan"]
            
            # Find the corresponding exit policy
            exit_policy = None
            for r in rules:
                if "exit_policy" in r:
                    exit_policy = r["exit_policy"]
                    break
            
            if exit_policy:
                try:
                    result = await run_analog_stats(
                        analog_plan=analog_plan,
                        trade_rule=exit_policy,
                        asof_time=asof_time,
                    )
                    stats_results.append(result.model_dump())
                except Exception as e:
                    logger.error(f"Stats Service error: {e}")
                    stats_results.append({
                        "plan_id": analog_plan.get("plan_id", "unknown"),
                        "status": "ERROR",
                        "sample_size": 0,
                        "error": str(e),
                    })
    
    logger.info(f"Stats Service: {len(stats_results)} plans evaluated")
    return {"stats": stats_results}


async def decision_node(state: SwingTradeState) -> dict[str, Any]:
    """Run Decision Agent."""
    return await decision_agent.run(state)


async def veto_node(state: SwingTradeState) -> dict[str, Any]:
    """Run Veto Agent."""
    return await veto_agent.run(state)


# ============================================================================
# Filter/routing nodes
# ============================================================================

async def early_filter_node(state: SwingTradeState) -> dict[str, Any]:
    """
    Early filter gate to skip tickers that don't meet criteria.
    
    Filters:
    - No chart setup found
    - Extreme adverse conditions (downtrend + high vol)
    """
    chart = state.get("chart", {})
    context = state.get("context", {})
    
    # Check for chart setup
    setup_type = chart.get("setup_type", "NO_SETUP")
    if setup_type == "NO_SETUP":
        logger.info(f"Filtered {state.get('symbol')}: NO_SETUP")
        return {
            "should_continue": False,
            "filter_reason": "NO_SETUP",
        }
    
    # Check for adverse conditions
    state_tags = context.get("state_tags", [])
    vol_basis = context.get("vol_basis", {})
    rv20 = vol_basis.get("value", 0) if isinstance(vol_basis, dict) else 0
    
    if "DOWNTREND" in state_tags and rv20 > 0.04:
        logger.info(f"Filtered {state.get('symbol')}: DOWNTREND + HIGH_VOL")
        return {
            "should_continue": False,
            "filter_reason": "DOWNTREND_HIGH_VOL",
        }
    
    logger.info(f"Passed filter {state.get('symbol')}: {setup_type}")
    return {
        "should_continue": True,
        "filter_reason": None,
    }


# ============================================================================
# Conditional edge functions
# ============================================================================

def should_continue_after_filter(state: SwingTradeState) -> Literal["continue", "end"]:
    """Continue or end based on filter result."""
    if state.get("should_continue", True):
        return "continue"
    return "end"


def needs_extraction(state: SwingTradeState) -> Literal["extract", "skip"]:
    """Check if Event Extractor should run."""
    if state.get("needs_extraction", False):
        return "extract"
    return "skip"


def has_ideas(state: SwingTradeState) -> Literal["has_ideas", "no_ideas"]:
    """Check if ideas were generated."""
    ideas = state.get("ideas", {})
    ideas_list = ideas.get("ideas", [])
    if ideas_list:
        return "has_ideas"
    return "no_ideas"


def check_decision(state: SwingTradeState) -> Literal["go", "no_go"]:
    """Check if decision is GO."""
    decision = state.get("decision", {})
    if decision.get("decision") == "GO":
        return "go"
    return "no_go"


# ============================================================================
# Graph construction
# ============================================================================

def create_swing_trade_graph() -> StateGraph:
    """
    Create the LangGraph StateGraph for swing trade analysis.
    
    PHASE 1: Parallel start (Event Triage + Context + Chart)
    PHASE 2: Early Filter
    PHASE 3: Idea Generation → Exit Policy → Analog Planner
    PHASE 4: Stats Service (deterministic backtesting)
    PHASE 5: Decision → Veto
    
    Returns:
        Compiled StateGraph
    """
    graph = StateGraph(SwingTradeState)
    
    # Add nodes
    graph.add_node("event_triage", event_triage_node)
    graph.add_node("event_extractor", event_extractor_node)
    graph.add_node("context", context_node)
    graph.add_node("chart", chart_node)
    graph.add_node("early_filter", early_filter_node)
    graph.add_node("idea_generator", idea_generator_node)
    graph.add_node("exit_policy", exit_policy_node)
    graph.add_node("analog_planner", analog_planner_node)
    graph.add_node("stats", stats_node)  # Stats Service node
    graph.add_node("decision", decision_node)
    graph.add_node("veto", veto_node)
    
    # ========================================
    # PHASE 1: Parallel start
    # ========================================
    graph.add_edge("__start__", "event_triage")
    graph.add_edge("__start__", "context")
    graph.add_edge("__start__", "chart")
    
    # All feed into early filter
    graph.add_edge("event_triage", "early_filter")
    graph.add_edge("context", "early_filter")
    graph.add_edge("chart", "early_filter")
    
    # ========================================
    # PHASE 2: Early filter
    # ========================================
    graph.add_conditional_edges(
        "early_filter",
        should_continue_after_filter,
        {
            "continue": "idea_generator",
            "end": END,
        },
    )
    
    # ========================================
    # PHASE 3: Idea generation pipeline
    # ========================================
    graph.add_conditional_edges(
        "idea_generator",
        has_ideas,
        {
            "has_ideas": "exit_policy",
            "no_ideas": END,
        },
    )
    
    graph.add_edge("exit_policy", "analog_planner")
    
    # ========================================
    # PHASE 4: Stats Service (deterministic)
    # ========================================
    graph.add_edge("analog_planner", "stats")
    graph.add_edge("stats", "decision")
    
    # ========================================
    # PHASE 5: Decision → Veto
    # ========================================
    graph.add_conditional_edges(
        "decision",
        check_decision,
        {
            "go": "veto",
            "no_go": END,
        },
    )
    
    graph.add_edge("veto", END)
    
    return graph.compile()


def create_initial_state(
    symbol: str,
    asof_time: datetime | None = None,
    trigger_type: str = "CHART_SETUP",
    trigger_refs: list[str] | None = None,
) -> SwingTradeState:
    """
    Create initial state for a swing trade analysis run.
    
    Args:
        symbol: Stock ticker symbol
        asof_time: Point-in-time (defaults to now)
        trigger_type: Type of trigger that initiated analysis
        trigger_refs: References to trigger sources
    
    Returns:
        Initial SwingTradeState
    """
    if asof_time is None:
        asof_time = datetime.utcnow()
    
    return SwingTradeState(
        request_id=f"{symbol}_{asof_time.strftime('%Y%m%d_%H%M%S')}",
        symbol=symbol,
        sector=get_sector(symbol),
        sector_etf=get_sector_etf(symbol),
        asof_time=asof_time,
        trigger_type=trigger_type,
        trigger_refs=trigger_refs or [],
        triage=None,
        facts=None,
        context=None,
        topdown=None,
        chart=None,
        ideas=None,
        rules=None,
        stats=None,
        decision=None,
        veto=None,
        narrative=None,
        needs_extraction=False,
        should_continue=True,
        filter_reason=None,
        errors=[],
    )


# Create compiled graph
graph = create_swing_trade_graph()


async def run_analysis(
    symbol: str,
    asof_time: datetime | None = None,
    trigger_type: str = "CHART_SETUP",
) -> SwingTradeState:
    """
    Run swing trade analysis for a single symbol.
    
    Args:
        symbol: Stock ticker symbol
        asof_time: Point-in-time (defaults to now)
        trigger_type: Type of trigger
    
    Returns:
        Final SwingTradeState with all agent outputs
    """
    initial_state = create_initial_state(
        symbol=symbol,
        asof_time=asof_time,
        trigger_type=trigger_type,
    )
    
    logger.info(f"Starting analysis for {symbol}")
    
    # Run the graph
    final_state = await graph.ainvoke(initial_state)
    
    logger.info(f"Analysis complete for {symbol}")
    return final_state
