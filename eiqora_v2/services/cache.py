"""
Caching layer for expensive operations.
Uses Redis if available, falls back to in-memory cache.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, TypeVar
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar("T")

# In-memory cache (fallback)
_cache: dict[str, tuple[Any, datetime]] = {}


def _get_cache_key(prefix: str, *args, **kwargs) -> str:
    """Generate cache key from function arguments."""
    key_data = json.dumps([prefix, args, kwargs], sort_keys=True, default=str)
    return hashlib.md5(key_data.encode()).hexdigest()


def cache_result(
    prefix: str,
    ttl_seconds: int = 3600,
):
    """
    Decorator to cache async function results.
    
    Args:
        prefix: Cache key prefix (e.g., "topdown")
        ttl_seconds: Time-to-live in seconds
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            cache_key = _get_cache_key(prefix, *args, **kwargs)
            
            # Check cache
            if cache_key in _cache:
                value, expires_at = _cache[cache_key]
                if datetime.now() < expires_at:
                    logger.debug(f"Cache hit: {prefix}")
                    return value
                else:
                    del _cache[cache_key]
            
            # Cache miss - call function
            logger.debug(f"Cache miss: {prefix}")
            result = await func(*args, **kwargs)
            
            # Store in cache
            expires_at = datetime.now() + timedelta(seconds=ttl_seconds)
            _cache[cache_key] = (result, expires_at)
            
            return result
        
        return wrapper
    return decorator


def clear_cache(prefix: str | None = None):
    """
    Clear cache entries.
    
    Args:
        prefix: If provided, only clear entries matching this prefix
    """
    global _cache
    
    if prefix is None:
        _cache.clear()
        logger.info("Cleared all cache entries")
    else:
        keys_to_delete = [k for k in _cache if k.startswith(prefix)]
        for k in keys_to_delete:
            del _cache[k]
        logger.info(f"Cleared {len(keys_to_delete)} cache entries with prefix {prefix}")


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics."""
    now = datetime.now()
    active = sum(1 for _, (_, expires) in _cache.items() if expires > now)
    expired = len(_cache) - active
    
    return {
        "total_entries": len(_cache),
        "active_entries": active,
        "expired_entries": expired,
    }


class TopDownCache:
    """
    Specialized cache for TopDown Agent results.
    
    TopDown results are valid for the entire trading day.
    Key is based on date only (not time).
    """
    
    _cache: dict[str, dict[str, Any]] = {}
    
    @classmethod
    def get(cls, asof_date: datetime) -> dict[str, Any] | None:
        """Get cached TopDown result for date."""
        key = asof_date.strftime("%Y-%m-%d")
        return cls._cache.get(key)
    
    @classmethod
    def set(cls, asof_date: datetime, result: dict[str, Any]):
        """Cache TopDown result for date."""
        key = asof_date.strftime("%Y-%m-%d")
        cls._cache[key] = result
        logger.info(f"Cached TopDown result for {key}")
    
    @classmethod
    def clear(cls):
        """Clear TopDown cache."""
        cls._cache.clear()


class SectorRegimeCache:
    """
    Cache for sector regime data.
    
    Sector regimes are computed from relative performance
    and are valid for the trading day.
    """
    
    _cache: dict[str, dict[str, str]] = {}
    
    @classmethod
    def get(cls, sector_etf: str, asof_date: datetime) -> str | None:
        """Get cached sector regime."""
        key = f"{sector_etf}_{asof_date.strftime('%Y-%m-%d')}"
        return cls._cache.get(key)
    
    @classmethod
    def set(cls, sector_etf: str, asof_date: datetime, regime: str):
        """Cache sector regime."""
        key = f"{sector_etf}_{asof_date.strftime('%Y-%m-%d')}"
        cls._cache[key] = regime
    
    @classmethod
    def clear(cls):
        """Clear sector regime cache."""
        cls._cache.clear()
