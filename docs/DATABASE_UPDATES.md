# Database Table Updates - Live Trading & Candidates

## Overview

This document details which PostgreSQL tables are updated during live trading operations and candidate selection.

---

## Data Collection (Continuous)

### Scheduled Pipelines

| Pipeline | Tables Updated | Frequency | Data |
|----------|---------------|-----------|------|
| **yfinance_news** | `yfinance_news`<br>`yfinance_news_relevance` | Every 4 hours | News articles + FinBERT sentiment scores |
| **earnings** | `earnings_event` | Daily 6 AM | Earnings calendar, actuals, estimates |
| **sec_rss** | `sec_filing` | Every 15 min (6-8 PM) | SEC filings (8-K, 10-Q, 10-K) |
| **hourly_bars** | `market_bar_hourly` | Hourly (9 AM - 4 PM) | OHLCV + indicators (RSI, VWAP, volume) |
| **stooq_daily** | `market_bar_daily` | Daily 6 AM | Daily OHLCV for all symbols |
| **universe** | `universe_snapshot`<br>`universe_member` | Daily 5 AM | SPY holdings, universe composition |
| **economic_calendar** | `economic_event` | Daily 7 AM | FOMC, CPI, NFP events |
| **vix** | `market_bar_daily` (^VIX) | Daily 6 AM | VIX volatility index |

---

## Candidate Selection (Daily)

### Tables Updated

#### `watchlist`
**When**: Daily at 8 AM (after market data is fresh)  
**Operation**: `INSERT ... ON CONFLICT DO UPDATE`

**Schema**:
```sql
CREATE TABLE watchlist (
    symbol TEXT,
    scan_date DATE,
    total_score NUMERIC,       -- Combined score (0.0-1.0)
    technical_score NUMERIC,   -- Technical indicators score
    profile_score NUMERIC,     -- LLM-derived profile score
    created_at TIMESTAMPTZ,
    PRIMARY KEY (symbol, scan_date)
);
```

**Data Written**:
- **Scope**: All symbols from `symbols.txt` (typically 50)
- **Filter**: Only symbols with `total_score >= threshold` (default 0.50)
- **Typical Result**: 10-15 candidates per day

**Example**:
```sql
INSERT INTO watchlist VALUES
('NVDA', '2026-01-05', 0.78, 0.82, 0.74, NOW()),
('AAPL', '2026-01-05', 0.71, 0.68, 0.74, NOW()),
...
```

#### `ticker_profile` (Indirect - 7-day cache)

**When**: On-demand when profile is stale (>7 days)  
**Operation**: `INSERT ... ON CONFLICT DO UPDATE`

**Schema**:
```sql
CREATE TABLE ticker_profile (
    symbol TEXT PRIMARY KEY,
    profile_data JSONB,  -- Entire TickerProfile as JSON
    updated_at TIMESTAMPTZ
);
```

**Data Written**:
- **profile_score**: 0.0-1.0 (LLM-derived)
- **bull_case**: Text summary
- **bear_case**: Text summary
- **catalysts**: List of upcoming catalysts
- **risks**: List of known risks
- **material_events**: List of ongoing sagas
- **score_breakdown**: Dict explaining score components

**Triggers Profile Update**:
1. First time seeing a symbol
2. Profile older than 7 days
3. User manually forces refresh

---

## Live Trading (Continuous)

### Trigger Detection (Hourly)

**Read-only** - No tables updated  
**Queries**:
- `watchlist` - Get candidate symbols
- `yfinance_news` + `yfinance_news_relevance` - Check sentiment
- `earnings_event` - Check earnings releases
- `sec_filing` - Check 8-K filings
- `market_bar_hourly` - Check technical triggers

### LLM Analysis (On Trigger)

**Read-only** - No tables updated  
**Queries**:
- `ticker_profile` - Get cached profile
- `yfinance_news` - Get recent news (72h)
- `market_bar_daily` - Get daily technicals
- `market_bar_hourly` - Get hourly data
- `sec_filing` - Get recent filings (30d)
- `position` - Get current active positions

### Signal Storage (On GO Decision)

#### `signal`
**When**: LLM agent pipeline outputs GO decision  
**Operation**: `INSERT`

**Schema**:
```sql
CREATE TABLE signal (
    signal_id SERIAL PRIMARY KEY,
    symbol TEXT,
    signal_type TEXT,           -- 'ENTRY', 'EXIT', 'TIGHTEN'
    action TEXT,                -- 'BUY', 'SELL'
    confidence NUMERIC,         -- 0.0-1.0
    entry_price NUMERIC,
    stop_loss NUMERIC,
    take_profit NUMERIC,
    position_size INTEGER,      -- Number of shares
    trigger_type TEXT,          -- 'earnings', 'news', 'technical', etc.
    trigger_details JSONB,      -- Full trigger context
    agent_reasoning JSONB,      -- All agent outputs
    created_at TIMESTAMPTZ,
    status TEXT DEFAULT 'PENDING'  -- 'PENDING', 'EXECUTED', 'FAILED', 'CANCELLED'
);
```

**Data Written**:
```sql
INSERT INTO signal (symbol, signal_type, action, confidence, entry_price, 
                    stop_loss, take_profit, position_size, trigger_type, 
                    trigger_details, agent_reasoning)
VALUES 
('NVDA', 'ENTRY', 'BUY', 0.82, 875.50, 850.00, 920.00, 50, 
 'news_sentiment', 
 '{"sentiment": 7.5, "headline": "..."}',
 '{"topdown": {...}, "fundamental": {...}, "decision": {...}}');
```

### Trade Execution (On Signal)

#### `signal` (Update)
**When**: Position opened in database  
**Operation**: `UPDATE`

**Updates**:
```sql
UPDATE signal 
SET status = 'EXECUTED',
    executed_at = NOW(),
    execution_price = 875.75  -- Actual fill price
WHERE signal_id = 123;
```

**Failure Case**:
```sql
UPDATE signal 
SET status = 'FAILED',
    error_message = 'Insufficient buying power'
WHERE signal_id = 123;
```

---

## Position Monitoring (4:30 PM Daily)

### Read Operations
- `position`: Get current active positions
- `signal`: Get entry details for each position
- `market_bar_daily`: Get current price action

### Reassessment Signals

If Position Monitor Agent recommends TIGHTEN or EXIT:

#### `signal` (New Entry)
```sql
INSERT INTO signal (symbol, signal_type, action, trigger_type, ...)
VALUES ('NVDA', 'TIGHTEN', 'MODIFY_STOP', 'position_reassessment', ...);
```

Or:
```sql
INSERT INTO signal (symbol, signal_type, action, trigger_type, ...)
VALUES ('AAPL', 'EXIT', 'SELL', 'position_reassessment', ...);
```

---

## Summary: Tables Modified by Component

### Candidate Selector → Writes
- ✅ `watchlist` (daily)
- ✅ `ticker_profile` (on cache miss)

### Live Pipeline → Writes
- ✅ `signal` (on GO decision)
- ✅ `signal` (update on execution)

### Position Monitor → Writes
- ✅ `signal` (TIGHTEN/EXIT recommendations)

### Data Collector → Writes
- ✅ `yfinance_news`
- ✅ `yfinance_news_relevance`
- ✅ `earnings_event`
- ✅ `sec_filing`
- ✅ `market_bar_hourly`
- ✅ `market_bar_daily`
- ✅ `universe_snapshot`
- ✅ `universe_member`
- ✅ `economic_event`

---

## Monitoring Queries

### Check Today's Candidates
```sql
SELECT symbol, total_score, technical_score, profile_score
FROM watchlist
WHERE scan_date = CURRENT_DATE
ORDER BY total_score DESC;
```

### Check Today's Signals
```sql
SELECT signal_id, symbol, signal_type, action, status, 
       confidence, created_at
FROM signal
WHERE created_at >= CURRENT_DATE
ORDER BY created_at DESC;
```

### Check Active Positions (via Signals)
```sql
SELECT symbol, entry_price, stop_loss, take_profit, 
       position_size, created_at
FROM signal
WHERE signal_type = 'ENTRY' 
  AND status = 'EXECUTED'
  AND symbol NOT IN (
      SELECT symbol FROM signal 
      WHERE signal_type = 'EXIT' 
        AND status = 'EXECUTED'
  );
```

### Check News Coverage
```sql
SELECT ticker, COUNT(*) as article_count,
       AVG(nr.score) as avg_sentiment
FROM yfinance_news yn
LEFT JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
WHERE yn.published_at >= NOW() - interval '24 hours'
GROUP BY ticker
ORDER BY article_count DESC;
```

---

## Backup Recommendations

### Critical Tables (Transactional)
- `signal` - All trade decisions and executions
- `watchlist` - Daily candidate history

**Frequency**: Daily, retain 90 days

### Reference Tables (Rebuilds)
- `ticker_profile` - Can be regenerated (slow but possible)
- `yfinance_news` - Can be re-fetched for recent data

**Frequency**: Weekly, retain 30 days

### Market Data (Archival)
- `market_bar_daily` - Historical price data
- `market_bar_hourly` - Intraday data

**Frequency**: Monthly, retain indefinitely
