"""Gemini Live declarations used by the isolated experiment only."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from rag_manager.tools.weather_tools import WEATHER_DECLARATION


ANIMATION_DECLARATION: dict[str, Any] = {
    "name": "trigger_scene",
    "description": (
        "Trigger one compiler-approved presentation scene. Use only a scene_id "
        "from the latest completed domain tool response, immediately before "
        "speaking that scene's supplied narration."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scene_id": {"type": "string"},
        },
        "required": ["scene_id"],
    },
}


def live_declarations() -> list[dict[str, Any]]:
    """Return fresh JSON-safe declarations so a caller cannot mutate globals."""
    return [deepcopy(WEATHER_DECLARATION), deepcopy(ANIMATION_DECLARATION)]
