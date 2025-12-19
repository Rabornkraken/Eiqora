# Research Report: The "Holistic Synthesis" Engine

**Date:** December 16, 2025
**Topic:** Re-balancing Eiqora's Decision Logic (Fundamentals First, Technicals Second)
**Status:** Deep Thinking / Revised Plan

## 1. The Critique: "Technical Tunnel Vision"

The previous plan (Technical Trade Setups) failed because it ignored the **"Why."**
It treated a stock like a wiggly line on a chart. It ignored that:
*   A stock with **massive earnings growth** *should not* be traded like a range-bound utility.
*   **Insider Buying** sets a "psychological floor" that often overrides technical support levels.
*   **Sentiment** drives momentum, often invalidating "Overbought" RSI signals.

## 2. The Core Insight: "Conviction Dictates Aggression" (With Context)

The system should not just ask "Where is support?"
It must ask: **"How badly do we want this asset, and is the price justified?"**

### The "Conviction-Pricing" Model (Baseline Tendency)

**CRITICAL NOTE:** These are *baselines*, not hard rules. The Trader Agent must apply judgment. A High Score (90) does **not** justify buying an infinitely expensive stock.

| Fundamental/Sentiment Score | Insider/Smart Money | Baseline Tendency | Contextual Override (The "AI Judgment") |
| :--- | :--- | :--- | :--- |
| **High (9-10)** | Buying | **"Chasing Alpha"** | *Override:* If P/E is extreme (>100x) or Macro is crashing, switch to "Wait for Pullback." |
| **Medium (5-8)** | Neutral | **"Value Hunting"** | *Override:* If sector is hot (AI/Tech), may justify paying a premium over support. |
| **Low (0-4)** | Selling | **"Deep Value / Avoid"** | *Override:* Only buy if it's a "Cigar Butt" deep value play (trading below book value). |

## 3. Data Sourcing Strategy: "The Scraper Approach"

To avoid expensive APIs, we will implement targeted web scrapers for public financial data.

### A. Smart Money (OpenInsider)
*   **Source:** `http://openinsider.com/search?q={TICKER}`
*   **Method:** HTML Parsing (`BeautifulSoup`).
*   **Target Data:**
    *   **Table:** Main results table.
    *   **Filter:** `Trade Type = "P - Purchase"` (Open Market Buy). Ignore Grants/Options.
    *   **Output:** List of recent Insider Buys (Date, Name, Price, Value).
    *   **Why:** This proves "Skin in the game."

### B. Fundamental Anchors (Finviz)
*   **Source:** `https://finviz.com/quote.ashx?t={TICKER}`
*   **Method:** HTML Parsing (`BeautifulSoup`).
*   **Target Data:**
    *   `Target Price`: Consensus Analyst Target.
    *   `Recom`: Mean Recommendation (1=Buy, 5=Sell).
    *   `Insider Own`: Institutional Ownership %.
    *   **Why:** Provides the "Fair Value" anchors needed to judge if the current price is a discount.

## 4. Revised Agent Roles

### A. The "Fundamental Scorer" (Bull/Bear Analysts)
*   **Old Role:** Write a text argument.
*   **New Role:** Output a **Quality/Conviction Score (0-100)** based on:
    *   Earnings Growth / Surprise magnitude.
    *   Competitive Moat.
    *   Macro Tailwinds.
    *   *Constraint:* Must justify the score with data.

### B. The "Smart Money" Scout (New/Upgraded)
*   **Role:** Identify the "Insider Floor."
*   **Logic:** "CEO bought at $150." -> This price is now a **Critical Fundamental Support Level**, likely stronger than any chart line.

### C. The "Trader" (The Synthesizer)
*   **Logic:**
    1.  **Read Conviction:** "Fundamentals are amazing (Score 90)."
    2.  **Check Smart Money:** "Insiders bought at $145."
    3.  **Check Technicals:** "Chart support is $140."
    4.  **Validation (The "No" Check):** "Is P/E > 50? Is RSI > 80?"
    5.  **Decision:** "Score is 90, so I want to be aggressive. BUT, RSI is 85 (Overbought). I will **not** buy Market. I will place a Limit Order at the Insider Floor ($145) instead of the Technical Support ($140)."

## 5. Summary

This model ensures that **Fundamentals are the Engine**, **Sentiment is the Fuel**, and **Technicals are just the Steering Wheel.**
We don't buy *because* of a chart pattern. We buy *because* the company is great, and use the chart only to not overpay. The AI's job is to balance the **Greed** (Score) against the **Fear** (Valuation/Technicals).