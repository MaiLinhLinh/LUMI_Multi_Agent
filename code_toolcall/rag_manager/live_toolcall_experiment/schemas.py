"""Strict, frontend-safe contracts for the Live tool-call experiment."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


_TARGET_ID = re.compile(r"^[a-z][a-z0-9._-]{0,199}$")

AnimationEffect = Literal[
    "reveal", "highlight", "pulse", "dim_others", "draw_circle",
    "draw_arrow", "trace_line", "draw_group_bracket",
    "trace_chart_segment", "draw_temperature_range",
]


class AnimationCommand(BaseModel):
    """One validated Live marker, never an arbitrary DOM command."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_id: str = Field(min_length=1, max_length=200)
    effect: AnimationEffect

    def model_post_init(self, __context: object) -> None:
        if not _TARGET_ID.fullmatch(self.target_id):
            raise ValueError("target_id has an invalid format")


class ActiveAnimationCapabilities(BaseModel):
    """Runtime target/effect allow-list derived from rendered template HTML."""

    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=80)
    allowed: dict[str, list[AnimationEffect]] = Field(default_factory=dict)

    def allows(self, command: AnimationCommand) -> bool:
        return command.effect in self.allowed.get(command.target_id, [])


class SceneTrigger(BaseModel):
    """The only animation decision Gemini Live makes for a compiled plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scene_id: str = Field(min_length=1, max_length=100)


class ActivePresentationScenes(BaseModel):
    """Compiler-approved scenes addressable by Gemini Live at runtime."""

    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=80)
    scenes: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def resolve(self, scene_id: str) -> dict[str, Any] | None:
        return self.scenes.get(scene_id)
