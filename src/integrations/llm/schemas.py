"""
Pydantic Schemas for LLM-based Observations and Summaries

Provides structured data models for parsing and validating LLM responses.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class ObservationSchema(BaseModel):
    """
    Schema for LLM-generated observation data.

    Represents a structured observation about what was accomplished
    in a single prompt-response cycle.
    """

    type: str = Field(
        ...,
        description="Type of observation: bugfix|feature|refactor|change|discovery|decision"
    )
    title: str = Field(
        ...,
        description="Short title capturing the core action (max 100 chars)",
        min_length=1,
        max_length=100
    )
    subtitle: str = Field(
        ...,
        description="One sentence explanation (max 200 chars)",
        min_length=1,
        max_length=200
    )
    narrative: str = Field(
        ...,
        description="Full context: What was done, how it works, why it matters"
    )
    facts: List[str] = Field(
        default_factory=list,
        description="Concise, self-contained factual statements"
    )
    concepts: List[str] = Field(
        default_factory=list,
        description="Technical concepts, patterns, and key terms"
    )
    files_read: List[str] = Field(
        default_factory=list,
        description="List of file paths that were read"
    )
    files_modified: List[str] = Field(
        default_factory=list,
        description="List of file paths that were modified"
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate that type is one of the allowed values."""
        allowed_types = {"bugfix", "feature", "refactor", "change", "discovery", "decision"}
        if v not in allowed_types:
            raise ValueError(f"type must be one of {allowed_types}, got '{v}'")
        return v

    @field_validator("title", "subtitle")
    @classmethod
    def truncate_long_strings(cls, v: str, info) -> str:
        """Truncate strings that exceed max length."""
        max_length = 100 if info.field_name == "title" else 200
        if len(v) > max_length:
            return v[:max_length]
        return v


class ObservationSkipSchema(BaseModel):
    """Schema for when observation should be skipped."""

    skip: bool = Field(
        ...,
        description="Whether to skip this observation"
    )
    reason: str = Field(
        default="routine operations",
        description="Reason for skipping"
    )


class SummarySchema(BaseModel):
    """
    Schema for LLM-generated session summary.

    Represents a structured summary of an entire session's work.
    """

    request: str = Field(
        ...,
        description="Short title capturing user's main request",
        min_length=1,
        max_length=500
    )
    investigated: str = Field(
        ...,
        description="What was explored and investigated (bullet points)"
    )
    learned: str = Field(
        ...,
        description="Key insights and understanding gained (bullet points)"
    )
    completed: str = Field(
        ...,
        description="Tasks completed and deliverables shipped (bullet points)"
    )
    next_steps: str = Field(
        default="",
        description="Next actions and planned work (bullet points)"
    )
    notes: str = Field(
        default="",
        description="Additional context and extra notes (bullet points)"
    )

    @field_validator("investigated", "learned", "completed", "next_steps", "notes")
    @classmethod
    def validate_bullet_format(cls, v: str) -> str:
        """Ensure text uses bullet point format."""
        if v and not any(marker in v for marker in ["•", "-", "*"]):
            # Auto-convert to bullet points if not already formatted
            lines = v.strip().split("\n")
            if len(lines) > 1:
                # Multiple lines - add bullets to each
                return "\n".join(f"• {line.strip()}" for line in lines if line.strip())
            else:
                # Single line - add one bullet
                return f"• {v}"
        return v


class ObservationWithTextSchema(ObservationSchema):
    """
    Extended observation schema with text field.

    The text field contains a concise summary of the observation
    for display purposes.
    """

    text: str = Field(
        default="",
        description="Concise text summary for display (derived from title, subtitle, narrative)"
    )

    def generate_text(self) -> str:
        """
        Generate a concise text summary from the observation fields.

        Returns:
            str: A concise text summary
        """
        parts = [
            f"**{self.title}**",
            self.subtitle,
        ]
        if self.narrative:
            parts.append(self.narrative[:200] + "..." if len(self.narrative) > 200 else self.narrative)
        return "\n\n".join(parts)


# Helper functions for converting between schemas and database format

def observation_to_db_format(observation: ObservationSchema, **extra_fields) -> dict:
    """
    Convert an ObservationSchema to database format.

    Args:
        observation: Pydantic observation model
        **extra_fields: Additional fields like id, session_id, etc.

    Returns:
        dict: Database-ready observation record
    """
    import json
    from datetime import datetime

    data = {
        "id": extra_fields.get("id", ""),
        "session_id": extra_fields.get("session_id", ""),
        "prompt_number": extra_fields.get("prompt_number", 0),
        "title": observation.title,
        "subtitle": observation.subtitle,
        "narrative": observation.narrative,
        "text": observation.text if isinstance(observation, ObservationWithTextSchema) else observation.generate_text(),
        "facts": json.dumps(observation.facts),
        "concepts": json.dumps(observation.concepts),
        "type": observation.type,
        "files_read": json.dumps(observation.files_read),
        "files_modified": json.dumps(observation.files_modified),
        "content_hash": extra_fields.get("content_hash", ""),
        "created_at": extra_fields.get("created_at", datetime.now().isoformat()),
        "sync_status": extra_fields.get("sync_status", "pending"),
    }
    return {k: v for k, v in data.items() if v is not None}


def summary_to_db_format(summary: SummarySchema, **extra_fields) -> dict:
    """
    Convert a SummarySchema to database format.

    Args:
        summary: Pydantic summary model
        **extra_fields: Additional fields like id, session_id, etc.

    Returns:
        dict: Database-ready summary record
    """
    from datetime import datetime

    data = {
        "id": extra_fields.get("id", ""),
        "session_id": extra_fields.get("session_id", ""),
        "project": extra_fields.get("project", ""),
        "request": summary.request,
        "investigated": summary.investigated,
        "learned": summary.learned,
        "completed": summary.completed,
        "next_steps": summary.next_steps,
        "notes": summary.notes,
        "created_at": extra_fields.get("created_at", datetime.now().isoformat()),
        "sync_status": extra_fields.get("sync_status", "pending"),
    }
    return {k: v for k, v in data.items() if v is not None}
