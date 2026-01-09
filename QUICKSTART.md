# Eiqora Trading System - Quickstart Guide

## Prerequisites Check

```bash
# 1. Python environment
python --version  # Should be 3.10+

# 2. Database running
docker ps | grep postgres  # Should show running container

# 3. Environment variables
cat .env | grep -E "DATABASE_URL|OPENROUTER|ALPACA"
```

---

## Step 1: Start Data Collection Scheduler

The scheduler runs all data pipelines automatically.

```bash
# Terminal 1: Data Collection
cd /Users/pan/Documents/Github/Eiqora
source .venv/bin/activate

# Run scheduler
python -m data_collection.scheduler
```

**What it does**:
- Startup: Runs all pipelines once immediately
- Then: Runs on schedule (hourly bars, 4h news, daily, etc.)
- YFinance news: Collects every 4 hours

**Monitor logs**: Look for `✅ YFinance news completed`

---

## Step 2: Build Initial Watchlist (One-time)

Generate daily candidate list (runs daily automatically after, but run once now).

```bash
# Terminal 2: Candidate Selection
cd /Users/pan/Documents/Github/Eiqora
source .venv/bin/activate

python -m eiqora_v2.live.candidate_selector
```

**Expected output**:
```
Building daily watchlist for 2026-01-05
Scoring 50 symbols...
Watchlist: 12 candidates saved (threshold >= 0.50)
Top candidates:
  - NVDA: 0.78 (tech: 0.82, profile: 0.74)
  - AAPL: 0.71 (tech: 0.68, profile: 0.74)
  ...
```

---

## Step 3: Test Trigger Detection

Check if triggers are being detected.

```bash
# Terminal 3: Test Triggers
python -m eiqora_v2.live.trigger_monitor
```

**Expected output**:
```
Scanning 12 watchlist symbols for triggers...
Found 3 triggers:
  - NVDA: news_sentiment (priority: MEDIUM, sentiment: 7.5)
  - AAPL: earnings_release (priority: HIGH)
  - META: hourly_bounce (priority: MEDIUM, RSI: 28)
```

---

## Step 4: Run Live Trading Pipeline (Dry Run)

Process triggers through LLM agents.

```bash
# Terminal 4: Live Pipeline
python -c "
from eiqora_v2.live.pipeline import LiveTradingPipeline
import asyncio

async def test():
    pipeline = LiveTradingPipeline()
    
    # Scan for triggers
    triggers = await pipeline.trigger_monitor.scan_watchlist()
    
    if triggers:
        print(f'\nProcessing {len(triggers)} triggers...\n')
        for trigger in triggers[:1]:  # Test first trigger only
            result = await pipeline.process_trigger(trigger)
            print(f'\nResult: {result}')
    else:
        print('No triggers found')

asyncio.run(test())
"
```

**Expected flow**:
1. Trigger detected (e.g., NVDA news sentiment)
2. Profile loaded (7-day cache)
3. 10-agent pipeline runs
4. Decision: GO/NO_GO
5. If GO: Signal stored (but not executed in dry run)

---

## Step 5: Monitor System Health

### Check Data Collection

```bash
# News articles collected
psql $DATABASE_URL -c "
SELECT COUNT(*) as total_articles,
       COUNT(DISTINCT ticker) as tickers_covered,
       MAX(published_at) as latest_article
FROM yfinance_news
WHERE published_at >= NOW() - interval '24 hours'
"

# Expected: 
# total_articles | tickers_covered | latest_article
#       50       |       15        | 2026-01-05 15:30:00
```

### Check Watchlist

```bash
psql $DATABASE_URL -c "
SELECT symbol, total_score, technical_score, profile_score
FROM watchlist
WHERE scan_date = CURRENT_DATE
ORDER BY total_score DESC
LIMIT 10
"
```

### Check Profiles

```bash
psql $DATABASE_URL -c "
SELECT symbol, 
       (profile_data->>'profile_score')::numeric as score,
       updated_at
FROM ticker_profile
ORDER BY updated_at DESC
LIMIT 5
"
```

---

## Step 6: Production Run (Optional)

**Only if you're ready for live trading!**

```bash
# Start full pipeline (continuously monitors and trades)
python -m eiqora_v2.live.pipeline
```

**What it does**:
- Rebuilds watchlist daily at 8 AM
- Scans for triggers every hour
- Processes triggers through LLM
- Opens and tracks positions on GO signals (database lifecycle)

**Safeguards**:
- Max 3 concurrent positions
- VIX regime adjustments
- Macro blackout periods (FOMC, CPI, NFP)
- Position Manager portfolio checks

---

## Troubleshooting

### Issue: "No news articles found"

```bash
# Check if YFinance pipeline ran
psql $DATABASE_URL -c "SELECT MAX(published_at) FROM yfinance_news"

# If empty, run manually:
python -m data_collection.pipelines.yfinance_news
```

### Issue: "No profile for symbol"

```bash
# Generate profile manually
python -c "
from eiqora_v2.services.profile_generator import ProfileGenerator
import asyncio

async def gen():
    pg = ProfileGenerator()
    profile = await pg.generate_profile('AAPL')
    print(f'Profile score: {profile.profile_score}')

asyncio.run(gen())
"
```

### Issue: "Triggers not detecting"

```bash
# Check watchlist exists
psql $DATABASE_URL -c "SELECT COUNT(*) FROM watchlist WHERE scan_date = CURRENT_DATE"

# If zero, rebuild:
python -m eiqora_v2.live.candidate_selector
```

---

## Monitoring Dashboard (Optional)

Create a simple monitoring loop:

```bash
# Terminal: Monitor
watch -n 60 'psql $DATABASE_URL -c "
SELECT 
    (SELECT COUNT(*) FROM yfinance_news WHERE published_at >= NOW() - interval \"1 day\") as news_24h,
    (SELECT COUNT(*) FROM watchlist WHERE scan_date = CURRENT_DATE) as watchlist,
    (SELECT COUNT(*) FROM signal WHERE created_at >= NOW() - interval \"1 day\") as signals_24h
"'
```

---

## Next Steps

1. **Test with Small Symbol Set**: Start with 5-10 symbols to validate
2. **Monitor for 24h**: Watch data collection, trigger detection
3. **Review LLM Decisions**: Check agent reasoning in logs
4. **Scale Up**: Gradually increase to full 50 symbols

---

## Quick Reference

| Component | Command | Frequency |
|-----------|---------|-----------|
| Data Collection | `python -m data_collection.scheduler` | Continuous |
| Watchlist Builder | `python -m eiqora_v2.live.candidate_selector` | Daily |
| Trigger Monitor | `python -m eiqora_v2.live.trigger_monitor` | Test only |
| Live Pipeline | `python -m eiqora_v2.live.pipeline` | Continuous |

**Key Files**:
- Logs: Check terminal output
- Config: `.env` for API keys
- Symbols: `data_collection/config/symbols.txt`

**Database**:
- News: `yfinance_news` + `yfinance_news_relevance`
- Watchlist: `watchlist`
- Profiles: `ticker_profile`
- Signals: `signal`
