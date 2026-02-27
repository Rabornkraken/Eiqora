"""Service layer for Economic Calendar API"""

import logging
from datetime import date, timedelta

from eiqora_v2.tools.db import get_connection
from eiqora_v2.api.models.calendar import EconomicEvent, EconomicCalendarResponse

logger = logging.getLogger(__name__)


class CalendarService:
    async def get_upcoming_events(self, days: int = 14) -> EconomicCalendarResponse:
        """Fetch economic events within the given date range."""
        start_date = date.today()
        end_date = start_date + timedelta(days=days)

        try:
            async with get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT event_name, event_date, event_time, country,
                           actual, forecast, previous, impact
                    FROM economic_event
                    WHERE event_date >= $1 AND event_date <= $2
                    ORDER BY event_date ASC, event_time ASC NULLS LAST
                    """,
                    start_date,
                    end_date,
                )

            events = [
                EconomicEvent(
                    event_name=row["event_name"],
                    event_date=row["event_date"].date() if hasattr(row["event_date"], 'date') else row["event_date"],
                    event_time=row["event_time"],
                    country=row["country"] or "US",
                    impact=row["impact"],
                    actual=row["actual"],
                    forecast=row["forecast"],
                    previous=row["previous"],
                )
                for row in rows
            ]

            return EconomicCalendarResponse(
                events=events,
                total=len(events),
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as e:
            logger.error(f"Failed to fetch economic events: {e}")
            return EconomicCalendarResponse(
                events=[],
                total=0,
                start_date=start_date,
                end_date=end_date,
            )


calendar_service = CalendarService()
