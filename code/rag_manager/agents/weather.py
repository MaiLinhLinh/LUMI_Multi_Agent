"""Weather Agent implementation using LangChain tool calling."""

from __future__ import annotations

import sys
import traceback
import unicodedata
from datetime import datetime, timedelta
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

WEATHER_PROVIDER = "openweathermap"


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
        query=query,
    )
    try:
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
    except Exception as exc:  # noqa: BLE001 - preserve the original workflow error
        _print_weather_exception_debug(
            exc,
            query=query,
            location_hint=location_hint,
            tool_state=tool_state,
        )
        raise
    answer = strip_thought_tags(_last_ai_text(result))
    return {
        "answer": answer or _fallback_weather_answer(tool_state),
        "weather_data": _build_weather_visualization_data(tool_state),
        "llm_usage": _extract_langchain_usage(result, settings.gemini_model),
    }


def build_weather_tools(
    *,
    cache: MemoryCache,
    settings: Settings,
    location_hint: str,
    query: str = "",
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
            _record_weather_tool_call(tool_state, "get_current_weather", cached=True)
            tool_state["current_weather_data"] = cached_data
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
            _record_weather_tool_call(tool_state, "get_current_weather", cached=False)
            tool_state["current_weather_data"] = response["data"]
            tool_state["last_weather_data"] = response["data"]
        else:
            error_data = {
                "location": resolved_location,
                "error": response.get("error", {}),
            }
            _record_weather_tool_call(tool_state, "get_current_weather", cached=False)
            _record_weather_error(tool_state, error_data)
            tool_state["last_weather_data"] = error_data
        return response

    @tool
    def get_weather_forecast(location: str = "", days: int = 3) -> dict[str, Any]:
        """Get a daily weather forecast summary for 1 to 5 days for a location."""
        resolved_location = _resolve_location(location, location_hint)
        tomorrow_only = _asks_for_tomorrow_only(query)
        # OpenWeather's 3-hour forecast starts with the current day. Request
        # enough data to reach tomorrow, then keep only tomorrow for a direct
        # "ngày mai" request. Other ranges retain their existing semantics.
        bounded_days = max(1, min(int(days), 5))
        service_days = max(2, bounded_days) if tomorrow_only else bounded_days
        cache_key = weather_cache_key(
            resolved_location,
            bucket=f"forecast:{service_days}:{weather_hour_bucket()}",
        )
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            if tomorrow_only:
                cached_data = _tomorrow_only_forecast(cached_data)
            _record_weather_tool_call(tool_state, "get_weather_forecast", cached=True)
            tool_state["forecast_weather_data"] = cached_data
            tool_state["last_weather_data"] = cached_data
            return {"ok": True, "data": cached_data, "cached": True}

        response = fetch_weather_forecast(
            resolved_location,
            api_key=settings.openweather_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            days=service_days,
        )
        if response.get("ok"):
            if tomorrow_only:
                response = {**response, "data": _tomorrow_only_forecast(response.get("data"))}
            cache.set(
                cache_key,
                response["data"],
                ttl_seconds=settings.weather_cache_ttl_seconds,
            )
            _record_weather_tool_call(tool_state, "get_weather_forecast", cached=False)
            tool_state["forecast_weather_data"] = response["data"]
            tool_state["last_weather_data"] = response["data"]
        else:
            error_data = {
                "location": resolved_location,
                "requested_days": bounded_days,
                "error": response.get("error", {}),
            }
            _record_weather_tool_call(tool_state, "get_weather_forecast", cached=False)
            _record_weather_error(tool_state, error_data)
            tool_state["last_weather_data"] = error_data
        return response

    return [get_current_time, get_current_weather, get_weather_forecast], tool_state


def _asks_for_tomorrow_only(query: str) -> bool:
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFD", query.casefold())
        if unicodedata.category(char) != "Mn"
    )
    normalized = " ".join(normalized.split())
    return any(marker in normalized for marker in ("ngay mai", "tomorrow"))


def _tomorrow_only_forecast(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    days = data.get("days")
    if not isinstance(days, list):
        return dict(data)

    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    tomorrow = (today + timedelta(days=1)).isoformat()
    matching_days = [day for day in days if isinstance(day, dict) and day.get("date") == tomorrow]
    result = dict(data)
    result["requested_days"] = 1
    result["days"] = matching_days[:1]
    return result


def _record_weather_tool_call(
    tool_state: dict[str, Any],
    tool_name: str,
    *,
    cached: bool,
) -> None:
    calls = tool_state.setdefault("tool_calls", [])
    if isinstance(calls, list):
        calls.append({"name": tool_name, "cached": cached})


def _record_weather_error(tool_state: dict[str, Any], error_data: dict[str, Any]) -> None:
    errors = tool_state.setdefault("errors", [])
    error = error_data.get("error", {})
    if isinstance(errors, list) and isinstance(error, dict) and error:
        errors.append(error)
    error_records = tool_state.setdefault("error_records", [])
    if isinstance(error_records, list):
        error_records.append(error_data)


def _build_weather_visualization_data(tool_state: dict[str, Any]) -> dict[str, Any]:
    current = _dict_or_none(tool_state.get("current_weather_data"))
    forecast = _dict_or_none(tool_state.get("forecast_weather_data"))
    errors = [error for error in tool_state.get("errors", []) if isinstance(error, dict)]
    error_records = [
        record for record in tool_state.get("error_records", []) if isinstance(record, dict)
    ]

    data = {
        "location": _weather_location(
            current=current,
            forecast=forecast,
            error_records=error_records,
        ),
        "current": _current_payload(current),
        "forecast": _forecast_payload(forecast),
    }
    envelope = {
        "domain": "weather",
        "schema_version": _weather_schema_version(current=current, forecast=forecast, errors=errors),
        "data_type": _weather_data_type(current=current, forecast=forecast, errors=errors),
        "location": data["location"]["name"],
        "data": data,
        "source": {
            "provider": WEATHER_PROVIDER,
            "tools_used": _weather_tools_used(tool_state),
        },
        "available_fields": _available_fields(data),
    }
    if errors:
        envelope["errors"] = errors
    return envelope


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) and not value.get("error") else None


def _weather_schema_version(
    *,
    current: dict[str, Any] | None,
    forecast: dict[str, Any] | None,
    errors: list[dict[str, Any]],
) -> str:
    if current and forecast:
        return "weather.combined.v1"
    if forecast:
        return "weather.forecast.v1"
    if current:
        return "weather.current.v1"
    if errors:
        return "weather.error.v1"
    return "weather.empty.v1"


def _weather_data_type(
    *,
    current: dict[str, Any] | None,
    forecast: dict[str, Any] | None,
    errors: list[dict[str, Any]],
) -> str:
    if current and forecast:
        return "combined"
    if forecast:
        return "forecast"
    if current:
        return "current"
    if errors:
        return "error"
    return "empty"


def _weather_location(
    *,
    current: dict[str, Any] | None,
    forecast: dict[str, Any] | None,
    error_records: list[dict[str, Any]],
) -> dict[str, Any]:
    source = current or forecast or {}
    if not source and error_records:
        source = error_records[0]
    return {
        "name": source.get("location", ""),
        "country": source.get("country", ""),
        "timezone_offset_seconds": source.get(
            "timezone_offset_seconds", source.get("timezone")
        ),
    }


def _current_payload(current: dict[str, Any] | None) -> dict[str, Any] | None:
    if not current:
        return None
    return {
        "timestamp": current.get("timestamp"),
        "timezone_offset_seconds": current.get(
            "timezone_offset_seconds", current.get("timezone")
        ),
        "observed_at_utc": current.get("observed_at_utc"),
        "observed_at_local": current.get("observed_at_local"),
        "condition": current.get("condition", {}),
        "temperature": current.get("temperature", {}),
        "humidity_percent": current.get("humidity_percent"),
        "pressure_hpa": current.get("pressure_hpa"),
        "wind": current.get("wind", {}),
        "cloudiness_percent": current.get("cloudiness_percent"),
    }


def _forecast_payload(forecast: dict[str, Any] | None) -> dict[str, Any] | None:
    if not forecast:
        return None
    return {
        "requested_days": forecast.get("requested_days"),
        "source_granularity": forecast.get("source_granularity"),
        "days": forecast.get("days", []),
    }


def _weather_tools_used(tool_state: dict[str, Any]) -> list[dict[str, Any]]:
    calls = tool_state.get("tool_calls", [])
    return [call for call in calls if isinstance(call, dict)]


def _available_fields(data: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    _collect_available_fields(data, "", fields)
    return fields


def _collect_available_fields(value: Any, prefix: str, fields: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            _collect_available_fields(child, child_prefix, fields)
        return
    if isinstance(value, list):
        if not value:
            return
        list_prefix = f"{prefix}[]" if prefix else "[]"
        for item in value:
            _collect_available_fields(item, list_prefix, fields)
        return
    if isinstance(value, str) and not value.strip():
        return
    if prefix not in fields:
        fields.append(prefix)


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
        max_retries=2,
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


def _print_weather_exception_debug(
    error: Exception,
    *,
    query: str,
    location_hint: str,
    tool_state: dict[str, Any],
) -> None:
    """Print provider and tool details before the workflow handles the error."""
    print("\n[WEATHER DEBUG] Weather Agent failed", file=sys.stderr)
    print(f"[WEATHER DEBUG] exception_type={error.__class__.__module__}.{error.__class__.__name__}", file=sys.stderr)
    print(f"[WEATHER DEBUG] message={error}", file=sys.stderr)
    print(f"[WEATHER DEBUG] args={error.args!r}", file=sys.stderr)
    for attribute in ("status_code", "code", "type", "reason"):
        value = getattr(error, attribute, None)
        if value is not None:
            print(f"[WEATHER DEBUG] {attribute}={value!r}", file=sys.stderr)

    response = getattr(error, "response", None)
    if response is not None:
        print(
            f"[WEATHER DEBUG] response_type={response.__class__.__module__}.{response.__class__.__name__}",
            file=sys.stderr,
        )
        for attribute in ("status_code", "reason", "text", "content"):
            value = getattr(response, attribute, None)
            if value is not None:
                print(f"[WEATHER DEBUG] response.{attribute}={value!r}", file=sys.stderr)

    for attribute in ("body", "details", "errors"):
        value = getattr(error, attribute, None)
        if value is not None:
            print(f"[WEATHER DEBUG] {attribute}={value!r}", file=sys.stderr)

    print(f"[WEATHER DEBUG] query={query!r}", file=sys.stderr)
    print(f"[WEATHER DEBUG] location_hint={location_hint!r}", file=sys.stderr)
    print(f"[WEATHER DEBUG] tool_calls={tool_state.get('tool_calls', [])!r}", file=sys.stderr)
    print(f"[WEATHER DEBUG] tool_errors={tool_state.get('errors', [])!r}", file=sys.stderr)
    print("[WEATHER DEBUG] traceback:", file=sys.stderr)
    traceback.print_exception(error, file=sys.stderr)


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
