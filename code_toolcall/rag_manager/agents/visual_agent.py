"""Deterministic visual node.

This node intentionally does not call an LLM.  Domain agents own natural-language
answers; the visual node owns only the safe conversion of validated domain output
to a UI panel.  Future interaction tools can be added here without changing the
renderer contract.
"""

from __future__ import annotations

import time
from typing import Any

from rag_manager.tools.visual_tools import VisualTools


def run_visual(
    tools: VisualTools,
    data: dict[str, Any],
    *,
    music_player: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render a domain result without another Gemini inference pass."""

    started = time.perf_counter()
    if music_player:
        tool_name = "render_music_player"
        args: dict[str, Any] = {"player_payload": music_player}
        result = tools.render_music_player(music_player)
    elif not isinstance(data.get("weather"), dict):
        # A music search may return candidates without a selected track.  There
        # is deliberately no weather fallback panel for that case.
        tool_name = "skip_visualization"
        args = {}
        result = {"status": "completed", "data": {}}
    else:
        compact_data = tools.compact_weather_data(data)
        template_id = tools.select_weather_template(compact_data)
        tool_name = "render_visualization"
        args = {"template_id": template_id}
        result = tools.render_visualization(args, compact_data)

    trace = {
        "tool": tool_name,
        "args": args,
        "status": result.get("status", "error"),
        "result": result,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "executor": "visual_code",
    }
    return {"payload": result.get("data", {}), "tool_trace": [trace]}
