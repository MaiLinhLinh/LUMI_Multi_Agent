"""Weather Agent implementation using a conditional two-stage LLM pipeline."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

from rag_manager.agents.weather_structured_schema import WeatherExtractionResponse
from rag_manager.config import Settings, load_settings
from rag_manager.llm.gemini_client import strip_thought_tags
from rag_manager.llm.prompts import (
    WEATHER_PIPELINE_EXTRACTION_SYSTEM_PROMPT,
    WEATHER_PIPELINE_RESPONSE_SYSTEM_PROMPT,
)
from rag_manager.services.weather_location_resolver import get_weather_location_resolver
from rag_manager.services.weather_redis import (
    WEATHER_REDIS_UNAVAILABLE_CODES,
    RedisWeatherStore,
    WeatherStore,
)
from rag_manager.services.weather_time_validator import (
    EXPECTED_TIMEZONE_OFFSET_SECONDS,
    MAX_FORECAST_DAYS,
    WEATHER_TIMEZONE,
    WeatherTimeValidator,
)
from rag_manager.state import AgentState

WEATHER_PROVIDER = "open-meteo"
_WEATHER_PIPELINE_STATUSES = {
    "needs_clarification",
    "unavailable",
    "error",
    "completed",
}
_WEATHER_PIPELINE_ERROR_ANSWER = (
    "Hệ thống chưa thể xử lý yêu cầu thời tiết lúc này. "
    "Bạn vui lòng thử lại sau."
)
_WEATHER_DATA_UNAVAILABLE_ANSWER = (
    "Hiện chưa có dữ liệu thời tiết phù hợp với địa điểm hoặc thời gian "
    "bạn yêu cầu. Bạn vui lòng thử lại sau."
)
_WEATHER_RESPONSE_FALLBACK = (
    "Dữ liệu thời tiết đã được cập nhật, nhưng hiện tôi chưa thể diễn giải "
    "thành câu trả lời. Bạn có thể xem đầy đủ thông tin ở bảng kết quả."
)
_WEATHER_HISTORY_LIMIT = 6


def run_weather_llm_pipeline(
    state: AgentState,
    *,
    store: WeatherStore | None = None,
    settings: Settings | None = None,
    client: object | None = None,
) -> AgentState:
    """Run the conditional weather pipeline without LangChain tool calling.

    Python owns extraction/schema classification, location/time validation,
    Redis access, and the final status. LLM2 asks for clarification when input
    is incomplete and turns verified Redis data into a user-facing response
    when the request completes.
    """

    started_at = perf_counter()
    settings = settings or load_settings()
    store = store or state.get("weather_store")
    query = state.get("query", "")
    query = query if isinstance(query, str) else ""
    history = state.get("history", [])
    history = history if isinstance(history, list) else []
    previous_weather_session = _normalized_weather_session(
        state.get("weather_session")
    )
    relevant_history = _weather_conversation_messages(
        query,
        history,
        weather_session=previous_weather_session,
    )
    last_resolved_request = (
        previous_weather_session.get("last_resolved_request", {})
        if previous_weather_session.get("active") is True
        else {}
    )
    if not isinstance(last_resolved_request, dict):
        last_resolved_request = {}

    usage: dict[str, Any] = {"call_1": {}}
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
            if not hasattr(pipeline_client, "chat_structured_json"):
                raise TypeError(
                    "Weather pipeline client must provide chat_structured_json()."
                )
            extraction = pipeline_client.chat_structured_json(
                WEATHER_PIPELINE_EXTRACTION_SYSTEM_PROMPT,
                _pipeline_extraction_message(
                    query,
                    relevant_history,
                    last_resolved_request,
                ),
                response_schema=WeatherExtractionResponse,
            )
            usage["call_1"] = _pipeline_client_usage(pipeline_client)
            _log_pipeline_event(
                stage="llm1_extraction",
                code="llm1_result",
                status="received",
                result=extraction,
            )
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
                                code="redis_store_exception",
                                message=str(exc) or exc.__class__.__name__,
                                retryable=True,
                                details={"exception_type": exc.__class__.__name__},
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

    normalized_status = status if status in _WEATHER_PIPELINE_STATUSES else "error"
    resolved_request = _resolved_weather_request(extraction)
    final_answer = ""
    response_mode = (
        "clarification"
        if normalized_status == "needs_clarification"
        else "weather_response"
        if normalized_status == "completed"
        else ""
    )
    if response_mode and pipeline_client is not None:
        try:
            if not hasattr(pipeline_client, "chat_text"):
                raise TypeError("Weather pipeline client must provide chat_text().")
            final_answer = pipeline_client.chat_text(
                WEATHER_PIPELINE_RESPONSE_SYSTEM_PROMPT,
                _pipeline_response_message(
                    query=query,
                    relevant_history=relevant_history,
                    extraction=extraction,
                    response_mode=response_mode,
                    validation_result=validation_result,
                    resolved_request=resolved_request,
                    weather_data=weather_data,
                ),
            )
            usage["call_2"] = _pipeline_client_usage(pipeline_client)
            if not isinstance(final_answer, str) or not final_answer.strip():
                raise ValueError("LLM2 returned an empty response.")
            final_answer = strip_thought_tags(final_answer)
            if not final_answer:
                raise ValueError("LLM2 returned an empty response after sanitization.")
        except Exception as exc:  # noqa: BLE001 - external LLM boundary
            if normalized_status == "completed":
                status = "completed"
                final_answer = _WEATHER_RESPONSE_FALLBACK
            else:
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
                    "pre_llm2_status": normalized_status,
                    "pre_llm2_error": weather_error,
                },
            )
            _log_pipeline_event(
                stage="llm2_response",
                code=weather_error["code"],
                pre_llm2_status=normalized_status,
                final_status=status,
                message=weather_error["message"],
            )
    elif response_mode:
        if normalized_status == "completed":
            status = "completed"
            final_answer = _WEATHER_RESPONSE_FALLBACK
        else:
            status = "error"
            final_answer = _WEATHER_PIPELINE_ERROR_ANSWER
        weather_error = _pipeline_error(
            stage="llm2_response",
            code="llm2_api_error",
            message="Weather pipeline client is unavailable.",
            retryable=True,
            details={
                "pre_llm2_status": normalized_status,
                "pre_llm2_error": weather_error,
            },
        )
        _log_pipeline_event(
            stage="llm2_response",
            code="llm2_api_error",
            pre_llm2_status=normalized_status,
            final_status=status,
        )
    elif normalized_status == "unavailable":
        final_answer = _WEATHER_DATA_UNAVAILABLE_ANSWER
    elif normalized_status == "error":
        final_answer = _WEATHER_PIPELINE_ERROR_ANSWER

    update: AgentState = {
        "weather_status": status,
        "weather_answer": final_answer,
        "final_response": final_answer,
        "cache_stats": {"weather": _pipeline_store_stats(store)},
        "timings": {"weather": _elapsed_since(started_at)},
        "llm_usage": {"weather": usage},
        "weather_session": _next_weather_session(
            previous_weather_session,
            status=status,
            extraction=extraction,
            resolved_request=resolved_request,
            canonical_request=canonical_request,
            query=query,
        ),
    }
    if status == "completed" and isinstance(weather_data, dict):
        update["weather_data"] = weather_data
    if weather_error:
        update["weather_error"] = weather_error
    return update


def _pipeline_extraction_message(
    query: str,
    relevant_history: list[dict[str, str]],
    last_resolved_request: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "query": query,
            "relevant_history": relevant_history,
            "last_resolved_request": last_resolved_request,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _pipeline_response_message(
    *,
    query: str,
    relevant_history: list[dict[str, str]],
    extraction: dict[str, Any],
    response_mode: str,
    validation_result: dict[str, Any],
    resolved_request: dict[str, Any],
    weather_data: dict[str, Any] | None,
) -> str:
    if response_mode == "clarification":
        payload = {
            "query": query,
            "relevant_history": relevant_history,
            "response_mode": response_mode,
            "extraction": extraction,
            "clarification_context": _pipeline_clarification_context(
                validation_result,
            ),
        }
    elif response_mode == "weather_response":
        payload = {
            "query": query,
            "relevant_history": relevant_history,
            "response_mode": response_mode,
            "resolved_request": resolved_request,
            "weather_facts": _weather_facts_for_llm(weather_data),
        }
    else:
        raise ValueError(f"Unsupported weather response mode: {response_mode!r}")

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )


def _resolved_weather_request(
    extraction: dict[str, Any],
) -> dict[str, Any]:
    """Return the complete LLM1-resolved slots accepted for this turn."""

    return {
        field: extraction.get(field)
        for field in (
            "location_text",
            "date_text",
            "time_of_day_text",
            "normalized_time",
            "request_type_candidate",
        )
    }


def _normalized_weather_session(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    session = dict(value)
    session["active"] = session.get("active") is True
    last_resolved = session.get("last_resolved_request")
    session["last_resolved_request"] = (
        dict(last_resolved) if isinstance(last_resolved, dict) else {}
    )
    return session


def _next_weather_session(
    previous: dict[str, Any],
    *,
    status: str,
    extraction: dict[str, Any],
    resolved_request: dict[str, Any],
    canonical_request: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    workflow_id = previous.get("workflow_id")
    if (
        previous.get("active") is not True
        or not isinstance(workflow_id, str)
        or not workflow_id.strip()
    ):
        workflow_id = f"weather_{uuid4().hex}"

    last_resolved = previous.get("last_resolved_request", {})
    last_canonical = previous.get("last_canonical_request", {})
    if status == "completed":
        last_resolved = resolved_request
        last_canonical = canonical_request

    return {
        "schema_version": "weather.session.v1",
        "workflow_id": workflow_id,
        "active": True,
        "last_status": status,
        "last_query": query,
        "last_resolved_request": (
            dict(last_resolved) if isinstance(last_resolved, dict) else {}
        ),
        "last_canonical_request": (
            dict(last_canonical) if isinstance(last_canonical, dict) else {}
        ),
        "pending_extraction": (
            dict(extraction) if status == "needs_clarification" else {}
        ),
    }


def _weather_facts_for_llm(
    weather_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Flatten rendered Weather data into the minimum factual LLM2 payload."""

    if not isinstance(weather_data, dict):
        return None
    raw_data = weather_data.get("data")
    raw_data = raw_data if isinstance(raw_data, dict) else {}
    location = raw_data.get("location")
    location = location if isinstance(location, dict) else {}
    place = _optional_text(location.get("name")) or _optional_text(
        weather_data.get("location")
    )
    forecast = raw_data.get("forecast")
    if isinstance(forecast, dict):
        selection = forecast.get("hourly_selection")
        if isinstance(selection, dict):
            return _hourly_weather_facts(place, forecast, selection)
        return _daily_weather_facts(place, forecast)

    current = raw_data.get("current")
    if isinstance(current, dict):
        return _current_weather_facts(place, current)
    return None


def _current_weather_facts(
    place: str | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    temperature = _dict_value(current.get("temperature"))
    wind = _dict_value(current.get("wind"))
    return _without_empty_values(
        {
            "kind": "current",
            "place": place,
            "observed_at": current.get("observed_at_local"),
            "condition": _condition_description(current.get("condition")),
            "temp_c": temperature.get("current_celsius"),
            "feels_c": temperature.get("feels_like_celsius"),
            "humidity_pct": current.get("humidity_percent"),
            "pressure_hpa": current.get("pressure_hpa"),
            "wind_ms": wind.get("speed_mps"),
            "wind_deg": wind.get("degrees", wind.get("direction_degrees")),
            "cloud_pct": current.get("cloudiness_percent"),
        }
    )


def _hourly_weather_facts(
    place: str | None,
    forecast: dict[str, Any],
    selection: dict[str, Any],
) -> dict[str, Any]:
    raw_days = forecast.get("days")
    days = raw_days if isinstance(raw_days, list) else []
    day = next((item for item in days if isinstance(item, dict)), {})
    raw_intervals = day.get("intervals")
    intervals = raw_intervals if isinstance(raw_intervals, list) else []
    interval = next((item for item in intervals if isinstance(item, dict)), {})
    return _without_empty_values(
        {
            "kind": "hourly_forecast",
            "place": place,
            "date": day.get("date"),
            "requested_time": selection.get("requested_time_of_day"),
            "matched_interval_start": selection.get(
                "matched_interval_start_time"
            ),
            "matched_interval_end": _matched_interval_end(selection),
            "resolution_min": selection.get("resolution_minutes"),
            "forecast_at": interval.get("forecast_at_local"),
            "condition": _condition_description(interval.get("condition")),
            "temp_c": interval.get("temperature_celsius"),
            "feels_c": interval.get("feels_like_celsius"),
            "humidity_pct": interval.get("humidity_percent"),
            "rain_pct": _probability_percent(interval.get("rain_probability")),
            "rain_mm": interval.get("rain_1h_mm"),
            "pressure_hpa": interval.get("pressure_hpa"),
            "wind_ms": interval.get("wind_speed_mps"),
            "wind_deg": interval.get("wind_degrees"),
            "cloud_pct": interval.get("cloudiness_percent"),
        }
    )


def _daily_weather_facts(
    place: str | None,
    forecast: dict[str, Any],
) -> dict[str, Any]:
    raw_days = forecast.get("days")
    raw_days = raw_days if isinstance(raw_days, list) else []
    days: list[dict[str, Any]] = []
    for raw_day in raw_days:
        if not isinstance(raw_day, dict):
            continue
        temperature = _dict_value(raw_day.get("temperature"))
        days.append(
            _without_empty_values(
                {
                    "date": raw_day.get("date"),
                    "condition": _condition_description(
                        raw_day.get("condition")
                    ),
                    "conditions": raw_day.get("common_conditions"),
                    "min_c": temperature.get("min_celsius"),
                    "max_c": temperature.get("max_celsius"),
                    "max_feels_c": raw_day.get(
                        "temperature_feels_like_celsius"
                    ),
                    "rain_max_pct": _probability_percent(
                        raw_day.get("max_rain_probability")
                    ),
                    "rain_total_mm": raw_day.get("total_rain_mm"),
                    "humidity_avg_pct": raw_day.get("humidity_percent"),
                    "pressure_avg_hpa": raw_day.get("pressure_hpa"),
                    "wind_avg_ms": raw_day.get("wind_speed_mps"),
                    "partial_day": raw_day.get("is_partial_day"),
                }
            )
        )
    return _without_empty_values(
        {
            "kind": "daily_forecast",
            "place": place,
            "days": days,
        }
    )


def _matched_interval_end(selection: dict[str, Any]) -> str | None:
    interval_start = _optional_text(selection.get("matched_interval_start_time"))
    resolution = selection.get("resolution_minutes")
    if interval_start is None:
        return None
    if not isinstance(resolution, int) or isinstance(resolution, bool):
        return None
    try:
        parsed = datetime.strptime(interval_start, "%H:%M")
    except ValueError:
        return None
    return (parsed + timedelta(minutes=resolution)).strftime("%H:%M")


def _condition_description(value: Any) -> str | None:
    condition = _dict_value(value)
    return _optional_text(condition.get("description")) or _optional_text(
        condition.get("main")
    )


def _probability_percent(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    percentage = value * 100 if 0 <= value <= 1 else value
    rounded = round(percentage, 1)
    return int(rounded) if rounded == int(rounded) else rounded


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _without_empty_values(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {}
    }


def _pipeline_clarification_context(
    validation_result: dict[str, Any],
) -> dict[str, Any]:
    if validation_result.get("status") != "needs_clarification":
        return {}
    details = validation_result.get("details")
    details = details if isinstance(details, dict) else {}
    missing_fields = _clarification_missing_fields(details.get("missing_fields"))
    field = _clarification_field(
        details.get("field"),
        stage=validation_result.get("stage"),
        missing_fields=missing_fields,
    )
    context: dict[str, Any] = {
        "field": field,
        "reason_code": str(
            validation_result.get("code") or "needs_clarification"
        ),
    }
    requested_text = details.get("requested_text")
    if isinstance(requested_text, str) and requested_text.strip():
        context["requested_text"] = requested_text.strip()
    if missing_fields:
        context["missing_fields"] = missing_fields
    candidates = details.get("candidates")
    if isinstance(candidates, list) and candidates:
        context["candidates"] = [
            dict(candidate)
            for candidate in candidates
            if isinstance(candidate, dict)
        ]

    conflict_keys = (
        "normalized_time",
        "time_supported_by_text",
        "provided_date",
        "provided_weekday",
        "actual_weekday",
        "matching_weekday_date",
        "required_format",
    )
    conflict_details = {
        key: details[key]
        for key in conflict_keys
        if key in details and details[key] is not None
    }
    if conflict_details:
        context["conflict_details"] = conflict_details
    return context


def _clarification_missing_fields(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        field.strip()
        for field in value
        if isinstance(field, str) and field.strip()
    ]


def _clarification_field(
    value: Any,
    *,
    stage: Any,
    missing_fields: list[str],
) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if missing_fields:
        return {
            "location": "location_text",
            "date": "date_text",
            "time": "time_of_day_text",
        }.get(missing_fields[0], missing_fields[0])
    if stage == "location":
        return "location_text"
    if stage == "time":
        return "date_text"
    return None


def _pipeline_missing_extraction_fields(extraction: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    location_text = extraction.get("location_text")
    if not isinstance(location_text, str) or not location_text.strip():
        missing.append("location")

    date_text = extraction.get("date_text")
    time_of_day_text = extraction.get("time_of_day_text")
    request_type = extraction.get("request_type_candidate")
    has_date = isinstance(date_text, str) and bool(date_text.strip())
    has_time_of_day = isinstance(time_of_day_text, str) and bool(
        time_of_day_text.strip()
    )
    if not has_date and (request_type != "current" or has_time_of_day):
        missing.append("date")
    return missing


def _pipeline_validate_request(
    extraction: dict[str, Any],
    *,
    settings: Settings,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    location_text = str(extraction.get("location_text", "")).strip()
    date_text = _optional_text(extraction.get("date_text"))
    time_of_day_text = _optional_text(extraction.get("time_of_day_text"))
    normalized_time = _optional_text(extraction.get("normalized_time"))
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
            date_text,
            time_of_day_text=time_of_day_text,
            normalized_time=normalized_time,
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
        for field in (
            "time_of_day_text",
            "normalized_time",
            "requested_time_of_day",
            "forecast_interval_start_time",
            "requested_hour",
            "requested_minute",
            "forecast_interval_minutes",
        ):
            if field in time_result:
                request[field] = time_result[field]
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
        error = _pipeline_error(
            stage="redis",
            code="redis_store_exception",
            message=str(exc) or exc.__class__.__name__,
            retryable=True,
            details={"exception_type": exc.__class__.__name__},
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
        raw_error = response.get("error", {})
        explicit_code = raw_error.get("code") if isinstance(raw_error, dict) else None
        if not isinstance(explicit_code, str) or not explicit_code.strip():
            error = _pipeline_error(
                stage="redis",
                code="redis_error_contract_violation",
                message="Redis weather error response is missing a machine-readable code.",
                retryable=False,
                details={"redis_error": raw_error},
            )
            return "error", response, error, None
        error_code = explicit_code.strip()
        if error_code in WEATHER_REDIS_UNAVAILABLE_CODES:
            return "unavailable", response, None, None
        message = (
            str(raw_error.get("message", "Redis weather lookup failed."))
            if isinstance(raw_error, dict)
            else "Redis weather lookup failed."
        )
        error = _pipeline_error(
            stage="redis",
            code=error_code,
            message=message,
            retryable=(
                bool(raw_error.get("retryable"))
                if isinstance(raw_error, dict)
                else False
            ),
            details=(
                raw_error.get("details")
                if isinstance(raw_error, dict)
                and isinstance(raw_error.get("details"), dict)
                else None
            ),
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

        if "requested_time_of_day" in request:
            selected_data, selection_issue = _select_hourly_forecast(
                data,
                request=request,
            )
            if selection_issue is not None:
                return "unavailable", selection_issue, None, None
            data = selected_data
            response = {**response, "data": data}

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


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


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


def _valid_canonical_forecast_time(start_date: Any, days: Any) -> bool:
    if not isinstance(start_date, str) or not isinstance(days, int):
        return False
    try:
        date.fromisoformat(start_date)
    except ValueError:
        return False
    return 1 <= days <= MAX_FORECAST_DAYS


def _expected_forecast_dates(start_date: str, days: int) -> list[str]:
    first_date = date.fromisoformat(start_date)
    return [
        (first_date + timedelta(days=offset)).isoformat()
        for offset in range(days)
    ]


def _select_hourly_forecast(
    data: dict[str, Any],
    *,
    request: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    requested_hour = request.get("requested_hour")
    requested_minute = request.get("requested_minute")
    requested_time = request.get("requested_time_of_day")
    interval_start_time = request.get("forecast_interval_start_time")
    if (
        isinstance(requested_hour, bool)
        or not isinstance(requested_hour, int)
        or not 0 <= requested_hour <= 23
        or isinstance(requested_minute, bool)
        or not isinstance(requested_minute, int)
        or not 0 <= requested_minute <= 59
        or not isinstance(requested_time, str)
        or not isinstance(interval_start_time, str)
    ):
        return {}, _hourly_forecast_issue(
            "invalid_hourly_selection",
            "The validated hourly forecast selection is invalid.",
            request=request,
        )

    raw_days = data.get("days")
    if not isinstance(raw_days, list):
        return {}, _hourly_forecast_issue(
            "forecast_time_unavailable",
            "The active snapshot does not contain hourly forecast data.",
            request=request,
        )

    selected_days: list[dict[str, Any]] = []
    matched_times: list[str] = []
    unavailable_dates: list[str] = []
    for raw_day in raw_days:
        if not isinstance(raw_day, dict):
            continue
        day_text = raw_day.get("date")
        intervals = raw_day.get("intervals")
        matches: list[dict[str, Any]] = []
        if isinstance(intervals, list):
            for interval in intervals:
                if not isinstance(interval, dict):
                    continue
                forecast_at_local = interval.get("forecast_at_local")
                if not isinstance(forecast_at_local, str):
                    continue
                try:
                    parsed = datetime.fromisoformat(forecast_at_local)
                except ValueError:
                    continue
                if parsed.hour == requested_hour:
                    matches.append(interval)

        if not matches:
            unavailable_dates.append(str(day_text or ""))
            continue

        first_match_time = str(matches[0]["forecast_at_local"])
        last_match_time = str(matches[-1]["forecast_at_local"])
        matched_times.extend(
            str(interval["forecast_at_local"])
            for interval in matches
            if isinstance(interval.get("forecast_at_local"), str)
        )
        selected_days.append(
            {
                "date": day_text,
                "day_grouping": raw_day.get(
                    "day_grouping", data.get("day_grouping")
                ),
                "interval_count": len(matches),
                "coverage_start_local": first_match_time,
                "coverage_end_local": last_match_time,
                "is_partial_day": True,
                "requested_time_of_day": requested_time,
                "matched_interval_start_time": interval_start_time,
                "intervals": matches,
            }
        )

    if unavailable_dates or len(selected_days) != len(raw_days):
        return {}, _hourly_forecast_issue(
            "forecast_time_unavailable",
            "The active snapshot does not contain the requested hourly interval.",
            request=request,
            unavailable_dates=unavailable_dates,
        )

    selected = dict(data)
    selected["days"] = selected_days
    selected["interval_count"] = len(matched_times)
    selected["coverage_start_local"] = matched_times[0] if matched_times else None
    selected["coverage_end_local"] = matched_times[-1] if matched_times else None
    selected["hourly_selection"] = {
        "requested_time_of_day": requested_time,
        "matched_interval_start_time": interval_start_time,
        "resolution_minutes": request.get("forecast_interval_minutes", 60),
        "minute_offset_within_interval": requested_minute,
        "matched_times_local": matched_times,
    }
    return selected, None


def _hourly_forecast_issue(
    code: str,
    message: str,
    *,
    request: dict[str, Any],
    unavailable_dates: list[str] | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "requested_time_of_day": request.get("requested_time_of_day"),
        "forecast_interval_start_time": request.get(
            "forecast_interval_start_time"
        ),
    }
    if unavailable_dates:
        details["unavailable_dates"] = unavailable_dates
    return {
        "ok": False,
        "error": {
            "source": "weather_response_validation",
            "code": code,
            "message": message,
            "retryable": True,
            "status_code": None,
            "details": details,
        },
    }


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


def _build_weather_visualization_data(tool_state: dict[str, Any]) -> dict[str, Any]:
    current = _dict_or_none(tool_state.get("current_weather_data"))
    forecast = _dict_or_none(tool_state.get("forecast_weather_data"))
    hourly_current, presentation = _hourly_forecast_presentation(forecast)
    display_current = current or hourly_current
    errors = [error for error in tool_state.get("errors", []) if isinstance(error, dict)]
    error_records = [
        record for record in tool_state.get("error_records", []) if isinstance(record, dict)
    ]

    data = {
        "location": _weather_location(
            current=display_current,
            forecast=forecast,
            error_records=error_records,
        ),
        "current": _current_payload(display_current),
        "forecast": _forecast_payload(forecast),
        "presentation": presentation,
    }
    envelope = {
        "domain": "weather",
        "schema_version": _weather_schema_version(
            current=display_current,
            forecast=forecast,
            errors=errors,
        ),
        "data_type": _weather_data_type(
            current=display_current,
            forecast=forecast,
            errors=errors,
        ),
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


def _hourly_forecast_presentation(
    forecast: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not forecast:
        return None, None
    selection = forecast.get("hourly_selection")
    days = forecast.get("days")
    if not isinstance(selection, dict) or not isinstance(days, list) or len(days) != 1:
        return None, None
    day_payload = days[0]
    if not isinstance(day_payload, dict):
        return None, None
    intervals = day_payload.get("intervals")
    if not isinstance(intervals, list) or len(intervals) != 1:
        return None, None
    interval = intervals[0]
    if not isinstance(interval, dict):
        return None, None

    requested_time = _optional_text(selection.get("requested_time_of_day"))
    interval_start = _optional_text(selection.get("matched_interval_start_time"))
    day_text = _optional_text(day_payload.get("date"))
    if requested_time is None or interval_start is None or day_text is None:
        return None, None

    current_like = {
        "location_id": forecast.get("location_id"),
        "location": forecast.get("location"),
        "country": forecast.get("country"),
        "timezone": forecast.get("timezone"),
        "timezone_offset_seconds": forecast.get("timezone_offset_seconds"),
        "timestamp": interval.get("timestamp"),
        "observed_at_utc": interval.get("forecast_at_utc"),
        "observed_at_local": interval.get("forecast_at_local"),
        "condition": interval.get("condition", {}),
        "temperature": {
            "current_celsius": interval.get("temperature_celsius"),
            "feels_like_celsius": interval.get("feels_like_celsius"),
            "min_celsius": None,
            "max_celsius": None,
        },
        "humidity_percent": interval.get("humidity_percent"),
        "pressure_hpa": interval.get("pressure_hpa"),
        "wind": {
            "speed_mps": interval.get("wind_speed_mps"),
            "degrees": interval.get("wind_degrees"),
        },
        "cloudiness_percent": interval.get("cloudiness_percent"),
        "data_origin": "forecast_interval",
    }
    formatted_date = day_text
    try:
        formatted_date = date.fromisoformat(day_text).strftime("%d/%m/%Y")
    except ValueError:
        pass
    interval_end = _hourly_interval_end(interval_start)
    interval_notice = ""
    if requested_time != interval_start:
        interval_notice = (
            f"Dữ liệu theo khung giờ {interval_start}–{interval_end}."
        )
    presentation = {
        "mode": "hourly_forecast",
        "time_label": f"Dự báo lúc {requested_time} ngày {formatted_date}",
        "requested_time_of_day": requested_time,
        "matched_interval_start_time": interval_start,
        "matched_interval_end_time": interval_end,
        "interval_notice": interval_notice,
        "source_granularity": forecast.get("source_granularity"),
    }
    return current_like, presentation


def _hourly_interval_end(interval_start: str) -> str:
    try:
        parsed = datetime.strptime(interval_start, "%H:%M")
    except ValueError:
        return interval_start
    return (parsed + timedelta(hours=1)).strftime("%H:%M")


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
        "data_origin": current.get("data_origin"),
    }


def _forecast_payload(forecast: dict[str, Any] | None) -> dict[str, Any] | None:
    if not forecast:
        return None
    return {
        "requested_days": forecast.get("requested_days"),
        "source_granularity": forecast.get("source_granularity"),
        "day_grouping": forecast.get("day_grouping"),
        "interval_time_basis": forecast.get("interval_time_basis"),
        "hourly_selection": forecast.get("hourly_selection"),
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


def _weather_conversation_messages(
    query: str,
    history: list[dict[str, Any]],
    *,
    weather_session: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    session = weather_session if isinstance(weather_session, dict) else {}
    active = session.get("active") is True
    workflow_id = session.get("workflow_id")
    workflow_id = workflow_id if isinstance(workflow_id, str) else ""
    has_session_boundary = bool(workflow_id) or "active" in session

    valid_history: list[dict[str, Any]] = []
    for message in history:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        if content.strip():
            valid_history.append(message)

    # The current query can already be present in app history. It is supplied
    # separately to both LLM stages, so exclude it from relevant_history.
    if (
        valid_history
        and valid_history[-1].get("role") == "user"
        and valid_history[-1].get("content") == query
    ):
        valid_history = valid_history[:-1]

    selected: list[dict[str, str]] = []
    tagged_history = any(
        isinstance(message.get("domain"), str) for message in valid_history
    )
    if active and tagged_history:
        for message in reversed(valid_history):
            domain = message.get("domain")
            message_workflow_id = message.get("workflow_id")
            if domain != "weather":
                break
            if workflow_id and message_workflow_id not in {None, "", workflow_id}:
                break
            selected.append(
                {"role": str(message["role"]), "content": str(message["content"])}
            )
            if len(selected) >= _WEATHER_HISTORY_LIMIT:
                break
        selected.reverse()
    elif not tagged_history and (active or not has_session_boundary):
        # Compatibility for terminal/tests that predate domain metadata.
        selected = [
            {"role": str(message["role"]), "content": str(message["content"])}
            for message in valid_history[-_WEATHER_HISTORY_LIMIT:]
        ]

    return selected[-_WEATHER_HISTORY_LIMIT:]


def _elapsed_since(started_at: float) -> float:
    return perf_counter() - started_at
