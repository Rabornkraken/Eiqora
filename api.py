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

# Import OLD Eiqora Core (commented out - not needed for database endpoints)
# from eiqora.graph.workflow import create_graph

# Import Database utilities
from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.positions import get_open_positions

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
    Queries real database for trading metrics.
    """
    from datetime import datetime
    
    async with get_connection() as conn:
        # Total analyses count
        total_analyses = await conn.fetchval(
            "SELECT COUNT(*) FROM analysis_log"
        ) or 0
        
        # GO trades count (actual trades)
        go_count = await conn.fetchval(
            "SELECT COUNT(*) FROM analysis_log WHERE final_decision = 'GO'"
        ) or 0
        
        # Win rate from closed positions
        win_rate_result = await conn.fetchrow("""
            SELECT 
                COUNT(*) FILTER (WHERE realized_pnl_pct > 0) as wins,
                COUNT(*) as total
            FROM position
            WHERE status = 'CLOSED'
        """)
        
        if win_rate_result and win_rate_result['total'] > 0:
            win_rate = (win_rate_result['wins'] / win_rate_result['total']) * 100
            win_rate_str = f"{win_rate:.1f}%"
        else:
            win_rate_str = "N/A"
        
        # Current equity and total return
        account = await conn.fetchrow(
            "SELECT equity, starting_equity, realized_pnl FROM account_state WHERE account_id = 'main'"
        )
        
        if account and account['equity']:
            current_equity = float(account['equity'])
            starting_equity = float(account['starting_equity']) if account['starting_equity'] else 100000
            total_return_pct = ((current_equity - starting_equity) / starting_equity) * 100
            total_return = f"+{total_return_pct:.2f}%" if total_return_pct >= 0 else f"{total_return_pct:.2f}%"
            current_equity_str = f"${current_equity:,.2f}"
        else:
            # Fallback if no account data
            current_equity_str = "$100,000.00"
            total_return = "+0.00%"
        
        # Sharpe ratio placeholder (requires advanced calculation)
        sharpe_ratio = "N/A"
        
        return {
            "total_analyses": total_analyses,
            "total_trades": go_count,
            "win_rate": win_rate_str,
            "total_return": total_return,
            "current_equity": current_equity_str,
            "sharpe_ratio": sharpe_ratio,
            "last_updated": datetime.now().isoformat()
        }

@app.get("/api/equity-history")
async def get_equity_history():
    """
    Get equity history for the line chart from account_snapshot table.
    """
    from datetime import datetime
    
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT 
                asof_ts::date as date,
                equity
            FROM account_snapshot
            WHERE account_id = 'main'
            ORDER BY asof_ts ASC
            LIMIT 365
        """)
        
        if not rows:
            # Fallback: return starting equity
            return [{
                "date": datetime.now().strftime("%Y-%m-%d"),
                "equity": 100000
            }]
        
        return [
            {
                "date": row["date"].isoformat(),
                "equity": float(row["equity"])
            }
            for row in rows
        ]

@app.get("/api/decisions")
async def get_decisions(
    limit: int = 100,
    offset: int = 0,
    symbol: str = None,
    decision: str = None
):
    """
    Get list of trading decisions from analysis_log table.
    """
    async with get_connection() as conn:
        # Build query with optional filters
        query = "SELECT analysis_id, symbol, analysis_time, trigger_type, final_decision, decision_reason FROM analysis_log"
        conditions = []
        params = []
        param_idx = 1
        
        if symbol:
            conditions.append(f"symbol = ${param_idx}")
            params.append(symbol)
            param_idx += 1
        
        if decision:
            conditions.append(f"final_decision = ${param_idx}")
            params.append(decision.upper())
            param_idx += 1
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += f" ORDER BY analysis_time DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
        params.extend([limit, offset])
        
        rows = await conn.fetch(query, *params)
        
        return [
            {
                "analysis_id": str(row["analysis_id"]),
                "symbol": row["symbol"],
                "analysis_time": row["analysis_time"].isoformat(),
                "trigger_type": row["trigger_type"],
                "final_decision": row["final_decision"],
                "decision_reason": row["decision_reason"]
            }
            for row in rows
        ]

@app.get("/api/decisions/{analysis_id}")
async def get_decision_details(analysis_id: str):
    """
    Get full details of a single analysis including all agent outputs from database.
    """
    import json
    
    async with get_connection() as conn:
        row = await conn.fetchrow("""
            SELECT *
            FROM analysis_log
            WHERE analysis_id = $1
        """, analysis_id)
        
        if not row:
            raise HTTPException(status_code=404, detail="Analysis not found")
        
        # Helper to parse JSONB fields
        def parse_json(value):
            if value is None:
                return {}
            if isinstance(value, str):
                return json.loads(value)
            return value
        
        return {
            "analysis_id": str(row["analysis_id"]),
            "symbol": row["symbol"],
            "analysis_time": row["analysis_time"].isoformat(),
            "trigger_type": row["trigger_type"],
            "final_decision": row["final_decision"],
            "decision_reason": row["decision_reason"],
            "trigger_detail": parse_json(row.get("trigger_detail")),
            "topdown_output": parse_json(row.get("topdown_output")),
            "context_output": parse_json(row.get("context_output")),
            "chart_output": parse_json(row.get("chart_output")),
            "fundamental_output": parse_json(row.get("fundamental_output")),
            "idea_generator_output": parse_json(row.get("idea_generator_output")),
            "exit_policy_output": parse_json(row.get("exit_policy_output")),
            "red_team_output": parse_json(row.get("red_team_output")),
            "decision_output": parse_json(row.get("decision_output")),
            "position_manager_output": parse_json(row.get("position_manager_output")),
            "risk_model_output": parse_json(row.get("risk_model_output")),
        }


@app.get("/api/positions")
async def get_positions():
    """
    Get current portfolio positions/holdings from database.
    """
    try:
        positions = await get_open_positions(refresh_prices=True)
        
        # Transform to frontend format
        result = []
        for pos in positions:
            # Calculate shares - try actual shares first, then calculate from size_pct
            shares = pos.get("shares")
            if shares is None and pos.get("size_pct"):
                # Rough approximation if we don't have actual shares
                # This shouldn't happen in production but provides fallback
                shares = 100  # Placeholder
            
            shares_value = float(shares) if shares else 0
            entry_price = float(pos["entry_price"])
            current_price = float(pos["current_price"])
            
            result.append({
                "symbol": pos["symbol"],
                "shares": shares_value,
                "entry_price": entry_price,
                "current_price": current_price,
                "market_value": current_price * shares_value,
                "unrealized_pnl": float(pos.get("unrealized_pnl", 0)),
                "unrealized_pnl_pct": float(pos.get("unrealized_pnl_pct", 0)),
                "entry_date": pos["entry_date"].isoformat() if pos.get("entry_date") else None,
                "days_held": pos.get("days_held", 0)
            })
        
        return result
    except Exception as e:
        logger.error(f"Error fetching positions: {e}")
        import traceback
        traceback.print_exc()
        # Return empty array instead of 500 error
        return []


@app.get("/api/trading-history")
async def get_trading_history(limit: int = 50):
    """
    Get completed trading history from database.
    """
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT 
                symbol,
                direction,
                entry_price,
                exit_price,
                entry_date,
                exit_date,
                shares,
                realized_pnl,
                realized_pnl_pct,
                exit_type,
                exit_reason
            FROM position
            WHERE status = 'CLOSED'
            ORDER BY exit_date DESC
            LIMIT $1
        """, limit)
        
        result = []
        for row in rows:
            # Calculate hold period
            entry = row["entry_date"]
            exit_date = row["exit_date"]
            hold_period = (exit_date - entry).days if exit_date and entry else 0
            
            result.append({
                "symbol": row["symbol"],
                "action": "SELL" if row.get("direction") == "SHORT" else "BUY",
                "shares": float(row["shares"]) if row["shares"] else 0,
                "entry_price": float(row["entry_price"]),
                "exit_price": float(row["exit_price"]) if row["exit_price"] else 0,
                "entry_date": entry.isoformat() if entry else None,
                "exit_date": exit_date.isoformat() if exit_date else None,
                "hold_period": hold_period,
                "pnl": float(row["realized_pnl"]) if row["realized_pnl"] else 0,
                "pnl_pct": float(row["realized_pnl_pct"]) if row["realized_pnl_pct"] else 0,
            })
        
        return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
