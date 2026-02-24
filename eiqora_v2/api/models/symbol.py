"""Pydantic models for Symbol Detail API"""

from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class SymbolQuote(BaseModel):
    symbol: str
    price: float
    prev_close: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[int] = None
    date: date


class PriceBar(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None


class TechnicalIndicators(BaseModel):
    date: date
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    adx_14: Optional[float] = None
    atr_14: Optional[float] = None
    mfi_14: Optional[float] = None
    cmf_20: Optional[float] = None
    stochastic_k: Optional[float] = None
    stochastic_d: Optional[float] = None
    bb_upper_20: Optional[float] = None
    bb_middle_20: Optional[float] = None
    bb_lower_20: Optional[float] = None
    bb_width: Optional[float] = None
    support_level: Optional[float] = None
    resistance_level: Optional[float] = None
    volume_z_20: Optional[float] = None


class SymbolNewsItem(BaseModel):
    doc_id: int
    title: str
    published_at: Optional[datetime] = None
    provider: Optional[str] = None
    url: Optional[str] = None
    relevance_score: Optional[float] = None


class EarningsEvent(BaseModel):
    earnings_date: date
    time_of_day: Optional[str] = None
    eps_est: Optional[float] = None
    eps_actual: Optional[float] = None
    revenue_est: Optional[float] = None
    revenue_actual: Optional[float] = None


class AnalystRating(BaseModel):
    firm: str
    rating_date: Optional[datetime] = None
    action: Optional[str] = None
    to_grade: Optional[str] = None
    from_grade: Optional[str] = None
    price_target: Optional[float] = None


class OptionsSnapshot(BaseModel):
    as_of_date: Optional[date] = None
    put_call_ratio_volume: Optional[float] = None
    put_call_ratio_oi: Optional[float] = None
    atm_iv: Optional[float] = None
    max_pain_strike: Optional[float] = None
    highest_oi_call_strike: Optional[float] = None
    highest_oi_put_strike: Optional[float] = None
    total_call_volume: Optional[int] = None
    total_put_volume: Optional[int] = None
    total_call_oi: Optional[int] = None
    total_put_oi: Optional[int] = None


class FundamentalSummary(BaseModel):
    # Analyst consensus
    analyst_consensus: Optional[str] = None
    avg_price_target: Optional[float] = None
    upside_pct: Optional[float] = None
    upgrades_30d: Optional[int] = None
    downgrades_30d: Optional[int] = None
    buy_count: Optional[int] = None
    hold_count: Optional[int] = None
    sell_count: Optional[int] = None
    # Earnings trajectory
    beat_rate: Optional[float] = None
    eps_trend: Optional[str] = None
    revenue_trend: Optional[str] = None
    consecutive_beats: Optional[int] = None
    # Dividend
    has_dividend: bool = False
    dividend_yield: Optional[float] = None
    annual_dividend: Optional[float] = None
    # Profile
    revenue_growth_3y: Optional[float] = None
    profitability_trend: Optional[str] = None


class IntradayBar(BaseModel):
    datetime: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None


class SymbolDetailResponse(BaseModel):
    quote: SymbolQuote
    price_history: List[PriceBar]
    intraday_history: List[IntradayBar] = []
    indicators: Optional[TechnicalIndicators] = None
    news: List[SymbolNewsItem]
    earnings: List[EarningsEvent]
    analyst_ratings: List[AnalystRating]
    options: Optional[OptionsSnapshot] = None
    fundamentals: Optional[FundamentalSummary] = None
