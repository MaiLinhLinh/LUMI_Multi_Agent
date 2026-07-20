"""Structured Output schema for Music LLM1 extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MusicAction = Literal["play", "search", "next", "replay", "stop"]
MusicSortField = Literal["release_date", "popularity"]
MusicSortOrder = Literal["asc", "desc"]
MusicCandidateDecision = Literal["selected", "needs_clarification"]
MusicCandidateConfidence = Literal["high", "low"]


class MusicExtractionResponse(BaseModel):
    """Music request fields constrained by the Gemini API."""

    action: MusicAction | None = Field(
        description="Requested music action, or null when uncertain.",
    )
    search_query: str | None = Field(
        description=(
            "Standalone retrieval text built only from the user's current request "
            "and relevant history, or null when no catalog lookup is needed."
        ),
    )
    title: str | None = Field(
        description=(
            "Song title explicitly supplied or inherited from user context, or null."
        ),
    )
    artist: str | None = Field(
        description=(
            "Artist or group explicitly supplied or inherited from user context, "
            "or null."
        ),
    )
    genre: str | None = Field(
        description="Requested music genre, or null.",
    )
    mood: str | None = Field(
        description="Requested mood such as relaxing, sad, or energetic, or null.",
    )
    language: str | None = Field(
        description="Requested music language, or null.",
    )
    version: str | None = Field(
        description=(
            "Requested version such as official MV, audio, live, remix, acoustic, "
            "or karaoke, or null."
        ),
    )
    sort_by: MusicSortField | None = Field(
        description=(
            "Structured ranking field, or null when no explicit ranking was requested."
        ),
    )
    sort_order: MusicSortOrder | None = Field(
        description="Ranking direction paired with sort_by, or null.",
    )
    selection_index: int | None = Field(
        ge=1,
        description=(
            "One-based candidate index explicitly selected by the user, or null."
        ),
    )


class MusicCandidateResolutionResponse(BaseModel):
    """Constrain Music LLM2 to a backend-provided candidate list."""

    decision: MusicCandidateDecision = Field(
        description=(
            "Select one candidate only when the request clearly identifies it; "
            "otherwise request clarification."
        ),
    )
    selection_index: int | None = Field(
        default=None,
        ge=1,
        description=(
            "One-based index from the provided candidate list when decision is "
            "selected; otherwise null."
        ),
    )
    confidence: MusicCandidateConfidence = Field(
        description="High for an unambiguous selection; otherwise low.",
    )
    question: str | None = Field(
        default=None,
        description=(
            "One concise Vietnamese clarification question when the decision is "
            "needs_clarification; otherwise null."
        ),
    )
