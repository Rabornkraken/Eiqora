"""
Fundamental and Sentiment schemas.
"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


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
    
    data_status: DataStatus = Field(description="Data freshness status")
    
    analysis_timestamp: datetime = Field(description="When analysis was performed")
    
    error: str | None = Field(default=None, description="Error message if failed")
