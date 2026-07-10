"""Weather Agent implementation using LangChain tool calling."""

from __future__ import annotations

import json
from datetime import datetime
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from rag_manager.cache import MemoryCache, weather_cache_key, weather_hour_bucket
from rag_manager.config import Settings, load_settings
from rag_manager.llm.gemini_client import strip_thought_tags
from rag_manager.llm.prompts import WEATHER_TOOL_AGENT_SYSTEM_PROMPT
from rag_manager.services.weather_api import fetch_weather, fetch_weather_forecast
from rag_manager.state import AgentState


def run_weather_agent(
    state: AgentState,
    *,
    cache: MemoryCache | None = None,
    settings: Settings | None = None,
    client: object | None = None,
) -> AgentState:
    """Run the tool-calling weather agent and return state updates."""
    started_at = perf_counter()
    cache = cache or MemoryCache()
    settings = settings or load_settings()
    query = state.get("query", "")
    location_hint = extract_weather_location(state)

    agent_result = run_weather_tool_agent(
        query=query if isinstance(query, str) else "",
        location_hint=location_hint,
        cache=cache,
        settings=settings,
    )

    update: AgentState = {
        "weather_data": agent_result["weather_data"],
        "weather_answer": agent_result["answer"],
        "cache_stats": {"weather": cache.stats()},
        "timings": {"weather": _elapsed_since(started_at)},
    }
    usage = agent_result.get("llm_usage")
    if isinstance(usage, dict) and usage:
        update["llm_usage"] = {"weather": usage}
    return update


def run_weather_tool_agent(
    *,
    query: str,
    location_hint: str,
    cache: MemoryCache,
    settings: Settings,
) -> dict[str, Any]:
    """Invoke a LangChain agent that chooses weather tools for the query."""
    tools, tool_state = build_weather_tools(
        cache=cache,
        settings=settings,
        location_hint=location_hint,
    )
    model = _create_weather_model(settings)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=WEATHER_TOOL_AGENT_SYSTEM_PROMPT,
    )
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _weather_agent_user_message(query, location_hint),
                }
            ]
        }
    )
    answer = strip_thought_tags(_last_ai_text(result))
    return {
        "answer": answer or _fallback_weather_answer(tool_state),
        "weather_data": tool_state.get("last_weather_data", {}),
        "llm_usage": _extract_langchain_usage(result, settings.gemini_model),
    }


def build_weather_tools(
    *,
    cache: MemoryCache,
    settings: Settings,
    location_hint: str,
) -> tuple[list[Any], dict[str, Any]]:
    """Create weather tools bound to the current request context."""
    tool_state: dict[str, Any] = {}

    @tool
    def get_current_time(timezone_name: str = "Asia/Bangkok") -> dict[str, Any]:
        """Return the current date and time for resolving relative weather timeframes."""
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            timezone = ZoneInfo("UTC")
            timezone_name = "UTC"
        now = datetime.now(timezone)
        return {
            "timezone": timezone_name,
            "iso_datetime": now.isoformat(),
            "date": now.date().isoformat(),
            "weekday": now.strftime("%A"),
        }

    @tool
    def get_current_weather(location: str = "") -> dict[str, Any]:
        """Get current weather for a location using OpenWeather current weather data."""
        resolved_location = _resolve_location(location, location_hint)
        cache_key = weather_cache_key(
            resolved_location,
            bucket=f"current:{weather_hour_bucket()}",
        )
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            tool_state["last_weather_data"] = cached_data
            return {"ok": True, "data": cached_data, "cached": True}

        response = fetch_weather(
            resolved_location,
            api_key=settings.openweather_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
        if response.get("ok"):
            cache.set(
                cache_key,
                response["data"],
                ttl_seconds=settings.weather_cache_ttl_seconds,
            )
            tool_state["last_weather_data"] = response["data"]
        else:
            tool_state["last_weather_data"] = {
                "location": resolved_location,
                "error": response.get("error", {}),
            }
        return response

    @tool
    def get_weather_forecast(location: str = "", days: int = 3) -> dict[str, Any]:
        """Get a daily weather forecast summary for 1 to 5 days for a location."""
        resolved_location = _resolve_location(location, location_hint)
        bounded_days = max(1, min(int(days), 5))
        cache_key = weather_cache_key(
            resolved_location,
            bucket=f"forecast:{bounded_days}:{weather_hour_bucket()}",
        )
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            tool_state["last_weather_data"] = cached_data
            return {"ok": True, "data": cached_data, "cached": True}

        response = fetch_weather_forecast(
            resolved_location,
            api_key=settings.openweather_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            days=bounded_days,
        )
        if response.get("ok"):
            cache.set(
                cache_key,
                response["data"],
                ttl_seconds=settings.weather_cache_ttl_seconds,
            )
            tool_state["last_weather_data"] = response["data"]
        else:
            tool_state["last_weather_data"] = {
                "location": resolved_location,
                "requested_days": bounded_days,
                "error": response.get("error", {}),
            }
        return response

    return [get_current_time, get_current_weather, get_weather_forecast], tool_state


def extract_weather_location(state: AgentState) -> str:
    intent = state.get("intent", {})
    location = intent.get("location", "") if isinstance(intent, dict) else ""
    if isinstance(location, str) and location.strip():
        return location.strip()

    query = state.get("query", "")
    return query.strip() if isinstance(query, str) else ""


def _create_weather_model(settings: Settings) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        api_key=settings.gemini_api_key,
        temperature=0.2,
        max_tokens=1024,
        request_timeout=settings.request_timeout_seconds,
        max_retries=0,
        thinking_level="minimal",
    )


def _weather_agent_user_message(query: str, location_hint: str) -> str:
    return "\n".join(
        [
            f"User query: {query}",
            f"Location hint from manager: {location_hint}",
            "Choose and call the weather tools needed before answering.",
        ]
    )


def _resolve_location(location: str, location_hint: str) -> str:
    return location.strip() or location_hint.strip()


def _last_ai_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return _message_content_text(message.content)
    return ""


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content) if content is not None else ""


def _fallback_weather_answer(tool_state: dict[str, Any]) -> str:
    data = tool_state.get("last_weather_data", {})
    if isinstance(data, dict) and data.get("error"):
        return "Mình chưa thể lấy dữ liệu thời tiết từ công cụ hiện tại."
    if data:
        return "Mình đã lấy được dữ liệu thời tiết nhưng chưa tạo được câu trả lời."
    return "Mình chưa có dữ liệu thời tiết để trả lời."


def _extract_langchain_usage(result: dict[str, Any], model: str) -> dict[str, Any]:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    usage: dict[str, Any] = {}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        usage = _merge_usage_dicts(usage, _usage_from_ai_message(message))

    if not usage:
        return {}
    usage["model"] = model
    return usage


def _usage_from_ai_message(message: AIMessage) -> dict[str, Any]:
    usage_metadata = message.usage_metadata or {}
    response_metadata = message.response_metadata or {}
    token_usage = response_metadata.get("token_usage", {})
    if not isinstance(token_usage, dict):
        token_usage = {}

    prompt_tokens = _int_value(
        usage_metadata.get("input_tokens"),
        token_usage.get("prompt_tokens"),
        token_usage.get("prompt_token_count"),
    )
    completion_tokens = _int_value(
        usage_metadata.get("output_tokens"),
        token_usage.get("completion_tokens"),
        token_usage.get("candidates_token_count"),
    )
    total_tokens = _int_value(
        usage_metadata.get("total_tokens"),
        token_usage.get("total_tokens"),
        token_usage.get("total_token_count"),
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "thoughts_tokens": _int_value(token_usage.get("thoughts_token_count")),
        "total_tokens": total_tokens,
        "cached_tokens": _int_value(
            token_usage.get("cached_content_token_count"),
            token_usage.get("total_cached_tokens"),
        ),
        "prefix_cache_hit": False,
        "cache_hit_ratio": None,
        "kv_cache_hit": "not_exposed_by_gemini_api",
        "raw_usage_keys": sorted(set(usage_metadata) | set(token_usage)),
    }


def _merge_usage_dicts(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return dict(new)
    merged = dict(existing)
    for key in ("prompt_tokens", "completion_tokens", "thoughts_tokens", "total_tokens"):
        merged[key] = _sum_optional_ints(merged.get(key), new.get(key))
    cached_tokens = _sum_optional_ints(merged.get("cached_tokens"), new.get("cached_tokens"))
    merged["cached_tokens"] = cached_tokens
    merged["prefix_cache_hit"] = bool(cached_tokens and cached_tokens > 0)
    prompt_tokens = merged.get("prompt_tokens")
    if isinstance(prompt_tokens, int) and isinstance(cached_tokens, int) and prompt_tokens > 0:
        merged["cache_hit_ratio"] = round(cached_tokens / prompt_tokens, 4)
    merged["raw_usage_keys"] = sorted(
        set(merged.get("raw_usage_keys", [])) | set(new.get("raw_usage_keys", []))
    )
    return merged


def _sum_optional_ints(left: Any, right: Any) -> int | None:
    values = [value for value in (left, right) if isinstance(value, int)]
    return sum(values) if values else None


def _int_value(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int):
            return value
    return None


def _elapsed_since(started_at: float) -> float:
    return perf_counter() - started_at
