from rag_manager.agents import news as news_agent
from rag_manager.cache import MemoryCache, news_cache_key
from rag_manager.config import Settings


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def chat_text(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.2,
    ) -> str:
        self.messages.append(user_message)
        assert "News JSON:" in user_message
        assert temperature == 0.2
        return "cached news answer"


class FailingClient:
    def chat_text(self, *args, **kwargs) -> str:
        raise AssertionError("Gemini should not format missing news API key errors")


def _settings(*, gnews_api_key: str = "") -> Settings:
    return Settings(
        gemini_api_key="gemini-key",
        gemini_base_url="",
        gemini_model="",
        openweather_api_key="",
        gnews_api_key=gnews_api_key,
        weather_cache_ttl_seconds=3600,
        news_cache_ttl_seconds=900,
        wiki_cache_ttl_seconds=None,
        request_timeout_seconds=8,
        debug_routing=False,
    )


def test_news_agent_cache_hit_does_not_call_service(monkeypatch) -> None:
    def fail_fetch_news(*args, **kwargs):
        raise AssertionError("fetch_news should not be called on cache hit")

    monkeypatch.setattr(news_agent, "fetch_news", fail_fetch_news)

    settings = _settings()
    cache = MemoryCache()
    cached_data = {
        "total_articles": 1,
        "articles": [
            {
                "title": "AI update",
                "description": "Cached article",
                "source": "Example News",
                "published_at": "2026-07-10T01:00:00Z",
                "url": "https://example.com/ai",
            }
        ],
    }
    cache.set(news_cache_key("AI"), cached_data)

    result = news_agent.run_news_agent(
        {
            "query": "Latest AI news",
            "intent": {"news_query": "AI"},
        },
        cache=cache,
        settings=settings,
        client=FakeClient(),
    )

    assert result["news_data"] == cached_data
    assert result["news_answer"] == "cached news answer"
    assert result["cache_stats"]["news"]["hits"] == 1
    assert result["cache_stats"]["news"]["misses"] == 0
    assert result["timings"]["news"] >= 0


def test_news_agent_missing_api_key_returns_clear_limit_without_gemini() -> None:
    result = news_agent.run_news_agent(
        {
            "query": "Tin công nghệ mới nhất hôm nay là gì?",
            "intent": {"news_query": "công nghệ"},
        },
        cache=MemoryCache(),
        settings=_settings(gnews_api_key=""),
        client=FailingClient(),
    )

    assert result["news_data"]["query"] == "công nghệ"
    assert result["news_data"]["error"]["message"] == "Missing GNEWS_API_KEY."
    assert "thiếu GNEWS_API_KEY" in result["news_answer"]
    assert "file .env" in result["news_answer"]
    assert result["cache_stats"]["news"]["hits"] == 0
    assert result["cache_stats"]["news"]["misses"] == 1
    assert result["timings"]["news"] >= 0


def test_news_agent_temporary_error_is_not_cached(monkeypatch) -> None:
    calls = 0

    def fake_fetch_news(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "ok": False,
            "error": {
                "source": "news",
                "message": "News request timed out.",
                "status_code": None,
            },
        }

    monkeypatch.setattr(news_agent, "fetch_news", fake_fetch_news)

    cache = MemoryCache()
    state = {
        "query": "Tin công nghệ mới nhất hôm nay là gì?",
        "intent": {"news_query": "công nghệ"},
    }

    first_result = news_agent.run_news_agent(
        state,
        cache=cache,
        settings=_settings(gnews_api_key="news-key"),
        client=FakeClient(),
    )
    second_result = news_agent.run_news_agent(
        state,
        cache=cache,
        settings=_settings(gnews_api_key="news-key"),
        client=FakeClient(),
    )

    assert calls == 2
    assert first_result["news_data"]["error"]["message"] == "News request timed out."
    assert second_result["news_data"]["error"]["message"] == "News request timed out."
    assert second_result["cache_stats"]["news"]["size"] == 0
