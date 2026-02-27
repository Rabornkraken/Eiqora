"""Service layer for News Feed API"""

import logging
from typing import Optional

from eiqora_v2.tools.db import get_connection
from eiqora_v2.api.models.news import NewsArticle, NewsResponse

logger = logging.getLogger(__name__)


class NewsService:
    async def get_news(
        self,
        symbol: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> NewsResponse:
        """Fetch news articles with optional symbol filter and pagination."""
        offset = (page - 1) * page_size

        try:
            async with get_connection() as conn:
                if symbol:
                    count = await conn.fetchval(
                        "SELECT COUNT(*) FROM yfinance_news WHERE ticker = $1",
                        symbol.upper(),
                    )
                    rows = await conn.fetch(
                        """
                        SELECT n.doc_id, n.ticker, n.title, n.published_at,
                               n.provider, n.url, r.score
                        FROM yfinance_news n
                        LEFT JOIN yfinance_news_relevance r ON n.doc_id = r.doc_id
                        WHERE n.ticker = $1
                        ORDER BY n.published_at DESC
                        LIMIT $2 OFFSET $3
                        """,
                        symbol.upper(),
                        page_size,
                        offset,
                    )
                else:
                    count = await conn.fetchval(
                        "SELECT COUNT(*) FROM yfinance_news"
                    )
                    rows = await conn.fetch(
                        """
                        SELECT n.doc_id, n.ticker, n.title, n.published_at,
                               n.provider, n.url, r.score
                        FROM yfinance_news n
                        LEFT JOIN yfinance_news_relevance r ON n.doc_id = r.doc_id
                        ORDER BY n.published_at DESC
                        LIMIT $1 OFFSET $2
                        """,
                        page_size,
                        offset,
                    )

            articles = [
                NewsArticle(
                    doc_id=row["doc_id"],
                    ticker=row["ticker"],
                    title=row["title"],
                    published_at=row["published_at"],
                    provider=row["provider"],
                    url=row["url"],
                    relevance_score=float(row["score"]) if row["score"] is not None else None,
                )
                for row in rows
            ]

            return NewsResponse(
                articles=articles,
                total=count or 0,
                page=page,
                page_size=page_size,
            )
        except Exception as e:
            logger.error(f"Failed to fetch news: {e}")
            return NewsResponse(
                articles=[],
                total=0,
                page=page,
                page_size=page_size,
            )


    async def get_tickers(self) -> list[str]:
        """Return distinct tickers that have news articles."""
        try:
            async with get_connection() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT ticker FROM yfinance_news ORDER BY ticker"
                )
            return [row["ticker"] for row in rows]
        except Exception as e:
            logger.error(f"Failed to fetch news tickers: {e}")
            return []


news_service = NewsService()
