"""
Event Extractor Agent output schema.
"""

from typing import Literal
from pydantic import BaseModel, Field


class GuidanceChange(BaseModel):
    """Guidance change details."""
    metric: str = Field(description="Financial metric (e.g., 'revenue', 'EPS', 'margins')")
    direction: Literal["RAISE", "LOWER", "MAINTAIN", "INITIATE", "WITHDRAW"] = Field(
        description="Direction of guidance change"
    )
    magnitude: str | None = Field(default=None, description="Magnitude if quantified")


class TransactionDetail(BaseModel):
    """Insider or M&A transaction details."""
    counterparty: str | None = Field(default=None, description="Counterparty if applicable")
    value: str | None = Field(default=None, description="Transaction value if disclosed")
    transaction_type: str = Field(description="Type of transaction")


class ExtractedFact(BaseModel):
    """A single extracted fact from a document."""
    fact_type: str = Field(description="Type of fact (e.g., 'guidance_change', 'executive_departure')")
    description: str = Field(max_length=500, description="Concise description of the fact")
    source_doc_id: str = Field(description="Document ID this fact was extracted from")
    confidence: float = Field(ge=0.0, le=1.0, description="Extraction confidence")
    

class EventExtractorOutput(BaseModel):
    """Output schema for Event Extractor Agent."""
    
    event_summary: str = Field(
        max_length=300,
        description="One-line summary of the event"
    )
    
    facts: list[ExtractedFact] = Field(
        default_factory=list,
        description="List of extracted facts"
    )
    
    guidance_changes: list[GuidanceChange] = Field(
        default_factory=list,
        description="Guidance changes if any"
    )
    
    transactions: list[TransactionDetail] = Field(
        default_factory=list,
        description="Transaction details if M&A or insider"
    )
    
    sentiment: Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"] = Field(
        description="Overall sentiment of the event"
    )
    
    materiality: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="Materiality assessment for trading"
    )
    
    catalyst_date: str | None = Field(
        default=None,
        description="Date of catalyst if identifiable (YYYY-MM-DD)"
    )
