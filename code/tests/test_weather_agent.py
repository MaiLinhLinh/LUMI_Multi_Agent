from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from rag_manager.agents import weather as weather_agent
from rag_manager.config import Settings
from langchain_core.messages import AIMessage


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


def _redis_unavailable() -> dict:
    return {
        "ok": False,
        "error": {
            "source": "weather_redis",
            "message": "No active weather snapshot is available in Redis.",
            "status_code": None,
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


def test_weather_langchain_usage_logs_each_llm_cache_result(capsys) -> None:
    message = AIMessage(
        content="weather answer",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_token_details": {"cache_read": 80},
        },
    )

    usage = weather_agent._extract_langchain_usage(
        {"messages": [message]},
        "gemini-weather",
    )

    assert usage["cached_tokens"] == 80
    assert usage["cache_hit_ratio"] == 0.8
    assert usage["saved_tokens_estimated"] == 80
    terminal_output = capsys.readouterr().out
    assert "[LLM_CACHE][source=weather_langchain][call=1]" in terminal_output
    assert "cached_tokens=80" in terminal_output
    assert "cache_hit_ratio=0.8000" in terminal_output
    assert "saved_tokens_estimated=80" in terminal_output


def test_weather_agent_returns_tool_agent_result(monkeypatch) -> None:
    calls = []

    def fake_run_weather_tool_agent(*, query, history, store, settings):
        calls.append(
            {
                "query": query,
                "history": history,
                "store": store,
                "settings": settings,
            }
        )
        return {
            "status": "completed",
            "answer": "tool weather answer",
            "weather_data": {"location": "Ha Noi"},
            "llm_usage": {
                "model": "weather-model",
                "prompt_tokens": 50,
                "completion_tokens": 10,
                "total_tokens": 60,
            },
        }

    monkeypatch.setattr(weather_agent, "run_weather_tool_agent", fake_run_weather_tool_agent)

    store = StubWeatherStore()
    settings = _settings()
    result = weather_agent.run_weather_agent(
        {
            "query": "Weather in Ha Noi",
            "history": [{"role": "user", "content": "Weather in Ha Noi"}],
        },
        store=store,
        settings=settings,
    )

    assert calls == [
        {
            "query": "Weather in Ha Noi",
            "history": [{"role": "user", "content": "Weather in Ha Noi"}],
            "store": store,
            "settings": settings,
        }
    ]
    assert result["weather_data"] == {"location": "Ha Noi"}
    assert result["weather_status"] == "completed"
    assert result["weather_answer"] == "tool weather answer"
    assert result["cache_stats"]["weather"]["hits"] == 0
    assert result["cache_stats"]["weather"]["misses"] == 0
    assert result["timings"]["weather"] >= 0
    assert result["llm_usage"]["weather"]["prompt_tokens"] == 50


def test_validator_resolves_location_without_manager_hint() -> None:
    resolver = StubLocationResolver()
    tools, tool_state = weather_agent.build_weather_tools(
        store=StubWeatherStore(),
        resolver=resolver,
    )

    result = _tool_by_name(tools, "validate_weather_request").invoke(
        {
            "location_text": "Hà Nội",
            "time_text": "hiện tại",
            "request_type_candidate": "current",
        }
    )

    assert result == {
        "status": "ready_for_redis",
        "request": {"request_type": "current", "location_id": "ha_noi"},
    }
    assert resolver.calls == ["Hà Nội"]
    assert "ha_noi" in tool_state["resolved_locations"]


def test_validator_returns_location_clarification_before_time_validation() -> None:
    class MissingLocationResolver:
        def resolve(self, location_text: str) -> dict:
            return {
                "ok": False,
                "error": {
                    "source": "weather_location_resolver",
                    "code": "location_not_found",
                    "message": "not found",
                    "status_code": None,
                },
                "candidates": [],
            }

    store = StubWeatherStore()
    tools, tool_state = weather_agent.build_weather_tools(
        store=store,
        resolver=MissingLocationResolver(),
        reference_datetime=datetime(2026, 7, 13, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )

    result = _tool_by_name(tools, "validate_weather_request").invoke(
        {
            "location_text": "Paris",
            "time_text": "thứ Tư ngày 17/7/2026",
            "request_type_candidate": "forecast",
        }
    )

    assert result["stage"] == "location"
    assert result["code"] == "location_not_found"
    assert tool_state["weather_status"] == "needs_clarification"
    assert store.current_calls == []
    assert store.forecast_calls == []


def test_weekday_date_conflict_never_accesses_redis() -> None:
    store = StubWeatherStore()
    tools, tool_state = weather_agent.build_weather_tools(
        store=store,
        resolver=StubLocationResolver(),
        reference_datetime=datetime(2026, 7, 13, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )

    result = _tool_by_name(tools, "validate_weather_request").invoke(
        {
            "location_text": "Hà Nội",
            "time_text": "thứ Tư ngày 17/7/2026",
            "request_type_candidate": "forecast",
        }
    )

    assert result["stage"] == "time"
    assert result["code"] == "weekday_date_conflict"
    assert result["details"]["matching_weekday_date"] == "2026-07-15"
    assert tool_state["weather_status"] == "needs_clarification"
    assert store.forecast_calls == []


def test_current_weather_tool_reads_active_redis_snapshot() -> None:
    cached_data = {
        "location": "Ha Noi",
        "timezone_offset_seconds": 25200,
        "temperature": {"current_celsius": 30},
        "condition": {"description": "cloudy"},
    }
    store = StubWeatherStore(
        current={"ok": True, "data": cached_data, "cached": True}
    )
    resolver = StubLocationResolver()
    tools, tool_state = weather_agent.build_weather_tools(
        store=store,
        resolver=resolver,
    )

    validation = _tool_by_name(tools, "validate_weather_request").invoke(
        {
            "location_text": "Ha Noi",
            "time_text": "bây giờ",
            "request_type_candidate": "current",
        }
    )
    result = _tool_by_name(tools, "get_current_weather").invoke(
        {"location_id": "llm_must_not_override_validated_id"}
    )

    assert validation["status"] == "ready_for_redis"
    assert result == {"ok": True, "data": cached_data, "cached": True}
    assert tool_state["last_weather_data"] == cached_data
    assert store.current_calls == ["ha_noi"]
    assert resolver.calls == ["Ha Noi"]
    assert store.stats()["hits"] == 1


def test_current_weather_tool_returns_missing_snapshot_error() -> None:
    store = StubWeatherStore()
    tools, tool_state = weather_agent.build_weather_tools(
        store=store,
        resolver=StubLocationResolver(),
    )

    _tool_by_name(tools, "validate_weather_request").invoke(
        {
            "location_text": "Hà Nội",
            "time_text": "hiện tại",
            "request_type_candidate": "current",
        }
    )
    result = _tool_by_name(tools, "get_current_weather").invoke(
        {"location_id": "ha_noi"}
    )

    assert result["ok"] is False
    assert result["error"]["source"] == "weather_redis"
    assert "No active weather snapshot" in result["error"]["message"]
    assert tool_state["last_weather_data"]["location"] == "Hà Nội"
    assert tool_state["last_weather_data"]["location_id"] == "ha_noi"
    assert tool_state["weather_status"] == "unavailable"


def test_snapshot_timezone_mismatch_is_a_technical_error() -> None:
    store = StubWeatherStore(
        current={
            "ok": True,
            "data": {"location": "Hà Nội", "timezone_offset_seconds": 0},
            "cached": True,
        }
    )
    tools, tool_state = weather_agent.build_weather_tools(
        store=store,
        resolver=StubLocationResolver(),
    )
    _tool_by_name(tools, "validate_weather_request").invoke(
        {
            "location_text": "Hà Nội",
            "time_text": "hiện tại",
            "request_type_candidate": "current",
        }
    )

    result = _tool_by_name(tools, "get_current_weather").invoke(
        {"location_id": "ha_noi"}
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "snapshot_timezone_mismatch"
    assert tool_state["weather_status"] == "error"
    assert "current_weather_data" not in tool_state


def test_weather_data_tool_requires_resolver_first() -> None:
    store = StubWeatherStore(
        current={"ok": True, "data": {"location": "Hà Nội"}, "cached": True}
    )
    tools, _tool_state = weather_agent.build_weather_tools(
        store=store,
        location_hint="",
        resolver=StubLocationResolver(),
    )

    result = _tool_by_name(tools, "get_current_weather").invoke(
        {"location_id": "ha_noi"}
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "tool_call_mismatch"
    assert store.current_calls == []


def test_validator_rejects_forecast_longer_than_five_days() -> None:
    store = StubWeatherStore(
        forecast={
            "ok": True,
            "cached": True,
                "data": {},
        }
    )
    tools, tool_state = weather_agent.build_weather_tools(
        store=store,
        resolver=StubLocationResolver(),
        reference_datetime=datetime(2026, 7, 13, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    )

    result = _tool_by_name(tools, "validate_weather_request").invoke(
        {
            "location_text": "Ha Noi",
            "time_text": "9 ngày tới",
            "request_type_candidate": "forecast",
        }
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_range_exceeded"
    assert tool_state["weather_status"] == "needs_clarification"
    assert store.forecast_calls == []


def test_forecast_tool_filters_from_explicit_start_date() -> None:
    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    tomorrow = (today + timedelta(days=1)).isoformat()
    store = StubWeatherStore(
        forecast={
            "ok": True,
                "data": {
                    "location": "Ha Noi",
                    "timezone_offset_seconds": 25200,
                "requested_days": 3,
                "days": [{"date": tomorrow}, {"date": "2099-01-01"}],
            },
        }
    )
    tools, tool_state = weather_agent.build_weather_tools(
        store=store,
        query="Thời tiết Hà Nội ngày mai",
        resolver=StubLocationResolver(),
    )

    validation = _tool_by_name(tools, "validate_weather_request").invoke(
        {
            "location_text": "Hà Nội",
            "time_text": "ngày mai",
            "request_type_candidate": "forecast",
        }
    )
    result = _tool_by_name(tools, "get_weather_forecast").invoke(
        {"location_id": "ha_noi", "days": 1, "start_date": tomorrow}
    )

    assert store.forecast_calls == [("ha_noi", 1, tomorrow)]
    assert validation["request"]["start_date"] == tomorrow
    assert result["data"]["requested_days"] == 1
    assert result["data"]["requested_start_date"] == tomorrow
    assert result["data"]["days"] == [{"date": tomorrow}]
    assert tool_state["forecast_weather_data"]["days"] == [{"date": tomorrow}]


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


def _tool_by_name(tools: list[object], name: str):
    for weather_tool in tools:
        if getattr(weather_tool, "name", "") == name:
            return weather_tool
    raise AssertionError(f"Tool not found: {name}")
