from rag_manager.agents import weather as weather_agent
from rag_manager.cache import MemoryCache, weather_cache_key, weather_hour_bucket
from rag_manager.config import Settings


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


def test_weather_agent_returns_tool_agent_result(monkeypatch) -> None:
    calls = []

    def fake_run_weather_tool_agent(*, query, location_hint, cache, settings):
        calls.append(
            {
                "query": query,
                "location_hint": location_hint,
                "cache": cache,
                "settings": settings,
            }
        )
        return {
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

    cache = MemoryCache()
    settings = _settings()
    result = weather_agent.run_weather_agent(
        {
            "query": "Weather in Ha Noi",
            "intent": {"location": "Ha Noi"},
        },
        cache=cache,
        settings=settings,
    )

    assert calls == [
        {
            "query": "Weather in Ha Noi",
            "location_hint": "Ha Noi",
            "cache": cache,
            "settings": settings,
        }
    ]
    assert result["weather_data"] == {"location": "Ha Noi"}
    assert result["weather_answer"] == "tool weather answer"
    assert result["cache_stats"]["weather"]["hits"] == 0
    assert result["cache_stats"]["weather"]["misses"] == 0
    assert result["timings"]["weather"] >= 0
    assert result["llm_usage"]["weather"]["prompt_tokens"] == 50


def test_current_weather_tool_cache_hit_does_not_call_service(monkeypatch) -> None:
    def fail_fetch_weather(*args, **kwargs):
        raise AssertionError("fetch_weather should not be called on cache hit")

    monkeypatch.setattr(weather_agent, "fetch_weather", fail_fetch_weather)

    cache = MemoryCache()
    cached_data = {
        "location": "Ha Noi",
        "temperature": {"current_celsius": 30},
        "condition": {"description": "cloudy"},
    }
    cache.set(
        weather_cache_key("Ha Noi", bucket=f"current:{weather_hour_bucket()}"),
        cached_data,
    )
    tools, tool_state = weather_agent.build_weather_tools(
        cache=cache,
        settings=_settings(),
        location_hint="Ha Noi",
    )

    result = _tool_by_name(tools, "get_current_weather").invoke({"location": "Ha Noi"})

    assert result == {"ok": True, "data": cached_data, "cached": True}
    assert tool_state["last_weather_data"] == cached_data
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 0


def test_current_weather_tool_returns_missing_api_key_error() -> None:
    cache = MemoryCache()
    tools, tool_state = weather_agent.build_weather_tools(
        cache=cache,
        settings=_settings(openweather_api_key=""),
        location_hint="Hà Nội",
    )

    result = _tool_by_name(tools, "get_current_weather").invoke({"location": "Hà Nội"})

    assert result["ok"] is False
    assert result["error"]["message"] == "Missing OPENWEATHER_API_KEY."
    assert tool_state["last_weather_data"]["location"] == "Hà Nội"
    assert tool_state["last_weather_data"]["error"]["message"] == "Missing OPENWEATHER_API_KEY."


def test_forecast_tool_calls_forecast_service_and_caches(monkeypatch) -> None:
    calls = []

    def fake_fetch_weather_forecast(location, *, api_key, timeout_seconds, days):
        calls.append(
            {
                "location": location,
                "api_key": api_key,
                "timeout_seconds": timeout_seconds,
                "days": days,
            }
        )
        return {
            "ok": True,
            "data": {
                "location": location,
                "requested_days": days,
                "days": [{"date": "2026-07-10"}],
            },
        }

    monkeypatch.setattr(weather_agent, "fetch_weather_forecast", fake_fetch_weather_forecast)

    cache = MemoryCache()
    tools, tool_state = weather_agent.build_weather_tools(
        cache=cache,
        settings=_settings(),
        location_hint="Ha Noi",
    )

    result = _tool_by_name(tools, "get_weather_forecast").invoke(
        {"location": "Ha Noi", "days": 9}
    )

    assert calls == [
        {
            "location": "Ha Noi",
            "api_key": "weather-key",
            "timeout_seconds": 8,
            "days": 5,
        }
    ]
    assert result["ok"] is True
    assert result["data"]["requested_days"] == 5
    assert tool_state["last_weather_data"]["requested_days"] == 5
    assert cache.stats()["misses"] == 1
    assert cache.stats()["size"] == 1


def test_tomorrow_forecast_keeps_only_tomorrow(monkeypatch) -> None:
    calls = []
    today = weather_agent.datetime.now(weather_agent.ZoneInfo("Asia/Bangkok")).date()
    tomorrow = (today + weather_agent.timedelta(days=1)).isoformat()

    def fake_fetch_weather_forecast(location, *, api_key, timeout_seconds, days):
        calls.append(days)
        return {
            "ok": True,
            "data": {
                "location": location,
                "requested_days": days,
                "days": [{"date": tomorrow}, {"date": "2099-01-01"}],
            },
        }

    monkeypatch.setattr(weather_agent, "fetch_weather_forecast", fake_fetch_weather_forecast)
    tools, tool_state = weather_agent.build_weather_tools(
        cache=MemoryCache(),
        settings=_settings(),
        location_hint="Ha Noi",
        query="Thời tiết Hà Nội ngày mai",
    )

    result = _tool_by_name(tools, "get_weather_forecast").invoke(
        {"location": "Ha Noi", "days": 3}
    )

    assert calls == [3]
    assert result["data"]["requested_days"] == 1
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
