"""Explicit tool-to-domain selection for the Live experiment."""

from __future__ import annotations

from rag_manager.config import Settings
from rag_manager.llm.function_calling_runtime import GeminiFunctionCallingRuntime
from rag_manager.tools.visual_tools import VisualTools
from rag_manager.tools.weather_tools import WeatherTools

from .weather_bridge import WeatherLiveBridge


class LiveDomainRegistry:
    """Own domain adapters; add a new domain here without changing Live core rules."""

    _TOOL_DOMAIN = {"get_weather": "weather"}

    def __init__(self, settings: Settings) -> None:
        planner_runtime = GeminiFunctionCallingRuntime(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )
        self.weather = WeatherLiveBridge(
            WeatherTools(settings),
            VisualTools(),
            planner_runtime=planner_runtime,
        )

    def domain_for_tool(self, tool_name: str) -> str | None:
        return self._TOOL_DOMAIN.get(tool_name)
