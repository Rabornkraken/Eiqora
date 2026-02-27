"""
Positions Router - REST endpoints for position monitoring
"""
from fastapi import APIRouter, HTTPException

from ..models.positions import (
    PositionsResponse,
    PositionDetail,
    ExitRequest,
    ExitResponse,
)
from ..services.positions_service import positions_service
from eiqora_v2.tools.broker import (
    get_alpaca_account,
    get_alpaca_positions,
    _is_alpaca_configured,
)

router = APIRouter()


@router.get("/open", response_model=PositionsResponse)
async def get_open_positions():
    """
    Get all open positions

    Retrieve all currently open positions with their current status and P&L.

    Returns:
        PositionsResponse with list of open positions
    """
    try:
        positions = await positions_service.get_open_positions()
        return positions

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch positions: {str(e)}")


@router.get("/broker/account")
async def get_broker_account():
    """Return Alpaca paper trading account info."""
    if not _is_alpaca_configured():
        return {"configured": False}
    account = await get_alpaca_account()
    if account is None:
        raise HTTPException(status_code=502, detail="Failed to fetch Alpaca account")
    return {"configured": True, **account}


@router.get("/broker/positions")
async def get_broker_positions():
    """Return all positions held on Alpaca."""
    if not _is_alpaca_configured():
        return {"configured": False, "positions": []}
    positions = await get_alpaca_positions()
    if positions is None:
        raise HTTPException(status_code=502, detail="Failed to fetch Alpaca positions")
    return {"configured": True, "positions": positions}


@router.get("/{position_id}", response_model=PositionDetail)
async def get_position_details(position_id: str):
    """
    Get detailed information for a specific position

    Retrieve detailed information including alerts, sector, and performance metrics.

    Args:
        position_id: Position identifier

    Returns:
        PositionDetail with detailed position information
    """
    position = await positions_service.get_position_details(position_id)

    if position is None:
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found")

    return position


@router.post("/{position_id}/exit", response_model=ExitResponse)
async def initiate_position_exit(position_id: str, request: ExitRequest):
    """
    Initiate exit for a position

    Manually exit a position with the specified reason.

    Args:
        position_id: Position identifier
        request: Exit request with reason and type

    Returns:
        ExitResponse with exit details
    """
    exit_response = await positions_service.initiate_exit(position_id, request)

    if exit_response is None:
        raise HTTPException(
            status_code=404, detail=f"Position {position_id} not found or already closed"
        )

    return exit_response
