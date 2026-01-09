# Eiqora System - Quick Start Commands

## Directory
**Run all commands from**: `/Users/pan/Documents/Github/Eiqora`

```bash
cd /Users/pan/Documents/Github/Eiqora
```

---

## 1. Check System Status

```bash
python check_status.py
```

**What it shows**: News count, watchlist count, signals count

---

## 2. Build Watchlist (takes ~2-5 minutes due to LLM calls)

```bash
source .venv/bin/activate
python -c "
from eiqora_v2.live.candidate_selector import CandidateSelector
from datetime import datetime, timezone
import asyncio

async def run():
    print('Building watchlist...')
    selector = CandidateSelector(threshold=0.50)
    watchlist = await selector.build_watchlist(datetime.now(timezone.utc))
    await selector.save_watchlist(watchlist, datetime.now(timezone.utc).date())
    print(f'✅ {len(watchlist)} candidates saved')

asyncio.run(run())
" 2>&1 | grep -E "(Building|candidates|ERROR)" | grep -v "positions"
```

---

## 3. Check Watchlist Results

```bash
python check_status.py
```

---

## 4. Scan for Triggers

```bash
source .venv/bin/activate
python -c "
from eiqora_v2.live.trigger_monitor import TriggerMonitor
import asyncio

async def run():
    monitor = TriggerMonitor()
    triggers = await monitor.scan_watchlist()
    print(f'\n{len(triggers)} triggers found:')
    for t in triggers[:5]:
        print(f'  {t.symbol}: {t.trigger_type} (priority: {t.priority})')

asyncio.run(run())
"
```

---

## 5. Check Triggers in Database

```bash
python -c "
from data_collection.db.connection import get_connection
from datetime import datetime

conn = get_connection()
cur = conn.cursor()

# Check recent news sentiment
cur.execute('''
    SELECT yn.ticker, yn.title, nr.score
    FROM yfinance_news yn
    JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
    WHERE yn.published_at >= NOW() - interval '\''24 hours'\''
      AND nr.score > 2.0
    ORDER BY nr.score DESC
    LIMIT 5
''')

print('\nHigh-Sentiment News (score > 2.0):')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[2]:.1f} - {row[1][:60]}...')

# Check earnings
cur.execute('''
    SELECT symbol, earnings_date, eps_actual, eps_est
    FROM earnings_event
    WHERE earnings_date BETWEEN NOW() - interval '\''24 hours'\'' AND NOW() + interval '\''24 hours'\''
    ORDER BY earnings_date DESC
    LIMIT 5
''')

print('\nEarnings (24h window):')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]} (EPS: {row[2]} vs {row[3]})')

conn.close()
"
```

---

## 6. Full Test (Watchlist → Triggers → Details)

```bash
source .venv/bin/activate
python bin/test_signals.py
```

**Note**: This takes 2-5 minutes to build profiles for 50 symbols

---

## 7. Start Full System (Background)

```bash
./start_trading_system.sh start
```

**Then check logs**:
```bash
./start_trading_system.sh logs
```

**Or check status**:
```bash
./start_trading_system.sh status
```

**To stop**:
```bash
./start_trading_system.sh stop
```

---

## Quick Data Collection Test

```bash
source .venv/bin/activate
python -c "
from data_collection.db.connection import get_connection

conn = get_connection()
cur = conn.cursor()

cur.execute('SELECT COUNT(*) FROM yfinance_news')
total = cur.fetchone()[0]

cur.execute('SELECT COUNT(DISTINCT ticker) FROM yfinance_news')
tickers = cur.fetchone()[0]

cur.execute('SELECT MAX(published_at) FROM yfinance_news')
latest = cur.fetchone()[0]

print(f'YFinance News:')
print(f'  Total articles: {total}')
print(f'  Tickers covered: {tickers}')
print(f'  Latest: {latest}')

conn.close()
"
```

---

## Troubleshooting

### If "No candidates" after building watchlist:
- Lower threshold: `CandidateSelector(threshold=0.30)`
- Check if market data exists: `python check_status.py`

### If "No triggers":
- Check news sentiment: See command #5 above
- Check if watchlist is populated: `python check_status.py`

### If LLM calls fail:
- Check `OPENROUTER_API_KEY` in `.env`
- Check `DEFAULT_MODEL` in `eiqora_v2/config/settings.py`
