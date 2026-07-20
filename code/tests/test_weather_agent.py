import json

import pytest

from rag_manager.agents import weather as weather_agent
from rag_manager.agents.weather_structured_schema import WeatherExtractionResponse
from rag_manager.config import Settings


class StubWeatherStore:
    def __init__(self, *, current=None, forecast=None) -> None:
        self.current = current
        self.forecast = forecast
        self.current_calls: list[str] = []
        self.forecast_calls: list[tuple[str, int, str | None]] = []
        self._hits = 0
        self._misses = 0

    def get_current(self, location: str) -> dict:
        self.current_calls.append(location)
        response = self.current or _redis_unavailable()
        self._record(response)
        return response

    def get_forecast(
        self,
        location: str,
        *,
        days: int,
        start_date: str | None = None,
    ) -> dict:
        self.forecast_calls.append((location, days, start_date))
        response = self.forecast or _redis_unavailable()
        self._record(response)
        if not response.get("ok"):
            return response
        data = dict(response["data"])
        data["requested_days"] = days
        stored_days = data.get("days", [])
        if start_date:
            stored_days = [day for day in stored_days if day.get("date", "") >= start_date]
            data["requested_start_date"] = start_date
        data["days"] = stored_days[:days]
        return {**response, "data": data}

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "errors": 0}

    def _record(self, response: dict) -> None:
        if response.get("ok"):
            self._hits += 1
        else:
            self._misses += 1


class StubLocationResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, location_text: str) -> dict:
        self.calls.append(location_text)
        if not location_text.strip():
            return {
                "ok": False,
                "error": {
                    "source": "weather_location_resolver",
                    "code": "missing_location",
                    "message": "missing",
                    "status_code": None,
                },
            }
        return {
            "ok": True,
            "location_id": "ha_noi",
            "canonical_name": "Hà Nội",
            "requested_text": location_text,
            "matched_name": "Hà Nội",
            "match_type": "exact",
            "confidence": 1.0,
        }


def _redis_unavailable(
    message: str = "No active weather snapshot is available in Redis.",
) -> dict:
    return {
        "ok": False,
        "error": {
            "source": "weather_redis",
            "code": "snapshot_unavailable",
            "message": message,
            "retryable": True,
            "status_code": None,
            "details": {},
        },
    }


def _settings(*, openweather_api_key: str = "weather-key") -> Settings:
    return Settings(
        gemini_api_key="gemini-key",
        gemini_base_url="",
        gemini_model="gemma-4-26b-a4b-it",
        openweather_api_key=openweather_api_key,
        gnews_api_key="",
        weather_cache_ttl_seconds=3600,
        news_cache_ttl_seconds=900,
        wiki_cache_ttl_seconds=None,
        request_timeout_seconds=8,
        debug_routing=False,
    )


class StubPipelineClient:
    def __init__(self, extraction: dict, answer: str = "weather answer") -> None:
        self.extraction = extraction
        self.answer = answer
        self.calls: list[tuple[str, str]] = []
        self.last_usage: dict = {}
        self.extraction_response_schema: type | None = None

    def chat_structured_json(
        self,
        system_prompt: str,
        user_message: str,
        *,
        response_schema: type,
        temperature: float = 0.0,
    ) -> dict:
        self.calls.append(("extraction", user_message))
        self.extraction_response_schema = response_schema
        self.last_usage = {
            "model": "weather-model",
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
        }
        return response_schema.model_validate(self.extraction).model_dump()

    def chat_text(self, system_prompt: str, user_message: str) -> str:
        self.calls.append(("response", user_message))
        self.last_usage = {
            "model": "weather-model",
            "prompt_tokens": 30,
            "completion_tokens": 10,
            "total_tokens": 40,
        }
        return self.answer


def test_weather_pipeline_completes_current_request(monkeypatch) -> None:
    resolver = StubLocationResolver()
    monkeypatch.setattr(
        weather_agent,
        "get_weather_location_resolver",
        lambda _path=None: resolver,
    )
    store = StubWeatherStore(
        current={
            "ok": True,
            "cached": True,
            "snapshot": {
                "snapshot_id": "snapshot-1",
                "schema_version": "weather.snapshot.v4",
                "generated_at_utc": "2026-07-16T03:00:00+00:00",
                "age_seconds": 60,
                "provider": "openweathermap",
            },
            "data": {
                "location": "Hà Nội",
                "country": "VN",
                "timezone_offset_seconds": 25200,
                "temperature": {"current_celsius": 30},
                "condition": {"description": "có mây"},
            },
        }
    )
    client = StubPipelineClient(
        {
            "location_text": "Hà Nội",
            "date_text": None,
            "time_of_day_text": None,
            "normalized_time": None,
            "request_type_candidate": "current",
        },
        answer="Hà Nội hiện có mây, nhiệt độ 30°C.",
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "Thời tiết Hà Nội hiện tại", "history": []},
        store=store,
        settings=_settings(),
        client=client,
    )

    assert result["weather_status"] == "completed"
    assert result["weather_answer"] == client.answer
    assert result["final_response"] == client.answer
    assert result["weather_data"]["data_type"] == "current"
    assert store.current_calls == ["ha_noi"]
    assert resolver.calls == ["Hà Nội"]
    assert [call[0] for call in client.calls] == ["extraction", "response"]
    assert client.extraction_response_schema is WeatherExtractionResponse
    assert result["llm_usage"]["weather"]["call_1"]["prompt_tokens"] == 20
    assert result["llm_usage"]["weather"]["call_2"]["prompt_tokens"] == 30
    response_context = json.loads(client.calls[1][1])
    assert set(response_context) == {
        "query",
        "relevant_history",
        "response_mode",
        "resolved_request",
        "weather_facts",
    }
    assert response_context["response_mode"] == "weather_response"
    assert response_context["weather_facts"] == {
        "kind": "current",
        "place": "Hà Nội",
        "condition": "có mây",
        "temp_c": 30,
    }
    assert result["weather_session"]["last_resolved_request"][
        "location_text"
    ] == client.extraction["location_text"]


def test_weather_pipeline_missing_location_never_reads_redis() -> None:
    store = StubWeatherStore()
    client = StubPipelineClient(
        {
            "location_text": None,
            "date_text": "ngày mai",
            "time_of_day_text": None,
            "normalized_time": None,
            "request_type_candidate": "forecast",
        },
        answer="Bạn muốn xem thời tiết ở địa điểm nào?",
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "Ngày mai thời tiết thế nào?", "history": []},
        store=store,
        settings=_settings(),
        client=client,
    )

    assert result["weather_status"] == "needs_clarification"
    assert result["weather_answer"] == "Bạn muốn xem thời tiết ở địa điểm nào?"
    assert store.current_calls == []
    assert store.forecast_calls == []
    assert [call[0] for call in client.calls] == ["extraction", "response"]
    assert result["llm_usage"]["weather"]["call_2"]["prompt_tokens"] == 30
    assert "weather_data" not in result
    response_context = json.loads(client.calls[1][1])
    assert set(response_context) == {
        "query",
        "relevant_history",
        "response_mode",
        "extraction",
        "clarification_context",
    }
    assert response_context["clarification_context"] == {
        "field": "location_text",
        "reason_code": "missing_weather_requirements",
        "missing_fields": ["location"],
    }


def test_weather_pipeline_tells_llm2_the_exact_invalid_date_field(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        weather_agent,
        "get_weather_location_resolver",
        lambda _path=None: StubLocationResolver(),
    )
    client = StubPipelineClient(
        {
            "location_text": "Hà Nội",
            "date_text": "sáng mai",
            "time_of_day_text": "9h",
            "normalized_time": "09:00",
            "request_type_candidate": "forecast",
        },
        answer="Bạn muốn xem thời tiết vào ngày nào?",
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "9h", "history": []},
        store=StubWeatherStore(),
        settings=_settings(),
        client=client,
    )

    response_context = json.loads(client.calls[1][1])
    assert result["weather_status"] == "needs_clarification"
    assert response_context["clarification_context"] == {
        "field": "date_text",
        "reason_code": "unrecognized_date",
        "requested_text": "sáng mai",
    }


def test_weather_extraction_schema_has_exact_required_fields() -> None:
    schema = WeatherExtractionResponse.model_json_schema()
    expected_fields = {
        "location_text",
        "date_text",
        "time_of_day_text",
        "normalized_time",
        "request_type_candidate",
    }

    assert set(schema["properties"]) == expected_fields
    assert set(schema["required"]) == expected_fields


def test_weather_pipeline_unavailable_snapshot_is_not_treated_as_input_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        weather_agent,
        "get_weather_location_resolver",
        lambda _path=None: StubLocationResolver(),
    )
    store = StubWeatherStore()
    client = StubPipelineClient(
        {
            "location_text": "Hà Nội",
            "date_text": None,
            "time_of_day_text": None,
            "normalized_time": None,
            "request_type_candidate": "current",
        },
        answer="Dữ liệu thời tiết đã xác thực hiện chưa có trong bộ nhớ đệm.",
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "Thời tiết Hà Nội hiện tại", "history": []},
        store=store,
        settings=_settings(),
        client=client,
    )

    assert result["weather_status"] == "unavailable"
    assert result["weather_answer"].startswith("Hiện chưa có dữ liệu thời tiết")
    assert [call[0] for call in client.calls] == ["extraction"]
    assert store.current_calls == ["ha_noi"]
    assert "weather_error" not in result
    assert "weather_data" not in result


@pytest.mark.parametrize(
    "message",
    [
        "No active weather snapshot is available in Redis.",
        "Chưa có ảnh chụp thời tiết đang hoạt động trong Redis.",
    ],
)
def test_weather_status_uses_redis_error_code_not_message(
    monkeypatch,
    message: str,
) -> None:
    monkeypatch.setattr(
        weather_agent,
        "get_weather_location_resolver",
        lambda _path=None: StubLocationResolver(),
    )
    store = StubWeatherStore(current=_redis_unavailable(message))
    client = StubPipelineClient(
        {
            "location_text": "Hà Nội",
            "date_text": None,
            "time_of_day_text": None,
            "normalized_time": None,
            "request_type_candidate": "current",
        }
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "Thời tiết Hà Nội hiện tại", "history": []},
        store=store,
        settings=_settings(),
        client=client,
    )

    assert result["weather_status"] == "unavailable"


def test_weather_error_without_code_is_contract_violation(monkeypatch) -> None:
    monkeypatch.setattr(
        weather_agent,
        "get_weather_location_resolver",
        lambda _path=None: StubLocationResolver(),
    )
    store = StubWeatherStore(
        current={
            "ok": False,
            "error": {
                "source": "weather_redis",
                "message": "No active weather snapshot is available in Redis.",
            },
        }
    )
    client = StubPipelineClient(
        {
            "location_text": "Hà Nội",
            "date_text": None,
            "time_of_day_text": None,
            "normalized_time": None,
            "request_type_candidate": "current",
        }
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "Thời tiết Hà Nội hiện tại", "history": []},
        store=store,
        settings=_settings(),
        client=client,
    )

    assert result["weather_status"] == "error"
    assert result["weather_error"]["code"] == "redis_error_contract_violation"


def test_weather_store_exception_is_not_classified_from_message(monkeypatch) -> None:
    class RaisingStore(StubWeatherStore):
        def get_current(self, location: str) -> dict:
            raise ValueError("invalid JSON connection timeout")

    monkeypatch.setattr(
        weather_agent,
        "get_weather_location_resolver",
        lambda _path=None: StubLocationResolver(),
    )
    client = StubPipelineClient(
        {
            "location_text": "Hà Nội",
            "date_text": None,
            "time_of_day_text": None,
            "normalized_time": None,
            "request_type_candidate": "current",
        }
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "Thời tiết Hà Nội hiện tại", "history": []},
        store=RaisingStore(),
        settings=_settings(),
        client=client,
    )

    assert result["weather_status"] == "error"
    assert result["weather_error"]["code"] == "redis_store_exception"


def test_weather_pipeline_uses_python_forecast_request_unchanged(monkeypatch) -> None:
    class FixedForecastValidator:
        def validate(self, *_args, **_kwargs) -> dict:
            return {
                "status": "valid",
                "request_type": "forecast",
                "start_date": "2026-07-16",
                "days": 2,
                "reference_datetime": "2026-07-15T10:00:00+07:00",
            }

    monkeypatch.setattr(
        weather_agent,
        "get_weather_location_resolver",
        lambda _path=None: StubLocationResolver(),
    )
    monkeypatch.setattr(
        weather_agent,
        "WeatherTimeValidator",
        FixedForecastValidator,
    )
    store = StubWeatherStore(
        forecast={
            "ok": True,
            "cached": True,
            "data": {
                "location": "Hà Nội",
                "timezone_offset_seconds": 25200,
                "days": [
                    {"date": "2026-07-16"},
                    {"date": "2026-07-17"},
                ],
            },
        }
    )
    client = StubPipelineClient(
        {
            "location_text": "Hà Nội",
            "date_text": "hai ngày tới",
            "time_of_day_text": None,
            "normalized_time": None,
            "request_type_candidate": "forecast",
        }
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "Thời tiết Hà Nội hai ngày tới", "history": []},
        store=store,
        settings=_settings(),
        client=client,
    )

    assert result["weather_status"] == "completed"
    assert store.forecast_calls == [("ha_noi", 2, "2026-07-16")]
    assert result["weather_data"]["data"]["forecast"]["days"] == [
        {"date": "2026-07-16"},
        {"date": "2026-07-17"},
    ]


def test_weather_pipeline_selects_only_the_requested_hour(monkeypatch) -> None:
    class FixedHourlyValidator:
        def validate(self, *_args, **_kwargs) -> dict:
            return {
                "status": "valid",
                "request_type": "forecast",
                "start_date": "2026-07-16",
                "days": 1,
                "reference_datetime": "2026-07-16T08:00:00+07:00",
                "time_of_day_text": "lúc 9 giờ 30",
                "requested_time_of_day": "09:30",
                "forecast_interval_start_time": "09:00",
                "requested_hour": 9,
                "requested_minute": 30,
                "forecast_interval_minutes": 60,
            }

    monkeypatch.setattr(
        weather_agent,
        "get_weather_location_resolver",
        lambda _path=None: StubLocationResolver(),
    )
    monkeypatch.setattr(
        weather_agent,
        "WeatherTimeValidator",
        FixedHourlyValidator,
    )
    intervals = [
        {
            "forecast_at_local": "2026-07-16T09:00:00+07:00",
            "temperature_celsius": 29,
        },
        {
            "forecast_at_local": "2026-07-16T10:00:00+07:00",
            "temperature_celsius": 30,
        },
    ]
    store = StubWeatherStore(
        forecast={
            "ok": True,
            "cached": True,
            "data": {
                "location": "Hà Nội",
                "timezone_offset_seconds": 25200,
                "source_granularity": "1-hour forecast intervals",
                "day_grouping": "location_local_date",
                "interval_time_basis": "location_local_time",
                "days": [
                    {
                        "date": "2026-07-16",
                        "intervals": intervals,
                    }
                ],
            },
        }
    )
    client = StubPipelineClient(
        {
            "location_text": "Hà Nội",
            "date_text": "hôm nay",
            "time_of_day_text": "lúc 9 giờ 30",
            "normalized_time": "09:30",
            "request_type_candidate": "forecast",
        }
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "Thời tiết Hà Nội hôm nay lúc 9 giờ 30", "history": []},
        store=store,
        settings=_settings(),
        client=client,
    )

    assert result["weather_status"] == "completed"
    assert [call[0] for call in client.calls] == ["extraction", "response"]
    selected = result["weather_data"]["data"]["forecast"]
    assert selected["hourly_selection"]["requested_time_of_day"] == "09:30"
    assert selected["hourly_selection"]["matched_interval_start_time"] == "09:00"
    assert selected["days"][0]["intervals"] == [intervals[0]]
    assert result["weather_data"]["data"]["forecast"]["hourly_selection"] == (
        selected["hourly_selection"]
    )
    assert result["weather_data"]["schema_version"] == "weather.combined.v1"
    assert result["weather_data"]["data_type"] == "combined"
    assert result["weather_data"]["data"]["current"]["temperature"] == {
        "current_celsius": 29,
        "feels_like_celsius": None,
        "min_celsius": None,
        "max_celsius": None,
    }
    assert result["weather_data"]["data"]["presentation"] == {
        "mode": "hourly_forecast",
        "time_label": "Dự báo lúc 09:30 ngày 16/07/2026",
        "requested_time_of_day": "09:30",
        "matched_interval_start_time": "09:00",
        "matched_interval_end_time": "10:00",
        "interval_notice": "Dữ liệu theo khung giờ 09:00–10:00.",
        "source_granularity": "1-hour forecast intervals",
    }
    response_context = json.loads(client.calls[1][1])
    assert "weather_data" not in response_context
    assert response_context["weather_facts"] == {
        "kind": "hourly_forecast",
        "place": "Hà Nội",
        "date": "2026-07-16",
        "requested_time": "09:30",
        "matched_interval_start": "09:00",
        "matched_interval_end": "10:00",
        "resolution_min": 60,
        "forecast_at": "2026-07-16T09:00:00+07:00",
        "temp_c": 29,
    }


def test_weather_redis_error_log_includes_error_details(capsys) -> None:
    weather_agent._print_redis_lookup(
        "get_weather_forecast",
        "ha_noi",
        {
            "ok": False,
            "error": {
                "source": "weather_redis",
                "code": "redis_lookup_failed",
                "message": "Redis weather lookup failed: connection timed out",
            },
        },
        weather_agent.perf_counter(),
    )

    terminal_output = capsys.readouterr().out

    assert "[WEATHER_REDIS]" in terminal_output
    assert "ok=False" in terminal_output
    assert "error_source='weather_redis'" in terminal_output
    assert "error_code='redis_lookup_failed'" in terminal_output
    assert (
        "error_message='Redis weather lookup failed: connection timed out'"
        in terminal_output
    )


def test_weather_visualization_data_wraps_current_weather() -> None:
    envelope = weather_agent._build_weather_visualization_data(
        {
            "current_weather_data": {
                "location": "Ha Noi",
                "country": "VN",
                "timestamp": 1783670000,
                "timezone": 25200,
                "observed_at_utc": "2026-07-10T07:53:20+00:00",
                "observed_at_local": "2026-07-10T14:53:20+07:00",
                "condition": {"main": "Clouds", "description": "cloudy"},
                "temperature": {"current_celsius": 30},
                "humidity_percent": 70,
                "pressure_hpa": 1008,
                "wind": {"speed_mps": 3.4},
                "cloudiness_percent": 40,
            },
            "tool_calls": [{"name": "get_current_weather", "cached": False}],
        }
    )

    assert envelope["domain"] == "weather"
    assert envelope["schema_version"] == "weather.current.v1"
    assert envelope["data_type"] == "current"
    assert envelope["location"] == "Ha Noi"
    assert envelope["data"]["location"]["country"] == "VN"
    assert envelope["data"]["current"]["temperature"]["current_celsius"] == 30
    assert envelope["data"]["current"]["observed_at_local"] == "2026-07-10T14:53:20+07:00"
    assert "current.observed_at_local" in envelope["available_fields"]
    assert envelope["data"]["forecast"] is None
    assert "current.temperature.current_celsius" in envelope["available_fields"]
    assert envelope["source"]["tools_used"] == [
        {"name": "get_current_weather", "cached": False}
    ]


def test_weather_visualization_data_combines_current_and_forecast() -> None:
    envelope = weather_agent._build_weather_visualization_data(
        {
            "current_weather_data": {
                "location": "Ha Noi",
                "country": "VN",
                "timestamp": 1783670000,
                "timezone": 25200,
                "condition": {"description": "cloudy"},
                "temperature": {"current_celsius": 30},
            },
            "forecast_weather_data": {
                "location": "Ha Noi",
                "country": "VN",
                "timezone": 25200,
                "requested_days": 3,
                "source_granularity": "3-hour forecast intervals",
                "days": [
                    {
                        "date": "2026-07-10",
                        "temperature": {"min_celsius": 28.5, "max_celsius": 30.0},
                        "max_rain_probability": 0.6,
                        "intervals": [],
                    }
                ],
            },
            "tool_calls": [
                {"name": "get_current_weather", "cached": False},
                {"name": "get_weather_forecast", "cached": False},
            ],
        }
    )

    assert envelope["schema_version"] == "weather.combined.v1"
    assert envelope["data_type"] == "combined"
    assert envelope["data"]["current"]["temperature"]["current_celsius"] == 30
    assert envelope["data"]["forecast"]["requested_days"] == 3
    assert envelope["data"]["forecast"]["days"][0]["max_rain_probability"] == 0.6
    assert "forecast.days[].temperature.max_celsius" in envelope["available_fields"]


def test_weather_visualization_data_preserves_error_metadata() -> None:
    envelope = weather_agent._build_weather_visualization_data(
        {
            "error_records": [
                {
                    "location": "Ha Noi",
                    "error": {
                        "source": "weather",
                        "message": "Missing OPENWEATHER_API_KEY.",
                        "status_code": None,
                    },
                }
            ],
            "errors": [
                {
                    "source": "weather",
                    "message": "Missing OPENWEATHER_API_KEY.",
                    "status_code": None,
                }
            ],
            "tool_calls": [{"name": "get_current_weather", "cached": False}],
        }
    )

    assert envelope["schema_version"] == "weather.error.v1"
    assert envelope["data_type"] == "error"
    assert envelope["location"] == "Ha Noi"
    assert envelope["data"]["current"] is None
    assert envelope["data"]["forecast"] is None
    assert envelope["errors"][0]["message"] == "Missing OPENWEATHER_API_KEY."


def test_weather_visualization_data_empty_has_no_available_empty_fields() -> None:
    envelope = weather_agent._build_weather_visualization_data({})

    assert envelope["schema_version"] == "weather.empty.v1"
    assert envelope["data_type"] == "empty"
    assert envelope["location"] == ""
    assert envelope["available_fields"] == []


def test_weather_visualization_data_ignores_blank_string_fields() -> None:
    envelope = weather_agent._build_weather_visualization_data(
        {
            "current_weather_data": {
                "location": "  ",
                "country": "",
                "condition": {"description": ""},
                "temperature": {"current_celsius": 30},
            }
        }
    )

    assert "location.name" not in envelope["available_fields"]
    assert "location.country" not in envelope["available_fields"]
    assert "current.condition.description" not in envelope["available_fields"]
    assert "current.temperature.current_celsius" in envelope["available_fields"]


def test_weather_history_uses_only_active_weather_workflow() -> None:
    history = [
        {"role": "user", "content": "Thời tiết Huế hôm nay", "domain": "weather", "workflow_id": "old"},
        {"role": "assistant", "content": "Huế có mưa", "domain": "weather", "workflow_id": "old"},
        {"role": "user", "content": "Bật nhạc", "domain": "music"},
        {"role": "assistant", "content": "Đang phát nhạc", "domain": "music"},
        {"role": "user", "content": "Thời tiết Hà Nội ngày mai", "domain": "weather", "workflow_id": "current"},
        {"role": "assistant", "content": "Bạn muốn xem lúc nào?", "domain": "weather", "workflow_id": "current"},
        {"role": "user", "content": "9 giờ"},
    ]

    relevant = weather_agent._weather_conversation_messages(
        "9 giờ",
        history,
        weather_session={"active": True, "workflow_id": "current"},
    )

    assert relevant == [
        {"role": "user", "content": "Thời tiết Hà Nội ngày mai"},
        {"role": "assistant", "content": "Bạn muốn xem lúc nào?"},
    ]


def test_inactive_weather_session_does_not_reuse_old_history() -> None:
    relevant = weather_agent._weather_conversation_messages(
        "Thời tiết thì sao?",
        [
            {
                "role": "user",
                "content": "Thời tiết Hà Nội ngày mai",
                "domain": "weather",
                "workflow_id": "weather-old",
            }
        ],
        weather_session={"active": False, "workflow_id": "weather-old"},
    )

    assert relevant == []


def test_weather_extraction_message_carries_last_resolved_request() -> None:
    previous = {
        "location_text": "Hà Nội",
        "date_text": "ngày mai",
        "time_of_day_text": None,
        "normalized_time": None,
        "request_type_candidate": "forecast",
    }

    payload = json.loads(
        weather_agent._pipeline_extraction_message(
            "9 giờ",
            [],
            previous,
        )
    )

    assert payload["last_resolved_request"] == previous
    assert payload["query"] == "9 giờ"


def test_weather_llm_payload_uses_compact_daily_facts() -> None:
    facts = weather_agent._weather_facts_for_llm(
        {
            "data_type": "forecast",
            "data": {
                "location": {"name": "Hà Nội"},
                "current": None,
                "presentation": None,
                "forecast": {
                    "requested_days": 1,
                    "hourly_selection": None,
                    "days": [
                        {
                            "date": "2026-07-21",
                            "max_rain_probability": 0.6,
                            "intervals": [{"time": "09:00"}, {"time": "10:00"}],
                        }
                    ],
                },
            },
        }
    )

    assert facts == {
        "kind": "daily_forecast",
        "place": "Hà Nội",
        "days": [{"date": "2026-07-21", "rain_max_pct": 60}],
    }


def test_completed_weather_keeps_data_when_llm2_fails(monkeypatch) -> None:
    class FailingResponseClient(StubPipelineClient):
        def chat_text(self, system_prompt: str, user_message: str) -> str:
            self.calls.append(("response", user_message))
            raise RuntimeError("LLM2 unavailable")

    monkeypatch.setattr(
        weather_agent,
        "get_weather_location_resolver",
        lambda _path=None: StubLocationResolver(),
    )
    client = FailingResponseClient(
        {
            "location_text": "Hà Nội",
            "date_text": None,
            "time_of_day_text": None,
            "normalized_time": None,
            "request_type_candidate": "current",
        }
    )
    store = StubWeatherStore(
        current={
            "ok": True,
            "data": {
                "location": "Hà Nội",
                "country": "VN",
                "timezone_offset_seconds": 25200,
                "temperature": {"current_celsius": 30},
                "condition": {"description": "có mây"},
            },
        }
    )

    result = weather_agent.run_weather_llm_pipeline(
        {"query": "Thời tiết Hà Nội hiện tại", "history": []},
        store=store,
        settings=_settings(),
        client=client,
    )

    assert result["weather_status"] == "completed"
    assert result["weather_data"]["data_type"] == "current"
    assert result["weather_error"]["code"] == "llm2_api_error"
    assert result["weather_answer"] == weather_agent._WEATHER_RESPONSE_FALLBACK
