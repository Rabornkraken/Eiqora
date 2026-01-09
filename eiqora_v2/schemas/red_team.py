"""
Red Team output schema.
"""

from typing import Literal

from pydantic import BaseModel, Field


class RedTeamOutput(BaseModel):
    """Output schema for Red Team Agent."""

    decision: Literal["BLOCK", "CAUTION", "ALLOW"] = Field(
        description="Red team posture toward the trade"
    )
    critical: bool = Field(
        description="True if the trade should be blocked due to a critical issue"
    )
    key_risks: list[str] = Field(
        default_factory=list,
        description="Top red flags or contradictions"
    )
    missing_data: list[str] = Field(
        default_factory=list,
        description="Missing inputs that reduce confidence"
    )
    summary: str = Field(
        max_length=400,
        description="Concise red-team summary"
    )
