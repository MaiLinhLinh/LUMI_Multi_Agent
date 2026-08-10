"""Shared server-side validation for Gemini Live visual function calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gemini_live.presentation import LiveFactPack


PRESENT_VISUAL_TOOL = {
    "name": "present_visual",
    "description": (
        "Animate one verified visual anchor currently supplied by the backend. "
        "Use an anchor_id and an allowed effect_id exactly as given."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "anchor_id": {
                "type": "string",
                "description": "A verified visual anchor such as a, b, c, d, or e.",
            },
            "effect_id": {
                "type": "string",
                "description": "An allowed visual effect ID such as circle or highlight.",
            },
        },
        "required": ["anchor_id", "effect_id"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class FactPresentationState:
    """Server-only current anchor-to-DOM evidence for one rendered panel."""

    template_id: str
    anchor_target_map: dict[str, dict[str, Any]]
    effect_id_map: dict[str, str]

    @classmethod
    def from_fact_pack(cls, *, template_id: str, pack: LiveFactPack) -> "FactPresentationState":
        return cls(
            template_id=template_id,
            anchor_target_map=dict(pack.anchor_target_map),
            effect_id_map=dict(pack.effect_id_map),
        )

    def resolve(self, *, anchor_id: str, effect_id: str) -> dict[str, str]:
        evidence = self.anchor_target_map.get(anchor_id)
        if not isinstance(evidence, dict):
            raise ValueError("unknown anchor_id for the active presentation")
        allowed = evidence.get("allowed_effect_ids")
        if not isinstance(allowed, list) or effect_id not in allowed:
            raise ValueError("effect_id is not allowed for this anchor")
        target_id = evidence.get("target_id")
        effect = self.effect_id_map.get(effect_id)
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("anchor has no resolved visual target")
        if not isinstance(effect, str) or not effect:
            raise ValueError("unsupported effect_id")
        return {
            "anchor_id": anchor_id,
            "target_id": target_id,
            "effect_id": effect_id,
            "effect": effect,
        }


@dataclass(frozen=True)
class RenderedPresentation:
    """Trusted rendered panel associated with the active fact presentation."""

    panel: dict[str, Any]
