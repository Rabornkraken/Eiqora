"""
Event Triage Agent output schema.
"""

from typing import Literal
from pydantic import BaseModel, Field


class TriageOutput(BaseModel):
    """Output schema for Event Triage Agent."""
    
    event_type: Literal[
        "EARNINGS",
        "GUIDANCE",
        "LEGAL",
        "MNA",  # Mergers & Acquisitions
        "MGMT",  # Management changes
        "INSIDER",
        "MACRO",
        "TECHNICAL_ONLY",
        "UNKNOWN",
    ] = Field(
        description="Canonical event type classification"
    )
    
    priority: Literal["HIGH", "MED", "LOW"] = Field(
        description="Priority level for processing"
    )
    
    freshness_hours: float = Field(
        ge=0,
        description="Hours since the most recent relevant document"
    )
    
    doc_ids: list[str] = Field(
        default_factory=list,
        description="Document IDs to process (e.g., 'doc:12345')"
    )
    
    needs_extraction: bool = Field(
        description="Whether Event Extractor should run on these docs"
    )
    
    reason: str = Field(
        max_length=200,
        description="Brief explanation of classification"
    )
    
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in classification (not price prediction)"
    )
