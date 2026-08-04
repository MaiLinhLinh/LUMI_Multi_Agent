"""Server-side active-scene state shared by all presentation domains."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gemini_live.presentation.contract_compiler import CompiledPresentationPlan


@dataclass(frozen=True)
class ActiveAnimationCapabilities:
    template_id: str
    allowed: dict[str, list[str]]


@dataclass(frozen=True)
class ActivePresentationScenes:
    template_id: str
    scenes: dict[str, dict[str, Any]]

    def resolve(self, scene_id: str) -> dict[str, Any] | None:
        return self.scenes.get(scene_id)


@dataclass(frozen=True)
class LivePresentation:
    """Frontend panel plus ordered scenes, independent of any business domain."""

    panel: dict[str, Any]
    scenes: ActivePresentationScenes


def active_scenes_from_compiled_plan(
    *,
    domain_id: str,
    template_id: str,
    compiled_plan: CompiledPresentationPlan,
) -> ActivePresentationScenes:
    """Convert a safe contract to triggerable scenes using only domain_id."""
    scenes: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(compiled_plan.steps, start=1):
        scene_id = f"{domain_id}-scene-{index}"
        scenes[scene_id] = {
            "scene_id": scene_id,
            "narration": step.narration,
            "spoken_text": step.spoken_text,
            "target_id": step.target_id,
            "effect": step.effect,
            "gesture": step.gesture,
            "actions": [action.model_dump(mode="json") for action in step.actions],
        }
    return ActivePresentationScenes(template_id=template_id, scenes=scenes)


def scene_instruction(scenes: ActivePresentationScenes, index: int = 0) -> dict[str, Any] | None:
    """Return the exact next text unit; Gemini Live never receives future scenes."""
    ordered = list(scenes.scenes.values())
    if index < 0 or index >= len(ordered):
        return None
    scene = ordered[index]
    return {
        "scene_id": scene["scene_id"],
        "narration": scene.get("spoken_text") or scene["narration"],
    }
