"""Structured Output schema for Weather LLM1 extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WeatherExtractionResponse(BaseModel):
    """Weather request fields constrained by the Gemini API."""

    location_text: str | None = Field(
        description="Raw location phrase from the query or relevant history, or null.",
    )
    date_text: str | None = Field(
        description=(
            "Raw date or date-range phrase from the query or relevant history, "
            "or null for current conditions."
        ),
    )
    time_of_day_text: str | None = Field(
        description=(
            "Raw specific time-of-day phrase such as '9h' or 'lúc 14:30', "
            "or null for current or whole-day requests."
        ),
    )
    normalized_time: str | None = Field(
        description=(
            "The same explicitly requested clock time normalized as 24-hour "
            "HH:MM, or null when no exact clock time was stated."
        ),
    )
    request_type_candidate: Literal["current", "forecast"] | None = Field(
        description="Tentative weather request type, or null when uncertain.",
    )
