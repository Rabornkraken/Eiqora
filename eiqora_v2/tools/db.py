"""
Database connection utilities.
Parses DATABASE_URL from environment for asyncpg connections.
"""

import os
import asyncpg
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import urlparse

_pool: asyncpg.Pool | None = None


def _get_database_dsn() -> str:
    """Get DATABASE_URL, cleaning up any driver prefixes."""
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")

    # Remove driver prefix if present (e.g., postgresql+psycopg://)
    if "+psycopg" in url:
        url = url.replace("+psycopg", "")
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "")

    return url


async def get_pool() -> asyncpg.Pool:
    """Get or create the database connection pool."""
    global _pool
    if _pool is None:
        dsn = _get_database_dsn()
        # asyncpg handles DSN parsing including sslmode
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=20,
        )
    return _pool


async def close_pool():
    """Close the connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """Get a database connection from the pool."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
