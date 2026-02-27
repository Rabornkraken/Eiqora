"""News Feed API router"""

from typing import List, Optional

from fastapi import APIRouter, Query

from eiqora_v2.api.models.news import NewsResponse
from eiqora_v2.api.services.news_service import news_service

router = APIRouter()


@router.get("/", response_model=NewsResponse)
async def get_news(
    symbol: Optional[str] = Query(default=None, description="Filter by ticker symbol"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=25, ge=1, le=100, description="Articles per page"),
):
    """Get news articles with optional symbol filter and pagination.

    Args:
        symbol: Optional ticker symbol to filter by
        page: Page number (1-indexed)
        page_size: Number of articles per page (max 100)

    Returns:
        NewsResponse with paginated articles and total count
    """
    return await news_service.get_news(
        symbol=symbol, page=page, page_size=page_size
    )


@router.get("/tickers", response_model=List[str])
async def get_news_tickers():
    """Get list of all tickers that have news articles."""
    return await news_service.get_tickers()
