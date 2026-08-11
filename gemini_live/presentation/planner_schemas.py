"""Shared verified-fact contract used by domain adapters and Gemini Live."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AnswerOperation = Literal["lookup", "argmax", "argmin", "compare", "trend", "summary"]


class GroundedFact(BaseModel):
    """A result calculated by code from validated domain data."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    metric: str = Field(min_length=1, max_length=100)
    operation: AnswerOperation
    value: Any
    unit: str | None = Field(default=None, max_length=32)
    entity: dict[str, Any] = Field(default_factory=dict)
    focus: str = Field(min_length=1, max_length=100)
    visualizable: bool = True
