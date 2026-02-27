"""Pydantic models for Economic Calendar API"""

from datetime import date, time
from typing import List, Optional

from pydantic import BaseModel


class EconomicEvent(BaseModel):
    event_name: str
    event_date: date
    event_time: Optional[time] = None
    country: str = "US"
    impact: Optional[str] = None
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None


class EconomicCalendarResponse(BaseModel):
    events: List[EconomicEvent]
    total: int
    start_date: date
    end_date: date
