"""
Position management utilities for querying and tracking open positions.

Interfaces with Alpaca API to get current portfolio state.
"""

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def _get_alpaca_client():
    """Get Alpaca trading client."""
    try:
        from alpaca.trading.client import TradingClient
        from eiqora_v2.config.settings import get_settings
        
        settings = get_settings()
        
        client = TradingClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_API_SECRET,
            paper=settings.ALPACA_PAPER_TRADING,
        )
        return client
    except ImportError:
        logger.error("alpaca-py not installed. Run: pip install alpaca-py")
        return None
    except Exception as e:
        logger.error(f"Failed to create Alpaca client: {e}")
        return None


async def get_open_positions() -> list[dict[str, Any]]:
    """
    Query Alpaca for current open positions.
    
    Returns:
        List of position dicts: [
            {
                "symbol": "NVDA",
                "qty": 100,
                "side": "long",
                "entry_price": 150.0,
                "current_price": 155.0,
                "unrealized_pl": 500.0,
                "unrealized_plpc": 0.033,
                "market_value": 15500.0,
                "days_held": 5,
            },
            ...
        ]
    """
    client = _get_alpaca_client()
    
    if not client:
        # Fallback to database query
        logger.warning("Alpaca client unavailable, using database fallback")
        return await _get_positions_from_db()
    
    try:
        # Get all open positions from Alpaca
        positions = client.get_all_positions()
        
        result = []
        for p in positions:
            # Calculate days held
            try:
                entry_dt = datetime.fromisoformat(str(p.created_at).replace('Z', '+00:00'))
                days_held = (datetime.now(entry_dt.tzinfo) - entry_dt).days
            except:
                days_held = 0
            
            result.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": p.side,
                "entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "market_value": float(p.market_value),
                "days_held": days_held,
            })
        
        logger.info(f"Retrieved {len(result)} open positions from Alpaca")
        return result
        
    except Exception as e:
        logger.error(f"Failed to get positions from Alpaca: {e}")
        logger.info("Falling back to database")
        return await _get_positions_from_db()


async def _get_positions_from_db() -> list[dict[str, Any]]:
    """Fallback: query positions from database (signal table)."""
    from eiqora_v2.tools.db import get_connection
    
    try:
        async with get_connection() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (symbol)
                    symbol,
                    entry_price,
                    stop_loss,
                    take_profit,
                    conviction,
                    created_at
                FROM signal
                WHERE action = 'GO'
                  AND created_at > NOW() - INTERVAL '30 days'
                ORDER BY symbol, created_at DESC
            """)
            
            positions = []
            for row in rows:
                days_held = (datetime.utcnow() - row["created_at"]).days
                
                positions.append({
                    "symbol": row["symbol"],
                    "qty": 0,
                    "side": "long",
                    "entry_price": float(row["entry_price"]) if row["entry_price"] else 0,
                    "current_price": 0,
                    "unrealized_pl": 0,
                    "unrealized_plpc": 0,
                    "market_value": 0,
                    "days_held": days_held,
                })
            
            logger.info(f"Retrieved {len(positions)} positions from database")
            return positions
            
    except Exception as e:
        logger.error(f"Failed to get positions from database: {e}")
        return []


async def get_position_by_symbol(symbol: str) -> dict[str, Any] | None:
    """
    Get position details for a specific symbol.
    
    Args:
        symbol: Stock ticker
        
    Returns:
        Position dict or None if no position exists
    """
    positions = await get_open_positions()
    for pos in positions:
        if pos["symbol"] == symbol:
            return pos
    return None


async def has_open_position(symbol: str) -> bool:
    """
    Check if we have an open position in the given symbol.
    
    Args:
        symbol: Stock ticker
        
    Returns:
        True if position exists, False otherwise
    """
    position = await get_position_by_symbol(symbol)
    return position is not None

