"""Weather Agent implementation using LangChain tool calling."""

from __future__ import annotations

import sys
import traceback
from datetime import date, datetime, timedelta
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from rag_manager.config import Settings, load_settings
from rag_manager.llm.gemini_client import print_llm_cache_metrics, strip_thought_tags
from rag_manager.llm.prompts import WEATHER_TOOL_AGENT_SYSTEM_PROMPT
from rag_manager.services.weather_location_resolver import (
    LOCATION_RESOLVER_SOURCE,
    WeatherLocationResolver,
    get_weather_location_resolver,
)
from rag_manager.services.weather_redis import RedisWeatherStore, WeatherStore
from rag_manager.services.weather_time_validator import (
    EXPECTED_TIMEZONE_OFFSET_SECONDS,
    MAX_FORECAST_DAYS,
    WEATHER_TIMEZONE,
    WeatherTimeValidator,
)
from rag_manager.state import AgentState

WEATHER_PROVIDER = "openweathermap"


def run_weather_agent(
    state: AgentState,
    *,
    store: WeatherStore | None = None,
    settings: Settings | None = None,
    client: object | None = None,
) -> AgentState:
    """Run the tool-calling weather agent and return state updates."""
    started_at = perf_counter()
    settings = settings or load_settings()
    store = store or state.get("weather_store") or RedisWeatherStore.from_settings(settings)
    query = state.get("query", "")
    history = state.get("history", [])
    try:
        agent_result = run_weather_tool_agent(
            query=query if isinstance(query, str) else "",
            history=history if isinstance(history, list) else [],
            store=store,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 - convert agent failure into graph state
        answer = "Hệ thống chưa thể xử lý yêu cầu thời tiết lúc này. Bạn vui lòng thử lại sau."
        return {
            "weather_status": "error",
            "weather_answer": answer,
            "weather_error": {
                "stage": "weather_agent",
                "code": "agent_execution_failed",
                "message": str(exc) or exc.__class__.__name__,
                "retryable": True,
            },
            "final_response": answer,
            "cache_stats": {"weather": store.stats()},
            "timings": {"weather": _elapsed_since(started_at)},
        }

    update: AgentState = {
        "weather_status": agent_result["status"],
        "weather_answer": agent_result["answer"],
        "cache_stats": {"weather": store.stats()},
        "timings": {"weather": _elapsed_since(started_at)},
    }
    weather_data = agent_result.get("weather_data")
    if agent_result["status"] == "completed" and isinstance(weather_data, dict):
        update["weather_data"] = weather_data
    if agent_result["status"] in {"needs_clarification", "unavailable", "error"}:
        update["final_response"] = agent_result["answer"]
    weather_error = agent_result.get("weather_error")
    if isinstance(weather_error, dict) and weather_error:
        update["weather_error"] = weather_error
    usage = agent_result.get("llm_usage")
    if isinstance(usage, dict) and usage:
        update["llm_usage"] = {"weather": usage}
    return update


def run_weather_tool_agent(
    *,
    query: str,
    history: list[dict[str, str]],
    store: WeatherStore,
    settings: Settings,
) -> dict[str, Any]:
    """Invoke a LangChain agent that chooses weather tools for the query."""
    tools, tool_state = build_weather_tools(
        store=store,
        query=query,
        resolver=get_weather_location_resolver(
            settings.weather_locations_file or None
        ),
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
                "messages": _weather_conversation_messages(query, history)
            }
        )
    except Exception as exc:  # noqa: BLE001 - preserve the original workflow error
        _print_weather_exception_debug(
            exc,
            query=query,
            history=history,
            tool_state=tool_state,
        )
        raise
    answer = strip_thought_tags(_last_ai_text(result))
    status = _weather_status(tool_state, answer)
    final_answer = answer or _fallback_weather_answer(tool_state, status=status)
    weather_error = tool_state.get("weather_error")
    return {
        "status": status,
        "answer": final_answer,
        "weather_data": (
            _build_weather_visualization_data(tool_state)
            if status == "completed"
            else None
        ),
        "weather_error": weather_error if isinstance(weather_error, dict) else {},
        "llm_usage": _extract_langchain_usage(result, settings.gemini_model),
    }


def build_weather_tools(
    *,
    store: WeatherStore,
    location_hint: str = "",
    query: str = "",
    resolver: WeatherLocationResolver | None = None,
    reference_datetime: datetime | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Create weather tools bound to the current request context."""
    tool_state: dict[str, Any] = {}
    resolved_resolver = resolver or get_weather_location_resolver()

    time_validator = WeatherTimeValidator()

    @tool
    def get_current_time(timezone_name: str = WEATHER_TIMEZONE) -> dict[str, Any]:
        """Return the current date and time for resolving relative weather timeframes."""
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            timezone = ZoneInfo("UTC")
            timezone_name = "UTC"
        now = datetime.now(timezone)
        _record_support_tool_call(tool_state, "get_current_time", source="system_clock")
        return {
            "timezone": timezone_name,
            "iso_datetime": now.isoformat(),
            "date": now.date().isoformat(),
            "weekday": now.strftime("%A"),
        }

    @tool
    def resolve_weather_location(location_text: str = "") -> dict[str, Any]:
        """Resolve one extracted Vietnamese place phrase to a stable location_id."""
        requested = location_text.strip() or location_hint.strip()
        response = resolved_resolver.resolve(requested)
        _record_support_tool_call(
            tool_state,
            "resolve_weather_location",
            source="location_catalog",
        )
        resolutions = tool_state.setdefault("location_resolutions", [])
        if isinstance(resolutions, list):
            resolutions.append(response)
        if response.get("ok"):
            location_id = str(response.get("location_id", ""))
            resolved_locations = tool_state.setdefault("resolved_locations", {})
            if isinstance(resolved_locations, dict):
                resolved_locations[location_id] = response
        else:
            _record_weather_error(tool_state, response)
        _print_location_resolution(requested, response)
        return response

    @tool
    def get_current_weather(location_id: str) -> dict[str, Any]:
        """Read current weather by a resolver-confirmed location_id from Redis."""
        resolution_error = _require_resolved_location(tool_state, location_id)
        if resolution_error:
            _record_weather_error(tool_state, resolution_error)
            return resolution_error
        lookup_started_at = perf_counter()
        response = store.get_current(location_id)
        _print_redis_lookup("get_current_weather", location_id, response, lookup_started_at)
        if response.get("ok"):
            _record_weather_tool_call(tool_state, "get_current_weather", cached=True)
            tool_state["current_weather_data"] = response["data"]
            tool_state["last_weather_data"] = response["data"]
        else:
            error_data = {
                "location": _resolved_location_name(tool_state, location_id),
                "location_id": location_id,
                "error": response.get("error", {}),
            }
            _record_weather_tool_call(tool_state, "get_current_weather", cached=False)
            _record_weather_error(tool_state, error_data)
            tool_state["last_weather_data"] = error_data
        return response

    @tool
    def get_weather_forecast(
        location_id: str,
        days: int = 3,
        start_date: str = "",
    ) -> dict[str, Any]:
        """Read forecast days by location_id, optional YYYY-MM-DD start date."""
        resolution_error = _require_resolved_location(tool_state, location_id)
        if resolution_error:
            _record_weather_error(tool_state, resolution_error)
            return resolution_error
        bounded_days = max(1, min(int(days), 5))
        lookup_started_at = perf_counter()
        response = store.get_forecast(
            location_id,
            days=bounded_days,
            start_date=start_date.strip() or None,
        )
        _print_redis_lookup("get_weather_forecast", location_id, response, lookup_started_at)
        if response.get("ok"):
            _record_weather_tool_call(tool_state, "get_weather_forecast", cached=True)
            tool_state["forecast_weather_data"] = response["data"]
            tool_state["last_weather_data"] = response["data"]
        else:
            error_data = {
                "location": _resolved_location_name(tool_state, location_id),
                "location_id": location_id,
                "requested_days": bounded_days,
                "requested_start_date": start_date.strip() or None,
                "error": response.get("error", {}),
            }
            _record_weather_tool_call(tool_state, "get_weather_forecast", cached=False)
            _record_weather_error(tool_state, error_data)
            tool_state["last_weather_data"] = error_data
        return response

    @tool
    def validate_weather_request(
        location_text: str | None = None,
        time_text: str | None = None,
        request_type_candidate: str | None = None,
    ) -> dict[str, Any]:
        """Validate raw location/time text and create the internal Redis request."""

        location_value = location_text.strip() if isinstance(location_text, str) else ""
        time_value = time_text.strip() if isinstance(time_text, str) else ""
        missing_fields = [
            field
            for field, value in (("location", location_value), ("time", time_value))
            if not value
        ]
        if missing_fields:
            stage = missing_fields[0] if len(missing_fields) == 1 else "extraction"
            validation = {
                "status": "needs_clarification",
                "stage": stage,
                "code": "missing_weather_requirements",
                "details": {"missing_fields": missing_fields},
            }
            tool_state["weather_validation"] = validation
            tool_state["weather_status"] = "needs_clarification"
            return validation

        location_result = resolve_weather_location.invoke(
            {"location_text": location_value}
        )
        if not location_result.get("ok"):
            raw_error = location_result.get("error", {})
            error = raw_error if isinstance(raw_error, dict) else {}
            validation = {
                "status": "needs_clarification",
                "stage": "location",
                "code": str(error.get("code", "location_not_found")),
                "details": {
                    "requested_text": location_value,
                    "candidates": location_result.get("candidates", []),
                },
            }
            tool_state["weather_validation"] = validation
            tool_state["weather_status"] = "needs_clarification"
            return validation

        location_id = str(location_result.get("location_id", ""))
        resolved_locations = tool_state.get("resolved_locations", {})
        if not (
            location_id
            and isinstance(resolved_locations, dict)
            and location_id in resolved_locations
        ):
            return _set_weather_tool_error(
                tool_state,
                stage="location",
                code="resolved_location_state_missing",
                message="Resolved location was not stored in resolved_locations.",
            )

        time_result = time_validator.validate(
            time_value,
            request_type_candidate=request_type_candidate,
            reference_datetime=reference_datetime,
        )
        if time_result.get("status") != "valid":
            tool_state["weather_validation"] = time_result
            tool_state["weather_status"] = "needs_clarification"
            return time_result

        request_type = time_result.get("request_type")
        request: dict[str, Any] = {
            "request_type": request_type,
            "location_id": location_id,
        }
        if request_type == "forecast":
            start_date = time_result.get("start_date")
            days = time_result.get("days")
            if not _valid_canonical_forecast_time(start_date, days):
                return _set_weather_tool_error(
                    tool_state,
                    stage="time",
                    code="invalid_canonical_time",
                    message="Time validator returned an invalid canonical forecast request.",
                )
            request.update({"start_date": start_date, "days": days})
        elif request_type != "current":
            return _set_weather_tool_error(
                tool_state,
                stage="time",
                code="invalid_request_type",
                message="Time validator returned an unsupported request type.",
            )

        validation = {"status": "ready_for_redis", "request": request}
        tool_state["weather_validation"] = validation
        tool_state["validation_context"] = {
            "reference_datetime": time_result.get("reference_datetime"),
            "timezone": WEATHER_TIMEZONE,
            "expected_timezone_offset_seconds": EXPECTED_TIMEZONE_OFFSET_SECONDS,
        }
        return validation

    @tool("get_current_weather")
    def guarded_get_current_weather(location_id: str = "") -> dict[str, Any]:
        """Read validated current weather from the active Redis snapshot."""

        request = _validated_data_request(
            tool_state,
            expected_request_type="current",
        )
        if request is None:
            return _tool_gate_error_response(tool_state, "get_current_weather")
        response = get_current_weather.invoke(
            {"location_id": str(request["location_id"])}
        )
        return _finalize_weather_data_response(
            tool_state,
            response,
            request=request,
            data_key="current_weather_data",
        )

    @tool("get_weather_forecast")
    def guarded_get_weather_forecast(
        location_id: str = "",
        days: int = 1,
        start_date: str = "",
    ) -> dict[str, Any]:
        """Read a validated forecast range from the active Redis snapshot."""

        request = _validated_data_request(
            tool_state,
            expected_request_type="forecast",
        )
        if request is None:
            return _tool_gate_error_response(tool_state, "get_weather_forecast")
        response = get_weather_forecast.invoke(
            {
                "location_id": str(request["location_id"]),
                "days": int(request["days"]),
                "start_date": str(request["start_date"]),
            }
        )
        return _finalize_weather_data_response(
            tool_state,
            response,
            request=request,
            data_key="forecast_weather_data",
        )

    return [
        validate_weather_request,
        guarded_get_current_weather,
        guarded_get_weather_forecast,
    ], tool_state


def _valid_canonical_forecast_time(start_date: Any, days: Any) -> bool:
    if not isinstance(start_date, str) or not isinstance(days, int):
        return False
    try:
        date.fromisoformat(start_date)
    except ValueError:
        return False
    return 1 <= days <= MAX_FORECAST_DAYS


def _set_weather_tool_error(
    tool_state: dict[str, Any],
    *,
    stage: str,
    code: str,
    message: str,
    retryable: bool = False,
) -> dict[str, Any]:
    weather_error = {
        "stage": stage,
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    response = {
        "ok": False,
        "status": "error",
        "error": {
            "source": "weather_validation",
            "code": code,
            "message": message,
            "status_code": None,
        },
    }
    tool_state["weather_status"] = "error"
    tool_state["weather_error"] = weather_error
    tool_state["weather_validation"] = response
    _record_weather_error(tool_state, response)
    return response


def _validated_data_request(
    tool_state: dict[str, Any],
    *,
    expected_request_type: str,
) -> dict[str, Any] | None:
    validation = tool_state.get("weather_validation")
    if not isinstance(validation, dict) or validation.get("status") != "ready_for_redis":
        return None
    request = validation.get("request")
    if not isinstance(request, dict):
        return None
    if request.get("request_type") != expected_request_type:
        return None
    location_id = request.get("location_id")
    resolved_locations = tool_state.get("resolved_locations", {})
    if not (
        isinstance(location_id, str)
        and isinstance(resolved_locations, dict)
        and location_id in resolved_locations
    ):
        return None
    if expected_request_type == "forecast" and not _valid_canonical_forecast_time(
        request.get("start_date"),
        request.get("days"),
    ):
        return None
    return request


def _tool_gate_error_response(
    tool_state: dict[str, Any],
    requested_tool: str,
) -> dict[str, Any]:
    validation = tool_state.get("weather_validation")
    expected_request_type = None
    if isinstance(validation, dict) and isinstance(validation.get("request"), dict):
        expected_request_type = validation["request"].get("request_type")
    message = (
        f"Tool {requested_tool!r} does not match the code-validated weather request."
    )
    response = {
        "ok": False,
        "error": {
            "source": "weather_data_tool_gate",
            "code": "tool_call_mismatch",
            "message": message,
            "status_code": None,
        },
        "details": {"expected_request_type": expected_request_type},
    }
    tool_state["weather_status"] = "error"
    tool_state["weather_error"] = {
        "stage": "data_tool_gate",
        "code": "tool_call_mismatch",
        "message": message,
        "retryable": False,
    }
    _record_weather_error(tool_state, response)
    return response


def _finalize_weather_data_response(
    tool_state: dict[str, Any],
    response: dict[str, Any],
    *,
    request: dict[str, Any],
    data_key: str,
) -> dict[str, Any]:
    if not response.get("ok"):
        if _is_unavailable_redis_response(response):
            tool_state["weather_status"] = "unavailable"
        else:
            error = response.get("error", {})
            message = (
                str(error.get("message", "Weather Redis lookup failed."))
                if isinstance(error, dict)
                else "Weather Redis lookup failed."
            )
            tool_state["weather_status"] = "error"
            tool_state["weather_error"] = {
                "stage": "redis",
                "code": "redis_lookup_failed",
                "message": message,
                "retryable": True,
            }
        return response

    data = response.get("data")
    if not isinstance(data, dict):
        return _invalidate_weather_data(
            tool_state,
            data_key=data_key,
            code="invalid_redis_weather_payload",
            message="Redis returned an invalid weather data payload.",
            status="error",
        )

    timezone_offset = data.get("timezone_offset_seconds", data.get("timezone"))
    if timezone_offset != EXPECTED_TIMEZONE_OFFSET_SECONDS:
        return _invalidate_weather_data(
            tool_state,
            data_key=data_key,
            code="snapshot_timezone_mismatch",
            message=(
                "Weather snapshot timezone does not match Asia/Ho_Chi_Minh "
                f"({EXPECTED_TIMEZONE_OFFSET_SECONDS} seconds)."
            ),
            status="error",
        )

    if request.get("request_type") == "forecast":
        expected_dates = _expected_forecast_dates(
            str(request["start_date"]),
            int(request["days"]),
        )
        returned_days = data.get("days")
        returned_dates = [
            item.get("date")
            for item in returned_days
            if isinstance(item, dict) and isinstance(item.get("date"), str)
        ] if isinstance(returned_days, list) else []
        if returned_dates != expected_dates:
            return _invalidate_weather_data(
                tool_state,
                data_key=data_key,
                code="forecast_date_unavailable",
                message="The active snapshot does not contain the exact requested forecast range.",
                status="unavailable",
                details={
                    "requested_dates": expected_dates,
                    "returned_dates": returned_dates,
                },
            )

    tool_state["weather_status"] = "completed"
    return response


def _expected_forecast_dates(start_date: str, days: int) -> list[str]:
    first_date = date.fromisoformat(start_date)
    return [
        (first_date + timedelta(days=offset)).isoformat()
        for offset in range(days)
    ]


def _invalidate_weather_data(
    tool_state: dict[str, Any],
    *,
    data_key: str,
    code: str,
    message: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_state.pop(data_key, None)
    response = {
        "ok": False,
        "error": {
            "source": "weather_response_validation",
            "code": code,
            "message": message,
            "status_code": None,
        },
    }
    if details:
        response["details"] = details
    tool_state["last_weather_data"] = {
        "location": _validated_location_name(tool_state),
        "error": response["error"],
        **({"details": details} if details else {}),
    }
    tool_state["weather_status"] = status
    _record_weather_error(tool_state, response)
    if status == "error":
        tool_state["weather_error"] = {
            "stage": "redis_response_validation",
            "code": code,
            "message": message,
            "retryable": False,
        }
    return response


def _validated_location_name(tool_state: dict[str, Any]) -> str:
    validation = tool_state.get("weather_validation", {})
    request = validation.get("request", {}) if isinstance(validation, dict) else {}
    location_id = request.get("location_id", "") if isinstance(request, dict) else ""
    return _resolved_location_name(tool_state, str(location_id))


def _is_unavailable_redis_response(response: dict[str, Any]) -> bool:
    error = response.get("error", {})
    message = str(error.get("message", "")).casefold() if isinstance(error, dict) else ""
    return any(
        marker in message
        for marker in (
            "no active weather snapshot",
            "is not present in active weather snapshot",
            "requested forecast",
        )
    )


def _record_weather_tool_call(
    tool_state: dict[str, Any],
    tool_name: str,
    *,
    cached: bool,
) -> None:
    calls = tool_state.setdefault("tool_calls", [])
    if isinstance(calls, list):
        calls.append({"name": tool_name, "cached": cached})


def _record_support_tool_call(
    tool_state: dict[str, Any],
    tool_name: str,
    *,
    source: str,
) -> None:
    calls = tool_state.setdefault("tool_calls", [])
    if isinstance(calls, list):
        calls.append({"name": tool_name, "source": source})


def _require_resolved_location(
    tool_state: dict[str, Any],
    location_id: str,
) -> dict[str, Any] | None:
    resolved_locations = tool_state.get("resolved_locations", {})
    if (
        isinstance(resolved_locations, dict)
        and location_id.strip()
        and location_id in resolved_locations
    ):
        return None
    return {
        "ok": False,
        "error": {
            "source": LOCATION_RESOLVER_SOURCE,
            "code": "location_not_resolved",
            "message": (
                "location_id chưa được xác nhận. "
                "Hãy gọi resolve_weather_location trước khi đọc dữ liệu thời tiết."
            ),
            "status_code": None,
        },
    }


def _resolved_location_name(tool_state: dict[str, Any], location_id: str) -> str:
    resolved_locations = tool_state.get("resolved_locations", {})
    if isinstance(resolved_locations, dict):
        resolution = resolved_locations.get(location_id, {})
        if isinstance(resolution, dict):
            name = resolution.get("canonical_name")
            if isinstance(name, str):
                return name
    return location_id


def _print_location_resolution(requested: str, response: dict[str, Any]) -> None:
    if response.get("ok"):
        print(
            "[WEATHER_LOCATION] "
            f"input={requested!r} "
            f"location_id={response.get('location_id')!r} "
            f"match_type={response.get('match_type')} "
            f"confidence={response.get('confidence')}"
        )
        return
    error = response.get("error", {})
    print(
        "[WEATHER_LOCATION] "
        f"input={requested!r} "
        f"error={error.get('code', 'unknown') if isinstance(error, dict) else 'unknown'}"
    )


def _print_redis_lookup(
    tool_name: str,
    location_id: str,
    response: dict[str, Any],
    started_at: float,
) -> None:
    elapsed_ms = (perf_counter() - started_at) * 1000
    print(
        "[WEATHER_REDIS] "
        f"tool={tool_name} "
        f"location_id={location_id!r} "
        f"ok={bool(response.get('ok'))} "
        f"snapshot_id={response.get('snapshot_id')!r} "
        f"lookup_ms={elapsed_ms:.2f}"
    )


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
        "location_id": source.get("location_id", ""),
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
        "day_grouping": forecast.get("day_grouping"),
        "interval_time_basis": forecast.get("interval_time_basis"),
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


def _create_weather_model(settings: Settings) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        api_key=settings.gemini_api_key,
        temperature=0.2,
        max_tokens=1024,
        request_timeout=settings.request_timeout_seconds,
        max_retries=2,
        #thinking_level="minimal",
    )


def _weather_conversation_messages(
    query: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for message in history:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        if content.strip():
            messages.append({"role": role, "content": content})
    if not (
        messages
        and messages[-1]["role"] == "user"
        and messages[-1]["content"] == query
    ):
        messages.append({"role": "user", "content": query})
    return messages


def _print_weather_exception_debug(
    error: Exception,
    *,
    query: str,
    history: list[dict[str, str]],
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
    print(f"[WEATHER DEBUG] history_messages={len(history)}", file=sys.stderr)
    print(f"[WEATHER DEBUG] tool_calls={tool_state.get('tool_calls', [])!r}", file=sys.stderr)
    print(f"[WEATHER DEBUG] tool_errors={tool_state.get('errors', [])!r}", file=sys.stderr)
    print("[WEATHER DEBUG] traceback:", file=sys.stderr)
    traceback.print_exception(error, file=sys.stderr)


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


def _weather_status(tool_state: dict[str, Any], answer: str) -> str:
    status = tool_state.get("weather_status")
    if status in {"needs_clarification", "unavailable", "error", "completed"}:
        return status

    validation = tool_state.get("weather_validation")
    if isinstance(validation, dict):
        if validation.get("status") == "needs_clarification":
            return "needs_clarification"
        if validation.get("status") == "ready_for_redis":
            _set_weather_tool_error(
                tool_state,
                stage="data_tool",
                code="data_tool_not_called",
                message="Weather validation succeeded but no Redis data tool completed.",
            )
            return "error"

    if answer.strip():
        return "needs_clarification"
    _set_weather_tool_error(
        tool_state,
        stage="weather_agent",
        code="missing_weather_agent_result",
        message="Weather Agent returned neither validation nor an answer.",
    )
    return "error"


def _fallback_weather_answer(
    tool_state: dict[str, Any],
    *,
    status: str,
) -> str:
    if status == "needs_clarification":
        validation = tool_state.get("weather_validation")
        if isinstance(validation, dict):
            code = validation.get("code")
            details = validation.get("details", {})
            details = details if isinstance(details, dict) else {}
            if code in {"missing_location", "location_not_found", "ambiguous_location"}:
                return "Bạn muốn xem thời tiết ở tỉnh hoặc thành phố nào?"
            if code == "missing_weather_requirements":
                missing_fields = details.get("missing_fields", [])
                if missing_fields == ["location"]:
                    return "Bạn muốn xem thời tiết ở tỉnh hoặc thành phố nào?"
                if missing_fields == ["time"]:
                    return "Bạn muốn xem thời tiết vào thời điểm nào?"
                return "Bạn muốn xem thời tiết ở đâu và vào thời điểm nào?"
            if code == "weekday_date_conflict":
                provided_date = _display_iso_date(details.get("provided_date"))
                matching_date = _display_iso_date(details.get("matching_weekday_date"))
                actual_weekday = details.get("actual_weekday", "ngày khác")
                provided_weekday = details.get("provided_weekday", "thứ đã nêu")
                return (
                    f"Ngày {provided_date} là {actual_weekday}. Bạn muốn xem "
                    f"{provided_weekday} ngày {matching_date} hay {actual_weekday} "
                    f"ngày {provided_date}?"
                )
            if code == "forecast_range_exceeded":
                return "Hệ thống chỉ hỗ trợ dự báo tối đa 5 ngày. Bạn có muốn xem 5 ngày đầu tiên không?"
        return "Bạn vui lòng cung cấp rõ địa điểm và thời gian muốn xem thời tiết."
    if status == "unavailable":
        return "Dữ liệu thời tiết cache hiện không có snapshot hoặc khoảng ngày bạn yêu cầu."
    if status == "error":
        return "Hệ thống chưa thể xử lý yêu cầu thời tiết lúc này. Bạn vui lòng thử lại sau."

    data = tool_state.get("last_weather_data", {})
    if data:
        return "Mình đã lấy được dữ liệu thời tiết nhưng chưa tạo được câu trả lời."
    return "Mình chưa có dữ liệu thời tiết để trả lời."


def _display_iso_date(value: Any) -> str:
    if not isinstance(value, str):
        return "không xác định"
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    return f"{parsed.day}/{parsed.month}/{parsed.year}"


def _extract_langchain_usage(result: dict[str, Any], model: str) -> dict[str, Any]:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    usage: dict[str, Any] = {}
    call_id = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        call_id += 1
        message_usage = _usage_from_ai_message(message)
        print_llm_cache_metrics(
            message_usage,
            source="weather_langchain",
            call_id=call_id,
        )
        usage = _merge_usage_dicts(usage, message_usage)

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
    input_token_details = usage_metadata.get("input_token_details", {})
    if not isinstance(input_token_details, dict):
        input_token_details = {}

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
    cached_tokens = _int_value(
        input_token_details.get("cache_read"),
        input_token_details.get("cached_tokens"),
        usage_metadata.get("cached_content_token_count"),
        usage_metadata.get("total_cached_tokens"),
        token_usage.get("cached_content_token_count"),
        token_usage.get("total_cached_tokens"),
    )
    cache_hit_ratio = None
    if isinstance(prompt_tokens, int) and isinstance(cached_tokens, int) and prompt_tokens > 0:
        cache_hit_ratio = round(cached_tokens / prompt_tokens, 4)

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "thoughts_tokens": _int_value(token_usage.get("thoughts_token_count")),
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "prefix_cache_hit": cached_tokens > 0 if isinstance(cached_tokens, int) else None,
        "cache_hit_ratio": cache_hit_ratio,
        "saved_tokens_estimated": cached_tokens,
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
    merged["prefix_cache_hit"] = (
        cached_tokens > 0 if isinstance(cached_tokens, int) else None
    )
    merged["saved_tokens_estimated"] = cached_tokens
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
