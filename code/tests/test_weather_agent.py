from rag_manager.agents import weather as weather_agent
from rag_manager.cache import MemoryCache, weather_cache_key
from rag_manager.config import Settings


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.last_usage = {
            "model": "weather-model",
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "total_tokens": 60,
            "cached_tokens": 25,
            "prefix_cache_hit": True,
            "cache_hit_ratio": 0.5,
            "kv_cache_hit": "not_exposed_by_gemini_api",
        }

    def chat_text(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.2,
    ) -> str:
        self.messages.append(user_message)
        assert "Weather JSON:" in user_message
        assert temperature == 0.2
        return "cached weather answer"


class FailingClient:
    def chat_text(self, *args, **kwargs) -> str:
        raise AssertionError("Gemini should not format missing weather API key errors")


def _settings(*, openweather_api_key: str = "") -> Settings:
    return Settings(
        gemini_api_key="gemini-key",
        gemini_base_url="",
        gemini_model="",
        openweather_api_key=openweather_api_key,
        gnews_api_key="",
        weather_cache_ttl_seconds=3600,
        news_cache_ttl_seconds=900,
        wiki_cache_ttl_seconds=None,
        request_timeout_seconds=8,
        debug_routing=False,
    )


def test_weather_agent_cache_hit_does_not_call_service(monkeypatch) -> None:
    def fail_fetch_weather(*args, **kwargs):
        raise AssertionError("fetch_weather should not be called on cache hit")

    monkeypatch.setattr(weather_agent, "fetch_weather", fail_fetch_weather)

    settings = _settings()
    cache = MemoryCache()
    cached_data = {
        "location": "Ha Noi",
        "temperature": {"current_celsius": 30},
        "condition": {"description": "cloudy"},
    }
    cache.set(weather_cache_key("Ha Noi"), cached_data)

    result = weather_agent.run_weather_agent(
        {
            "query": "Weather in Ha Noi",
            "intent": {"location": "Ha Noi"},
        },
        cache=cache,
        settings=settings,
        client=FakeClient(),
    )

    assert result["weather_data"] == cached_data
    assert result["weather_answer"] == "cached weather answer"
    assert result["cache_stats"]["weather"]["hits"] == 1
    assert result["cache_stats"]["weather"]["misses"] == 0
    assert result["timings"]["weather"] >= 0
    assert result["llm_usage"]["weather"]["cached_tokens"] == 25
    assert result["llm_usage"]["weather"]["prefix_cache_hit"] is True


def test_weather_agent_missing_api_key_returns_clear_limit_without_gemini() -> None:
    result = weather_agent.run_weather_agent(
        {
            "query": "Thời tiết Hà Nội hôm nay thế nào?",
            "intent": {"location": "Hà Nội"},
        },
        cache=MemoryCache(),
        settings=_settings(openweather_api_key=""),
        client=FailingClient(),
    )

    assert result["weather_data"]["location"] == "Hà Nội"
    assert result["weather_data"]["error"]["message"] == "Missing OPENWEATHER_API_KEY."
    assert "thiếu OPENWEATHER_API_KEY" in result["weather_answer"]
    assert "file .env" in result["weather_answer"]
    assert result["cache_stats"]["weather"]["hits"] == 0
    assert result["cache_stats"]["weather"]["misses"] == 1
    assert result["timings"]["weather"] >= 0


def test_weather_agent_temporary_error_is_not_cached(monkeypatch) -> None:
    calls = 0

    def fake_fetch_weather(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "ok": False,
            "error": {
                "source": "weather",
                "message": "Weather request timed out.",
                "status_code": None,
            },
        }

    monkeypatch.setattr(weather_agent, "fetch_weather", fake_fetch_weather)

    cache = MemoryCache()
    state = {
        "query": "Thời tiết Hà Nội hôm nay thế nào?",
        "intent": {"location": "Hà Nội"},
    }

    first_result = weather_agent.run_weather_agent(
        state,
        cache=cache,
        settings=_settings(openweather_api_key="weather-key"),
        client=FakeClient(),
    )
    second_result = weather_agent.run_weather_agent(
        state,
        cache=cache,
        settings=_settings(openweather_api_key="weather-key"),
        client=FakeClient(),
    )

    assert calls == 2
    assert first_result["weather_data"]["error"]["message"] == "Weather request timed out."
    assert second_result["weather_data"]["error"]["message"] == "Weather request timed out."
    assert second_result["cache_stats"]["weather"]["size"] == 0
