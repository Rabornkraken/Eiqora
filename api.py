import uuid
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yfinance as yf

# Import Eiqora Core
from eiqora.graph.workflow import create_graph

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("eiqora-api")

app = FastAPI(title="Eiqora API", description="Backend for Eiqora Financial Analysis Agent")

# CORS Configuration
origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- In-Memory Storage (Replace with Database later) ---
# Format: {run_id: {"status": "running"|"completed"|"failed", "result": ..., "created_at": ...}}
ANALYSIS_STORE: Dict[str, Dict[str, Any]] = {}

# --- Pydantic Models ---

class AnalysisRequest(BaseModel):
    ticker: str
    initial_capital: float = 100000.0
    initial_position: int = 0
    num_of_news: int = 20
    show_reasoning: bool = True

class AnalysisResponse(BaseModel):
    run_id: str
    status: str
    message: str

# --- Helper Functions ---

def parse_final_state_to_frontend_json(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Maps the Eiqora AgentState to the JSON structure expected by the React frontend.
    Now includes full agent contributions for transparent analysis.
    """
    final_decision_text = state.get("final_decision", "HOLD: No decision generated.")
    investment_plan = state.get("investment_plan", "")
    risk_assessment = state.get("risk_assessment", "")
    
    # Simple Heuristic Parsing for Action
    action = "HOLD"
    confidence = 0.5
    
    upper_decision = final_decision_text.upper()
    if "BUY" in upper_decision:
        action = "BUY"
        confidence = 0.8
    elif "SELL" in upper_decision:
        action = "SELL"
        confidence = 0.8
    
    # Parse debate_history into structured format
    debate_entries = []
    debate_history = state.get("debate_history", [])
    
    # DEBUG: Log the debate history structure
    logger.info(f"DEBUG: debate_history type: {type(debate_history)}, length: {len(debate_history) if debate_history else 0}")
    if debate_history:
        for i, msg in enumerate(debate_history[:2]):  # Log first 2 entries
            logger.info(f"DEBUG: debate_history[{i}] type: {type(msg)}, content preview: {str(msg)[:200]}")
    
    for msg in debate_history:
        content = msg.content if hasattr(msg, 'content') else str(msg)
        content_upper = content.upper()
        
        # Determine role and type from new label format
        if "**BULL OPENING:**" in content or "BULL OPENING" in content_upper:
            role = "bull"
            entry_type = "opening"
            clean_content = content.replace("**BULL OPENING:**", "").strip()
        elif "**BULL REBUTTAL:**" in content or "BULL REBUTTAL" in content_upper:
            role = "bull"
            entry_type = "rebuttal"
            clean_content = content.replace("**BULL REBUTTAL:**", "").strip()
        elif "**BEAR OPENING:**" in content or "BEAR OPENING" in content_upper:
            role = "bear"
            entry_type = "opening"
            clean_content = content.replace("**BEAR OPENING:**", "").strip()
        elif "**BEAR REBUTTAL:**" in content or "BEAR REBUTTAL" in content_upper:
            role = "bear"
            entry_type = "rebuttal"
            clean_content = content.replace("**BEAR REBUTTAL:**", "").strip()
        # Fallback to old format
        elif "**BULL:**" in content or "BULL:" in content_upper:
            role = "bull"
            entry_type = "opening" if len(debate_entries) < 2 else "rebuttal"
            clean_content = content.replace("**BULL:**", "").replace("BULL:", "").strip()
        elif "**BEAR:**" in content or "BEAR:" in content_upper:
            role = "bear"
            entry_type = "opening" if len(debate_entries) < 2 else "rebuttal"
            clean_content = content.replace("**BEAR:**", "").replace("BEAR:", "").strip()
        else:
            role = "bull" if len(debate_entries) % 2 == 0 else "bear"
            entry_type = "opening" if len(debate_entries) < 2 else "rebuttal"
            clean_content = content
        
        debate_entries.append({
            "role": role,
            "type": entry_type,
            "content": clean_content
        })
    
    logger.info(f"DEBUG: Final debate_entries count: {len(debate_entries)}")
    
    # Extract sentiment data - handle various formats
    raw_sentiment = state.get("sentiment_analysis", {})
    logger.info(f"DEBUG: sentiment_analysis type: {type(raw_sentiment)}, preview: {str(raw_sentiment)[:300]}")
    
    # Parse the nested sentiment structure
    sentiment_data = {}
    if isinstance(raw_sentiment, dict):
        # Check if there's an 'analysis' key with stringified JSON
        if 'analysis' in raw_sentiment:
            analysis_str = raw_sentiment['analysis']
            if isinstance(analysis_str, str):
                try:
                    # Parse the stringified JSON
                    parsed = json.loads(analysis_str)
                    sentiment_data = parsed
                    logger.info(f"DEBUG: Parsed sentiment analysis JSON successfully")
                except json.JSONDecodeError:
                    sentiment_data = {"summary": analysis_str}
            elif isinstance(analysis_str, dict):
                sentiment_data = analysis_str
            else:
                sentiment_data = {"summary": str(analysis_str)}
        else:
            sentiment_data = raw_sentiment
    elif isinstance(raw_sentiment, str):
        try:
            sentiment_data = json.loads(raw_sentiment)
        except json.JSONDecodeError:
            sentiment_data = {"summary": raw_sentiment}
    
    # Extract market data
    market_data = state.get("market_data", {})
    if isinstance(market_data, str):
        market_data = {"summary": market_data}
    
    result_json = {
        "ticker": state.get("ticker"),
        "action": action,
        "confidence": confidence,
        
        # NEW: Full Agent Contributions
        "agents": {
            "scouts": {
                "market": {
                    "name": "Market Scout",
                    "icon": "chart",
                    "data": market_data,
                },
                "news": {
                    "name": "News Scout",
                    "icon": "newspaper",
                    "summary": state.get("news_summary", "No news data available."),
                    "raw_sources": state.get("news_raw_sources", []),
                },
                "sentiment": {
                    "name": "Sentiment Scout",
                    "icon": "heart",
                    "analysis": sentiment_data,
                    "raw_sources": state.get("sentiment_raw_sources", []),
                }
            },
            "debate": {
                "rounds": len(debate_entries) // 2 if debate_entries else 0,
                "history": debate_entries
            },
            "management": {
                "trader": {
                    "name": "Trader",
                    "icon": "briefcase",
                    "plan": investment_plan,
                },
                "risk_manager": {
                    "name": "Risk Manager",
                    "icon": "shield",
                    "assessment": risk_assessment,
                },
                "decision_maker": {
                    "name": "Decision Maker",
                    "icon": "gavel",
                    "decision": final_decision_text,
                }
            }
        },
        
        # Keep existing fields for backward compatibility
        "reasoning": final_decision_text + "\n\n" + investment_plan,
        "risk_assessment_summary": {
            "risk_level": "Medium",
            "details": risk_assessment
        },
        "decision_factors": {
            "market_technical": {
                "signal": "neutral", 
                "confidence": 0.5, 
                "key_points": ["See full market data summary."]
            },
            "news_sentiment": {
                "signal": "neutral",
                "confidence": 0.5,
                "key_points": ["See news summary."]
            }
        },
        "monitoring_plan": {
            "investment_timeframe": "3-6 months",
            "exit_conditions": ["Price drops 10%", "Trend reversal confirmed"]
        },
        "alternative_scenarios": {},
        "portfolio_impact": {}
    }
    return result_json

def run_analysis_task(run_id: str, ticker: str):
    """
    Background task to run the LangGraph workflow.
    """
    logger.info(f"[{run_id}] Starting analysis for {ticker}")
    
    # Initialize steps list in store
    ANALYSIS_STORE[run_id]["steps"] = []
    
    try:
        app_graph = create_graph()
        initial_state = {
            "ticker": ticker,
            "debate_history": [],
            "iteration_count": 0
        }
        
        logger.info(f"[{run_id}] Graph created for {ticker}, invoking workflow...")
        
        # Define node-to-message mapping
        node_messages = {
            "market_scout": "Gathering latest market data...",
            "news_scout": "Searching for recent financial news...",
            "sentiment_scout": "Analyzing market sentiment...",
            "memory_retrieve": "Consulting historical memory...",
            "bull_analyst": "Bull Analyst is evaluating upside potential...",
            "bear_analyst": "Bear Analyst is identifying risks...",
            "trader": "Trader is formulating a strategy...",
            "risk_manager": "Risk Manager is assessing exposure...",
            "decision_maker": "Making final investment decision...",
            "memory_save": "Saving experience to memory..."
        }
        
        # Use stream to get intermediate updates
        # IMPORTANT: Accumulate state across all iterations
        accumulated_state = dict(initial_state)
        
        for output in app_graph.stream(initial_state):
            # output is a dict like {"node_name": state_update}
            for node_name, state_update in output.items():
                # Merge the update into accumulated state
                if isinstance(state_update, dict):
                    accumulated_state.update(state_update)
                
                message = node_messages.get(node_name, f"Processing step: {node_name}")
                timestamp = datetime.utcnow().isoformat()
                
                step_record = {
                    "step": node_name,
                    "message": message,
                    "timestamp": timestamp
                }
                
                # Include raw_sources for scout steps
                if node_name == "news_scout" and isinstance(state_update, dict):
                    step_record["raw_sources"] = state_update.get("news_raw_sources", [])
                elif node_name == "sentiment_scout" and isinstance(state_update, dict):
                    step_record["raw_sources"] = state_update.get("sentiment_raw_sources", [])
                elif node_name == "market_scout" and isinstance(state_update, dict):
                    market_data = state_update.get("market_data", {})
                    if isinstance(market_data, dict):
                        step_record["raw_data"] = market_data.get("raw_info", {})
                
                # Update Store
                ANALYSIS_STORE[run_id]["steps"].append(step_record)
                logger.info(f"[{run_id}] Step completed: {node_name} at {timestamp}")
        
        logger.info(f"[{run_id}] Workflow finished. Parsing results...")
        logger.info(f"[{run_id}] Accumulated state keys: {list(accumulated_state.keys())}")
        
        # Parse result from the ACCUMULATED state (not just the last delta)
        result_json = parse_final_state_to_frontend_json(accumulated_state)
        
        ANALYSIS_STORE[run_id]["status"] = "completed"
        ANALYSIS_STORE[run_id]["result"] = result_json
        ANALYSIS_STORE[run_id]["completed_at"] = datetime.utcnow().isoformat()
        
        logger.info(f"[{run_id}] Analysis completed successfully")
        
    except Exception as e:
        logger.error(f"[{run_id}] Analysis failed: {e}", exc_info=True)
        ANALYSIS_STORE[run_id]["status"] = "failed"
        ANALYSIS_STORE[run_id]["error"] = str(e)



# --- Chat Models ---

class ChatRequest(BaseModel):
    message: str
    history: List[Dict[str, Any]] = []  # Changed to Any to accept frontend's rich message objects

class ChatResponse(BaseModel):
    content: str
    action: Optional[str] = None # e.g., "analyze_stock"
    data: Optional[Dict[str, Any]] = None

# --- Chat Helper Functions ---

def process_chat_query(message: str) -> ChatResponse:
    """
    Flexible intent detection for natural language stock queries.
    """
    import re
    message_lower = message.lower()
    
    # First, try to extract any ticker symbols from the message
    # 1. All-caps tickers (e.g., NVDA, AAPL)
    tickers = re.findall(r'\b[A-Z]{1,5}\b', message)
    
    # 2. Lowercase tickers in common contexts
    if not tickers:
        # Look for patterns like "in nvda", "of aapl", "for msft", "about tsla"
        match = re.search(r'(?:in|of|for|about|analyze|analysis)\s+([a-zA-Z]{1,5})\b', message_lower)
        if match:
            tickers = [match.group(1).upper()]
    
    # Filter out common words that look like tickers
    if tickers:
        tickers = [t for t in tickers if t not in ["FOR", "ME", "NOW", "USA", "THE", "AND", "STOCK", "SHARE", "WHAT", "ARE", "CAN", "YOU", "HOW"]]
    
    # Broad intent detection - anything related to stock analysis
    analysis_keywords = [
        "analyze", "analysis", "risk", "risks", "invest", "investing", "investment",
        "buy", "sell", "hold", "should i", "worth", "good stock", "bad stock",
        "recommendation", "recommend", "opinion", "think about", "thoughts on",
        "price target", "forecast", "outlook", "potential", "opportunity"
    ]
    
    is_analysis_request = any(keyword in message_lower for keyword in analysis_keywords)
    
    # If we have a ticker AND it looks like an analysis request, trigger analysis
    if tickers and is_analysis_request:
        # Validate the ticker
        for t in tickers:
            validation = validate_ticker(t)
            if validation.get("valid"):
                return ChatResponse(
                    content=f"I've started a deep analysis for {t}. This will take a moment...",
                    action="analyze_stock",
                    data={"ticker": t}
                )
        
        # Ticker found but not valid
        return ChatResponse(content=f"I couldn't verify '{tickers[0]}' as a valid stock ticker. Please check the symbol and try again.")
    
    # If just "analyze" without clear ticker
    if "analyze" in message_lower or "analysis" in message_lower:
        if tickers:
            # Validate
            validation = validate_ticker(tickers[0])
            if validation.get("valid"):
                return ChatResponse(
                    content=f"I've started a deep analysis for {tickers[0]}. This will take a moment...",
                    action="analyze_stock",
                    data={"ticker": tickers[0]}
                )
        return ChatResponse(content="Sure, I can analyze a stock for you. Which ticker symbol are you interested in? (e.g., 'Analyze AAPL' or 'What are the risks of investing in NVDA?')")
    
    # Comparison intent
    if "compare" in message_lower or "vs" in message_lower or "versus" in message_lower:
        return ChatResponse(content="I can help you compare stocks! Please tell me which two companies you'd like to compare (e.g., 'Compare AAPL vs MSFT'). Note: I currently analyze them individually.")
    
    # Help / Greeting
    if any(word in message_lower for word in ["help", "hello", "hi", "hey"]):
        return ChatResponse(content="Hello! I'm Eiqora's AI assistant. I can help you with:\n\n1. **Stock Analysis**: 'What are the risks of NVDA?' or 'Analyze AAPL'\n2. **Investment Questions**: 'Should I invest in TSLA?'\n3. **Comparisons**: 'Compare AMD vs INTC'\n\nJust ask naturally - no need for exact commands!")
    
    # Default: Try to be helpful if there's a ticker
    if tickers:
        validation = validate_ticker(tickers[0])
        if validation.get("valid"):
            return ChatResponse(
                content=f"I can analyze {tickers[0]} for you! Would you like me to run a full analysis? Just say something like 'analyze {tickers[0]}' or 'what are the risks of {tickers[0]}'."
            )
    
    return ChatResponse(content=f"I'd be happy to help you analyze a stock! Try asking something like:\n\n• 'What are the risks of investing in NVDA?'\n• 'Should I buy AAPL?'\n• 'Analyze TSLA for me'\n\nJust mention a ticker symbol and I'll get started!")

# --- Endpoints ---

@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    return process_chat_query(request.message)

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/stats")
def get_stats():
    total = len(ANALYSIS_STORE)
    completed = sum(1 for v in ANALYSIS_STORE.values() if v["status"] == "completed")
    
    # Calculate success rate
    success_rate = "0%"
    if total > 0:
        rate = (completed / total) * 100
        success_rate = f"{rate:.0f}%"
        
    # Calculate analyses today
    today_str = datetime.utcnow().date().isoformat()
    analyses_today = sum(1 for v in ANALYSIS_STORE.values() if v.get("created_at", "").startswith(today_str))
    
    return {
        "total_analyses": total,
        "completed_analyses": completed,
        "active_users": 1,
        "uptime": "99.9%",
        "success_rate": success_rate,
        "analyses_today": analyses_today,
        "avg_analysis_time": "45s" # Mocked for now as we don't track duration yet
    }

@app.get("/tickers/validate/{ticker}")
def validate_ticker(ticker: str):
    """
    Validates a ticker using yfinance.
    """
    try:
        stock = yf.Ticker(ticker)
        # Fast check: try to get info. Note: yfinance info can be slow.
        # fast_info is better if available, or history.
        hist = stock.history(period="1d")
        if hist.empty:
             return {"ticker": ticker, "valid": False}
        
        info = stock.info
        return {
            "ticker": ticker,
            "valid": True,
            "name": info.get("longName", ticker),
            "exchange": info.get("exchange", "Unknown")
        }
    except Exception as e:
        return {"ticker": ticker, "valid": False, "error": str(e)}

@app.post("/analysis/start", response_model=AnalysisResponse)
def start_analysis(request: AnalysisRequest, background_tasks: BackgroundTasks):
    run_id = str(uuid.uuid4())
    
    ANALYSIS_STORE[run_id] = {
        "run_id": run_id,
        "ticker": request.ticker,
        "status": "running",
        "created_at": datetime.utcnow().isoformat()
    }
    
    # Start background task
    background_tasks.add_task(run_analysis_task, run_id, request.ticker)
    
    return {
        "run_id": run_id,
        "status": "running",
        "message": f"Analysis started for {request.ticker}"
    }

@app.get("/analysis/{run_id}")
def get_analysis_result(run_id: str):
    if run_id not in ANALYSIS_STORE:
        raise HTTPException(status_code=404, detail="Analysis not found")
    
    return ANALYSIS_STORE[run_id]

@app.get("/analysis")
def list_analyses():
    # Return list of analyses (summary)
    return [
        {
            "run_id": k,
            "ticker": v["ticker"],
            "status": v["status"],
            "created_at": v["created_at"]
        }
        for k, v in ANALYSIS_STORE.items()
    ]

# ========================================
# NEW ENDPOINTS FOR FRONTEND V2
# ========================================

@app.get("/api/dashboard-stats")
async def get_dashboard_stats():
    """
    Get dashboard statistics for the landing page header.
    Returns professional trading metrics.
    """
    from datetime import datetime
    
    # Dummy data with professional metrics
    starting_equity = 100000
    current_equity = 125430.25
    total_return = ((current_equity - starting_equity) / starting_equity) * 100
    
    total_trades = 42  # Only counting GO trades (actual trades)
    winning_trades = 28
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    
    return {
        "total_analyses": 156,
        "total_trades": total_trades,
        "win_rate": f"{win_rate:.1f}%",
        "total_return": f"+{total_return:.2f}%",
        "current_equity": f"${current_equity:,.2f}",
        "sharpe_ratio": "1.85",
        "last_updated": datetime.now().isoformat()
    }

@app.get("/api/equity-history")
async def get_equity_history():
    """
    Get equity history for the line chart.
    Returns dummy data for demo purposes showing a realistic equity curve.
    """
    from datetime import datetime, timedelta
    import random
    
    # Generate 30 days of dummy equity data with realistic fluctuations
    base_date = datetime.now() - timedelta(days=30)
    equity = 100000
    equity_data = []
    
    for i in range(31):
        # Random walk with slight upward bias
        change = random.uniform(-2000, 3000)
        equity = max(90000, equity + change)  # Don't go below 90k
        
        equity_data.append({
            "date": (base_date + timedelta(days=i)).strftime("%Y-%m-%d"),
            "equity": round(equity, 2)
        })
    
    return equity_data

@app.get("/api/decisions")
async def get_decisions(
    limit: int = 100,
    offset: int = 0,
    symbol: str = None,
    decision: str = None
):
    """
    Get list of trading decisions from analysis_log.
    Returns dummy data for demo purposes.
    """
    from datetime import datetime, timedelta
    import random
    from uuid import uuid4
    
    # Dummy symbols and reasons
    symbols = ["NVDA", "TSLA", "AAPL", "MSFT", "META", "GOOGL", "AMZN", "AMD"]
    triggers = ["news", "technical_breakout", "earnings", "sec_filing", "sentiment_shift"]
    
    go_reasons = [
        "Strong bullish momentum with high volume confirmation",
        "Positive earnings surprise with raised guidance",
        "Technical breakout above key resistance at $150",
        "Institutional accumulation detected, bullish sentiment",
        "Sector rotation favoring tech, strong fundamentals"
    ]
    
    no_go_reasons = [
        "Red team flagged significant downside risk in current macro environment",
        "Position manager rejected due to portfolio heat cap exceeded",
        "Weak technical setup, bearish divergence on RSI",
        "Fundamental concerns: declining margins and revenue miss",
        "Risk-reward ratio unfavorable, stop loss too wide"
    ]
    
    # Generate dummy decisions
    decisions = []
    base_time = datetime.now() - timedelta(days=15)
    
    for i in range(min(limit, 20)):  # Generate 20 dummy decisions
        is_go = random.random() < 0.3  # 30% GO rate
        
        decisions.append({
            "analysis_id": str(uuid4()),
            "symbol": random.choice(symbols),
            "analysis_time": (base_time + timedelta(hours=i*6)).isoformat(),
            "trigger_type": random.choice(triggers),
            "final_decision": "GO" if is_go else "NO_GO",
            "decision_reason": random.choice(go_reasons if is_go else no_go_reasons)
        })
    
    return decisions

@app.get("/api/decisions/{analysis_id}")
async def get_decision_details(analysis_id: str):
    """
    Get full details of a single analysis including all agent outputs.
    Returns dummy data for demo purposes.
    """
    from datetime import datetime
    import random
    
    # Generate dummy detailed analysis
    symbols = ["NVDA", "TSLA", "AAPL"]
    symbol = random.choice(symbols)
    is_go = random.random() < 0.3
    
    return {
        "analysis_id": analysis_id,
        "symbol": symbol,
        "analysis_time": datetime.now().isoformat(),
        "trigger_type": "technical_breakout",
        "trigger_detail": {
            "type": "breakout",
            "price": 245.50,
            "resistance_level": 240.00
        },
        "final_decision": "GO" if is_go else "NO_GO",
        "decision_reason": "Strong bullish momentum confirmed by all agents" if is_go else "Red team identified significant downside risks",
        "topdown_output": {
            "market_regime": "BULLISH",
            "spy_trend": "Strong uptrend, above 50-day MA",
            "vix_level": 14.5,
            "assessment": "Favorable macro conditions for equity longs"
        },
        "context_output": {
            "price": 245.50,
            "volume": "Above average",
            "relative_strength": "Outperforming sector by 3.2%",
            "summary": f"{symbol} showing strong momentum with increasing volume"
        },
        "chart_output": {
            "pattern": "Bull flag breakout",
            "support": 235.00,
            "resistance": 250.00,
            "technical_score": 8.5,
            "summary": "Clean technical setup with well-defined risk/reward"
        },
        "fundamental_output": {
            "revenue_growth": "12% YoY",
            "earnings_surprise": "+5%",
            "guidance": "Raised for next quarter",
            "analyst_rating": "Buy (15/20 analysts)",
            "summary": "Fundamentals remain strong with positive earnings momentum"
        },
        "idea_generator_output": {
            "thesis": f"Ride the momentum in {symbol} following technical breakout and earnings beat",
            "entry": 245.50,
            "target": 265.00,
            "stop_loss": 237.00,
            "r_multiple": 2.3
        },
        "exit_policy_output": {
            "initial_stop": 237.00,
            "trailing_stop": "8 ATR",
            "profit_target_1": 255.00,
            "profit_target_2": 265.00,
            "time_stop": "30 days"
        },
        "red_team_output": {
            "decision": "APPROVE" if is_go else "REJECT",
            "risks_identified": [
                "High valuation multiples vulnerable to rate changes",
                "Recent sector rotation away from tech",
                "Macroeconomic headwinds building"
            ] if not is_go else ["Minimal risk in current setup"],
            "summary": "Approved with normal position sizing" if is_go else "Rejected due to unfavorable risk-reward in current environment"
        },
        "decision_output": {
            "final_call": "GO" if is_go else "NO_GO",
            "conviction": 0.75 if is_go else 0.25,
            "position_size": "2.5% of portfolio" if is_go else "N/A",
            "reasoning": "All agents align on bullish setup" if is_go else "Risk management concerns override technical setup"
        },
        "position_manager_output": {
            "approved": is_go,
            "portfolio_impact": "0.25% risk per trade" if is_go else "No impact",
            "total_exposure": "23% of portfolio" if is_go else "N/A"
        },
        "risk_model_output": {
            "position_size_pct": 2.5 if is_go else 0,
            "risk_per_trade_pct": 3.5,
            "portfolio_heat": 23.4,
            "max_heat_allowable": 90.0
        }
    }


@app.get("/api/positions")
async def get_positions():
    """
    Get current portfolio positions/holdings.
    Returns dummy data for demo purposes.
    """
    from datetime import datetime, timedelta
    
    # Dummy positions data
    positions = [
        {
            "symbol": "NVDA",
            "shares": 150,
            "entry_price": 450.25,
            "current_price": 498.30,
            "market_value": 150 * 498.30,
            "entry_date": (datetime.now() - timedelta(days=15)).isoformat()
        },
        {
            "symbol": "TSLA",
            "shares": 200,
            "entry_price": 245.80,
            "current_price": 251.45,
            "market_value": 200 * 251.45,
            "entry_date": (datetime.now() - timedelta(days=8)).isoformat()
        },
        {
            "symbol": "AAPL",
            "shares": 300,
            "entry_price": 185.50,
            "current_price": 188.92,
            "market_value": 300 * 188.92,
            "entry_date": (datetime.now() - timedelta(days=22)).isoformat()
        },
        {
            "symbol": "GOOGL",
            "shares": 100,
            "entry_price": 138.20,
            "current_price": 142.75,
            "market_value": 100 * 142.75,
            "entry_date": (datetime.now() - timedelta(days=5)).isoformat()
        },
    ]
    
    return positions


@app.get("/api/trading-history")
async def get_trading_history():
    """
    Get completed trading history.
    Returns dummy data for demo purposes.
    """
    from datetime import datetime, timedelta
    import random
    
    # Dummy trading history
    trades = [
        {
            "symbol": "META",
            "action": "BUY",
            "shares": 100,
            "entry_price": 285.50,
            "exit_price": 312.80,
            "entry_date": (datetime.now() - timedelta(days=45)).isoformat(),
            "exit_date": (datetime.now() - timedelta(days=28)).isoformat()
        },
        {
            "symbol": "AMD",
            "action": "BUY",
            "shares": 250,
            "entry_price": 112.30,
            "exit_price": 125.90,
            "entry_date": (datetime.now() - timedelta(days=60)).isoformat(),
            "exit_date": (datetime.now() - timedelta(days=42)).isoformat()
        },
        {
            "symbol": "NFLX",
            "action": "BUY",
            "shares": 50,
            "entry_price": 420.15,
            "exit_price": 385.20,
            "entry_date": (datetime.now() - timedelta(days=35)).isoformat(),
            "exit_date": (datetime.now() - timedelta(days=18)).isoformat()
        },
        {
            "symbol": "MSFT",
            "action": "BUY",
            "shares": 150,
            "entry_price": 338.25,
            "exit_price": 358.40,
            "entry_date": (datetime.now() - timedelta(days=52)).isoformat(),
            "exit_date": (datetime.now() - timedelta(days=25)).isoformat()
        },
        {
            "symbol": "AMZN",
            "action": "BUY",
            "shares": 80,
            "entry_price": 142.50,
            "exit_price": 155.30,
            "entry_date": (datetime.now() - timedelta(days=70)).isoformat(),
            "exit_date": (datetime.now() - timedelta(days=48)).isoformat()
        },
        {
            "symbol": "COIN",
            "action": "BUY",
            "shares": 120,
            "entry_price": 78.90,
            "exit_price": 72.15,
            "entry_date": (datetime.now() - timedelta(days=38)).isoformat(),
            "exit_date": (datetime.now() - timedelta(days=21)).isoformat()
        },
    ]
    
    return trades


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
@app.get("/api/positions")
async def get_positions():
    """
    Get current portfolio positions/holdings.
    Returns dummy data for demo purposes.
    """
    from datetime import datetime, timedelta
    
    # Dummy positions data
    positions = [
        {
            "symbol": "NVDA",
            "shares": 150,
            "entry_price": 450.25,
            "current_price": 498.30,
            "market_value": 150 * 498.30,
            "entry_date": (datetime.now() - timedelta(days=15)).isoformat()
        },
        {
            "symbol": "TSLA",
            "shares": 200,
            "entry_price": 245.80,
            "current_price": 251.45,
            "market_value": 200 * 251.45,
            "entry_date": (datetime.now() - timedelta(days=8)).isoformat()
        },
        {
            "symbol": "AAPL",
            "shares": 300,
            "entry_price": 185.50,
            "current_price": 188.92,
            "market_value": 300 * 188.92,
            "entry_date": (datetime.now() - timedelta(days=22)).isoformat()
        },
        {
            "symbol": "GOOGL",
            "shares": 100,
            "entry_price": 138.20,
            "current_price": 142.75,
            "market_value": 100 * 142.75,
            "entry_date": (datetime.now() - timedelta(days=5)).isoformat()
        },
    ]
    
    return positions
