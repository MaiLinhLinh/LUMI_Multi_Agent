"""Weather domain implementation for the independent Gemini Live application."""

from __future__ import annotations

import asyncio
from typing import Any

from gemini_live.domains.base import DomainRequest, DomainResult, LiveDomain
from gemini_live.presentation import PresentationRequest
from gemini_live.settings import Settings

from .adapter import WeatherPresentationAdapter
from .context import WeatherContextResolver
from .prompt import WEATHER_LIVE_GUIDANCE
from .tools import WEATHER_DECLARATION, WeatherTools
from .view_model import VisualTools, _weather_contract

class WeatherLiveDomain(LiveDomain):
    """Own Weather tools, context, facts, templates, and presentation planning."""

    def __init__(
        self,
        settings: Settings,
    ) -> None:
        self._weather = WeatherTools(settings)
        self._visual = VisualTools()
        self._adapter = WeatherPresentationAdapter()
        self._context = WeatherContextResolver()

    @property
    def domain_id(self) -> str:
        return "weather"

    @property
    def tool_declarations(self) -> tuple[dict[str, Any], ...]:
        return (WEATHER_DECLARATION,)

    @property
    def prompt_guidance(self) -> str:
        return WEATHER_LIVE_GUIDANCE

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        request: DomainRequest,
        context: dict[str, Any],
    ) -> DomainResult:
        if tool_name != "get_weather":
            raise ValueError(f"Weather does not own tool {tool_name!r}.")
        resolved_arguments = self._context.resolve_tool_arguments(arguments, context)
        outcome = await asyncio.to_thread(
            self._execute_get_weather,
            resolved_arguments,
            context,
        )

        return outcome

    def _execute_get_weather(
        self,
        args: dict[str, Any],
        weather_context: dict[str, Any],
    ) -> DomainResult:
        result = self._weather.get_weather(args, weather_context=weather_context)
        status = str(result.get("status") or "error")
        if status != "completed":
            return DomainResult(
                status=str(result.get("status") or "error"),
                context=dict(weather_context),
                detail=self._failure_detail(result),
            )

        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        compact_data = self._visual.compact_weather_data(data)
        template_id = self._visual.select_weather_template(data)
        domain_data = result.get("_llm_response", {}).get("weather_facts", {})
        domain_data = domain_data if isinstance(domain_data, dict) else {}
        return DomainResult(
            status="completed",
            context=self._next_weather_context(result, weather_context),
            presentation=PresentationRequest(
                domain_id=self.domain_id,
                template_id=template_id,
                view_model=_weather_contract(compact_data),
                adapter=self._adapter,
                domain_data=domain_data,
                compact_data=compact_data,
            ),
        )

    @staticmethod
    def _failure_detail(result: dict[str, Any]) -> str | None:
        clarification = result.get("clarification") if isinstance(result.get("clarification"), dict) else {}
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        question = clarification.get("question")
        message = error.get("message")
        return str(question or message) if question or message else None

    @staticmethod
    def _next_weather_context(result: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if not data.get("location_id"):
            return dict(previous)
        next_context = {
            "last_location_id": data["location_id"],
            "last_location_name": data.get("location") or previous.get("last_location_name", ""),
            "last_request_type": data.get("request_type", "forecast"),
            "last_start_date": data.get("requested_date", ""),
            "last_days": data.get("requested_days", 1),
        }
        snapshot = result.get("_session_snapshot")
        if isinstance(snapshot, dict):
            next_context["session_snapshot"] = snapshot
        return next_context
