"""
Realistic Backtesting Engine for Eiqora V2.

Features:
- Full LLM agent pipeline for decision making
- TP/SL exit conditions (not fixed time)
- Point-in-time data (no data leakage)
- Date masking for LLM prompts
- Multiple trigger types
- Trade logging with full statistics
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import psycopg
from decimal import Decimal

logger = logging.getLogger(__name__)


def get_db_url() -> str:
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    return url.replace("postgresql+psycopg://", "postgresql://")


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class Trigger:
    """A trading trigger event."""
    type: str  # 'earnings', 'sec', 'macro', 'technical', 'news'
    ticker: str
    date: date
    detail: dict


@dataclass 
class Trade:
    """An executed trade with TP/SL."""
    trade_id: Optional[int] = None
    ticker: str = ""
    direction: str = "long"
    entry_date: datetime = None
    entry_price: float = 0.0
    
    # TP/SL exits (realistic!)
    take_profit: float = 0.0
    stop_loss: float = 0.0
    max_holding_days: int = 30
    
    exit_date: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""  # 'tp', 'sl', 'invalidation', 'time_stop', 'close'
    
    shares: int = 0
    position_size: float = 0.0
    
    # Trigger & decision info
    trigger_type: str = ""
    trigger_detail: dict = field(default_factory=dict)
    agent_signals: dict = field(default_factory=dict)
    llm_decision: Optional[dict] = None
    confidence_score: float = 0.0
    
    # Performance
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    holding_hours: Optional[int] = None
    max_drawdown_pct: Optional[float] = None


@dataclass
class Portfolio:
    """Portfolio state for backtest."""
    cash: float = 100000.0
    positions: dict = field(default_factory=dict)  # ticker -> Trade
    trades: list = field(default_factory=list)


# ============================================================================
# Date Masker
# ============================================================================

class DateMasker:
    """Mask dates in text to prevent LLM data leakage."""
    
    def __init__(self, as_of_date: date):
        self.as_of_date = as_of_date
    
    def mask(self, text: str) -> str:
        """Replace absolute dates with relative descriptions."""
        if not text:
            return text
        
        text = text.replace("2025", "this year")
        text = text.replace("2024", "last year")
        text = text.replace("2026", "next year")
        
        return text
    
    def get_context(self) -> str:
        """Context for LLM prompts."""
        return (
            "Note: You are analyzing historical data for backtesting. "
            "Specific dates have been masked. Treat this as a point-in-time analysis."
        )


# ============================================================================
# LLM Decision Maker (integrates with real agents)
# ============================================================================
# LLM Decision Maker (LLM-Only, No Fallback)
# ============================================================================

class LLMDecisionMaker:
    """Uses LLM agents to make trading decisions. LLM-only, no rule-based fallback."""
    
    def __init__(self, db_url: str):
        self.db_url = db_url
    
    async def make_decision(
        self, 
        ticker: str, 
        trigger: Trigger, 
        as_of_date: date,
        current_price: float,
    ) -> dict:
        """
        Make trading decision using LLM multi-agent pipeline.
        
        Returns:
            {
                'action': 'enter_long' | 'enter_short' | 'hold',
                'confidence': 0.0-1.0,
                'entry_price': float,
                'take_profit': float,
                'stop_loss': float,
                'max_holding_days': int,
                'reasoning': str,
                'agent_outputs': dict,
            }
        """
        try:
            from eiqora_v2.orchestrator import BacktestOrchestrator
            
            logger.info(f"    🤖 Running LLM multi-agent pipeline for {ticker}...")
            
            # Run the full multi-agent pipeline
            orchestrator = BacktestOrchestrator()
            result = await orchestrator.run(
                symbol=ticker,
                asof_time=datetime.combine(as_of_date, datetime.min.time()).replace(tzinfo=timezone.utc),
                trigger={
                    "type": trigger.type,
                    "detail": trigger.detail,
                },
            )
            
            logger.info(f"    🤖 LLM decision: {result.get('action')} (confidence: {result.get('confidence', 0):.1%})")
            
            return result
            
        except Exception as e:
            # LLM failed - return hold (skip trade) instead of fallback rules
            logger.error(f"    ❌ LLM decision failed for {ticker}: {e}")
            return {
                'action': 'hold',
                'confidence': 0.0,
                'reasoning': f"LLM pipeline failed: {e}",
                'agent_outputs': {'error': str(e)},
            }


# ============================================================================
# Trigger Scanner
# ============================================================================
# Trigger Scanner with Tiered System
# ============================================================================

class TriggerScanner:
    """
    Scan for trading triggers on a given date with TIERED SYSTEM:
    
    TIER 1 (High Confidence - Run Full LLM Pipeline):
      - Technical: hourly breakout + daily trend confirmation + indicator confluence
      - Earnings: post-earnings gap/move within 1 day
      - SEC: 8-K material events
    
    TIER 2 (Context Modifiers - Affect Position Management):
      - VIX spikes
      - FOMC/CPI/NFP proximity
      - Handled by PositionMonitorAgent, NOT returned as entry triggers
    
    TIER 3 (Skip - No Action):
      - Corporate actions (dividends/splits)
      - Low-significance news
    """
    
    def __init__(self, db_url: str):
        self.db_url = db_url
    
    def scan(self, ticker: str, check_date: date) -> list[Trigger]:
        """
        Scan for TIER 1 triggers only (entry signals).
        Tier 2 events are handled by PositionMonitorAgent for open positions.
        Tier 3 events are skipped entirely.
        """
        triggers = []
        
        # === TIER 1: HIGH CONFIDENCE ENTRY SIGNALS ===
        
        # Technical with indicator confluence (primary entry signal)
        triggers.extend(self._scan_technical_advanced(ticker, check_date))
        
        # Post-earnings momentum (1 day after earnings only)
        triggers.extend(self._scan_post_earnings(ticker, check_date))
        
        # Material SEC 8-K filings
        triggers.extend(self._scan_sec_material(ticker, check_date))
        
        # === TIER 2: CONTEXT MODIFIERS (handled by PositionMonitorAgent) ===
        # VIX, FOMC, macro - NOT returned here, only affects open positions
        
        # === TIER 3: SKIP ENTIRELY ===
        # Corporate actions (dividends, splits) - ignored
        # Low-sig news - ignored
        
        return triggers
    
    def _scan_earnings(self, ticker: str, check_date: date) -> list[Trigger]:
        """Check for earnings within ±2 days."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT earnings_date, eps_actual, eps_est, revenue_actual
                    FROM earnings_event
                    WHERE symbol = %s 
                    AND earnings_date BETWEEN %s AND %s
                """, (ticker, check_date - timedelta(days=2), check_date + timedelta(days=2)))
                rows = cur.fetchall()
                
        triggers = []
        for row in rows:
            triggers.append(Trigger(
                type='earnings',
                ticker=ticker,
                date=check_date,
                detail={
                    'earnings_date': str(row[0]),
                    'eps_actual': float(row[1]) if row[1] else None,
                    'eps_estimate': float(row[2]) if row[2] else None,
                    'revenue_actual': float(row[3]) if row[3] else None,
                }
            ))
        return triggers
    
    def _scan_sec_filings(self, ticker: str, check_date: date) -> list[Trigger]:
        """Check for SEC 8-K filings."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT sf.form_type, sf.filed_at, sf.description
                    FROM sec_filing sf
                    JOIN security s ON sf.cik = s.cik
                    WHERE s.ticker = %s 
                    AND DATE(sf.filed_at) = %s
                    AND sf.form_type IN ('8-K', '8-K/A')
                """, (ticker, check_date))
                rows = cur.fetchall()
                
        triggers = []
        for row in rows:
            triggers.append(Trigger(
                type='sec',
                ticker=ticker,
                date=check_date,
                detail={
                    'form_type': row[0],
                    'filed_at': str(row[1]),
                    'description': row[2],
                }
            ))
        return triggers
    
    def _scan_macro(self, ticker: str, check_date: date) -> list[Trigger]:
        """Check for major macro events (FOMC, CPI, NFP only - not all indicators)."""
        MAJOR_EVENTS = ['FOMC', 'CPI', 'NFP', 'PCE', 'GDP']
        
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT indicator, actual, is_release_day
                    FROM economic_indicator
                    WHERE indicator_date = %s
                    AND is_release_day = TRUE
                """, (check_date,))
                rows = cur.fetchall()
        
        triggers = []
        for row in rows:
            indicator = row[0]
            # Only trigger on MAJOR events
            if any(major in indicator.upper() for major in MAJOR_EVENTS):
                triggers.append(Trigger(
                    type='macro',
                    ticker=ticker,  # Apply to specific ticker
                    date=check_date,
                    detail={
                        'indicator': indicator,
                        'actual': float(row[1]) if row[1] else None,
                    }
                ))
        return triggers
    
    def _scan_vix(self, ticker: str, check_date: date) -> list[Trigger]:
        """Check for VIX spikes (>25% above 20-day average)."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT date, close
                    FROM market_bar_daily
                    WHERE symbol = 'IDX_VIX' AND date <= %s
                    ORDER BY date DESC
                    LIMIT 21
                """, (check_date,))
                rows = cur.fetchall()
        
        if len(rows) < 20:
            return []
        
        today_vix = float(rows[0][1])
        avg_vix = sum(float(r[1]) for r in rows[1:]) / len(rows[1:])
        
        triggers = []
        # VIX spike: >25% above average
        if today_vix > avg_vix * 1.25:
            triggers.append(Trigger(
                type='vix_spike',
                ticker=ticker,
                date=check_date,
                detail={
                    'vix': today_vix,
                    'avg_vix': avg_vix,
                    'spike_pct': (today_vix / avg_vix - 1) * 100,
                }
            ))
        return triggers
    
    def _scan_technical_advanced(self, ticker: str, check_date: date) -> list[Trigger]:
        """
        TIER 1: Advanced multi-timeframe technical triggers with indicator confluence.
        
        Multi-timeframe approach:
        - HOURLY: Primary signal (breakout/breakdown)
        - DAILY: Trend confirmation (MA, RSI, MACD, ADX)
        - Enriches triggers with indicator values for LLM analysis
        """
        triggers = []
        
        # === DAILY CONTEXT (confirmation) ===
        daily_context = self._get_daily_context(ticker, check_date)
        if not daily_context:
            return []
        
        # === GET INDICATORS for enrichment ===
        # Import here to avoid circular imports
        from eiqora_v2.tools.prices import get_indicators
        import asyncio
        from datetime import datetime, timezone
        
        asof_time = datetime.combine(check_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        indicators = asyncio.run(get_indicators(ticker, 60, asof_time))
        
        # === HOURLY SIGNALS (primary triggers) ===
        hourly_triggers = self._scan_hourly_signals(ticker, check_date, daily_context)
        
        for trigger in hourly_triggers:
            # Add daily context to trigger detail
            trigger.detail['daily_context'] = daily_context
            
            # === ENRICH WITH INDICATOR CONFLUENCE ===
            if not indicators.get('error'):
                trigger.detail['indicators'] = {
                    'rsi14': indicators.get('rsi14', 50),
                    'macd_histogram': indicators.get('macd', {}).get('histogram', 0),
                    'adx14': indicators.get('adx14', 25),
                    'bollinger_position': indicators.get('bollinger', {}).get('price_position', 'MIDDLE'),
                    'obv_trend': indicators.get('obv', {}).get('trend', 'NEUTRAL'),
                    'state_tags': indicators.get('state_tags', []),
                }
            
            triggers.append(trigger)
        
        return triggers
        
        return triggers
    
    def _get_daily_context(self, ticker: str, check_date: date) -> dict | None:
        """Get daily timeframe context for confirmation."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT date, open, high, low, close, volume
                    FROM market_bar_daily
                    WHERE symbol = %s AND date <= %s
                    ORDER BY date DESC
                    LIMIT 60
                """, (ticker, check_date))
                rows = cur.fetchall()
        
        if len(rows) < 20:
            return None
        
        closes = [float(r[4]) for r in rows]
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        volumes = [int(r[5] or 0) for r in rows]
        
        # Calculate daily metrics
        current_price = closes[0]
        ma20 = sum(closes[:20]) / 20
        ma50 = sum(closes[:50]) / 50 if len(closes) >= 50 else ma20
        high_20d = max(highs[1:21]) if len(highs) > 20 else max(highs[1:])
        low_20d = min(lows[1:21]) if len(lows) > 20 else min(lows[1:])
        avg_volume = sum(volumes[1:21]) / 20 if len(volumes) > 20 else sum(volumes[1:]) / max(1, len(volumes[1:]))
        
        # Trend determination
        trend = "UPTREND" if current_price > ma20 and ma20 > ma50 else \
                "DOWNTREND" if current_price < ma20 and ma20 < ma50 else "SIDEWAYS"
        
        # Distance to key levels
        distance_to_high = (high_20d - current_price) / current_price
        distance_to_low = (current_price - low_20d) / current_price
        
        return {
            'trend': trend,
            'current_price': current_price,
            'ma20': ma20,
            'ma50': ma50,
            'high_20d': high_20d,
            'low_20d': low_20d,
            'avg_volume': avg_volume,
            'distance_to_high_pct': distance_to_high * 100,
            'distance_to_low_pct': distance_to_low * 100,
            'above_ma20': current_price > ma20,
            'above_ma50': current_price > ma50,
        }
    
    def _scan_hourly_signals(self, ticker: str, check_date: date, daily_context: dict) -> list[Trigger]:
        """Scan hourly bars for intraday triggers."""
        triggers = []
        
        # Get hourly bars for the check date and previous day
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT datetime, open, high, low, close, volume
                    FROM market_bar_hourly
                    WHERE symbol = %s 
                    AND datetime::date BETWEEN %s AND %s
                    ORDER BY datetime ASC
                """, (ticker, check_date - timedelta(days=2), check_date))
                rows = cur.fetchall()
        
        if len(rows) < 8:  # Need at least 8 hourly bars
            return []
        
        # Group by date
        hourly_today = [r for r in rows if r[0].date() == check_date]
        hourly_prev = [r for r in rows if r[0].date() < check_date]
        
        if not hourly_today or not hourly_prev:
            return []
        
        # Calculate hourly metrics
        today_volumes = [int(r[5] or 0) for r in hourly_today]
        prev_volumes = [int(r[5] or 0) for r in hourly_prev]
        avg_hourly_volume = sum(prev_volumes) / max(1, len(prev_volumes))
        
        prev_highs = [float(r[2]) for r in hourly_prev]
        prev_lows = [float(r[3]) for r in hourly_prev]
        hourly_high_range = max(prev_highs) if prev_highs else 0
        hourly_low_range = min(prev_lows) if prev_lows else 0
        
        # Check each hourly bar for signals
        for bar in hourly_today:
            bar_time = bar[0]
            bar_open = float(bar[1])
            bar_high = float(bar[2])
            bar_low = float(bar[3])
            bar_close = float(bar[4])
            bar_volume = int(bar[5] or 0)
            
            # 1. HOURLY BREAKOUT: Close above previous day's hourly range high
            if bar_close > hourly_high_range * 1.002:  # 0.2% buffer
                # Only trigger if daily trend confirms
                if daily_context['trend'] in ('UPTREND', 'SIDEWAYS'):
                    triggers.append(Trigger(
                        type='technical',
                        ticker=ticker,
                        date=check_date,
                        detail={
                            'signal': 'hourly_breakout_high',
                            'bar_time': str(bar_time),
                            'close': bar_close,
                            'level': hourly_high_range,
                            'confirmed_by': 'daily_uptrend' if daily_context['trend'] == 'UPTREND' else 'daily_sideways',
                        }
                    ))
                    break  # Only one breakout trigger per day
            
            # 2. HOURLY BREAKDOWN: Close below previous day's hourly range low
            if bar_close < hourly_low_range * 0.998:  # 0.2% buffer
                if daily_context['trend'] in ('DOWNTREND', 'SIDEWAYS'):
                    triggers.append(Trigger(
                        type='technical',
                        ticker=ticker,
                        date=check_date,
                        detail={
                            'signal': 'hourly_breakdown_low',
                            'bar_time': str(bar_time),
                            'close': bar_close,
                            'level': hourly_low_range,
                            'confirmed_by': 'daily_downtrend' if daily_context['trend'] == 'DOWNTREND' else 'daily_sideways',
                        }
                    ))
                    break
            
            # 3. HOURLY VOLUME SURGE: >3x average hourly volume
            if bar_volume > avg_hourly_volume * 3:
                triggers.append(Trigger(
                    type='technical',
                    ticker=ticker,
                    date=check_date,
                    detail={
                        'signal': 'hourly_volume_surge',
                        'bar_time': str(bar_time),
                        'volume': bar_volume,
                        'avg_volume': avg_hourly_volume,
                        'ratio': bar_volume / avg_hourly_volume if avg_hourly_volume > 0 else 0,
                        'price_direction': 'up' if bar_close > bar_open else 'down',
                    }
                ))
                break  # Only one volume surge trigger per day
        
        # 4. SUPPORT BOUNCE: Price near MA20 and bouncing in uptrend
        ma20 = daily_context.get('ma20', 0)
        current_price = daily_context.get('current_price', 0)
        if ma20 > 0 and current_price > 0:
            distance_to_ma20_pct = (current_price - ma20) / ma20
            # Price within 2% of MA20 and daily trend is up or sideways
            if -0.02 < distance_to_ma20_pct < 0.02:
                if daily_context['trend'] in ('UPTREND', 'SIDEWAYS'):
                    triggers.append(Trigger(
                        type='technical',
                        ticker=ticker,
                        date=check_date,
                        detail={
                            'signal': 'support_bounce_ma20',
                            'close': current_price,
                            'ma20': ma20,
                            'distance_pct': distance_to_ma20_pct * 100,
                            'trend': daily_context['trend'],
                        }
                    ))
        
        # 5. PULLBACK ENTRY: Uptrend but price pulled back (lower from recent high)
        high_20d = daily_context.get('high_20d', 0)
        if high_20d > 0 and current_price > 0:
            pullback_pct = (high_20d - current_price) / high_20d
            # Price 3-8% below 20d high, still in uptrend - classic pullback entry
            if 0.03 < pullback_pct < 0.08 and daily_context['trend'] == 'UPTREND':
                triggers.append(Trigger(
                    type='technical',
                    ticker=ticker,
                    date=check_date,
                    detail={
                        'signal': 'pullback_entry',
                        'close': current_price,
                        'high_20d': high_20d,
                        'pullback_pct': pullback_pct * 100,
                        'above_ma20': daily_context.get('above_ma20', False),
                    }
                ))
        
        # Daily breakout removed - using hourly as primary trigger, daily for confirmation only
        
        return triggers
    
    def _scan_post_earnings(self, ticker: str, check_date: date) -> list[Trigger]:
        """
        TIER 1: Post-earnings momentum trigger.
        Only triggers 1 day AFTER earnings with significant move (>3% gap or beat/miss).
        """
        triggers = []
        
        # Look for earnings that happened YESTERDAY
        yesterday = check_date - timedelta(days=1)
        while yesterday.weekday() >= 5:
            yesterday -= timedelta(days=1)
        
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT earnings_date, eps_actual, eps_est, revenue_actual
                    FROM earnings_event
                    WHERE symbol = %s AND earnings_date = %s
                """, (ticker, yesterday))
                row = cur.fetchone()
        
        if not row:
            return []
        
        eps_actual = float(row[1]) if row[1] else None
        eps_est = float(row[2]) if row[2] else None
        
        # Check for beat/miss
        earnings_surprise = None
        if eps_actual and eps_est and eps_est != 0:
            earnings_surprise = (eps_actual - eps_est) / abs(eps_est)
        
        # Get price gap
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT date, open, close
                    FROM market_bar_daily
                    WHERE symbol = %s AND date IN (%s, %s)
                    ORDER BY date DESC
                """, (ticker, check_date, yesterday))
                bars = cur.fetchall()
        
        if len(bars) < 2:
            return []
        
        today_open = float(bars[0][1])
        yesterday_close = float(bars[1][2])
        gap_pct = (today_open - yesterday_close) / yesterday_close
        
        # Only trigger if significant gap (>3%) OR meaningful beat/miss (>10%)
        is_significant = abs(gap_pct) > 0.03 or (earnings_surprise and abs(earnings_surprise) > 0.10)
        
        if is_significant:
            triggers.append(Trigger(
                type='post_earnings',
                ticker=ticker,
                date=check_date,
                detail={
                    'earnings_date': str(yesterday),
                    'eps_actual': eps_actual,
                    'eps_estimate': eps_est,
                    'earnings_surprise_pct': earnings_surprise * 100 if earnings_surprise else None,
                    'gap_pct': gap_pct * 100,
                    'direction': 'beat' if (earnings_surprise and earnings_surprise > 0) else 'miss' if earnings_surprise else 'gap',
                }
            ))
        
        return triggers
    
    def _scan_sec_material(self, ticker: str, check_date: date) -> list[Trigger]:
        """
        TIER 1: Material SEC 8-K filings only.
        8-K indicates material events (M&A, leadership changes, guidance).
        """
        triggers = []
        
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT sf.form_type, sf.filed_at, sf.description
                    FROM sec_filing sf
                    JOIN security s ON sf.cik = s.cik
                    WHERE s.ticker = %s 
                    AND DATE(sf.filed_at) = %s
                    AND sf.form_type IN ('8-K', '8-K/A')
                """, (ticker, check_date))
                rows = cur.fetchall()
        
        for row in rows:
            triggers.append(Trigger(
                type='sec_8k',
                ticker=ticker,
                date=check_date,
                detail={
                    'form_type': row[0],
                    'filed_at': str(row[1]),
                    'description': row[2] or 'Material event',
                }
            ))
        
        return triggers
    
    # === LEGACY METHODS (kept for reference, not called in Tier 1 scan) ===
    
    def _scan_corporate_actions(self, ticker: str, check_date: date) -> list[Trigger]:
        """TIER 3 (SKIPPED): Corporate actions don't trigger trades."""
        return []  # Explicitly skip - dividends/splits are not trading signals
    
    def _scan_news(self, ticker: str, check_date: date) -> list[Trigger]:
        """Check for significant news (high volume of articles)."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                # Count articles for this ticker today (use ticker column directly)
                cur.execute("""
                    SELECT COUNT(*) 
                    FROM gdelt_news 
                    WHERE DATE(published_at) = %s
                    AND ticker = %s
                """, (check_date, ticker))
                today_count = cur.fetchone()[0] or 0
                
                # Get 7-day average
                cur.execute("""
                    SELECT COUNT(*) / 7.0
                    FROM gdelt_news 
                    WHERE DATE(published_at) BETWEEN %s AND %s
                    AND ticker = %s
                """, (check_date - timedelta(days=7), check_date - timedelta(days=1), ticker))
                avg_count = cur.fetchone()[0] or 1
        
        triggers = []
        # News surge: 3x average article count
        if today_count > avg_count * 3 and today_count >= 3:
            triggers.append(Trigger(
                type='news',
                ticker=ticker,
                date=check_date,
                detail={'signal': 'news_surge', 'count': today_count, 'avg_count': avg_count}
            ))
        
        return triggers


# ============================================================================
# Backtest Engine
# ============================================================================

class RealisticBacktestEngine:
    """Realistic backtest engine using LLM multi-agent pipeline (mandatory)."""
    
    def __init__(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        initial_capital: float = 100000.0,
        position_size_pct: float = 0.1,
        name: Optional[str] = None,
    ):
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.name = name or f"Backtest_{start_date}_{end_date}"
        
        self.db_url = get_db_url()
        self.run_id = uuid.uuid4()
        self.portfolio = Portfolio(cash=initial_capital)
        self.trigger_scanner = TriggerScanner(self.db_url)
        self.decision_maker = LLMDecisionMaker(self.db_url)  # LLM-only
    
    def run(self) -> dict:
        """Run the backtest."""
        return asyncio.run(self._run_async())
    
    async def _run_async(self) -> dict:
        """Async run implementation."""
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        
        logger.info(f"Starting REALISTIC backtest: {self.name}")
        logger.info(f"Period: {self.start_date} to {self.end_date}")
        logger.info(f"Tickers: {self.tickers}")
        logger.info(f"Decision Mode: LLM Multi-Agent Pipeline")
        logger.info(f"Initial capital: ${self.initial_capital:,.2f}")
        
        self._create_run()
        
        current_date = self.start_date
        while current_date <= self.end_date:
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            
            # Process new triggers (only for tickers without open positions)
            for ticker in self.tickers:
                if ticker not in self.portfolio.positions:
                    await self._process_ticker(ticker, current_date)
            
            # Check TP/SL on hourly bars for open positions
            await self._check_tpsl_exits(current_date)
            
            # Monitor open positions for Tier 2 events (VIX, FOMC, earnings, etc.)
            await self._monitor_open_positions(current_date)
            
            current_date += timedelta(days=1)
        
        self._close_all_positions(self.end_date)
        
        stats = self._calculate_stats()
        self._update_run(stats)
        
        logger.info(f"Backtest complete!")
        logger.info(f"Total trades: {stats['total_trades']}")
        logger.info(f"Win rate: {stats['win_rate']:.1%}")
        logger.info(f"Total P&L: ${stats['total_pnl']:,.2f}")
        
        return stats
    
    async def _check_tpsl_exits(self, check_date: date):
        """Check TP/SL exits using hourly bars."""
        positions_to_close = []
        
        for ticker, trade in self.portfolio.positions.items():
            # Get hourly bars for today
            hourly_bars = self._get_hourly_bars(ticker, check_date)
            
            for bar in hourly_bars:
                bar_high = float(bar[2])
                bar_low = float(bar[3])
                bar_close = float(bar[4])
                bar_time = bar[0]
                
                if isinstance(bar_time, datetime) and trade.entry_date:
                    bar_dt = bar_time
                    entry_dt = trade.entry_date
                    if bar_dt.tzinfo and not entry_dt.tzinfo:
                        bar_dt = bar_dt.replace(tzinfo=None)
                    if entry_dt.tzinfo and not bar_dt.tzinfo:
                        entry_dt = entry_dt.replace(tzinfo=None)
                    if bar_dt < entry_dt:
                        continue
                
                # Check TP hit
                if trade.direction == 'long' and bar_high >= trade.take_profit:
                    trade.exit_price = trade.take_profit
                    trade.exit_date = bar_time if isinstance(bar_time, datetime) else datetime.combine(check_date, datetime.min.time())
                    trade.exit_reason = 'tp'
                    positions_to_close.append((ticker, trade))
                    logger.info(f"  TP HIT: {ticker} @ ${trade.take_profit:.2f}")
                    break
                
                # Check SL hit
                elif trade.direction == 'long' and bar_low <= trade.stop_loss:
                    trade.exit_price = trade.stop_loss
                    trade.exit_date = bar_time if isinstance(bar_time, datetime) else datetime.combine(check_date, datetime.min.time())
                    trade.exit_reason = 'sl'
                    positions_to_close.append((ticker, trade))
                    logger.info(f"  SL HIT: {ticker} @ ${trade.stop_loss:.2f}")
                    break
            
            # Check time stop
            if ticker not in [p[0] for p in positions_to_close]:
                days_held = (check_date - trade.entry_date.date()).days
                if days_held >= trade.max_holding_days:
                    daily_close = self._get_daily_close(ticker, check_date)
                    if daily_close:
                        trade.exit_price = daily_close
                        trade.exit_date = datetime.combine(check_date, datetime.min.time())
                        trade.exit_reason = 'time_stop'
                        positions_to_close.append((ticker, trade))
                        logger.info(f"  TIME STOP: {ticker} @ ${daily_close:.2f} (held {days_held} days)")
        
        # Close positions
        for ticker, trade in positions_to_close:
            self._finalize_exit(ticker, trade)
    
    async def _process_ticker(self, ticker: str, check_date: date):
        """Process a single ticker for potential entry."""
        signal_date = self._previous_trading_day(check_date)
        triggers = self.trigger_scanner.scan(ticker, signal_date)
        
        if not triggers:
            return
        
        logger.info(f"  {check_date} {ticker}: {len(triggers)} triggers (signal {signal_date})")
        
        # Get entry price from first hourly bar of the day
        entry_bar = self._get_entry_bar(ticker, check_date)
        if not entry_bar:
            return
        
        entry_time, entry_price = entry_bar
        
        for trigger in triggers:
            # Get decision from LLM or rules
            decision = await self.decision_maker.make_decision(
                ticker, trigger, signal_date, entry_price
            )
            
            if decision['action'] in ('enter_long', 'enter_short'):
                decision = self._override_entry_levels(decision, entry_price)
                self._execute_entry(ticker, decision, trigger, entry_time)
                break
    
    def _execute_entry(self, ticker: str, decision: dict, trigger: Trigger, entry_time: datetime):
        """Execute trade entry with proper TP/SL."""
        entry_price = decision['entry_price']
        position_size = self.portfolio.cash * self.position_size_pct
        shares = int(position_size / entry_price)
        
        if shares < 1:
            return
        
        direction = 'long' if decision['action'] == 'enter_long' else 'short'
        
        trade = Trade(
            ticker=ticker,
            direction=direction,
            entry_date=entry_time,
            entry_price=entry_price,
            take_profit=decision['take_profit'],
            stop_loss=decision['stop_loss'],
            max_holding_days=decision.get('max_holding_days', 30),
            shares=shares,
            position_size=shares * entry_price,
            trigger_type=trigger.type,
            trigger_detail=trigger.detail,
            agent_signals=decision.get('agent_outputs', {}),
            confidence_score=decision['confidence'],
        )
        
        self.portfolio.positions[ticker] = trade
        self.portfolio.cash -= trade.position_size
        
        tp_pct = ((trade.take_profit - entry_price) / entry_price) * 100
        sl_pct = ((entry_price - trade.stop_loss) / entry_price) * 100
        
        logger.info(
            f"    ENTRY: {direction.upper()} {ticker} @ ${entry_price:.2f} x {shares} "
            f"| TP: ${trade.take_profit:.2f} (+{tp_pct:.1f}%) "
            f"| SL: ${trade.stop_loss:.2f} (-{sl_pct:.1f}%)"
        )
    
    def _finalize_exit(self, ticker: str, trade: Trade):
        """Finalize trade exit and calculate P&L."""
        if trade.direction == 'long':
            trade.pnl = (trade.exit_price - trade.entry_price) * trade.shares
            trade.pnl_pct = (trade.exit_price - trade.entry_price) / trade.entry_price
        else:
            trade.pnl = (trade.entry_price - trade.exit_price) * trade.shares
            trade.pnl_pct = (trade.entry_price - trade.exit_price) / trade.entry_price
        
        # Calculate holding hours (handle timezone-aware vs naive datetimes)
        try:
            exit_dt = trade.exit_date.replace(tzinfo=None) if trade.exit_date.tzinfo else trade.exit_date
            entry_dt = trade.entry_date.replace(tzinfo=None) if trade.entry_date and trade.entry_date.tzinfo else trade.entry_date
            trade.holding_hours = int((exit_dt - entry_dt).total_seconds() / 3600) if entry_dt else 0
        except Exception:
            trade.holding_hours = 0
        
        self.portfolio.cash += trade.shares * trade.exit_price
        self.portfolio.trades.append(trade)
        del self.portfolio.positions[ticker]
        
        self._log_trade(trade)
        
        pnl_symbol = "+" if trade.pnl >= 0 else ""
        logger.info(
            f"    EXIT ({trade.exit_reason.upper()}): {ticker} @ ${trade.exit_price:.2f} "
            f"| P&L: {pnl_symbol}${trade.pnl:.2f} ({pnl_symbol}{trade.pnl_pct:.1%})"
        )
    
    async def _monitor_open_positions(self, check_date: date):
        """
        Monitor open positions for Tier 2 events (VIX, FOMC, earnings, trend).
        Uses PositionMonitorAgent to recommend HOLD/TIGHTEN/WIDEN/EXIT actions.
        """
        if not self.portfolio.positions:
            return
        
        try:
            from eiqora_v2.agents.position_monitor import PositionMonitorAgent
            
            positions_to_close = []
            monitor_agent = PositionMonitorAgent()
            
            for ticker, trade in self.portfolio.positions.items():
                # Get current price
                current_price = self._get_daily_close(ticker, check_date)
                if not current_price:
                    continue
                
                # Build position context for the agent
                state = {
                    "symbol": ticker,
                    "asof_time": datetime.combine(check_date, datetime.min.time()).replace(tzinfo=timezone.utc),
                    "position": {
                        "direction": trade.direction,
                        "entry_date": trade.entry_date,
                        "entry_price": trade.entry_price,
                        "current_price": current_price,
                        "stop_loss": trade.stop_loss,
                        "take_profit": trade.take_profit,
                        "max_holding_days": trade.max_holding_days,
                    },
                }
                
                # Run monitor agent
                result = await monitor_agent.run(state)
                monitor_output = result.get("position_monitor", {})
                
                action = monitor_output.get("action", "HOLD")
                reason = monitor_output.get("reason", "")
                
                if action == "EXIT":
                    # Exit now
                    trade.exit_price = current_price
                    trade.exit_date = datetime.combine(check_date, datetime.min.time())
                    trade.exit_reason = f"monitor_exit ({reason[:30]})"
                    positions_to_close.append((ticker, trade))
                    logger.info(f"  🔔 MONITOR EXIT: {ticker} - {reason}")
                
                elif action == "TIGHTEN":
                    # Tighten stop loss
                    new_sl = monitor_output.get("new_stop_loss")
                    if new_sl and new_sl > trade.stop_loss:  # For long positions
                        old_sl = trade.stop_loss
                        trade.stop_loss = new_sl
                        logger.info(f"  🔔 TIGHTEN SL: {ticker} ${old_sl:.2f} → ${new_sl:.2f}")
                
                elif action == "WIDEN":
                    # Widen stop loss (give more room)
                    new_sl = monitor_output.get("new_stop_loss")
                    if new_sl and new_sl < trade.stop_loss:  # For long positions
                        old_sl = trade.stop_loss
                        trade.stop_loss = new_sl
                        logger.info(f"  🔔 WIDEN SL: {ticker} ${old_sl:.2f} → ${new_sl:.2f}")
            
            # Close positions recommended for exit
            for ticker, trade in positions_to_close:
                self._finalize_exit(ticker, trade)
                
        except ImportError:
            # Agent not available, skip monitoring
            pass
        except Exception as e:
            logger.warning(f"Position monitoring failed: {e}")
    
    def _close_all_positions(self, exit_date: date):
        """Close all positions at end of backtest."""
        for ticker in list(self.portfolio.positions.keys()):
            trade = self.portfolio.positions[ticker]
            exit_price = self._get_daily_close(ticker, exit_date)
            if exit_price:
                trade.exit_price = exit_price
                trade.exit_date = datetime.combine(exit_date, datetime.min.time())
                trade.exit_reason = 'close'
                self._finalize_exit(ticker, trade)
    
    def _get_hourly_bars(self, ticker: str, check_date: date) -> list:
        """Get hourly bars for a date."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT datetime, open, high, low, close, volume
                    FROM market_bar_hourly
                    WHERE symbol = %s AND DATE(datetime) = %s
                    ORDER BY datetime
                """, (ticker, check_date))
                return cur.fetchall()

    def _get_entry_bar(self, ticker: str, check_date: date) -> Optional[tuple[datetime, float]]:
        """Get the first hourly bar of the day for entry pricing."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT datetime, open
                    FROM market_bar_hourly
                    WHERE symbol = %s AND DATE(datetime) = %s
                    ORDER BY datetime ASC
                    LIMIT 1
                """, (ticker, check_date))
                row = cur.fetchone()
                if not row:
                    return None
                return row[0], float(row[1])
    
    def _get_daily_open(self, ticker: str, check_date: date) -> Optional[float]:
        """Get daily open price."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT open FROM market_bar_daily
                    WHERE symbol = %s AND date = %s
                """, (ticker, check_date))
                row = cur.fetchone()
                return float(row[0]) if row else None

    def _previous_trading_day(self, day: date) -> date:
        """Get the previous trading day (skips weekends)."""
        prev_day = day - timedelta(days=1)
        while prev_day.weekday() >= 5:
            prev_day -= timedelta(days=1)
        return prev_day

    def _override_entry_levels(self, decision: dict, entry_price: float) -> dict:
        """Recompute entry/TP/SL based on hourly entry price."""
        updated = decision.copy()
        updated["entry_price"] = entry_price
        
        agent_outputs = decision.get("agent_outputs", {}) or {}
        decision_payload = agent_outputs.get("decision", {}) or {}
        rule = decision_payload.get("rule") if isinstance(decision_payload, dict) else None
        context = agent_outputs.get("context", {}) or {}
        vol_basis = context.get("vol_basis", {}) if isinstance(context, dict) else {}
        rv20 = vol_basis.get("value") if isinstance(vol_basis, dict) else None
        
        action = decision.get("action", "hold")
        direction = "LONG" if action == "enter_long" else "SHORT"
        
        tp_distance = None
        sl_distance = None
        
        if rule and rv20:
            tp_mult = rule.get("tp_mult", 4.0)
            sl_mult = rule.get("sl_mult", 2.0)
            tp_distance = entry_price * rv20 * tp_mult
            sl_distance = entry_price * rv20 * sl_mult
        else:
            decision_entry = decision.get("entry_price", entry_price)
            if decision.get("take_profit"):
                tp_distance = abs(decision["take_profit"] - decision_entry)
            if decision.get("stop_loss"):
                sl_distance = abs(decision_entry - decision["stop_loss"])
        
        if tp_distance and sl_distance:
            if direction == "LONG":
                updated["take_profit"] = entry_price + tp_distance
                updated["stop_loss"] = entry_price - sl_distance
            else:
                updated["take_profit"] = entry_price - tp_distance
                updated["stop_loss"] = entry_price + sl_distance
        
        return updated
    
    def _get_daily_close(self, ticker: str, check_date: date) -> Optional[float]:
        """Get daily close price."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT close FROM market_bar_daily
                    WHERE symbol = %s AND date <= %s
                    ORDER BY date DESC LIMIT 1
                """, (ticker, check_date))
                row = cur.fetchone()
                return float(row[0]) if row else None
    
    def _create_run(self):
        """Create backtest run record."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO backtest_run (run_id, name, start_date, end_date, tickers, config, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'running')
                """, (
                    str(self.run_id),
                    self.name,
                    self.start_date,
                    self.end_date,
                    self.tickers,
                    json.dumps({
                        'initial_capital': self.initial_capital,
                        'position_size_pct': self.position_size_pct,
                        'decision_mode': 'llm_multiagent',
                    }),
                ))
            conn.commit()
    
    def _log_trade(self, trade: Trade):
        """Log trade to database."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO backtest_trade 
                        (backtest_run_id, ticker, direction, entry_date, entry_price, 
                         exit_date, exit_price, shares, position_size, trigger_type, 
                         trigger_detail, agent_signals, confidence_score, pnl, pnl_pct, holding_hours)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    str(self.run_id),
                    trade.ticker,
                    trade.direction,
                    trade.entry_date,
                    trade.entry_price,
                    trade.exit_date,
                    trade.exit_price,
                    trade.shares,
                    trade.position_size,
                    trade.trigger_type,
                    json.dumps(trade.trigger_detail) if trade.trigger_detail else None,
                    f'{{"exit_reason": "{trade.exit_reason}", "tp": {trade.take_profit}, "sl": {trade.stop_loss}}}',
                    trade.confidence_score,
                    trade.pnl,
                    trade.pnl_pct,
                    trade.holding_hours,
                ))
            conn.commit()
    
    def _calculate_stats(self) -> dict:
        """Calculate backtest statistics."""
        trades = self.portfolio.trades
        
        if not trades:
            return {
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                'win_rate': 0.0, 'total_pnl': 0.0, 'avg_pnl_per_trade': 0.0,
                'sharpe_ratio': 0.0, 'max_drawdown': 0.0,
                'tp_exits': 0, 'sl_exits': 0, 'time_exits': 0,
            }
        
        winning = [t for t in trades if t.pnl and t.pnl > 0]
        losing = [t for t in trades if t.pnl and t.pnl < 0]
        total_pnl = sum(t.pnl or 0 for t in trades)
        
        # Count exit types
        tp_exits = len([t for t in trades if t.exit_reason == 'tp'])
        sl_exits = len([t for t in trades if t.exit_reason == 'sl'])
        time_exits = len([t for t in trades if t.exit_reason == 'time_stop'])
        
        return {
            'total_trades': len(trades),
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': len(winning) / len(trades),
            'total_pnl': total_pnl,
            'avg_pnl_per_trade': total_pnl / len(trades),
            'sharpe_ratio': 0.0,  # TODO
            'max_drawdown': 0.0,  # TODO
            'tp_exits': tp_exits,
            'sl_exits': sl_exits,
            'time_exits': time_exits,
        }
    
    def _update_run(self, stats: dict):
        """Update run with final stats."""
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE backtest_run
                    SET total_trades = %s, winning_trades = %s, losing_trades = %s,
                        win_rate = %s, total_pnl = %s, avg_pnl_per_trade = %s,
                        status = 'completed', completed_at = NOW()
                    WHERE run_id = %s
                """, (
                    stats['total_trades'], stats['winning_trades'], stats['losing_trades'],
                    stats['win_rate'], stats['total_pnl'], stats['avg_pnl_per_trade'],
                    str(self.run_id),
                ))
            conn.commit()


# ============================================================================
# CLI
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Eiqora LLM Multi-Agent Backtest")
    parser.add_argument("--tickers", nargs="+", default=["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN"])
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=100000)
    parser.add_argument("--collect", action="store_true", help="Run data collection pipelines first")
    parser.add_argument("--name", help="Backtest name")
    
    args = parser.parse_args()
    
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    
    # Run data collection if requested
    if args.collect:
        from eiqora_v2.backtest.data_collector import BacktestDataCollector
        
        logger.info("Running data collection pipelines...")
        collector = BacktestDataCollector(
            tickers=args.tickers,
            start_date=start_date,
            end_date=end_date,
        )
        collector.collect_all()
    
    engine = RealisticBacktestEngine(
        tickers=args.tickers,
        start_date=start_date,
        end_date=end_date,
        initial_capital=args.capital,
        name=args.name,
    )
    
    stats = engine.run()
    print("\n" + "="*60)
    print("BACKTEST RESULTS")
    print("="*60)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
