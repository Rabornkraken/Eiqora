# Daily Trigger Strategy Research (Jan 2026)

## Context
- Trading individual stocks only (no ETFs/indexes)
- Daily bars, long-only
- Entry at next day's open after signal (no look-ahead bias)
- Current system: 1.5x ATR stop loss, 3.0x ATR take profit
- Current performance: 38.2% win rate, +0.78% avg gain/trade

## Available Database Columns (market_bar_daily)
OHLCV, vwap, trade_count, rsi_14, rsi_20, macd, macd_signal, macd_hist,
bb_upper_20, bb_middle_20, bb_lower_20, bb_width, atr_14, adx_14, plus_di,
minus_di, stochastic_k, stochastic_d, mfi_14, cmf_20, obv, ad_line,
support_level, resistance_level, volume_z_20

## Available Tables
- options_daily_summary (put_call_ratio_volume, atm_iv, max_pain_strike)
- insider_transaction (insider buys/sells)
- sec_ftd (failures to deliver)
- analyst_rating (upgrades/downgrades, price targets)

---

## Finding 1: ATR Stops Hurt Mean-Reversion

Connors RSI(2) research (backtested 1990-2024) showed that adding stop losses
**reduced** mean-reversion performance. The price needs room to revert.

- Mean-reversion exit: time-based (5-7 days) or RSI crossing above 50
- Momentum/breakout exit: ATR-based stops work well here
- Source: QuantifiedStrategies - Connors RSI

## Finding 2: ADX Regime Filter (Highest Impact Single Change)

Split triggers by market regime using adx_14:

- ADX < 25: Range-bound -> mean-reversion strategies only
- ADX > 25 + rising + plus_di > minus_di: Trending -> momentum/breakout only
- Regime-switching achieved: Sharpe 1.21, 54.2% win rate, 16.5% max DD
- Source: Price Action Lab, Medium (Dual-Regime Adaptive Trading)

## Finding 3: Proven High Win-Rate Combinations

| Strategy                                | Win Rate | Avg Gain | Data Needed                     |
|-----------------------------------------|----------|----------|---------------------------------|
| RSI(2) < 5 + close > 200 SMA           | 75%+     | ~0.8%    | OHLC (compute in-memory)        |
| MACD histogram reversal + RSI < 40      | 73%      | 0.88%    | macd_hist, rsi_14               |
| StochRSI oversold bounce               | 78%      | 0.7%     | stochastic_k, stochastic_d      |
| RSI + Bollinger lower band touch        | 71%      | 2.3%     | rsi_14, bb_lower_20             |

## Finding 4: Insider Buying Confirmation

- Strongest documented anomaly in finance (Seyhun 1992)
- 60% of 1-year return variation explained by aggregate insider purchases
- C-suite buys most predictive
- Use as confirmation filter on oversold triggers
- Source: 2iQ Research

## Finding 5: Put/Call Ratio as Sentiment Filter

- PCR > 1.0 + technical oversold = contrarian buy signal
- Individual stock PCR more reliable than index-level
- Source: Pan & Poteshman (Journal of Finance 2006)

## Finding 6: FTD Negative Filter

- Rising FTDs = informed short selling
- Filter OUT stocks with high/rising FTDs from mean-reversion buys
- Exception: extremely elevated FTDs + insider buying = squeeze setup
- Source: Fotak et al. (Journal of Financial Economics 2014)

## Finding 7: What NOT to Do

- Bollinger squeeze breakout: poor results on daily stocks (QuantifiedStrategies)
- OBV as standalone trigger: divergence too hard to systematize
- Combining too many signals: severe overfitting risk (Alpha Architect)

---

## Implementation Priority

1. ADX regime filter (split mean-reversion vs momentum)
2. Regime-appropriate exits (time-based for MR, ATR for momentum)
3. RSI(2) ultra-short trigger (75%+ win rate documented)
4. MACD histogram reversal trigger (73% win rate with RSI)
5. BB lower band bounce trigger (71% win rate with RSI)
6. Insider buying confirmation filter
7. FTD negative filter
8. PCR sentiment filter

## Sources
- https://www.quantifiedstrategies.com/connors-rsi/
- https://www.quantifiedstrategies.com/macd-and-rsi-strategy/
- https://www.quantifiedstrategies.com/stochastic-rsi/
- https://www.quantifiedstrategies.com/adx-trading-strategy/
- https://alphaarchitect.com/backtesting-strategies-based-multiple-signals-beware-overfitting-biases/
- https://medium.com/@FMZQuant/dual-regime-adaptive-trading-system
- https://www.2iqresearch.com/blog/profiting-from-insider-transactions
- https://www.priceactionlab.com/Blog/2024/01/mean-reversion-and-momentum-regime-switching/
