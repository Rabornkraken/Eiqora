"""Standalone database connection utilities."""

import os
import asyncpg
from contextlib import asynccontextmanager
from typing import AsyncGenerator

_pool: asyncpg.Pool | None = None


def _get_dsn() -> str:
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    if "+psycopg" in url:
        url = url.replace("+psycopg", "")
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "")
    return url


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=_get_dsn(), min_size=1, max_size=5)
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
