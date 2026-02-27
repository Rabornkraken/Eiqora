"""
Fundamental and Sentiment schemas.
"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class AnalystConsensus(BaseModel):
    """Aggregated analyst rating consensus."""
    total_ratings_90d: int = Field(default=0, description="Ratings in last 90 days")
    avg_price_target: float | None = Field(default=None, description="Average price target")
    upside_pct: float | None = Field(default=None, description="(avg_pt - price) / price * 100")
    upgrade_count_30d: int = Field(default=0, description="Upgrades in last 30 days")
    downgrade_count_30d: int = Field(default=0, description="Downgrades in last 30 days")
    buy_count: int = Field(default=0, description="Buy-equivalent ratings")
    hold_count: int = Field(default=0, description="Hold-equivalent ratings")
    sell_count: int = Field(default=0, description="Sell-equivalent ratings")
    consensus: str = Field(default="MIXED", description="BUY | HOLD | SELL | MIXED")
    recent_actions: list[dict] = Field(default_factory=list, description="Last 5 actions")


class EarningsTrajectory(BaseModel):
    """Multi-quarter earnings trajectory."""
    quarters: list[dict] = Field(default_factory=list, description="Last 8 quarters of data")
    beat_rate: float | None = Field(default=None, description="% of quarters with EPS beat")
    consecutive_beats: int = Field(default=0, description="Current streak (negative = misses)")
    eps_trend: str = Field(default="INSUFFICIENT_DATA", description="ACCELERATING | DECELERATING | STABLE | INSUFFICIENT_DATA")
    revenue_trend: str = Field(default="INSUFFICIENT_DATA", description="ACCELERATING | DECELERATING | STABLE | INSUFFICIENT_DATA")
    quarters_available: int = Field(default=0)


class DividendSummary(BaseModel):
    """Dividend information from corporate actions."""
    has_dividend: bool = Field(default=False)
    annual_dividend: float | None = Field(default=None, description="Sum of last 4 quarterly dividends")
    yield_pct: float | None = Field(default=None, description="annual_div / current_price * 100")
    most_recent_amount: float | None = Field(default=None)
    most_recent_ex_date: str | None = Field(default=None)
    next_ex_date: str | None = Field(default=None, description="Future ex_date if exists")
    payment_count_1y: int = Field(default=0, description="Dividends in last 12 months")


class ProfileFundamentals(BaseModel):
    """Structured fundamentals from ticker_profile."""
    revenue_growth_3y: float | None = Field(default=None)
    profitability_trend: str | None = Field(default=None)
    valuation_summary: str | None = Field(default=None)
    earnings_trajectory: str | None = Field(default=None, description="Narrative from profile")


class SentimentSummary(BaseModel):
    """Aggregated sentiment from recent news."""
    overall: Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"] = Field(
        description="Overall sentiment"
    )
    news_count: int = Field(description="Number of news articles analyzed")
    positive_count: int = Field(default=0, description="Positive mentions")
    negative_count: int = Field(default=0, description="Negative mentions")
    neutral_count: int = Field(default=0, description="Neutral mentions")
    key_topics: list[str] = Field(default_factory=list, description="Main topics discussed")
    notable_headlines: list[str] = Field(default_factory=list, description="Important headlines")
    baseline_risks: list[str] = Field(default_factory=list, description="Known risks from profile (updated weekly)")
    baseline_catalysts: list[str] = Field(default_factory=list, description="Known catalysts from profile (updated weekly)")


class EarningsSnapshot(BaseModel):
    """Latest earnings information."""
    fiscal_quarter: str | None = Field(default=None, description="Fiscal quarter (e.g. Q3 2024)")
    eps_actual: float | None = Field(default=None, description="Actual EPS")
    eps_estimate: float | None = Field(default=None, description="Estimated EPS")
    eps_surprise_pct: float | None = Field(default=None, description="EPS surprise %")
    revenue_actual: float | None = Field(default=None, description="Actual revenue")
    revenue_growth_yoy: float | None = Field(default=None, description="YoY revenue growth %")
    guidance: Literal["RAISED", "MAINTAINED", "LOWERED", "NONE"] | None = Field(
        default=None, description="Guidance direction"
    )


class SECFilingSummary(BaseModel):
    """Summary of recent SEC filings."""
    recent_filings: list[dict] = Field(default_factory=list, description="Recent filing list")
    has_8k: bool = Field(default=False, description="Has material 8-K in window")
    has_10q: bool = Field(default=False, description="Has 10-Q in window")
    has_10k: bool = Field(default=False, description="Has 10-K in window")


class InsiderSummary(BaseModel):
    """Summary of recent insider transactions."""
    available: bool = Field(default=False, description="Insider data available")
    transactions_90d: int = Field(default=0, description="Total transactions in last 90 days")
    buy_count: int = Field(default=0, description="Buy transactions count")
    sell_count: int = Field(default=0, description="Sell transactions count")
    total_buy_value: float = Field(default=0.0, description="Total buy value in USD")
    total_sell_value: float = Field(default=0.0, description="Total sell value in USD")
    net_value: float = Field(default=0.0, description="Net buy value in USD")
    ceo_net_value: float = Field(default=0.0, description="CEO net buy value in USD")
    cfo_net_value: float = Field(default=0.0, description="CFO net buy value in USD")
    director_net_value: float = Field(default=0.0, description="Director net buy value in USD")
    other_net_value: float = Field(default=0.0, description="Other insider net buy value in USD")


class DataStatus(BaseModel):
    """Status of data sources."""
    news_fresh: bool = Field(description="News data is fresh")
    news_last_at: datetime | None = Field(default=None, description="Last news timestamp")
    sec_fresh: bool = Field(description="SEC data is fresh")
    sec_last_at: datetime | None = Field(default=None, description="Last SEC filing timestamp")
    earnings_fresh: bool = Field(description="Earnings data is fresh")
    collections_triggered: list[str] = Field(default_factory=list, description="Pipelines triggered")
    collection_errors: list[str] = Field(default_factory=list, description="Collection errors")


class FundamentalOutput(BaseModel):
    """Output schema for Fundamental Agent."""
    
    symbol: str = Field(description="Stock ticker")
    
    sentiment: SentimentSummary = Field(description="Aggregated sentiment from news")
    
    earnings: EarningsSnapshot | None = Field(default=None, description="Latest earnings")
    
    sec_filings: SECFilingSummary | None = Field(default=None, description="SEC filing summary")

    insider: InsiderSummary | None = Field(default=None, description="Insider transaction summary")

    analyst: AnalystConsensus | None = Field(default=None, description="Analyst rating consensus")

    earnings_trajectory: EarningsTrajectory | None = Field(default=None, description="Multi-quarter earnings trajectory")

    dividend: DividendSummary | None = Field(default=None, description="Dividend summary")

    profile_fundamentals: ProfileFundamentals | None = Field(default=None, description="Profile-based fundamentals")

    data_status: DataStatus = Field(description="Data freshness status")
    
    analysis_timestamp: datetime = Field(description="When analysis was performed")
    
    error: str | None = Field(default=None, description="Error message if failed")
