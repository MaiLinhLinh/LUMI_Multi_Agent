"""Structured Output schema for Weather LLM1 extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WeatherDateRangeResponse(BaseModel):
    """Semantic date-range intent extracted without calculating calendar dates."""

    type: Literal["single_day", "next_days", "full_week", "explicit_range"] = Field(
        description=(
            "Range meaning: one day, N future days, one or more full weeks, "
            "or an explicitly stated start/end range."
        ),
    )
    quantity: int | None = Field(
        description=(
            "The number stated by the user: future days for next_days or weeks "
            "for full_week; otherwise null."
        ),
    )
    end_date_text: str | None = Field(
        description=(
            "Raw explicit end-date phrase for explicit_range; otherwise null."
        ),
    )


class WeatherExtractionResponse(BaseModel):
    """Weather request fields constrained by the Gemini API."""

    location_text: str | None = Field(
        description="Raw location phrase from the query or relevant history, or null.",
    )
    date_text: str | None = Field(
        description=(
            "Raw start or anchor date phrase from the query or relevant history; "
            "null when no explicit anchor exists or for current conditions."
        ),
    )
    date_range: WeatherDateRangeResponse | None = Field(
        description=(
            "Semantic date range for a forecast, or null for current conditions "
            "and requests whose date meaning is still unknown."
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
