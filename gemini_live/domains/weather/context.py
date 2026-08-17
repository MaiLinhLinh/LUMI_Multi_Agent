"""Weather-specific confirmed-context handling for follow-up tool calls."""

from __future__ import annotations

from typing import Any


class WeatherContextResolver:
    """Inherit only a confirmed location; retain snapshot data server-side.

    Date/range interpretation remains Gemini Live's conversational task.  The
    backend never silently substitutes a different time range for the user.
    """

    def resolve_tool_arguments(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        resolved = dict(arguments)
        if not str(resolved.get("location_text") or "").strip():
            location = context.get("last_location_name")
            if isinstance(location, str) and location.strip():
                resolved["location_text"] = location.strip()
        return resolved
