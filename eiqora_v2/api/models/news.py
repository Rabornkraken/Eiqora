"""Pydantic models for News Feed API"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class NewsArticle(BaseModel):
    doc_id: int
    ticker: str
    title: str
    published_at: Optional[datetime] = None
    provider: Optional[str] = None
    url: Optional[str] = None
    relevance_score: Optional[float] = None


class NewsResponse(BaseModel):
    articles: List[NewsArticle]
    total: int
    page: int
    page_size: int
