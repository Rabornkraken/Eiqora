"""
Document retrieval tools.
Fetches news, SEC filings, and other documents from the document table.
Supports vector similarity search via pgvector.
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
            rows = await conn.fetch("""
                SELECT doc_id, source, doc_type, ticker, title, published_at, url,
                       LEFT(text, 2000) as text_preview
                FROM document
                WHERE ticker = $1
                  AND published_at <= $2
                  AND published_at >= $2 - interval '1 hour' * $3
                  AND doc_type = ANY($4)
                ORDER BY published_at DESC
                LIMIT $5
            """, symbol, asof_time, window_hours, doc_types, limit)
        else:
            rows = await conn.fetch("""
                SELECT doc_id, source, doc_type, ticker, title, published_at, url,
                       LEFT(text, 2000) as text_preview
                FROM document
                WHERE ticker = $1
                  AND published_at <= $2
                  AND published_at >= $2 - interval '1 hour' * $3
                ORDER BY published_at DESC
                LIMIT $4
            """, symbol, asof_time, window_hours, limit)
        
        return [dict(r) for r in rows]


async def get_document_by_id(doc_id: int) -> dict[str, Any] | None:
    """Fetch a single document by ID with full text."""
    async with get_connection() as conn:
        row = await conn.fetchrow("""
            SELECT doc_id, source, doc_type, ticker, title, published_at, url, text
            FROM document
            WHERE doc_id = $1
        """, doc_id)
        
        return dict(row) if row else None


async def get_document_chunks_by_similarity(
    query_embedding: list[float],
    symbol: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Vector similarity search on document_chunk table using pgvector.
    
    Args:
        query_embedding: Query vector (1536 dimensions)
        symbol: Optional filter by ticker
        limit: Maximum chunks to return
    
    Returns:
        List of chunk dicts with text, metadata, and distance score
    """
    async with get_connection() as conn:
        if symbol:
            rows = await conn.fetch("""
                SELECT dc.chunk_id, dc.doc_id, dc.text, dc.chunk_index,
                       d.ticker, d.title, d.doc_type, d.published_at,
                       dc.embedding <-> $1::vector AS distance
                FROM document_chunk dc
                JOIN document d ON dc.doc_id = d.doc_id
                WHERE dc.active = true
                  AND d.ticker = $2
                ORDER BY dc.embedding <-> $1::vector
                LIMIT $3
            """, query_embedding, symbol, limit)
        else:
            rows = await conn.fetch("""
                SELECT dc.chunk_id, dc.doc_id, dc.text, dc.chunk_index,
                       d.ticker, d.title, d.doc_type, d.published_at,
                       dc.embedding <-> $1::vector AS distance
                FROM document_chunk dc
                JOIN document d ON dc.doc_id = d.doc_id
                WHERE dc.active = true
                ORDER BY dc.embedding <-> $1::vector
                LIMIT $2
            """, query_embedding, limit)
        
        return [dict(r) for r in rows]


async def count_recent_documents(
    symbol: str,
    window_hours: int,
    asof_time: datetime,
) -> dict[str, int]:
    """
    Count documents by type in the recent window.
    Useful for triage to understand document volume.
    """
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT doc_type, COUNT(*) as count
            FROM document
            WHERE ticker = $1
              AND published_at <= $2
              AND published_at >= $2 - interval '1 hour' * $3
            GROUP BY doc_type
        """, symbol, asof_time, window_hours)
        
        return {r["doc_type"]: r["count"] for r in rows}
