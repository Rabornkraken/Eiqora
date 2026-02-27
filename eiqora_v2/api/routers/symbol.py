"""Symbol Detail API router"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from eiqora_v2.api.models.symbol import SymbolDetailResponse, SymbolListResponse
from eiqora_v2.api.services.symbol_service import symbol_service

router = APIRouter()


@router.get("/", response_model=SymbolListResponse)
async def list_symbols(
    search: Optional[str] = Query(None, description="Filter symbols by name (ILIKE)"),
):
    """List all active universe symbols with latest price data."""
    return await symbol_service.list_symbols(search=search)


@router.get("/{symbol}", response_model=SymbolDetailResponse)
async def get_symbol_detail(
    symbol: str,
    days: int = Query(default=90, ge=7, le=365, description="Days of price history"),
):
    """Get comprehensive detail for a single symbol.

    Includes price history, technical indicators, news, earnings,
    analyst ratings, and options flow data.

    Args:
        symbol: Ticker symbol (e.g. AAPL)
        days: Days of price history (default 90)

    Returns:
        SymbolDetailResponse with all data sections
    """
    result = await symbol_service.get_symbol_detail(symbol=symbol, days=days)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol.upper()}' not found")
    return result
