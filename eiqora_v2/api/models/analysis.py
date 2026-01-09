"""
Analysis API Request/Response Models
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Literal, Dict, Any, List


class AnalysisRequest(BaseModel):
    """Request to start a new analysis"""

    symbol: str = Field(..., description="Stock ticker symbol", min_length=1, max_length=10)
    trigger_type: Optional[
        Literal["CHART_SETUP", "NEWS_SENTIMENT", "EARNINGS", "SEC_8K", "MANUAL"]
    ] = Field(default="CHART_SETUP", description="Type of trigger for analysis")
    asof_time: Optional[datetime] = Field(
        default=None, description="Analysis as-of time (defaults to now)"
    )


class AnalysisResponse(BaseModel):
    """Response with analysis metadata"""

    analysis_id: str = Field(..., description="Unique analysis identifier")
    symbol: str = Field(..., description="Stock ticker symbol")
    status: Literal["running", "completed", "failed"] = Field(..., description="Analysis status")
    created_at: datetime = Field(..., description="Analysis creation timestamp")
    completed_at: Optional[datetime] = Field(None, description="Analysis completion timestamp")
    result: Optional[Dict[str, Any]] = Field(None, description="Analysis result (agent outputs)")
    error: Optional[str] = Field(None, description="Error message if failed")
    trigger_type: str = Field(..., description="Trigger type used for analysis")


class AgentOutput(BaseModel):
    """Output from a single agent"""

    agent_name: str = Field(..., description="Name of the agent")
    output: Dict[str, Any] = Field(..., description="Agent output data")
    execution_time_ms: int = Field(..., description="Execution time in milliseconds")


class AnalysisDetail(AnalysisResponse):
    """Detailed analysis response with agent outputs"""

    agent_outputs: Optional[Dict[str, Any]] = Field(
        None, description="All agent outputs keyed by agent name"
    )
    decision: Optional[Dict[str, Any]] = Field(None, description="Final decision output")
    steps: Optional[List[str]] = Field(None, description="Analysis steps completed")


class AnalysisListResponse(BaseModel):
    """Response listing multiple analyses"""

    analyses: List[AnalysisResponse] = Field(..., description="List of analyses")
    total: int = Field(..., description="Total count")
    limit: int = Field(..., description="Number of analyses returned")