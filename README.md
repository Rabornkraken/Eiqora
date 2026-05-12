# Eiqora - Quantitative Trading System

Complete end-to-end trading system with automated data collection, LLM-powered analysis, and execution capabilities.

## System Overview

```
┌──────────────────────────────────────────────────────────────┐
│ DATA COLLECTION (data_collection/)                          │
│ ├─ 18+ automated pipelines                                  │
│ ├─ Schedule: APScheduler (scheduler.py)                     │
│ └─ Storage: PostgreSQL + optional S3                        │
└────────────────────────┬─────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ LIVE TRADING SYSTEM (eiqora_v2/)                            │
│                                                              │
│ Stage 1: Candidate Selection (Daily)                        │
│   ├─ VIX regime check (adjust threshold)                    │
│   ├─ Daily technicals (0-1.0) × 50%                         │
│   ├─ LLM profile score (0-1.0) × 50%                        │
│   └─ Output: Watchlist (~10-15 symbols)                     │
│                                                              │
│ Stage 2: Trigger Detection (Continuous)                     │
│   ├─ Macro blackout check (FOMC/CPI/NFP)                    │
│   ├─ Earnings, SEC 8-K, News sentiment                      │
│   ├─ Hourly: VWAP, volume surge, RSI bounce                 │
│   └─ Output: (candidate + trigger) → LLM                    │
│                                                              │
│ Stage 3: LLM Analysis (On trigger)                          │
│   ├─ 8-agent pipeline with full context                     │
│   ├─ Context: profile + trigger + daily + hourly            │
│   ├─ Position Manager: checks portfolio exposure            │
│   ├─ Market stress alert (SPY drawdown)                     │
│   └─ Output: GO/NO_GO + entry/SL/TP                         │
│                                                              │
│ Stage 4: Trade Execution                                    │
│   ├─ Position sizing (10% max risk)                         │
│   ├─ Database position lifecycle tracking                   │
│   └─ Max 3 concurrent positions                             │
└──────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites
- Python 3.10+
- PostgreSQL 14+
- OpenRouter API key (for LLM)

### Installation

```bash
# Clone and setup
git clone <repo>
cd Eiqora
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Setup database
docker compose -f data_collection/docker-compose.yml up -d

# Apply schema
python -m data_collection.cli init-db
```

### Running the System

```bash
# 1. Start data collection (runs in background)
source .venv/bin/activate
export $(cat data_collection/config/.env | xargs)
python -m data_collection.scheduler

# 2. Build initial watchlist (one-time)
python -m eiqora_v2.live.candidate_selector

# 3. Start live trading pipeline
python -m eiqora_v2.live.pipeline
```

---

## Data Collection

Located in `data_collection/`, automated pipelines for market data ingestion.

### Active Pipelines

| Pipeline | Schedule | Data Source | Output Table(s) |
|----------|----------|-------------|-----------------|
| `universe` | Daily 5 AM | SPY holdings + config | `universe_snapshot`, `universe_member` |
| `stooq_daily` | Daily 6 AM | Stooq.com | `market_bar_daily` |
| `hourly_bars` | Hourly 9-4 ET | Market data API | `market_bar_hourly` |
| `earnings` | Daily 6 AM | NASDAQ API | `earnings_event` |
| `sec_rss` | Every 15 min (6-8 PM) | SEC EDGAR RSS | `sec_filing` |
| `sec_edgar` | Daily 7 PM | SEC EDGAR | `sec_filing_section`, `insider_transaction`, `sec_13f_holding` |
| `yfinance_news` | Every 4 hours | YFinance + CDP | `yfinance_news`, `yfinance_news_relevance` |
| `economic_calendar` | Daily 7 AM | Forex Factory (Stealth) | `economic_event` |
| `corporate_actions` | Weekly Sun 3 AM | Public filings | `corporate_action` |

### Pipeline Configuration

Edit `data_collection/config.yaml` for:
- Symbol universe (SPY holdings or custom `symbols.txt`)
- API endpoints
- Lookback periods
- Batch sizes

### Setup

1. **Clone repository**:
```bash
git clone https://github.com/yourusername/Eiqora.git
cd Eiqora
```

2. **Environment variables** (`.env`):
```bash
# Database
POSTGRES_USER=postgres
POSTGRES_PASSWORD=yourpassword
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=finance

# OpenRouter LLM
OPENROUTER_API_KEY=your_key_here

```

---

## Live Trading System  

Located in `eiqora_v2/`, implements 4-stage trigger-based trading.

### Key Components

#### 1. Candidate Selection (`eiqora_v2/live/candidate_selector.py`)

Scores tickers using 50/50 combination:

**Technical Score (0-1.0):**
- Trend: 30% (UPTREND/MIXED)
- RSI: 20% (favorable zone 30-50)
- MACD: 20% (histogram > 0)
- ADX: 20% (trend strength > 25)
- MA Proximity: 10% (within 3% of MA20)

**Profile Score (0-1.0) - LLM-derived:**
- Business fundamentals
- Growth trajectory
- Insider sentiment (weighted by role + dollar value)
- Earnings consistency
- Catalyst pipeline
- Recent news flow

**Profile Generation (`eiqora_v2/services/profile_generator.py`):**
1. Gathers quantitative signals via `signal_aggregator.py`:
   - Earnings: 12 quarters, beat rate, surprise magnitude
   - Insider: 90 days, weighted by CEO/CFO/Director
   - Sentiment: 90 days of news headlines
   - Corporate actions: dividends, splits
2. Passes structured data to LLM for contextual scoring
3. Caches for 7 days (weekly refresh)

#### 2. Trigger Detection (`eiqora_v2/live/trigger_monitor.py`)

Monitors watchlist for events:

| Trigger | Source | Priority | Lookback |
|---------|--------|----------|----------|
| Earnings release | `earnings_event` | HIGH | 24h |
| SEC 8-K filing | `sec_filing` | HIGH | 48h |
| News sentiment | `document` | MEDIUM | 24h |
| Hourly bounce | Hourly RSI < 30 | MEDIUM | 1h |
| VWAP support | Price near VWAP | MEDIUM | 1h |
| Volume surge | Vol > 2x avg | LOW | 1h |

**Macro Safeguards:**

**1. Blackout Periods** - Skips scans before high-impact events:
- FOMC (6h before)
- CPI release (6h before)
- Non-Farm Payrolls (6h before)

**2. VIX Regime Filter**:
- **VIX > 30**: Threshold → 0.70 (very selective)
- **VIX > 20**: Threshold → 0.60 (moderately selective)

**3. Yield/Bond Regime Filter** (New):
- **Rising Yields** (IEF < SMA20): "CAUTIOUS" Mode (Threshold +0.15)
- **High Bond Vol** (TLT Vol > 20%): "RISK_OFF" Mode (Threshold → 0.80)
- **Rationale**: Rising yields are structural headwinds for equities.

#### 3. LLM Analysis (`eiqora_v2/orchestrator.py`)

**Live Trading Orchestrator** processes triggers with 10-agent pipeline:

1. **TopDown**: Market regime analysis (SPY, VIX)
2. **Context**: Stock technicals
3. **Chart**: Setup classification
4. **Fundamental**: News/SEC/earnings analysis ⭐
5. **Idea Generator**: Synthesize trade ideas ⭐
6. **Exit Policy**: Define TP/SL/time stop
7. **Decision**: Initial GO/NO_GO decision ⭐
8. **Position Manager**: Contextual portfolio analysis
9. **Veto**: Final sanity checks
10. **Narrative**: Generate trade story (if approved)

**Hybrid Profile + Live Data Approach:**

Agents use **weekly profile as baseline context** while querying **fresh live data**:

| Data Type | Source | Refresh | Usage |
|-----------|--------|---------|-------|
| **Bull/Bear Case** | Profile (7d cache) | Weekly | Idea Generator baseline thesis |
| **Known Risks** | Profile (7d cache) | Weekly | Decision agent reference |
| **Catalysts** | Profile (7d cache) | Weekly | Fundamental agent baseline |
| **Fresh News** | `document` table | Live (24h) | Fundamental agent |
| **SEC Filings** | `sec_filing` table | Live (48h) | Fundamental agent |
| **Price Action** | `market_bar_*` | Live | Technical/Chart agents |

**Example - Fundamental Agent:**
```
Profile baseline: "Known risk: regulatory scrutiny"
Fresh query: New SEC 8-K filed today mentioning investigation
LLM synthesis: "Profile risk CONFIRMED by fresh filing → elevated concern"
```

**Position Manager (New):**
- Queries current open positions
- Calculates exposure, concentration, correlations
- **Market stress detection**: SPY drawdown > 10% triggers correlation warning
- Makes contextual decisions:
  - **APPROVE**: "Only 2 positions, good diversification"
  - **REDUCE_SIZE**: "Already 15% in SEMIS cluster, reduce to 8%"
  - **REJECT**: "3 positions at 40% exposure, not exceptional enough"
  - **STRESS**: "SPY -12%, correlation risk high, suggest smaller size or pause"
- **LLM-based**: Interprets portfolio state, no hard rules

Receives:
- `TickerProfile` (bull/bear case, catalysts, risks - **baseline context**)
- Trigger event details
- Daily technicals
- Hourly data (for context, not filtering)
- **Current portfolio state** (positions, exposure, P&L)

#### 4. Trade Execution (`eiqora_v2/live/signals.py`)

Signal management and order placement:
- Position sizing: up to 10% risk per trade
- ATR-based stops
- Conviction adjustments
- Max 3 concurrent positions

---

## Database Schema

PostgreSQL tables (see `data_collection/db/init/001_schema.sql`):

**Market Data:**
- `market_bar_daily` - Daily OHLCV
- `market_bar_hourly` - Intraday bars
- `ticker_profile` - LLM-generated profiles (7d cache)

**Fundamental:**
- `earnings_event` - Earnings calendar + actuals
- `sec_filing`, `sec_filing_section` - SEC filings
- `insider_transaction` - Form 4 data
- `sec_13f_holding` - Institutional holdings
- `corporate_action` - Dividends, splits

**News/Sentiment:**
- `yfinance_news` - News articles (YFinance + full text extraction)
- `yfinance_news_relevance` - Sentiment scores (FinBERT)

**Live Trading:**
- `watchlist` - Daily candidate scores
- `signal` - Trade signals from LLM
- `backtest_run` - Historical validation

---

## LLM Configuration

System uses OpenRouter for LLM calls (see `eiqora_v2/config/settings.py`):

- **Default model**: `deepseek/deepseek-v3.2`
- **Temperature**: 0.1 (deterministic)
- **Max retries**: 2
- **Timeout**: 60s
- **Output format**: Structured JSON with Pydantic validation

Profile generation is the most expensive LLM operation but cached for 7 days.

---

## Deprecated / Legacy Code

These directories exist for backward compatibility but are **not actively used**:

| Path | Status | Notes |
|------|--------|-------|
| `eiqora/` | ⚠️ DEPRECATED | Old v1 system, superseded by `eiqora_v2/` |
| `pipelines/` | ⚠️ DEPRECATED | Old pipeline wrappers, use `data_collection/pipelines/` |
| `api.py` | ⚠️ UNUSED | Old Flask API |
| `main.py` | ⚠️ UNUSED | Old entry point |

**Recommendation:** Can be safely deleted after confirming no external dependencies.

---

## Development

### Running Tests

```bash
# Unit tests
pytest eiqora_v2/tests/

# Test candidate selector
python -m eiqora_v2.live.candidate_selector

# Test trigger monitor
python -m eiqora_v2.live.trigger_monitor

# Test profile generation
python -c "from eiqora_v2.services.profile_generator import ProfileGenerator; import asyncio; asyncio.run(ProfileGenerator().generate_profile('AAPL'))"
```

### Backtest Mode

```bash
# Run historical backtest
python -m eiqora_v2.backtest.engine --start 2024-01-01 --end 2024-12-31
```

### Logging

Configure in each module:
```python
logging.basicConfig(level=logging.INFO)
```

---

## Architecture Decisions

### Why Trigger-Based vs Continuous Scanning?

**Previous approach:** Hourly scan all symbols → quality filter → LLM
**Problem:** 50+ LLM calls/hour = expensive + noisy

**Current approach:** Build watchlist → wait for triggers → analyze
**Benefits:**
- 90% fewer LLM calls
- Higher quality signals (only on meaningful events)
- Model-free candidate selection

### Why LLM for Profile Scoring?

**Previous approach:** Binary rules (has bull case? has catalysts?)
**Problem:** Ignores quality and context

**Current approach:** Structured quantitative data → LLM interprets contextually
**Example:** "$498M insider selling" gets different weight if it's CEO vs routine employee stock sales

### Why 7-Day Profile Cache?

Fundamentals (earnings, SEC filings) don't change daily. Weekly refresh balances cost vs freshness.

### Why LLM-Based Position Manager?

**Previous approach:** Hard rules (max 3 positions, max 40% exposure)  
**Problem:** Context-blind. Can't distinguish "exceptional 4th position that diversifies" from "mediocre addition"

**Current approach:** LLM interprets portfolio state contextually
**Example:** 
- "3 positions but all tech, add financial" → APPROVE
- "3 positions at 35% exposure, all profitable" → might APPROVE 4th
- "Already 15% in SEMIS (NVDA), AMD is redundant" → REJECT or REDUCE_SIZE

**Benefits:** Intelligent decisions vs rigid thresholds.

### Why Hybrid Profile + Live Data?

**Problem:** Profile cached for 7 days, but news/sentiment changes intraday.

**Solution:** Use profile for STRUCTURAL context, query live for TEMPORAL data:

| Profile (Weekly) | Live Data (Intraday) |
|------------------|----------------------|
| Bull/bear thesis | Breaking news (24h) |
| Business model | SEC filings (48h) |
| Long-term catalysts | Sentiment shifts |
| Known risks | Price action |

**Example:**
```
Profile: "Risk: regulatory scrutiny of AI practices"
Fresh SEC 8-K: Filed today announcing DOJ investigation
LLM: "Profile risk CONFIRMED → elevated concern for this trade"
```

**Benefits:**
- ✅ Structural context without redundant LLM calls
- ✅ Fresh data for time-sensitive decisions
- ✅ Cross-reference baseline with live developments

### Why Macro Event Safeguards?

**Problem:** System could trade into FOMC uncertainty or during correlation breakdowns.

**Solution:** 3-layer protection system:

**1. Macro Blackout (6h before events):**
```
FOMC in 4 hours → Skip trigger scan entirely
Rationale: Pre-event uncertainty causes whipsaws
```

**2. VIX Regime Filter (dynamic thresholds):**
```
VIX = 35 → Raise bar from 0.50 to 0.70
Rationale: High vol = lower signal quality, be selective
Result: ~40% fewer candidates in stress periods
```

**3. Market Stress Alert (SPY drawdown):**
```
SPY down 12% → Warn Position Manager about correlation risk
LLM Response: "All stocks falling together, reduce size to 6%"
Rationale: Diversification breaks down in crashes
```

**Historical Examples:**
- **March 2020 crash**: VIX hit 80 → threshold would've been 0.75+
- **FOMC Sept 2022**: 75bp hike surprise → blackout avoided pre-event trap
- **SVB failure**: SPY -4% intraday → correlation warning triggered

---

## Monitoring & Alerts

(To be implemented)

Planned monitoring:
- Data pipeline health (via scheduler logs)
- Watchlist size trends
- Trigger frequency by type
- LLM call volume & costs
- Signal win rate tracking
