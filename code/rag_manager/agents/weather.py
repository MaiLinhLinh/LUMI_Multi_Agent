"""Weather Agent implementation with a native two-call pipeline and legacy ReAct path."""

from __future__ import annotations

import json
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
from rag_manager.llm.prompts import (
    WEATHER_PIPELINE_EXTRACTION_SYSTEM_PROMPT,
    WEATHER_PIPELINE_RESPONSE_SYSTEM_PROMPT,
    WEATHER_TOOL_AGENT_SYSTEM_PROMPT,
)
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
_WEATHER_PIPELINE_STATUSES = {
    "needs_clarification",
    "unavailable",
    "error",
    "completed",
}
_WEATHER_PIPELINE_ERROR_ANSWER = (
    "Hệ thống không thể tạo câu trả lời thời tiết lúc này. "
    "Bạn vui lòng thử lại sau."
)


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


def run_weather_llm_pipeline(
    state: AgentState,
    *,
    store: WeatherStore | None = None,
    settings: Settings | None = None,
    client: object | None = None,
) -> AgentState:
    """Run the two-call weather pipeline without LangChain tool calling.

    Python owns extraction/schema classification, location/time validation,
    Redis access, and the final status. The second LLM call only renders the
    response for that status.
    """

    started_at = perf_counter()
    settings = settings or load_settings()
    store = store or state.get("weather_store")
    query = state.get("query", "")
    query = query if isinstance(query, str) else ""
    history = state.get("history", [])
    history = history if isinstance(history, list) else []

    usage: dict[str, Any] = {"call_1": {}, "call_2": {}}
    extraction: dict[str, Any] = {}
    validation_result: dict[str, Any] = {}
    canonical_request: dict[str, Any] = {}
    redis_result: dict[str, Any] | None = None
    redis_error: dict[str, Any] | None = None
    weather_error: dict[str, Any] = {}
    weather_data: dict[str, Any] | None = None
    status = "error"

    pipeline_client = client
    if pipeline_client is None:
        try:
            from rag_manager.llm.gemini_client import GeminiClient

            pipeline_client = GeminiClient(settings)
        except Exception as exc:  # noqa: BLE001 - external LLM boundary
            weather_error = _pipeline_error(
                stage="llm1_extraction",
                code="llm1_api_error",
                message=str(exc) or exc.__class__.__name__,
                retryable=True,
            )
            _log_pipeline_event(
                stage="llm1_extraction",
                code="llm1_api_error",
                status="error",
                message=weather_error["message"],
            )

    if pipeline_client is not None:
        try:
            if not hasattr(pipeline_client, "chat_json"):
                raise TypeError("Weather pipeline client must provide chat_json().")
            raw_extraction = pipeline_client.chat_json(
                WEATHER_PIPELINE_EXTRACTION_SYSTEM_PROMPT,
                _pipeline_extraction_message(query, history),
            )
            usage["call_1"] = _pipeline_client_usage(pipeline_client)
            extraction_error = _validate_pipeline_extraction(raw_extraction)
            if extraction_error:
                weather_error = extraction_error
                status = "error"
                extraction = raw_extraction if isinstance(raw_extraction, dict) else {}
                _log_pipeline_event(
                    stage="llm1_extraction",
                    code=extraction_error["code"],
                    status=status,
                    message=extraction_error.get("message", ""),
                )
            else:
                extraction = raw_extraction
                missing_fields = _pipeline_missing_extraction_fields(extraction)
                if missing_fields:
                    status = "needs_clarification"
                    validation_result = {
                        "status": "needs_clarification",
                        "stage": "extraction",
                        "code": "missing_weather_requirements",
                        "details": {"missing_fields": missing_fields},
                    }
                    _log_pipeline_event(
                        stage="llm1_extraction",
                        code="missing_weather_requirements",
                        status=status,
                        missing_fields=missing_fields,
                    )
                else:
                    (
                        status,
                        validation_result,
                        canonical_request,
                        weather_error,
                        location_result,
                    ) = _pipeline_validate_request(
                        extraction,
                        settings=settings,
                    )
                    if isinstance(location_result, dict) and location_result.get("ok"):
                        validation_result["location_resolution"] = location_result
                    _log_pipeline_event(
                        stage="validation",
                        code=str(validation_result.get("code", "ready_for_redis")),
                        status=status,
                        message=weather_error.get(
                            "message",
                            validation_result.get("details", {}),
                        ),
                    )

                    if status == "ready_for_redis":
                        if store is None:
                            try:
                                store = RedisWeatherStore.from_settings(settings)
                            except Exception as exc:  # noqa: BLE001 - Redis setup boundary
                                redis_error = _pipeline_error(
                                    stage="redis",
                                    code="redis_client_unavailable",
                                    message=str(exc) or exc.__class__.__name__,
                                    retryable=True,
                                )
                                _log_pipeline_event(
                                    stage="redis",
                                    code="redis_client_unavailable",
                                    status="error",
                                    message=redis_error["message"],
                                )
                        if store is None:
                            status = "error"
                            redis_error = redis_error or _pipeline_error(
                                stage="redis",
                                code="redis_client_unavailable",
                                message="Weather Redis store is unavailable.",
                                retryable=True,
                            )
                        else:
                            try:
                                (
                                    status,
                                    redis_result,
                                    redis_error,
                                    weather_data,
                                ) = _pipeline_read_redis(
                                    store,
                                    canonical_request,
                                    validation_result,
                                )
                            except Exception as exc:  # noqa: BLE001 - Redis boundary
                                status = "error"
                                redis_error = _pipeline_error(
                                    stage="redis",
                                    code="redis_response_error",
                                    message=str(exc) or exc.__class__.__name__,
                                    retryable=True,
                                )
                                weather_data = None
                        if redis_error:
                            weather_error = redis_error if status == "error" else {}
                        _log_pipeline_event(
                            stage="redis",
                            code=(
                                str(redis_error.get("code"))
                                if redis_error
                                else status
                            ),
                            status=status,
                            message=(
                                redis_error.get("message", "")
                                if redis_error
                                else _pipeline_response_error_message(redis_result)
                            ),
                        )
        except Exception as exc:  # noqa: BLE001 - normalize LLM1 failures
            status = "error"
            weather_error = _pipeline_error(
                stage="llm1_extraction",
                code="llm1_api_error",
                message=str(exc) or exc.__class__.__name__,
                retryable=True,
            )
            _log_pipeline_event(
                stage="llm1_extraction",
                code="llm1_api_error",
                status=status,
                message=weather_error["message"],
            )

    # Internal validation uses ready_for_redis; it must never escape as the
    # public weather status.
    if status == "ready_for_redis":
        status = "error"
        weather_error = _pipeline_error(
            stage="pipeline",
            code="redis_not_executed",
            message="Validated weather request did not reach Redis.",
            retryable=False,
        )

    pre_llm2_status = status if status in _WEATHER_PIPELINE_STATUSES else "error"
    pre_llm2_error = weather_error
    final_answer = ""
    if pipeline_client is not None:
        try:
            if not hasattr(pipeline_client, "chat_text"):
                raise TypeError("Weather pipeline client must provide chat_text().")
            final_answer = pipeline_client.chat_text(
                WEATHER_PIPELINE_RESPONSE_SYSTEM_PROMPT,
                _pipeline_response_message(
                    query=query,
                    history=history,
                    extraction=extraction,
                    status=pre_llm2_status,
                    validation_result=validation_result,
                    canonical_request=canonical_request,
                    redis_result=redis_result,
                    redis_error=redis_error,
                    processing_error=pre_llm2_error,
                ),
            )
            usage["call_2"] = _pipeline_client_usage(pipeline_client)
            if not isinstance(final_answer, str) or not final_answer.strip():
                raise ValueError("LLM2 returned an empty response.")
            final_answer = strip_thought_tags(final_answer)
            if not final_answer:
                raise ValueError("LLM2 returned an empty response after sanitization.")
        except Exception as exc:  # noqa: BLE001 - LLM2 has no fallback response
            status = "error"
            final_answer = _WEATHER_PIPELINE_ERROR_ANSWER
            weather_error = _pipeline_error(
                stage="llm2_response",
                code=(
                    "llm2_invalid_output"
                    if isinstance(exc, ValueError)
                    else "llm2_api_error"
                ),
                message=str(exc) or exc.__class__.__name__,
                retryable=True,
                details={
                    "pre_llm2_status": pre_llm2_status,
                    "pre_llm2_error": pre_llm2_error,
                },
            )
            _log_pipeline_event(
                stage="llm2_response",
                code=weather_error["code"],
                pre_llm2_status=pre_llm2_status,
                final_status="error",
                message=weather_error["message"],
            )
    else:
        status = "error"
        final_answer = _WEATHER_PIPELINE_ERROR_ANSWER
        weather_error = _pipeline_error(
            stage="llm2_response",
            code="llm2_api_error",
            message="Weather pipeline client is unavailable.",
            retryable=True,
            details={
                "pre_llm2_status": pre_llm2_status,
                "pre_llm2_error": pre_llm2_error,
            },
        )
        _log_pipeline_event(
            stage="llm2_response",
            code="llm2_api_error",
            pre_llm2_status=pre_llm2_status,
            final_status="error",
        )

    update: AgentState = {
        "weather_status": status,
        "weather_answer": final_answer,
        "final_response": final_answer,
        "cache_stats": {"weather": _pipeline_store_stats(store)},
        "timings": {"weather": _elapsed_since(started_at)},
        "llm_usage": {"weather": usage},
    }
    if status == "completed" and isinstance(weather_data, dict):
        update["weather_data"] = weather_data
    if weather_error:
        update["weather_error"] = weather_error
    return update


def _pipeline_extraction_message(query: str, history: list[Any]) -> str:
    return json.dumps(
        {
            "query": query,
            "relevant_history": _weather_conversation_messages(query, history),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _pipeline_response_message(
    *,
    query: str,
    history: list[Any],
    extraction: dict[str, Any],
    status: str,
    validation_result: dict[str, Any],
    canonical_request: dict[str, Any],
    redis_result: dict[str, Any] | None,
    redis_error: dict[str, Any] | None,
    processing_error: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "query": query,
            "relevant_history": _weather_conversation_messages(query, history),
            "extraction": extraction,
            "status": status,
            "validation_result": validation_result,
            "canonical_request": canonical_request,
            "redis_result": redis_result,
            "redis_error": redis_error,
            "processing_error": processing_error,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _validate_pipeline_extraction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _pipeline_error(
            stage="llm1_extraction",
            code="llm1_invalid_json" if isinstance(value, str) else "llm1_schema_error",
            message=(
                "LLM1 returned non-JSON text."
                if isinstance(value, str)
                else "LLM1 did not return a JSON object."
            ),
            retryable=False,
        )
    error_marker = value.get("error")
    if isinstance(error_marker, str) and error_marker in {
        "invalid_json",
        "json_not_object",
    }:
        return _pipeline_error(
            stage="llm1_extraction",
            code="llm1_invalid_json",
            message=str(value.get("message", "LLM1 returned invalid JSON.")),
            retryable=False,
        )
    required_fields = {"location_text", "time_text", "request_type_candidate"}
    missing = sorted(required_fields.difference(value))
    if missing:
        return _pipeline_error(
            stage="llm1_extraction",
            code="llm1_schema_error",
            message=f"LLM1 extraction is missing fields: {', '.join(missing)}.",
            retryable=False,
            details={"missing_fields": missing},
        )
    for field in ("location_text", "time_text"):
        field_value = value.get(field)
        if field_value is not None and not isinstance(field_value, str):
            return _pipeline_error(
                stage="llm1_extraction",
                code="llm1_schema_error",
                message=f"LLM1 field {field!r} must be a string or null.",
                retryable=False,
            )
    candidate = value.get("request_type_candidate")
    if candidate is not None and candidate not in {"current", "forecast"}:
        return _pipeline_error(
            stage="llm1_extraction",
            code="llm1_schema_error",
            message="LLM1 request_type_candidate must be current, forecast, or null.",
            retryable=False,
        )
    return {}


def _pipeline_missing_extraction_fields(extraction: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in ("location_text", "time_text"):
        value = extraction.get(field)
        if not isinstance(value, str) or not value.strip():
            missing.append("location" if field == "location_text" else "time")
    return missing


def _pipeline_validate_request(
    extraction: dict[str, Any],
    *,
    settings: Settings,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    location_text = str(extraction.get("location_text", "")).strip()
    time_text = str(extraction.get("time_text", "")).strip()
    try:
        resolver = get_weather_location_resolver(settings.weather_locations_file or None)
        location_result = resolver.resolve(location_text)
    except Exception as exc:  # noqa: BLE001 - resolver boundary
        error = _pipeline_error(
            stage="validation",
            code="location_resolver_error",
            message=str(exc) or exc.__class__.__name__,
            retryable=False,
        )
        return "error", {"status": "error", "code": error["code"]}, {}, error, {}

    if not isinstance(location_result, dict):
        error = _pipeline_error(
            stage="validation",
            code="location_resolver_invalid_response",
            message="Location resolver returned an invalid response.",
            retryable=False,
        )
        return "error", {"status": "error", "code": error["code"]}, {}, error, {}
    if not location_result.get("ok"):
        raw_error = location_result.get("error", {})
        raw_error = raw_error if isinstance(raw_error, dict) else {}
        validation = {
            "status": "needs_clarification",
            "stage": "location",
            "code": str(raw_error.get("code", "location_not_found")),
            "details": {
                "requested_text": location_text,
                "candidates": location_result.get("candidates", []),
                "message": raw_error.get("message", ""),
            },
        }
        return "needs_clarification", validation, {}, {}, location_result

    location_id = location_result.get("location_id")
    if not isinstance(location_id, str) or not location_id.strip():
        error = _pipeline_error(
            stage="validation",
            code="invalid_resolved_location",
            message="Location resolver did not return a valid location_id.",
            retryable=False,
        )
        return "error", {"status": "error", "code": error["code"]}, {}, error, location_result

    try:
        time_result = WeatherTimeValidator().validate(
            time_text,
            request_type_candidate=extraction.get("request_type_candidate"),
        )
    except Exception as exc:  # noqa: BLE001 - validator boundary
        error = _pipeline_error(
            stage="validation",
            code="time_validator_error",
            message=str(exc) or exc.__class__.__name__,
            retryable=False,
        )
        return "error", {"status": "error", "code": error["code"]}, {}, error, location_result

    if not isinstance(time_result, dict):
        error = _pipeline_error(
            stage="validation",
            code="time_validator_invalid_response",
            message="Time validator returned an invalid response.",
            retryable=False,
        )
        return "error", {"status": "error", "code": error["code"]}, {}, error, location_result
    validation = {
        "status": time_result.get("status"),
        "location_resolution": location_result,
        "time_validation": time_result,
    }
    if time_result.get("status") != "valid":
        validation.update(
            {
                "stage": "time",
                "code": str(time_result.get("code", "invalid_time")),
                "details": time_result.get("details", {}),
            }
        )
        return "needs_clarification", validation, {}, {}, location_result

    request_type = time_result.get("request_type")
    request: dict[str, Any] = {
        "request_type": request_type,
        "location_id": location_id,
    }
    if request_type == "forecast":
        start_date = time_result.get("start_date")
        days = time_result.get("days")
        if not _valid_canonical_forecast_time(start_date, days):
            error = _pipeline_error(
                stage="validation",
                code="invalid_canonical_time",
                message="Time validator returned an invalid canonical forecast request.",
                retryable=False,
            )
            return "error", {"status": "error", "code": error["code"]}, {}, error, location_result
        request.update({"start_date": start_date, "days": days})
    elif request_type != "current":
        error = _pipeline_error(
            stage="validation",
            code="invalid_request_type",
            message="Time validator returned an unsupported request type.",
            retryable=False,
        )
        return "error", {"status": "error", "code": error["code"]}, {}, error, location_result

    validation.update(
        {
            "status": "ready_for_redis",
            "request": request,
            "reference_datetime": time_result.get("reference_datetime"),
            "timezone": WEATHER_TIMEZONE,
            "expected_timezone_offset_seconds": EXPECTED_TIMEZONE_OFFSET_SECONDS,
        }
    )
    return "ready_for_redis", validation, request, {}, location_result


def _pipeline_read_redis(
    store: WeatherStore,
    request: dict[str, Any],
    validation_result: dict[str, Any],
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    location_id = str(request["location_id"])
    lookup_started_at = perf_counter()
    try:
        if request.get("request_type") == "current":
            response = store.get_current(location_id)
            tool_name = "get_current_weather"
        else:
            response = store.get_forecast(
                location_id,
                days=int(request["days"]),
                start_date=str(request["start_date"]),
            )
            tool_name = "get_weather_forecast"
    except Exception as exc:  # noqa: BLE001 - Redis boundary
        error_code = (
            "redis_invalid_json"
            if isinstance(exc, (json.JSONDecodeError, ValueError))
            else "redis_connection_error"
        )
        error = _pipeline_error(
            stage="redis",
            code=error_code,
            message=str(exc) or exc.__class__.__name__,
            retryable=True,
        )
        _log_pipeline_event(stage="redis", code=error["code"], status="error")
        return "error", None, error, None

    if not isinstance(response, dict):
        error = _pipeline_error(
            stage="redis",
            code="redis_invalid_response",
            message="Redis returned an invalid response object.",
            retryable=True,
        )
        return "error", None, error, None
    _print_redis_lookup(tool_name, location_id, response, lookup_started_at)
    if not response.get("ok"):
        if _pipeline_is_unavailable_response(response):
            return "unavailable", response, None, None
        raw_error = response.get("error", {})
        message = (
            str(raw_error.get("message", "Redis weather lookup failed."))
            if isinstance(raw_error, dict)
            else "Redis weather lookup failed."
        )
        error = _pipeline_error(
            stage="redis",
            code=_pipeline_redis_error_code(raw_error),
            message=message,
            retryable=True,
        )
        return "error", response, error, None

    data = response.get("data")
    if not isinstance(data, dict):
        error = _pipeline_error(
            stage="redis_response_validation",
            code="invalid_redis_weather_payload",
            message="Redis returned an invalid weather data payload.",
            retryable=False,
        )
        return "error", response, error, None
    timezone_offset = data.get("timezone_offset_seconds", data.get("timezone"))
    if timezone_offset != EXPECTED_TIMEZONE_OFFSET_SECONDS:
        error = _pipeline_error(
            stage="redis_response_validation",
            code="snapshot_timezone_mismatch",
            message=(
                "Weather snapshot timezone does not match Asia/Ho_Chi_Minh "
                f"({EXPECTED_TIMEZONE_OFFSET_SECONDS} seconds)."
            ),
            retryable=False,
        )
        return "error", response, error, None
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
            unavailable = {
                "ok": False,
                "error": {
                    "source": "weather_response_validation",
                    "code": "forecast_date_unavailable",
                    "message": "The active snapshot does not contain the exact requested forecast range.",
                    "status_code": None,
                },
                "details": {
                    "requested_dates": expected_dates,
                    "returned_dates": returned_dates,
                },
            }
            return "unavailable", unavailable, None, None

    tool_state: dict[str, Any] = {
        "weather_validation": validation_result,
        "resolved_locations": {
            location_id: validation_result.get("location_resolution", {})
        },
        "tool_calls": [{"name": tool_name, "cached": True}],
    }
    if request.get("request_type") == "current":
        tool_state["current_weather_data"] = data
    else:
        tool_state["forecast_weather_data"] = data
    return "completed", response, None, _build_weather_visualization_data(tool_state)


def _pipeline_is_unavailable_response(response: dict[str, Any]) -> bool:
    error = response.get("error", {})
    if not isinstance(error, dict):
        return False
    code = str(error.get("code", "")).casefold()
    if code in {
        "snapshot_unavailable",
        "snapshot_not_found",
        "location_not_in_snapshot",
        "forecast_date_unavailable",
        "requested_forecast_unavailable",
    }:
        return True
    return _is_unavailable_redis_response(response)


def _pipeline_redis_error_code(error: Any) -> str:
    if isinstance(error, dict):
        explicit_code = error.get("code")
        if isinstance(explicit_code, str) and explicit_code.strip():
            return explicit_code
        message = str(error.get("message", "")).casefold()
    else:
        message = str(error).casefold()
    if "not valid json" in message or "invalid json" in message:
        return "redis_invalid_json"
    if any(marker in message for marker in ("connection", "connect", "timed out", "timeout")):
        return "redis_connection_error"
    return "redis_lookup_failed"


def _pipeline_response_error_message(response: dict[str, Any] | None) -> str:
    if not isinstance(response, dict):
        return ""
    error = response.get("error")
    if isinstance(error, dict):
        return str(error.get("message", ""))
    return ""


def _pipeline_error(
    *,
    stage: str,
    code: str,
    message: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "stage": stage,
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if details:
        error["details"] = details
    return error


def _pipeline_client_usage(client: object) -> dict[str, Any]:
    usage = getattr(client, "last_usage", {})
    return dict(usage) if isinstance(usage, dict) else {}


def _pipeline_store_stats(store: WeatherStore | None) -> dict[str, int]:
    if store is None:
        return {}
    try:
        stats = store.stats()
    except Exception:  # noqa: BLE001 - diagnostics must not mask the result
        return {}
    return stats if isinstance(stats, dict) else {}


def _log_pipeline_event(*, stage: str, code: str, status: str | None = None, **details: Any) -> None:
    payload: dict[str, Any] = {"stage": stage, "code": code}
    if status is not None:
        payload["status"] = status
    payload.update(details)
    print(
        "[WEATHER_PIPELINE] "
        + json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True),
        flush=True,
    )


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
    error = response.get("error")
    error_details = ""
    if not response.get("ok") and isinstance(error, dict):
        error_details = (
            f" error_source={error.get('source')!r}"
            f" error_code={error.get('code')!r}"
            f" error_message={error.get('message')!r}"
        )
    print(
        "[WEATHER_REDIS] "
        f"tool={tool_name} "
        f"location_id={location_id!r} "
        f"ok={bool(response.get('ok'))} "
        f"snapshot_id={response.get('snapshot_id')!r} "
        f"lookup_ms={elapsed_ms:.2f}"
        f"{error_details}"
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
        if not isinstance(message, AIMessage) or not _ai_message_has_usage(message):
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


def _ai_message_has_usage(message: AIMessage) -> bool:
    usage_metadata = message.usage_metadata
    if isinstance(usage_metadata, dict) and bool(usage_metadata):
        return True
    response_metadata = message.response_metadata
    if not isinstance(response_metadata, dict):
        return False
    token_usage = response_metadata.get("token_usage")
    return isinstance(token_usage, dict) and bool(token_usage)


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
