# Swing-Trade Multi‑Agent System (LLM‑Only) — Optimized Implementation

> **Scope:** 50 mega-cap tickers from S&P 500  
> **LLM:** DeepSeek V3.2 via OpenRouter — cost-effective, strong structured JSON output  
> **Orchestration:** LangGraph for agent graph execution  
> **Style:** Swing trades (10–45 trading days)  
> **Key optimizations:** Parallelization, sector-aware caching, portfolio coordination

---

## 1) Ticker Universe & Sector Groupings

### 1.1 Universe definition

```json
{
  "universe_id": "MEGA50",
  "size": 50,
  "sector_etfs": {
    "Information Technology": "XLK",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI"
  }
}
```

### 1.2 Full ticker list by sector

| Sector | Tickers | Count |
|--------|---------|-------|
| **Information Technology** | NVDA, AAPL, MSFT, AVGO, ORCL, PLTR, AMD, MU, CSCO, IBM, CRM, APP, LRCX, AMAT | 14 |
| **Financials** | BRK.B, JPM, V, MA, BAC, WFC, MS, GS, AXP, C | 10 |
| **Health Care** | LLY, JNJ, ABBV, UNH, MRK, TMO, ABT | 7 |
| **Consumer Discretionary** | AMZN, TSLA, HD, MCD | 4 |
| **Communication Services** | GOOGL, GOOG, META, NFLX, TMUS | 5 |
| **Consumer Staples** | WMT, COST, PG, KO, PM | 5 |
| **Energy** | XOM, CVX | 2 |
| **Industrials** | GE, CAT, RTX | 3 |

### 1.3 Correlation clusters (for portfolio constraints)

```json
{
  "high_correlation_clusters": [
    {"cluster_id": "SEMIS", "tickers": ["NVDA", "AMD", "MU", "AVGO", "LRCX", "AMAT"], "max_simultaneous": 2},
    {"cluster_id": "FANMAG", "tickers": ["AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "NFLX"], "max_simultaneous": 3},
    {"cluster_id": "BIG_BANKS", "tickers": ["JPM", "BAC", "WFC", "C", "MS", "GS"], "max_simultaneous": 2},
    {"cluster_id": "CARD_NETWORKS", "tickers": ["V", "MA", "AXP"], "max_simultaneous": 1}
  ]
}
```

---

## 2) OpenRouter & LangGraph Configuration

### 2.1 OpenRouter setup

```python
# eiqora/config/settings.py
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
SITE_URL: str = "https://github.com/eiqora"  # For OpenRouter rankings
SITE_NAME: str = "Eiqora"

# Model Selection
DEFAULT_MODEL: str = "deepseek/deepseek-v3.2"
FAST_MODEL: str = "deepseek/deepseek-v3.2"
```

### 2.2 OpenRouter client configuration

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
    default_headers={
        "HTTP-Referer": "https://github.com/eiqora",
        "X-Title": "Eiqora",
    }
)

# Usage with structured output
response = await client.chat.completions.create(
    model="deepseek/deepseek-v3.2",
    messages=[{"role": "user", "content": prompt}],
    response_format={"type": "json_object"},
    temperature=0.1,  # Low temp for consistent structured output
)
```

### 2.3 Why LangGraph for orchestration

| Consideration | LangGraph | Raw asyncio | Verdict |
|---------------|-----------|-------------|---------|
| **State management** | Built-in persistent state across nodes | Manual dict passing | ✅ LangGraph |
| **Conditional routing** | Native `add_conditional_edges()` | Manual if/else | ✅ LangGraph |
| **Parallel execution** | Native fan-out with `Send()` | `asyncio.gather()` | 🟡 Similar |
| **Checkpointing** | Built-in with SQLite/Postgres | Manual implementation | ✅ LangGraph |
| **Debugging** | LangSmith integration, visual traces | Print statements | ✅ LangGraph |
| **Retry/error handling** | Node-level retry policies | Manual try/except | ✅ LangGraph |
| **Learning curve** | Moderate | Low | 🟡 asyncio simpler |

**Recommendation:** Use LangGraph. The agent graph has complex conditional flows (early filtering, branching based on `needs_extraction`, portfolio coordination), and LangGraph's state management + checkpointing will save significant implementation effort.

### 2.4 LangGraph graph definition

```python
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing import TypedDict, Annotated, Literal

class SwingTradeState(TypedDict):
    request_id: str
    symbol: str
    sector: str
    asof_time: str
    
    # Agent outputs (accumulated)
    triage: dict | None
    facts: dict | None
    context: dict | None
    topdown: dict | None  # Cached globally
    chart: dict | None
    ideas: dict | None
    rules: list[dict] | None
    stats: list[dict] | None
    decision: dict | None
    veto: dict | None
    narrative: dict | None
    
    # Control flow
    needs_extraction: bool
    should_continue: bool
    filter_reason: str | None

def create_swing_trade_graph():
    graph = StateGraph(SwingTradeState)
    
    # Add nodes
    graph.add_node("event_triage", event_triage_node)
    graph.add_node("context", context_node)
    graph.add_node("chart", chart_node)
    graph.add_node("event_extractor", event_extractor_node)
    graph.add_node("early_filter", early_filter_node)
    graph.add_node("idea_generator", idea_generator_node)
    graph.add_node("exit_policy", exit_policy_node)
    graph.add_node("analog_planner", analog_planner_node)
    graph.add_node("decision", decision_node)
    graph.add_node("sanity_veto", sanity_veto_node)
    graph.add_node("narrative", narrative_node)
    
    # Phase 1: Parallel initial agents
    graph.add_edge("__start__", "event_triage")
    graph.add_edge("__start__", "context")
    graph.add_edge("__start__", "chart")
    
    # Conditional extraction
    graph.add_conditional_edges(
        "event_triage",
        lambda s: "event_extractor" if s["needs_extraction"] else "early_filter",
        {"event_extractor": "event_extractor", "early_filter": "early_filter"}
    )
    graph.add_edge("event_extractor", "early_filter")
    graph.add_edge("context", "early_filter")
    graph.add_edge("chart", "early_filter")
    
    # Early filter gate
    graph.add_conditional_edges(
        "early_filter",
        lambda s: "idea_generator" if s["should_continue"] else END,
        {"idea_generator": "idea_generator", END: END}
    )
    
    # Sequential idea → exit → analog → decision
    graph.add_edge("idea_generator", "exit_policy")
    graph.add_edge("exit_policy", "analog_planner")
    graph.add_edge("analog_planner", "decision")
    graph.add_edge("decision", "sanity_veto")
    
    # Final gate
    graph.add_conditional_edges(
        "sanity_veto",
        lambda s: "narrative" if s["decision"]["decision"] == "SIGNAL" and not s["veto"]["veto"] else END,
        {"narrative": "narrative", END: END}
    )
    graph.add_edge("narrative", END)
    
    return graph.compile()
```

### 2.5 Rate limit & cost budgets

```yaml
rate_limits:
  openrouter:
    requests_per_minute: 500  # Tier-dependent
    tokens_per_minute: 10_000_000
    max_concurrent_requests: 50

cost_budget:
  per_full_analysis_sweep: $0.50  # 50 tickers × ~12 agents × ~800 tokens avg
  daily_cap: $10.00
  
# Estimated token usage per agent (avg)
agent_token_estimates:
  event_triage: {input: 500, output: 150}
  event_extractor: {input: 2000, output: 400}
  context: {input: 600, output: 200}
  topdown: {input: 800, output: 300}  # CACHED per sweep
  chart: {input: 1200, output: 250}
  idea_generator: {input: 1500, output: 400}
  exit_policy: {input: 1000, output: 500}
  analog_planner: {input: 800, output: 300}
  decision: {input: 600, output: 200}
  sanity_veto: {input: 400, output: 100}
  narrative: {input: 1200, output: 600}
```

### 2.6 Prompting strategy

```yaml
global_system_prompt: |
  You are a financial analysis agent in a multi-agent trading system.
  CRITICAL RULES:
  1. Return ONLY valid JSON matching the provided schema. No markdown, no explanation.
  2. Never invent performance metrics (win rate, expected return). Use only values from Stats Service.
  3. If uncertain, set quality flags; do not guess.
  4. All times are UTC. Never use data after asof_time.

json_formatting: |
  Use JSON mode (response_format: {"type": "json_object"})
  Structure prompts with static system context first, dynamic content last for caching benefits.
```

---

## 3) Data Layer Integration

The agent system connects to the existing `data_collection` pipeline via a tools/services layer. All tool functions query the Postgres database populated by scheduled pipelines.

### 3.1 Database tables → Tool mapping

| Tool Function | Database Table(s) | Pipeline Source |
|---------------|-------------------|-----------------|
| `get_prices(symbol, window)` | `market_bar_daily` | `stooq_daily`, `alpaca_ohlcv` |
| `get_documents(symbol, window, filters)` | `document`, `document_chunk` | `sec_edgar`, `gdelt`, `yfinance_news` |
| `get_events(symbol, window, filters)` | `sec_filing`, `document` | `sec_edgar`, `sec_rss` |
| `get_indicators(symbol, window)` | Computed from `market_bar_daily` | On-demand |
| `get_macro(series_ids)` | FRED tables | `fred_macro` |
| `get_earnings_calendar(symbol)` | Earnings tables | `earnings` |
| `run_analog_stats(plan, rule, asof)` | `analog_event`, `market_bar_daily` | Stats Service |
| `get_insider_transactions(symbol)` | `insider_transaction` | `sec_edgar` (Form 4) |

### 3.2 Tool implementations

```python
# eiqora/agents/tools/prices.py
from data_collection.db.connection import get_connection

async def get_prices(symbol: str, window_days: int, asof_time: datetime) -> list[dict]:
    """Fetch daily OHLCV bars from market_bar_daily."""
    conn = await get_connection()
    rows = await conn.fetch("""
        SELECT date, open, high, low, close, volume, vwap
        FROM market_bar_daily
        WHERE symbol = $1
          AND date <= $2::date
          AND date >= ($2::date - interval '1 day' * $3)
        ORDER BY date ASC
    """, symbol, asof_time, window_days)
    return [dict(r) for r in rows]


async def get_indicators(symbol: str, window_days: int, asof_time: datetime) -> dict:
    """Compute technical indicators from price data."""
    prices = await get_prices(symbol, window_days + 50, asof_time)  # Extra for MA lookback
    df = pd.DataFrame(prices)
    
    close = df["close"].astype(float)
    return {
        "ma20": float(close.rolling(20).mean().iloc[-1]),
        "ma50": float(close.rolling(50).mean().iloc[-1]),
        "ma200": float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None,
        "rv20": float(np.log(close / close.shift(1)).rolling(20).std().iloc[-1]),  # Realized vol
        "atr14": float(_atr(df, 14).iloc[-1]),
        "ret_20d": float((close.iloc[-1] / close.iloc[-20] - 1)) if len(close) >= 20 else None,
        "ret_60d": float((close.iloc[-1] / close.iloc[-60] - 1)) if len(close) >= 60 else None,
        "volume_z_20d": float((df["volume"].iloc[-1] - df["volume"].rolling(20).mean().iloc[-1]) 
                              / df["volume"].rolling(20).std().iloc[-1]),
    }
```

```python
# eiqora/agents/tools/documents.py

async def get_documents(
    symbol: str,
    window_hours: int,
    asof_time: datetime,
    doc_types: list[str] | None = None,
    limit: int = 20
) -> list[dict]:
    """Fetch recent documents for a symbol from document table."""
    conn = await get_connection()
    
    type_filter = ""
    if doc_types:
        type_filter = f"AND doc_type = ANY($5)"
    
    rows = await conn.fetch(f"""
        SELECT doc_id, source, doc_type, ticker, title, published_at, url, text
        FROM document
        WHERE ticker = $1
          AND published_at <= $2
          AND published_at >= $2 - interval '1 hour' * $3
          {type_filter}
        ORDER BY published_at DESC
        LIMIT $4
    """, symbol, asof_time, window_hours, limit, *([doc_types] if doc_types else []))
    
    return [dict(r) for r in rows]


async def get_document_chunks_by_similarity(
    query_embedding: list[float],
    symbol: str | None = None,
    limit: int = 10
) -> list[dict]:
    """Vector similarity search on document_chunk table."""
    conn = await get_connection()
    
    symbol_filter = "AND d.ticker = $3" if symbol else ""
    
    rows = await conn.fetch(f"""
        SELECT dc.chunk_id, dc.doc_id, dc.text, dc.chunk_index,
               d.ticker, d.title, d.doc_type, d.published_at,
               dc.embedding <-> $1::vector AS distance
        FROM document_chunk dc
        JOIN document d ON dc.doc_id = d.doc_id
        WHERE dc.active = true
          {symbol_filter}
        ORDER BY dc.embedding <-> $1::vector
        LIMIT $2
    """, query_embedding, limit, *([symbol] if symbol else []))
    
    return [dict(r) for r in rows]
```

```python
# eiqora/agents/tools/events.py

async def get_sec_filings(
    symbol: str,
    window_days: int,
    asof_time: datetime,
    form_types: list[str] | None = None
) -> list[dict]:
    """Fetch SEC filings for a symbol."""
    conn = await get_connection()
    
    # Get CIK for symbol from security table
    cik_row = await conn.fetchrow("""
        SELECT cik FROM security WHERE ticker = $1 AND is_primary = true
    """, symbol)
    
    if not cik_row:
        return []
    
    type_filter = "AND form_type = ANY($4)" if form_types else ""
    
    rows = await conn.fetch(f"""
        SELECT accession, cik, form_type, filed_at, report_period, 
               is_amendment, primary_doc_url
        FROM sec_filing
        WHERE cik = $1
          AND filed_at <= $2::date
          AND filed_at >= $2::date - interval '1 day' * $3
          {type_filter}
        ORDER BY filed_at DESC
    """, cik_row["cik"], asof_time, window_days, *([form_types] if form_types else []))
    
    return [dict(r) for r in rows]


async def get_macro_indicators(series_ids: list[str], asof_time: datetime) -> dict:
    """Fetch FRED macro indicators."""
    conn = await get_connection()
    
    # Assuming FRED data stored in a macro_series table
    rows = await conn.fetch("""
        SELECT series_id, date, value
        FROM fred_observation
        WHERE series_id = ANY($1)
          AND date <= $2::date
        ORDER BY series_id, date DESC
    """, series_ids, asof_time)
    
    # Return latest value per series
    result = {}
    seen = set()
    for r in rows:
        if r["series_id"] not in seen:
            result[r["series_id"]] = {"value": float(r["value"]), "date": r["date"]}
            seen.add(r["series_id"])
    return result
```

### 3.3 Analog Event table (new)

The Stats/Simulator requires labeled historical setups. Create a new table:

```sql
-- Add to data_collection/db/init/002_analog_events.sql

CREATE TABLE IF NOT EXISTS analog_event (
    analog_id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    event_date DATE NOT NULL,
    event_type TEXT NOT NULL,  -- PULLBACK_MA50, BREAKOUT_20D, etc.
    
    -- Context at event time
    sector_etf TEXT,
    vol_bucket TEXT,           -- LOW, MED, HIGH
    trend_bucket TEXT,         -- UP, DOWN, SIDEWAYS
    regime TEXT,               -- RISK_ON, RISK_OFF, HIGH_VOL, etc.
    
    -- For analog matching
    entry_price NUMERIC,
    invalidation_price NUMERIC,
    
    -- Metadata
    labeled_by TEXT,           -- 'chart_agent_v1', 'manual', etc.
    label_confidence NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE (symbol, event_date, event_type)
);

CREATE INDEX idx_analog_event_type ON analog_event (event_type);
CREATE INDEX idx_analog_event_sector ON analog_event (sector_etf);
CREATE INDEX idx_analog_event_regime ON analog_event (regime);
CREATE INDEX idx_analog_event_date ON analog_event (event_date);
```

### 3.4 Stats Service implementation

```python
# eiqora/agents/services/stats.py

async def run_analog_stats(
    analog_plan: dict,
    trade_rule: dict,
    asof_time: datetime
) -> dict:
    """
    Deterministic analog evaluation. NO LLM involved.
    Returns performance metrics for the trade rule applied to analog events.
    """
    conn = await get_connection()
    
    # Build query from analog_plan
    filters = analog_plan["filters"]
    query = """
        SELECT a.analog_id, a.symbol, a.event_date, a.entry_price, a.invalidation_price
        FROM analog_event a
        WHERE a.event_type = $1
          AND a.event_date < $2::date  -- Point-in-time: only past events
          AND a.event_date >= $2::date - interval '1 year' * $3
    """
    params = [analog_plan["event_type"], asof_time, analog_plan.get("lookback_years", 8)]
    
    # Add optional filters
    param_idx = 4
    if filters.get("sector_etf"):
        query += f" AND a.sector_etf = ${param_idx}"
        params.append(filters["sector_etf"])
        param_idx += 1
    if filters.get("vol_bucket"):
        query += f" AND a.vol_bucket = ${param_idx}"
        params.append(filters["vol_bucket"])
        param_idx += 1
    if filters.get("trend_bucket"):
        query += f" AND a.trend_bucket = ${param_idx}"
        params.append(filters["trend_bucket"])
        param_idx += 1
    if filters.get("regime"):
        query += f" AND a.regime = ${param_idx}"
        params.append(filters["regime"])
        param_idx += 1
    
    analogs = await conn.fetch(query, *params)
    
    # If insufficient samples, apply relaxation
    min_samples = analog_plan.get("min_samples", 30)
    if len(analogs) < min_samples:
        analogs = await _relax_and_refetch(conn, analog_plan, asof_time, min_samples)
    
    if len(analogs) < 8:  # Hard minimum
        return {"status": "INSUFFICIENT_DATA", "sample_size": len(analogs)}
    
    # Simulate trade rule on each analog
    results = []
    for analog in analogs:
        outcome = await _simulate_trade(
            symbol=analog["symbol"],
            entry_date=analog["event_date"],
            entry_price=float(analog["entry_price"]),
            trade_rule=trade_rule,
            asof_time=asof_time
        )
        if outcome:
            results.append(outcome)
    
    # Compute statistics
    returns = [r["return_pct"] for r in results]
    wins = [r for r in returns if r > 0]
    
    return {
        "status": "OK",
        "sample_size": len(results),
        "win_rate": len(wins) / len(results) if results else 0,
        "expected_return": np.mean(returns) if returns else 0,
        "median_return": np.median(returns) if returns else 0,
        "p10": np.percentile(returns, 10) if results else 0,
        "p90": np.percentile(returns, 90) if results else 0,
        "avg_hold_days": np.mean([r["hold_days"] for r in results]) if results else 0,
        "stability": _compute_stability(results, asof_time)
    }


async def _simulate_trade(
    symbol: str,
    entry_date: date,
    entry_price: float,
    trade_rule: dict,
    asof_time: datetime
) -> dict | None:
    """Simulate a single trade using the trade rule DSL."""
    conn = await get_connection()
    
    # Fetch price bars after entry
    exit_config = trade_rule["exit"]
    max_days = exit_config.get("time_stop_days", 45)
    
    bars = await conn.fetch("""
        SELECT date, open, high, low, close
        FROM market_bar_daily
        WHERE symbol = $1
          AND date > $2
          AND date <= $2 + interval '1 day' * $3
        ORDER BY date ASC
    """, symbol, entry_date, max_days + 5)
    
    if not bars:
        return None
    
    # Get volatility at entry for bracket calculation
    vol = await _get_volatility_at_date(symbol, entry_date, exit_config.get("vol_basis", "RV20"))
    
    tp_level = entry_price * (1 + vol * exit_config.get("tp_mult", 4))
    sl_level = entry_price * (1 - vol * exit_config.get("sl_mult", 2))
    
    # Walk through bars to find exit
    for i, bar in enumerate(bars):
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        bar_close = float(bar["close"])
        
        # Check stop loss
        if bar_low <= sl_level:
            return {
                "exit_type": "STOP_LOSS",
                "exit_price": sl_level,
                "return_pct": (sl_level / entry_price) - 1,
                "hold_days": i + 1
            }
        
        # Check take profit
        if bar_high >= tp_level:
            return {
                "exit_type": "TAKE_PROFIT",
                "exit_price": tp_level,
                "return_pct": (tp_level / entry_price) - 1,
                "hold_days": i + 1
            }
        
        # Check time stop
        if i + 1 >= exit_config.get("time_stop_days", 45):
            return {
                "exit_type": "TIME_STOP",
                "exit_price": bar_close,
                "return_pct": (bar_close / entry_price) - 1,
                "hold_days": i + 1
            }
    
    # No exit triggered (shouldn't happen with time stop)
    return None
```

### 3.5 Real-time trigger architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Data Collection Pipelines                     │
│  (Scheduled: Stooq 6am, SEC RSS 15min, GDELT 15min, etc.)        │
└───────────────────────────────┬─────────────────────────────────┘
                                │ INSERT INTO document/sec_filing
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Postgres NOTIFY Trigger                       │
├─────────────────────────────────────────────────────────────────┤
│  CREATE OR REPLACE FUNCTION notify_new_document()               │
│  RETURNS trigger AS $$                                          │
│  BEGIN                                                          │
│    PERFORM pg_notify('new_document', json_build_object(         │
│      'doc_id', NEW.doc_id,                                      │
│      'ticker', NEW.ticker,                                      │
│      'doc_type', NEW.doc_type                                   │
│    )::text);                                                    │
│    RETURN NEW;                                                  │
│  END;                                                           │
│  $$ LANGUAGE plpgsql;                                           │
│                                                                 │
│  CREATE TRIGGER document_insert_notify                          │
│  AFTER INSERT ON document                                       │
│  FOR EACH ROW EXECUTE FUNCTION notify_new_document();           │
└───────────────────────────────┬─────────────────────────────────┘
                                │ pg_notify('new_document', ...)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Event Listener Service                        │
├─────────────────────────────────────────────────────────────────┤
│  async def listen_for_documents():                              │
│      conn = await asyncpg.connect(DATABASE_URL)                 │
│      await conn.add_listener('new_document', handle_new_doc)    │
│                                                                 │
│  async def handle_new_doc(conn, pid, channel, payload):         │
│      doc = json.loads(payload)                                  │
│      if doc['ticker'] in MEGA50_TICKERS:                        │
│          if doc['doc_type'] in HIGH_PRIORITY_TYPES:             │
│              await trigger_agent_sweep(doc['ticker'])           │
└─────────────────────────────────────────────────────────────────┘
```

### 3.6 Tool access policy (concrete implementation)

```python
# eiqora/agents/tools/__init__.py

from .prices import get_prices, get_indicators
from .documents import get_documents, get_document_chunks_by_similarity
from .events import get_sec_filings, get_macro_indicators
from .stats import run_analog_stats

# Tool registry for LangGraph nodes
AGENT_TOOLS = {
    "get_prices": get_prices,
    "get_indicators": get_indicators,
    "get_documents": get_documents,
    "get_document_chunks": get_document_chunks_by_similarity,
    "get_sec_filings": get_sec_filings,
    "get_macro": get_macro_indicators,
    "run_analog_stats": run_analog_stats,
}

# Per-agent tool permissions
TOOL_PERMISSIONS = {
    "event_triage": ["get_documents", "get_sec_filings"],
    "event_extractor": ["get_documents", "get_document_chunks"],
    "context": ["get_prices", "get_indicators"],
    "topdown": ["get_macro", "get_prices"],
    "chart": ["get_prices", "get_indicators"],
    "idea_generator": ["get_documents"],  # For context only
    "exit_policy": ["get_indicators"],
    "analog_planner": [],  # No tools, just outputs plan
    "decision": ["run_analog_stats"],
    "sanity_veto": ["get_prices", "get_documents"],
    "narrative": [],  # No tools, just formats
}
```

---

## 4) Optimized Agent Execution Graph

### 4.1 Parallelization strategy

```
┌─────────────────────────────────────────────────────────────────┐
│                    PHASE 0: PRE-SWEEP (once per day)            │
├─────────────────────────────────────────────────────────────────┤
│  TopDown Agent → CACHE result for all 50 tickers               │
│  (Market regime + sector regimes - same for everyone)           │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│           PHASE 1: PARALLEL PER-TICKER (50 concurrent)          │
├─────────────────────────────────────────────────────────────────┤
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                │
│  │Event Triage│  │  Context   │  │   Chart    │  ── parallel ──│
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘                │
│        │               │               │                        │
│        └───────────────┴───────────────┘                        │
│                        │                                        │
│                        ▼                                        │
│  ┌────────────────────────────────────┐                        │
│  │ Event Extractor (if needs_extraction)│                       │
│  └────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│           PHASE 2: IDEA GENERATION (still parallel per ticker)  │
├─────────────────────────────────────────────────────────────────┤
│  Idea Generator → Exit Policy → Analog Planner                 │
│  (sequential within ticker, parallel across tickers)            │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│           PHASE 3: STATS (deterministic, highly parallel)       │
├─────────────────────────────────────────────────────────────────┤
│  run_analog_stats() for each (ticker, rule) pair                │
│  (pure CPU, no LLM - can run 200+ concurrent)                   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│           PHASE 4: DECISION + VETO (parallel per ticker)        │
├─────────────────────────────────────────────────────────────────┤
│  Decision Agent → Sanity/Veto Agent                             │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│           PHASE 5: PORTFOLIO COORDINATION (single, sequential)  │
├─────────────────────────────────────────────────────────────────┤
│  Portfolio Coordinator Agent (NEW)                              │
│  - Enforces correlation cluster limits                          │
│  - Enforces sector concentration caps                           │
│  - Ranks and selects top N ideas                                │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│           PHASE 6: NARRATIVE (parallel for selected ideas)      │
├─────────────────────────────────────────────────────────────────┤
│  Narrative Agent (only for ideas passing portfolio gate)        │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Early filtering gates

To reduce LLM calls, add filtering between phases:

```yaml
early_filters:
  after_phase_1:
    # Skip rest of pipeline if no setup found
    - condition: "chart.setup_type == 'NO_SETUP'"
      action: "SKIP_TICKER"
      log: true
    
    # Skip if context shows extreme adverse conditions  
    - condition: "context.state_tags contains 'DOWNTREND' AND context.vol_basis.value > 0.04"
      action: "SKIP_TICKER"
      log: true
    
    # Skip if TopDown vetoes new positions in this sector
    - condition: "topdown.policy_hints.avoid_new_longs AND idea.direction == 'LONG'"
      action: "SKIP_TICKER"
      log: true

estimated_filter_rate: 0.60  # ~30 tickers proceed past Phase 1
```

---

## 5) New Agent: Portfolio Coordinator

### 5.1 Purpose

The original plan lacked portfolio-level coordination. This agent runs **once per sweep** after all individual ticker decisions, enforcing:

1. Correlation cluster limits
2. Sector concentration caps
3. Total portfolio exposure
4. Idea ranking and selection

### 5.2 Input

```json
{
  "candidate_signals": [
    {
      "symbol": "NVDA",
      "sector": "Information Technology",
      "clusters": ["SEMIS", "FANMAG"],
      "direction": "LONG",
      "metrics": {
        "win_rate": 0.62,
        "expected_return": 0.028,
        "p10": -0.018
      }
    }
  ],
  "current_positions": [
    {"symbol": "AMD", "direction": "LONG", "days_held": 5}
  ],
  "portfolio_constraints": {
    "max_positions": 10,
    "max_per_sector": 3,
    "max_per_cluster": {"SEMIS": 2, "FANMAG": 3, "BIG_BANKS": 2, "CARD_NETWORKS": 1},
    "max_gross_exposure_pct": 100
  }
}
```

### 5.3 Output

```json
{
  "approved_signals": [
    {"symbol": "NVDA", "rank": 1, "reason": "Best expected return, cluster limit OK"}
  ],
  "rejected_signals": [
    {"symbol": "AMD", "reason": "SEMIS cluster already has NVDA + MU"}
  ],
  "portfolio_state": {
    "current_positions": 8,
    "positions_after_signals": 9,
    "sector_breakdown": {"Information Technology": 4, "Financials": 2}
  }
}
```

---

## 6) Caching Strategy

### 6.1 TopDown Agent caching

TopDown output is **identical for all 50 tickers** within a time window:

```yaml
topdown_cache:
  key_format: "topdown:{date}:{hour}"
  ttl_hours: 4  # Refresh 6x per trading day
  scope: global  # Same cache for all tickers

# Example: Run TopDown once at 09:30, cache until 13:30
# Saves 49 redundant LLM calls per sweep
```

### 6.2 Sector regime caching

Sector-specific regime data (relative strength, sector ETF trend) cached per sector:

```yaml
sector_cache:
  key_format: "sector_regime:{sector_etf}:{date}"
  ttl_hours: 24
  precompute_for: ["XLK", "XLF", "XLV", "XLY", "XLC", "XLP", "XLE", "XLI"]
```

### 6.3 Prompt structure for caching

Structure prompts to maximize cache benefits (many providers cache repeated prefixes):

```
[ STATIC SYSTEM PROMPT | STATIC SCHEMA | CACHED TOPDOWN | DYNAMIC TICKER DATA ]
       ─────────────────────────────────────────────────  ──────────────────
                 CACHEABLE (reused across calls)              PER-TICKER
```

---

## 7) Analog Query Relaxation (Standardized)

### 7.1 Default relaxation order

When `sample_size < min_samples`, relax filters in this order:

```json
{
  "default_relaxation_order": [
    {"drop": "regime", "after": "sector_etf regime vol_bucket trend_bucket"},
    {"drop": "vol_bucket", "after": "sector_etf trend_bucket"},
    {"drop": "sector_etf", "after": "trend_bucket"},
    {"drop": "trend_bucket", "after": "event_type only"}
  ],
  "min_samples_thresholds": {
    "primary": 30,
    "relaxed": 15,
    "minimum_acceptable": 8
  },
  "on_insufficient_samples": "INSUFFICIENT_DATA"
}
```

### 7.2 Logging for sparse analogs

```json
{
  "log_sparse_analogs": true,
  "sparse_threshold": 15,
  "output": "sparse_analog_log.jsonl",
  "fields": ["symbol", "event_type", "filters", "sample_size", "asof_time"]
}
```

---

## 8) Updated Contracts

### 8.1 AnalysisRequest (updated for MEGA50)

```json
{
  "request_id": "uuid",
  "symbol": "NVDA",
  "sector": "Information Technology",
  "sector_etf": "XLK",
  "clusters": ["SEMIS"],
  "asof_time": "2025-12-30T15:00:00Z",
  "trigger": {
    "type": "SEC_8K|IR_RSS|NEWS|MACRO|CHART_SETUP|SCHEDULED_RECHECK",
    "refs": ["doc:...", "event:...", "scan:..."],
    "notes": "optional"
  },
  "constraints": {
    "universe": "MEGA50",
    "trade_style": "SWING",
    "direction_allowed": ["LONG", "SHORT"],
    "max_candidates": 5
  },
  "cached_topdown_ref": "topdown:2025-12-30:15"
}
```

### 8.2 Decision Agent (updated gates for mega-caps)

Mega-caps have tighter spreads and higher liquidity. Adjust gates:

```yaml
decision_gates:
  mega50:
    sample_size_min: 25  # Slightly lower OK due to focused universe
    win_rate_min: 0.55   # Mega-caps have tighter edge; accept slightly lower
    expected_return_min: 0.008  # ~0.8% after spreads (tight)
    p10_max: -0.035  # Tighter drawdown limit
    
  # Compare to original S&P 500:
  sp500_default:
    sample_size_min: 30
    win_rate_min: 0.56
    expected_return_min: 0.010
    p10_max: -0.04
```

---

## 9) Optimized Orchestration Pseudocode

```python
async def run_sweep(tickers: List[str], asof_time: datetime):
    # PHASE 0: Pre-compute TopDown (once, cached)
    topdown = await cache.get_or_compute(
        key=f"topdown:{asof_time.date()}:{asof_time.hour}",
        fn=lambda: TopDownAgent(asof_time)
    )
    
    # PHASE 1-4: Parallel per-ticker processing
    async def process_ticker(symbol: str):
        # Phase 1: Parallel within ticker
        triage, context, chart = await asyncio.gather(
            EventTriage(symbol, asof_time),
            ContextAgent(symbol, asof_time),
            ChartAgent(symbol, asof_time)
        )
        
        # Early filter
        if chart.setup_type == "NO_SETUP":
            return None
        if topdown.policy_hints.avoid_new_longs and should_be_long(context):
            return None
        
        # Phase 1b: Extraction if needed
        facts = None
        if triage.needs_extraction:
            facts = await EventExtractor(triage.doc_ids)
        
        # Phase 2: Sequential idea generation
        ideas = await IdeaGenerator(triage, facts, context, topdown, chart)
        if not ideas.candidates:
            return None
            
        rules = await ExitPolicy(ideas, context, topdown, chart)
        
        # Phase 3: Parallel analog eval (non-LLM)
        stats = {}
        plans = [await AnalogPlanner(triage, ideas, context, topdown, chart, rule) 
                 for rule in rules.trade_rules]
        stats = await asyncio.gather(*[
            run_analog_stats(plan, rule, asof_time) 
            for plan, rule in zip(plans, rules.trade_rules)
        ])
        
        # Phase 4: Decision + Veto
        decision = await DecisionAgent(ideas, stats, topdown)
        if decision.decision != "SIGNAL":
            return None
            
        veto = await SanityVeto(symbol, triage, stats, decision)
        if veto.veto:
            return None
            
        return {
            "symbol": symbol,
            "sector": get_sector(symbol),
            "clusters": get_clusters(symbol),
            "decision": decision,
            "stats": stats[decision.selected_rule_index],
            "ideas": ideas,
            "rules": rules
        }
    
    # Run all tickers in parallel (with semaphore for rate limiting)
    semaphore = asyncio.Semaphore(50)  # Max 50 concurrent
    async def limited_process(symbol):
        async with semaphore:
            return await process_ticker(symbol)
    
    results = await asyncio.gather(*[limited_process(t) for t in tickers])
    candidates = [r for r in results if r is not None]
    
    # PHASE 5: Portfolio coordination (single, sequential)
    current_positions = await get_current_positions()
    portfolio_decision = await PortfolioCoordinator(
        candidates, current_positions, PORTFOLIO_CONSTRAINTS
    )
    
    # PHASE 6: Generate narratives for approved signals
    for signal in portfolio_decision.approved_signals:
        candidate = next(c for c in candidates if c["symbol"] == signal["symbol"])
        narrative = await NarrativeAgent(candidate)
        await persist_trade_idea(narrative)
    
    return portfolio_decision
```

---

## 10) Monitoring Enhancements

### 10.1 Batch monitoring (optimized)

Instead of 50 separate Monitor Agent calls, batch process:

```python
async def run_monitor_sweep(active_ideas: List[Idea], asof_time: datetime):
    # Group by sector for efficient context sharing
    by_sector = group_by(active_ideas, key=lambda x: x.sector)
    
    for sector, ideas in by_sector.items():
        # Share sector context across sector
        sector_context = await cache.get(f"sector_regime:{sector}:{asof_time.date()}")
        
        # Batch monitor call (single LLM call per sector)
        monitor_results = await BatchMonitorAgent(ideas, sector_context, asof_time)
        
        for idea, result in zip(ideas, monitor_results):
            if result.action in ["INVALIDATE", "TIGHTEN"]:
                await InvalidationAgent(idea, result)
```

### 10.2 Invalidation event priority

For 50 high-profile tickers, news breaks fast. Prioritize invalidation checks:

```yaml
invalidation_priority:
  HIGH:  # Check within 5 min of market event
    - GUIDANCE_DOWN
    - SEC_INVESTIGATION
    - EARNINGS_MISS
    - CEO_DEPARTURE
    
  MEDIUM:  # Check within 30 min
    - ANALYST_DOWNGRADE
    - SECTOR_SHOCK
    
  LOW:  # Check at next scheduled sweep
    - PRICE_BREACH
    - TIME_STOP_NEAR
```

---

## 11) Deliverables (Updated)

1. **Universe config**: `universe_mega50.json` with tickers, sectors, clusters
2. **Agent registry**: Names, schemas, allowed enums (unchanged from original)
3. **Prompt templates**: Optimized for JSON mode with OpenRouter
4. **JSON Schema validators**: With one-retry wrapper
5. **Data tools layer** (`eiqora/agents/tools/`):
   - `get_prices`, `get_indicators` → `market_bar_daily`
   - `get_documents`, `get_document_chunks` → `document`, `document_chunk`
   - `get_sec_filings`, `get_macro` → `sec_filing`, FRED tables
   - `run_analog_stats` → `analog_event`, Stats Service
6. **Database schema additions**:
   - `analog_event` table (historical setups for Stats/Simulator)
   - Postgres NOTIFY triggers for real-time event-driven runs
7. **LangGraph orchestrator**:
   - StateGraph with conditional edges
   - TopDown caching layer
   - Early filtering gates
   - Portfolio Coordinator integration
   - Checkpointing with SQLite/Postgres
8. **Stats/Simulator**: `run_analog_stats(analog_plan, trade_rule, asof_time)`
9. **Monitor runner**: `run_monitor_sweep(active_ideas, asof_time)` with batching
10. **Dashboard metrics**:
    - LLM cost per sweep
    - Tokens by agent
    - Cache hit rate
    - Filter drop rate by phase

---

## 12) First Milestone (Optimized)

### Week 1: Data Tools + Core Pipeline (5 tickers)
- Implement tool functions (`get_prices`, `get_documents`, `get_indicators`) against `data_collection` tables
- Implement Event Triage → Context → Chart → Idea Generator → Exit Policy
- Use NVDA, AAPL, JPM, XOM, LLY (one per major sector)
- Validate JSON schemas end-to-end with real data from pipelines

### Week 2: Stats + Decision
- Create `analog_event` table and backfill historical setups using Chart Agent
- Implement deterministic Stats/Simulator with real historical data
- Implement Decision Agent with mega-cap gates
- Implement Sanity/Veto

### Week 3: Portfolio + Scale
- Implement Portfolio Coordinator with cluster limits
- Scale to full 50 tickers
- Implement caching layer
- Implement early filtering gates
- Add Postgres NOTIFY triggers for real-time events

### Week 4: Monitoring + Polish
- Implement batch Monitor Agent
- Implement Invalidation Agent
- Add cost/latency dashboards
- Walk-forward backtest on 2024 data

---

## Appendix A: Prompt Template Example

```python
CHART_AGENT_PROMPT = """
You are a technical analysis agent. Analyze the price/volume data and classify the setup.

ALLOWED SETUP TYPES (must use exactly one):
- BREAKOUT_20D, BREAKOUT_60D
- PULLBACK_MA20, PULLBACK_MA50
- BASE_BREAKOUT
- REVERSAL_AFTER_SELL_OFF
- RANGE_FADE_HIGH, RANGE_FADE_LOW
- NO_SETUP

SCHEMA:
{
  "setup_type": "string (from allowed list)",
  "entry_trigger": {"type": "BREAK_YDAY_HIGH|CLOSE_ABOVE_LEVEL", "level": number},
  "invalidation": {"type": "CLOSE_BELOW_LEVEL", "level": number},
  "setup_quality": {"volume_confirm": boolean, "compression": boolean, "score": 0.0-1.0}
}

Return ONLY valid JSON. No explanation.

SYMBOL: {symbol}
AS_OF_TIME: {asof_time}
PRICE DATA (last 60 bars):
{price_data}
INDICATORS:
{indicators}
"""
```

---

## Appendix B: Sector ETF Reference

| Sector | ETF | Ticker Count |
|--------|-----|--------------|
| Information Technology | XLK | 14 |
| Financials | XLF | 10 |
| Health Care | XLV | 7 |
| Communication Services | XLC | 5 |
| Consumer Staples | XLP | 5 |
| Consumer Discretionary | XLY | 4 |
| Industrials | XLI | 3 |
| Energy | XLE | 2 |
