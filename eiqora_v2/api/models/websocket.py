"""
WebSocket Message Models
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Any, Dict, Optional, Literal


class WebSocketMessage(BaseModel):
    """Base WebSocket message"""

    event_type: str = Field(..., description="Type of event")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Event timestamp")
    data: Dict[str, Any] = Field(default_factory=dict, description="Event data")


class AnalysisProgressMessage(WebSocketMessage):
    """Analysis progress update"""

    event_type: Literal["analysis_progress"] = "analysis_progress"
    data: Dict[str, Any] = Field(
        ..., description="Progress data including step, agent, and status"
    )


class TriggerDetectedMessage(WebSocketMessage):
    """New trigger detected"""

    event_type: Literal["trigger_detected"] = "trigger_detected"
    data: Dict[str, Any] = Field(..., description="Trigger data including symbol and type")


class PositionUpdateMessage(WebSocketMessage):
    """Position update"""

    event_type: Literal["position_update"] = "position_update"
    data: Dict[str, Any] = Field(..., description="Position update data")


class ErrorMessage(WebSocketMessage):
    """Error message"""

    event_type: Literal["error"] = "error"
    data: Dict[str, Any] = Field(..., description="Error data including message and details")