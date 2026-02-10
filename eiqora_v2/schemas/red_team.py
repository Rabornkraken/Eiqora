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

    # Short perspective fields (merged from ShortPerspectiveAgent)
    short_case_strength: Literal["STRONG", "MODERATE", "WEAK", "NONE"] = Field(
        default="NONE",
        description="Strength of the contrarian short case"
    )
    short_score: float = Field(
        default=0.0,
        description="Quantitative short score (0-10)"
    )
    recommend_reject_long: bool = Field(
        default=False,
        description="True if short case is strong enough to reject the long trade"
    )
    short_red_flags: list[str] = Field(
        default_factory=list,
        description="Short-case red flags from deterministic scoring"
    )
