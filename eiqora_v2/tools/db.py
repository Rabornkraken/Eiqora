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


def _parse_database_url() -> dict:
    """Parse DATABASE_URL into asyncpg connection args."""
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    
    # Remove driver prefix if present (e.g., postgresql+psycopg://)
    if "+psycopg" in url:
        url = url.replace("+psycopg", "")
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "")
    
    parsed = urlparse(url)
    
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "postgres",
        "database": parsed.path.lstrip("/") or "finance",
    }


async def get_pool() -> asyncpg.Pool:
    """Get or create the database connection pool."""
    global _pool
    if _pool is None:
        db_config = _parse_database_url()
        _pool = await asyncpg.create_pool(
            host=db_config["host"],
            port=db_config["port"],
            user=db_config["user"],
            password=db_config["password"],
            database=db_config["database"],
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

