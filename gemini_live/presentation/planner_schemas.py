"""Strict data contracts between the planner, compiler, and web client.

The planner may select only semantic focus names and whitelisted effects.  It
never supplies HTML, CSS, JavaScript, or arbitrary DOM selectors.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .speech_text import derive_speech_text


PresentationEffect = Literal[
    "reveal",
    "highlight",
    "pulse",
    "dim_others",
    "draw_circle",
    "draw_arrow",
    "trace_line",
    "draw_group_bracket",
    "trace_chart_segment",
    "draw_temperature_range",
    "reveal_items",
]
PresentationActionEffect = Literal[
    "reveal", "highlight", "pulse", "dim_others", "draw_circle", "draw_arrow", "trace_line",
    "draw_group_bracket", "trace_chart_segment", "draw_temperature_range", "reveal_items",
]

PresentationGesture = Literal[
    "idle",
    "speaking",
    "explain",
    "point_left",
    "point_right",
    "concerned",
]

PresentationEmphasis = Literal["low", "medium", "high"]
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
    effect_hint: PresentationEffect = "highlight"
    # Deterministic evidence calculated by the domain adapter. The planner may
    # see it, but never writes or changes it.
    visual_evidence: dict[str, Any] = Field(default_factory=dict)


class PresentationStep(BaseModel):
    """One semantic segment the planner wants Lumi to present."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    narration: str = Field(min_length=1, max_length=800)
    # The Planner selects a fact only. Focus, entity and visual evidence are
    # trusted properties of that fact and are hydrated by the Compiler.
    fact_id: str = Field(min_length=1, max_length=80)
    emphasis: PresentationEmphasis = "medium"
    gesture: PresentationGesture = "explain"
    effect: PresentationEffect = "highlight"

    @field_validator("narration")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class PresentationPlan(BaseModel):
    """Planner output before it has been checked against template capability."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["presentation_plan.v1"] = "presentation_plan.v1"
    # A weather bulletin needs a short sequence of visual beats (opening,
    # current conditions, key facts, and a close), not one long monologue.
    # The frontend still receives and validates one step at a time.
    steps: list[PresentationStep] = Field(min_length=1, max_length=6)


class CompiledPresentationAction(BaseModel):
    """One frontend-safe animation beat relative to the scene audio start."""

    model_config = ConfigDict(extra="forbid")

    target_ids: list[str] = Field(min_length=1, max_length=24)
    effect: PresentationActionEffect
    start_ms: int = Field(default=0, ge=0, le=120000)
    duration_ms: int = Field(default=900, ge=100, le=5000)
    payload: dict[str, Any] = Field(default_factory=dict)


class CompiledPresentationStep(BaseModel):
    """A step safe for the frontend after capability-aware compilation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    narration: str = Field(min_length=1, max_length=800)
    # ``narration`` stays exactly as the Planner wrote it for chat/UI.  The two
    # forms below are deterministic contracts for TTS and future CTC alignment.
    spoken_text: str = Field(default="", min_length=1, max_length=1600)
    alignment_text: str = Field(default="", min_length=1, max_length=1600)
    target_id: str = Field(min_length=1, max_length=200)
    effect: PresentationEffect
    gesture: PresentationGesture
    actions: list[CompiledPresentationAction] = Field(default_factory=list, max_length=8)

    @field_validator("narration", "target_id")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def derive_missing_speech_forms(self) -> "CompiledPresentationStep":
        spoken, alignment = derive_speech_text(self.narration)
        if not self.spoken_text:
            self.spoken_text = spoken
        if not self.alignment_text:
            self.alignment_text = derive_speech_text(self.spoken_text)[1]
        if not self.spoken_text.strip() or not self.alignment_text.strip():
            raise ValueError("speech text forms must not be blank")
        return self


class CompiledPresentationPlan(BaseModel):
    """A complete compiler result, ready for a future stream event."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["compiled_presentation_plan.v1"] = (
        "compiled_presentation_plan.v1"
    )
    steps: list[CompiledPresentationStep] = Field(min_length=1, max_length=6)
