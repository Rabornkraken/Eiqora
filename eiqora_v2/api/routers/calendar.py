"""Economic Calendar API router"""

from fastapi import APIRouter, Query

from eiqora_v2.api.models.calendar import EconomicCalendarResponse
from eiqora_v2.api.services.calendar_service import calendar_service

router = APIRouter()


@router.get("/upcoming", response_model=EconomicCalendarResponse)
async def get_upcoming_events(
    days: int = Query(default=14, ge=1, le=90, description="Number of days to look ahead"),
):
    """Get upcoming economic events.

    Args:
        days: Number of days to look ahead (default 14, max 90)

    Returns:
        EconomicCalendarResponse with events grouped by date
    """
    return await calendar_service.get_upcoming_events(days=days)
