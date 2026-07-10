"""Weather Agent implementation."""

from __future__ import annotations

import json
from time import perf_counter
from typing import TYPE_CHECKING

from rag_manager.cache import MemoryCache, weather_cache_key
from rag_manager.config import Settings, load_settings
from rag_manager.llm.prompts import WEATHER_SYSTEM_PROMPT
from rag_manager.services.weather_api import fetch_weather
from rag_manager.state import AgentState

if TYPE_CHECKING:
    from rag_manager.llm.gemini_client import GeminiClient as GeminiClientType


def run_weather_agent(
    state: AgentState,
    *,
    cache: MemoryCache | None = None,
    settings: Settings | None = None,
    client: "GeminiClientType | None" = None,
) -> AgentState:
    """Run the weather agent and return state updates."""
    started_at = perf_counter()
    cache = cache or MemoryCache()
    settings = settings or load_settings()
    location = extract_weather_location(state)
    cache_key = weather_cache_key(location)

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        llm_client = _get_client(client, settings)
        answer = format_weather_answer(
            llm_client,
            state.get("query", ""),
            cached_data,
        )
        return {
            "weather_data": cached_data,
            "weather_answer": answer,
            "cache_stats": {"weather": cache.stats()},
            "timings": {"weather": _elapsed_since(started_at)},
            **_llm_usage_update("weather", llm_client),
        }

    response = fetch_weather(
        location,
        api_key=settings.openweather_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
    if not response.get("ok"):
        weather_data = {"location": location, "error": response["error"]}
        if _is_missing_openweather_key_error(response):
            answer = _missing_openweather_key_answer()
            usage_update: AgentState = {}
        else:
            llm_client = _get_client(client, settings)
            answer = format_weather_answer(
                llm_client,
                state.get("query", ""),
                weather_data,
            )
            usage_update = _llm_usage_update("weather", llm_client)
        return {
            "weather_data": weather_data,
            "weather_answer": answer,
            "cache_stats": {"weather": cache.stats()},
            "timings": {"weather": _elapsed_since(started_at)},
            **usage_update,
        }

    weather_data = response["data"]
    cache.set(
        cache_key,
        weather_data,
        ttl_seconds=settings.weather_cache_ttl_seconds,
    )
    llm_client = _get_client(client, settings)
    answer = format_weather_answer(
        llm_client,
        state.get("query", ""),
        weather_data,
    )
    return {
        "weather_data": weather_data,
        "weather_answer": answer,
        "cache_stats": {"weather": cache.stats()},
        "timings": {"weather": _elapsed_since(started_at)},
        **_llm_usage_update("weather", llm_client),
    }


def extract_weather_location(state: AgentState) -> str:
    intent = state.get("intent", {})
    location = intent.get("location", "") if isinstance(intent, dict) else ""
    if isinstance(location, str) and location.strip():
        return location.strip()

    query = state.get("query", "")
    return query.strip() if isinstance(query, str) else ""


def format_weather_answer(
    client: "GeminiClientType",
    query: str,
    weather_data: dict,
) -> str:
    user_message = "\n".join(
        [
            f"User query: {query}",
            "Weather JSON:",
            json.dumps(weather_data, ensure_ascii=False, sort_keys=True),
        ]
    )
    return client.chat_text(
        WEATHER_SYSTEM_PROMPT,
        user_message,
        temperature=0.2,
    )


def _create_gemini_client(settings: Settings) -> "GeminiClientType":
    from rag_manager.llm.gemini_client import GeminiClient

    return GeminiClient(settings)


def _get_client(
    client: "GeminiClientType | None",
    settings: Settings,
) -> "GeminiClientType":
    return client or _create_gemini_client(settings)


def _is_missing_openweather_key_error(response: dict) -> bool:
    error = response.get("error", {})
    if not isinstance(error, dict):
        return False
    message = error.get("message", "")
    return isinstance(message, str) and "OPENWEATHER_API_KEY" in message


def _missing_openweather_key_answer() -> str:
    return (
        "Mình chưa thể tra cứu thời tiết vì thiếu OPENWEATHER_API_KEY. "
        "Hãy cấu hình OPENWEATHER_API_KEY trong file .env hoặc biến môi trường rồi chạy lại."
    )


def _elapsed_since(started_at: float) -> float:
    return perf_counter() - started_at


def _llm_usage_update(agent_name: str, client: object) -> AgentState:
    usage = getattr(client, "last_usage", {})
    if not isinstance(usage, dict) or not usage:
        return {}
    return {"llm_usage": {agent_name: usage}}
