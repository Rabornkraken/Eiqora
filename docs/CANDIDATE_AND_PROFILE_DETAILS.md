# Candidate Selection & Profile Generation

This document details the first stage of the Eiqora pipeline: selecting the best daily candidates for trading. This process filters thousands of stocks down to a manageable "Watchlist" (10-15 symbols) using a hybrid Quantitative/LLM approach.

## 1. Candidate Selection (`CandidateSelector`)

**File:** `eiqora_v2/live/candidate_selector.py`

### Strategy
The selector scores every symbol in the universe (S&P 500 + others) on a 0.0 to 1.0 scale.
*   **Weighting:** 50% Daily Technicals + 50% Fundamental Profile.
*   **Threshold:** Default **0.60**. Adjusted dynamically based on market volatility (VIX).

### Macro Safeguards
Before scoring, the system checks market regimes to protect capital.

```python
# eiqora_v2/live/candidate_selector.py

# Dynamic threshold based on VIX
if vix_level > 30:
    adjusted_threshold = 0.70  # High vol: be very selective
elif vix_level > 20:
    adjusted_threshold = 0.60
else:
    adjusted_threshold = self.threshold  # Normal

# Yield Curve / Bond Regime Check
if regime["mode"] == "RISK_OFF":
    adjusted_threshold = max(adjusted_threshold, 0.80)
```

### Technical Scoring (Rule-Based)
The technical score is deterministic, ensuring we only trade stocks with strong momentum and clean charts.

**Key Factors:**
*   **Trend (25%):** Is the stock in a "UPTREND" state?
*   **MA Alignment (15%):** Price > MA20 > MA50.
*   **Momentum (15%):** 20-day and 60-day returns.
*   **Money Flow (15%):** MFI, CMF, and OBV trend (Accumulation/Distribution).
*   **Options Sentiment (5%):** Put/Call Ratio < 0.7 (Bullish).

```python
# eiqora_v2/live/candidate_selector.py

def score_daily_technicals(self, symbol, asof_time):
    # ...
    # Trend regime (net 0.25)
    if "UPTREND" in state_tags:
        add_score("trend_up", 0.25)
    
    # MA alignment (net 0.15)
    if ma20_state == "ABOVE":
        add_score("ma20_above", 0.05)
        
    # Money Flow (net 0.15)
    if cmf_20 > 0.15:
        add_score("cmf_strong_accumulation", 0.07)
```

---

## 2. Profile Generation (`ProfileGenerator`)

**File:** `eiqora_v2/services/profile_generator.py`

### Philosophy: Hybrid Scoring
We cannot rely solely on LLMs for scoring because they lack mathematical precision. We use a **Hybrid Approach**:
1.  **Quantitative Base (Code):** Calculates a grounded score from earnings beats, insider buying, and sentiment trends.
2.  **Qualitative Adjustment (LLM):** The LLM reads news/SEC filings and *adjusts* the base score (+/- 0.20).

### Data Layering
The generator pulls data across multiple time horizons to build a complete picture.

```python
# eiqora_v2/services/profile_generator.py

async def _gather_layered_data(self, symbol):
    # A. Fundamentals (3 Years) - Earnings trends
    # B. Major Events (1 Year) - Lawsuits, M&A
    # C. Recent News (90 Days) - Current narrative
    # D. SEC Filings (2 Years) - Structural risks
    # E. Options Data (30 Days) - Market positioning
    # F. Supply Chain - Peer performance
```

### The LLM Synthesis
The prompt explicitly provides the base score and asks the LLM to justify any deviation based on "Material Events" (lawsuits, regulatory risk, etc.).

```python
# eiqora_v2/services/profile_generator.py

# STEP 1: Calculate quantitative base score
base_score, quant_breakdown = calculate_quantitative_base_score(signals, ...)

# STEP 2: LLM Adjustment Prompt
prompt = f"""
### QUANTITATIVE BASE SCORE = {base_score:.3f}

**Component Scores:**
- Earnings Quality: {quant_breakdown.get('earnings_score'):.2f}
- Insider Sentiment: {quant_breakdown.get('insider_score'):.2f}

**Your Task**: Adjust this base score by -0.20 to +0.20 based on qualitative factors.
- Lawsuits/regulatory issues: -0.10 to -0.20
- Strong management/strategic moves: +0.05 to +0.15
"""
```

### Caching Strategy
Profiles are expensive to generate. They are cached in the database for **7 days**. This balances freshness with cost.

```python
# eiqora_v2/services/profile_generator.py

# Check freshness - weekly refresh
age = datetime.utcnow() - profile.last_updated
if age < timedelta(days=7):
    return profile
```

---

## 3. Workflow Summary

1.  **Scheduler** triggers `build_watchlist` daily (e.g., 8:00 AM).
2.  **Selector** loads universe (S&P 500).
3.  **Loop per Symbol:**
    *   Check for imminent high-risk events (skip if Earnings tomorrow).
    *   Calculate **Technical Score** (0-1.0).
    *   Fetch/Generate **Profile Score** (0-1.0).
    *   Compute Weighted Average.
4.  **Filter:** Keep symbols > Threshold (e.g., 0.60).
5.  **Store:** Save to `daily_watchlist` table.
6.  **Use:** The `TriggerMonitor` will *only* scan these 10-15 symbols for intraday trade signals.
