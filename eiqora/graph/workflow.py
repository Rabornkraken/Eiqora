from langgraph.graph import StateGraph, END, START
from eiqora.core.state import AgentState
from eiqora.core.memory import memory_store
import logging

# Import Agents
from eiqora.domain.scouts.market.agent import MarketScout
from eiqora.domain.scouts.technical.agent import TechnicalScout
from eiqora.domain.scouts.earnings.agent import EarningsScout
from eiqora.domain.scouts.news.agent import NewsScout
from eiqora.domain.scouts.sentiment.agent import SentimentScout
from eiqora.domain.traders.analysts.bull import BullAnalyst
from eiqora.domain.traders.analysts.bear import BearAnalyst
from eiqora.domain.traders.managers.trader import Trader
from eiqora.domain.traders.managers.risk import RiskManager
from eiqora.domain.traders.managers.decision import DecisionMaker

logger = logging.getLogger('eiqora-workflow')

def create_graph():
    # 1. Initialize Agents
    market_scout = MarketScout()
    technical_scout = TechnicalScout()
    earnings_scout = EarningsScout()
    news_scout = NewsScout()
    sentiment_scout = SentimentScout()
    
    bull = BullAnalyst()
    bear = BearAnalyst()
    trader = Trader()
    risk = RiskManager()
    decision = DecisionMaker()

    # 2. Define Node Wrappers
    def run_market(state: AgentState):
        return {"market_data": market_scout.gather(state["ticker"])}

    def run_technical(state: AgentState):
        return {"technical_indicators": technical_scout.gather(state["ticker"])}

    def run_earnings(state: AgentState):
        return {"earnings_reports": earnings_scout.gather(state["ticker"])}

    def run_news(state: AgentState):
        result = news_scout.gather(state["ticker"])
        # Handle both old (str) and new (dict) return types
        if isinstance(result, dict):
            return {
                "news_summary": result.get("summary", ""),
                "news_raw_sources": result.get("raw_sources", [])
            }
        return {"news_summary": result}

    def run_sentiment(state: AgentState):
        result = sentiment_scout.gather(state["ticker"])
        # Handle the new structure with raw_sources
        return {
            "sentiment_analysis": result.get("analysis", result),
            "sentiment_raw_sources": result.get("raw_sources", [])
        }

    def run_memory_retrieval(state: AgentState):
        # Retrieve based on news summary or ticker
        query = f"{state['ticker']} {state.get('news_summary', '')[:100]}"
        mems = memory_store.retrieve_similar(query)
        formatted = [f"Situation: {m['metadata']['situation']} -> Outcome: {m['metadata']['outcome']}" for m in mems]
        return {"relevant_memories": formatted}

    # Parallel Round 1: Opening Arguments (Bull and Bear analyze data independently)
    def run_bull_opening(state: AgentState):
        logger.info("DEBUG: run_bull_opening called")
        result = bull.argue(state, mode="opening")
        logger.info(f"DEBUG: Bull opening completed")
        return result

    def run_bear_opening(state: AgentState):
        logger.info("DEBUG: run_bear_opening called")
        result = bear.argue(state, mode="opening")
        logger.info(f"DEBUG: Bear opening completed")
        return result
    
    # Parallel Round 2: Rebuttals (Bull rebuts Bear, Bear rebuts Bull - independent!)
    def run_bull_rebuttal(state: AgentState):
        logger.info("DEBUG: run_bull_rebuttal called")
        result = bull.argue(state, mode="rebuttal")
        logger.info(f"DEBUG: Bull rebuttal completed")
        return result

    def run_bear_rebuttal(state: AgentState):
        logger.info("DEBUG: run_bear_rebuttal called")
        result = bear.argue(state, mode="rebuttal")
        logger.info(f"DEBUG: Bear rebuttal completed")
        return result
        
    # Parallel Management: Trader and Risk Manager (independent analysis)
    def run_trader(state: AgentState):
        return trader.plan(state)

    def run_risk(state: AgentState):
        return risk.assess(state)

    def run_decision(state: AgentState):
        return decision.decide(state)

    def run_memory_save(state: AgentState):
        # Save the experience
        situation = f"Market Summary for {state['ticker']}: {state.get('news_summary', '')[:200]}"
        outcome = state.get("final_decision", "No decision")
        embedding_text = f"{state['ticker']} {situation}"
        memory_store.add_memory(state['ticker'], situation, outcome, embedding_text)
        return {} # No state update needed

    # 3. Build Graph
    workflow = StateGraph(AgentState)

    # Add Nodes - Scouts
    workflow.add_node("market_scout", run_market)
    workflow.add_node("technical_scout", run_technical)
    workflow.add_node("earnings_scout", run_earnings)
    workflow.add_node("news_scout", run_news)
    workflow.add_node("sentiment_scout", run_sentiment)
    workflow.add_node("memory_retrieve", run_memory_retrieval)
    
    # Add Nodes - Debate (separate nodes for openings and rebuttals)
    workflow.add_node("bull_opening", run_bull_opening)
    workflow.add_node("bear_opening", run_bear_opening)
    workflow.add_node("bull_rebuttal", run_bull_rebuttal)
    workflow.add_node("bear_rebuttal", run_bear_rebuttal)
    
    # Add Nodes - Management
    workflow.add_node("trader", run_trader)
    workflow.add_node("risk_manager", run_risk)
    workflow.add_node("decision_maker", run_decision)
    workflow.add_node("memory_save", run_memory_save)

    # ===== EDGES =====
    
    # Phase 1: Parallel Scout Execution
    # START -> All 5 Scouts run in parallel
    workflow.add_edge(START, "market_scout")
    workflow.add_edge(START, "technical_scout")
    workflow.add_edge(START, "earnings_scout")
    workflow.add_edge(START, "news_scout")
    workflow.add_edge(START, "sentiment_scout")
    
    # Converge: All 5 Scouts -> Memory Retrieval
    workflow.add_edge("market_scout", "memory_retrieve")
    workflow.add_edge("technical_scout", "memory_retrieve")
    workflow.add_edge("earnings_scout", "memory_retrieve")
    workflow.add_edge("news_scout", "memory_retrieve")
    workflow.add_edge("sentiment_scout", "memory_retrieve")
    
    # Phase 2: Parallel Debate Round 1 (Opening Arguments)
    # Memory Retrieval -> Both Bull and Bear openings run in parallel
    workflow.add_edge("memory_retrieve", "bull_opening")
    workflow.add_edge("memory_retrieve", "bear_opening")
    
    # Phase 3: Parallel Debate Round 2 (Rebuttals)
    # Both openings must complete -> Both rebuttals run in parallel
    workflow.add_edge("bull_opening", "bull_rebuttal")
    workflow.add_edge("bear_opening", "bull_rebuttal")  # Bull rebuttal needs Bear's opening
    workflow.add_edge("bull_opening", "bear_rebuttal")  # Bear rebuttal needs Bull's opening
    workflow.add_edge("bear_opening", "bear_rebuttal")
    
    # Phase 4: Management Analysis (Risk Manager needs Trader's investment_plan)
    # Both rebuttals must complete -> Trader runs first
    workflow.add_edge("bull_rebuttal", "trader")
    workflow.add_edge("bear_rebuttal", "trader")
    
    # Trader -> Risk Manager (sequential - Risk Manager needs investment_plan)
    workflow.add_edge("trader", "risk_manager")
    
    # Phase 5: Final Decision (needs both Trader and Risk Manager)
    workflow.add_edge("risk_manager", "decision_maker")
    
    # Phase 6: Memory Save and End
    workflow.add_edge("decision_maker", "memory_save")
    workflow.add_edge("memory_save", END)

    return workflow.compile()