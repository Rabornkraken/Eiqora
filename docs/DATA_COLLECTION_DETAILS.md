# Data Collection Subsystem Details

This document provides a deep dive into the data collection architecture of Eiqora. It details the specific APIs, methods, parsing logic, and storage schemas for each major data pipeline.

## Overview

The system uses a mix of direct API access, library wrappers (like `yfinance`), and headless browsing (Playwright/CDP) to aggregate data. All data is normalized and stored in a PostgreSQL database.

---

## 1. Daily Market Data (OHLCV)

**Pipeline:** `data_collection/pipelines/stooq_daily.py`  
**Source:** [Stooq.com](https://stooq.com)

### Method
The pipeline fetches daily CSV exports directly from Stooq. This is efficient for bulk historical data. It uses a `ThreadPoolExecutor` to fetch symbols in parallel.

### Data Acquisition
It constructs a URL for the CSV download endpoint:

```python
# data_collection/pipelines/stooq_daily.py

def _fetch_stooq_csv(session, settings, base_url, endpoint, symbol):
    params = {"s": symbol, "i": "d"} # i=d means daily interval
    url = f"{base_url}{endpoint}"
    response = request_with_retries(session, "GET", url, settings=settings, params=params)
    return response.content, response.status_code
```

### Parsing & Processing
The raw CSV content is parsed using Python's `csv` module. It filters out rows outside the requested date range or with missing data.

```python
# data_collection/pipelines/stooq_daily.py

reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
bar_rows: list[tuple[Any, ...]] = []
for row in reader:
    if not row.get("Date"):
        continue
    bar_date = datetime.strptime(row["Date"], "%Y-%m-%d").date()
    # ... date filtering ...
    
    bar_rows.append((
        symbol,
        bar_date,
        float(row["Open"]) if row.get("Open") else None,
        # ... high, low, close ...
        int(float(row["Volume"])) if row.get("Volume") else None,
        # ...
        "stooq",
    ))
```

### Storage
**Table:** `market_bar_daily`
Data is inserted using `ON CONFLICT DO UPDATE` to handle re-runs gracefully.

```sql
INSERT INTO market_bar_daily
    (symbol, date, open, high, low, close, volume, vwap, trade_count, source)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol, date)
DO UPDATE SET
    open = EXCLUDED.open,
    -- ... other fields ...
    source = EXCLUDED.source
```

---

## 2. Intraday Market Data (Hourly)

**Pipeline:** `data_collection/pipelines/hourly_bars.py`  
**Source:** Yahoo Finance (via `yfinance`)

### Method
Uses the `yfinance` library to fetch hourly bars. Since the API has limits on how much hourly data can be fetched at once (~60 days), the pipeline chunks requests.

### Data Acquisition

```python
# data_collection/pipelines/hourly_bars.py

t = yf.Ticker(request_symbol)

# yfinance only allows ~60 days of hourly data at a time
while current_start < end_date:
    chunk_end = min(current_start + timedelta(days=59), end_date)
    
    hist = t.history(
        start=current_start.isoformat(),
        end=(chunk_end + timedelta(days=1)).isoformat(),
        interval="1h"
    )
    # ... process rows ...
```

### Storage
**Table:** `market_bar_hourly`

```python
# data_collection/pipelines/hourly_bars.py

cursor.execute("""
    INSERT INTO market_bar_hourly 
        (symbol, datetime, open, high, low, close, volume, source)
    VALUES (%s, %s, %s, %s, %s, %s, %s, 'yfinance')
    ON CONFLICT (symbol, datetime) 
    DO UPDATE SET 
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume
""", (...))
```

---

## 3. News & Sentiment

**Pipeline:** `data_collection/pipelines/yfinance_news.py`  
**Source:** Yahoo Finance + Publisher Websites

### Method
1.  **Metadata:** Fetches news headlines and links via `yfinance`.
2.  **Content:** Uses a custom **Chrome DevTools Protocol (CDP)** client to fetch full article text, bypassing simple anti-bot protections on some publisher sites.
3.  **Sentiment:** Uses the `ProsusAI/finbert` model (via Hugging Face `transformers`) to score sentiment.

### Data Acquisition (Full Text)

```python
# data_collection/pipelines/yfinance_news.py

# Fetch full article text using CDP browser
if url:
    html, error = fetch_with_cdp(url)
    if html:
        article_text = _extract_text(html)
```

### Sentiment Analysis
The text is chunked and passed to FinBERT. Scores are aggregated to a -10 to +10 scale.

```python
# data_collection/pipelines/yfinance_news.py

def _score_news(title: str, text: str) -> float:
    pipe = _get_sentiment_pipeline()
    full_text = f"{title}. {text or ''}"
    chunks = _chunk_text(full_text)[:5]
    
    results = pipe(chunks)
    
    total_sentiment = 0.0
    for res in results:
        lbl = res['label'].lower()
        conf = res['score']
        if lbl == 'positive':
            total_sentiment += conf * 10
        elif lbl == 'negative':
            total_sentiment -= conf * 10
            
    return total_sentiment / len(chunks)
```

### Storage
**Tables:** 
*   `yfinance_news`: Stores title, text, publisher, URL.
*   `yfinance_news_relevance`: Stores the computed sentiment score.

---

## 4. SEC Filings (8-K, 13-F, etc.)

**Pipeline:** `data_collection/pipelines/sec_rss.py`  
**Source:** SEC EDGAR (RSS & Daily Index)

### Method
This pipeline has two modes:
1.  **Real-time:** Polls the SEC Atom RSS feed every 15 minutes for immediate alerts.
2.  **Backfill:** Parses the SEC "Daily Index" text files for comprehensive history.

### Data Acquisition

```python
# data_collection/pipelines/sec_rss.py

params = {
    "action": "getcurrent",
    "type": form, # e.g., "8-K"
    "output": "atom",
}
response = request_with_retries(session, "GET", SEC_RSS_URL, settings=common.http, params=params)
entries = _parse_feed(response.content)
```

### Storage
**Table:** `sec_filing`

```python
# data_collection/pipelines/sec_rss.py

cursor.execute(
    """
    INSERT INTO sec_filing
        (accession, cik, form_type, filed_at, report_period, is_amendment, primary_doc_url, description)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (accession)
    DO UPDATE SET ...
    """,
    (...)
)
```

---

## 5. Earnings Calendar

**Pipeline:** `data_collection/pipelines/earnings.py`  
**Source:** NASDAQ (Primary), YFinance (Secondary), SEC (Fallback)

### Method
The pipeline prioritizes NASDAQ's API because it provides the most accurate "Estimated EPS" and "Time of Day" (Pre-market/After-hours). It rotates User-Agents and manages sessions carefully to avoid blocks.

### Data Acquisition (NASDAQ)
It mimics a browser request to NASDAQ's JSON API.

```python
# data_collection/pipelines/earnings.py

def _nasdaq_headers(settings: HttpSettings) -> dict[str, str]:
    return {
        "User-Agent": settings.user_agent,
        "Referer": "https://www.nasdaq.com/market-activity/earnings",
        "Origin": "https://www.nasdaq.com",
        # ...
    }

response = request_with_retries(session, "GET", endpoint, settings=fast_settings, params=params, headers=headers)
payload = response.json()
```

### Storage
**Table:** `earnings_event`
Stores estimates vs. actuals, along with calculated fields like `revenue_growth_yoy`.

```python
# data_collection/pipelines/earnings.py

# Upsert logic that preserves existing data if new data is null
INSERT INTO earnings_event (...)
VALUES (...)
ON CONFLICT (symbol, earnings_date)
DO UPDATE SET
    eps_est = COALESCE(EXCLUDED.eps_est, earnings_event.eps_est),
    eps_actual = COALESCE(EXCLUDED.eps_actual, earnings_event.eps_actual),
    # ...
```

---

## 6. Economic Calendar (Macro)

**Pipeline:** `data_collection/pipelines/economic_calendar.py`  
**Source:** Forex Factory

### Method
Since Forex Factory does not have a public API and is heavily protected, this pipeline uses **Playwright** with `playwright-stealth`. It renders the page in a headless browser to extract the event table.

### Data Acquisition

```python
# data_collection/pipelines/economic_calendar.py

async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True)
    # ...
    # Apply stealth mode
    stealth = Stealth(navigator_webdriver=True)
    await stealth.apply_stealth_async(page)
    
    await page.goto(url, timeout=60000, wait_until='domcontentloaded')
    
    # Wait for calendar rows
    await page.wait_for_selector('tr.calendar__row[data-event-id]', timeout=15000, state='attached')
    
    content = await page.content()
```

### Parsing
It uses `BeautifulSoup` to parse the rendered HTML table, identifying high-impact events based on CSS classes.

```python
# data_collection/pipelines/economic_calendar.py

# Get impact level from icon class
impact = 'medium'
if impact_cell:
    impact_classes = ' '.join(impact_cell.get('class', []))
    if 'icon--ff-impact-red' in impact_classes:
        impact = 'high'
```

### Storage
**Table:** `economic_event`
Stores `actual`, `forecast`, `previous`, and `impact` level (High/Medium/Low).