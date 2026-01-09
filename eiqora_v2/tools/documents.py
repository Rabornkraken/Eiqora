"""
Document retrieval tools.
Fetches news articles from YFinance pipeline.
SEC filings and vector search currently disabled (migrating from GDELT to YFinance).
"""

from datetime import datetime
from typing import Any

from eiqora_v2.tools.db import get_connection


async def get_documents(
    symbol: str,
    window_hours: int,
    asof_time: datetime,
    doc_types: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Fetch recent documents for a symbol.
    
    Args:
        symbol: Stock ticker symbol
        window_hours: Hours to look back from asof_time
        asof_time: Point-in-time reference
        doc_types: Optional filter for document types (e.g., ["SEC_8K", "NEWS"])
        limit: Maximum documents to return
    
    Returns:
        List of document dicts with title, text, metadata
    """
    async with get_connection() as conn:
        if doc_types:
            # YFinance news pipeline only has 'NEWS' type
            # If filtering by doc_type and 'NEWS' not in list, return empty
            if 'NEWS' not in doc_types:
                return []
            
            rows = await conn.fetch("""
                SELECT yn.doc_id, yn.provider as source, 'NEWS' as doc_type, 
                       yn.ticker, yn.title, yn.published_at, yn.url,
                       LEFT(yn.text, 2000) as text_preview
                FROM yfinance_news yn
                WHERE yn.ticker = $1
                  AND yn.published_at <= $2
                  AND yn.published_at >= $2 - interval '1 hour' * $3
                ORDER BY yn.published_at DESC
                LIMIT $4
            """, symbol, asof_time, window_hours, limit)
        else:
            rows = await conn.fetch("""
                SELECT yn.doc_id, yn.provider as source, 'NEWS' as doc_type,
                       yn.ticker, yn.title, yn.published_at, yn.url,
                       LEFT(yn.text, 2000) as text_preview
                FROM yfinance_news yn
                WHERE yn.ticker = $1
                  AND yn.published_at <= $2
                  AND yn.published_at >= $2 - interval '1 hour' * $3
                ORDER BY yn.published_at DESC
                LIMIT $4
            """, symbol, asof_time, window_hours, limit)
        
        return [dict(r) for r in rows]


async def get_document_by_id(doc_id: int) -> dict[str, Any] | None:
    """Fetch a single document by ID with full text."""
    async with get_connection() as conn:
        row = await conn.fetchrow("""
            SELECT yn.doc_id, yn.provider as source, 'NEWS' as doc_type, 
                   yn.ticker, yn.title, yn.published_at, yn.url, yn.text
            FROM yfinance_news yn
            WHERE yn.doc_id = $1
        """, doc_id)
        
        return dict(row) if row else None


async def get_document_chunks_by_similarity(
    query_embedding: list[float],
    symbol: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Vector similarity search for news embeddings.
    
    NOTE: Currently disabled - YFinance news doesn't have embeddings yet.
    Would need to add an embeddings table for yfinance_news first.
    
    Args:
        query_embedding: Query vector (1536 dimensions)
        symbol: Optional filter by ticker
        limit: Maximum chunks to return
    
    Returns:
        Empty list (feature disabled pending YFinance embedding generation)
    """
    # TODO: Generate embeddings for yfinance_news and enable this feature
    return []


async def count_recent_documents(
    symbol: str,
    window_hours: int,
    asof_time: datetime,
) -> dict[str, int]:
    """
    Count documents by type in the recent window.
    YFinance news are all type 'NEWS'.
    """
    async with get_connection() as conn:
        row = await conn.fetchrow("""
            SELECT COUNT(*) as count
            FROM yfinance_news
            WHERE ticker = $1
              AND published_at <= $2
              AND published_at >= $2 - interval '1 hour' * $3
        """, symbol, asof_time, window_hours)
        
        count = row["count"] if row else 0
        return {"NEWS": count} if count > 0 else {}
