"""Common state-transition tool for the active visual panel."""

from __future__ import annotations


PANEL_ACTION_TOOL = {
    "name": "panel_action",
    "description": (
        "Change the state of one or more anchors on the active panel. "
        "Use action_id 'reveal' only for regions that are currently hidden."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action_id": {"type": "string", "enum": ["reveal"]},
            "anchor_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
        },
        "required": ["action_id", "anchor_ids"],
        "additionalProperties": False,
    },
}
