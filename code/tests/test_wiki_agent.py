from rag_manager.agents import wiki as wiki_agent
from rag_manager.cache import MemoryCache, wiki_cache_key
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
        assert "Wiki JSON:" in user_message
        assert temperature == 0.2
        return "cached wiki answer"


class FailingClient:
    def chat_text(self, *args, **kwargs) -> str:
        raise AssertionError("Gemini should not format wiki not found errors")


def _settings() -> Settings:
    return Settings(
        gemini_api_key="gemini-key",
        gemini_base_url="",
        gemini_model="",
        openweather_api_key="",
        gnews_api_key="",
        weather_cache_ttl_seconds=3600,
        news_cache_ttl_seconds=900,
        wiki_cache_ttl_seconds=None,
        request_timeout_seconds=8,
        debug_routing=False,
    )


def test_wiki_agent_cache_hit_does_not_call_service(monkeypatch) -> None:
    def fail_fetch_wiki_summary(*args, **kwargs):
        raise AssertionError("fetch_wiki_summary should not be called on cache hit")

    monkeypatch.setattr(wiki_agent, "fetch_wiki_summary", fail_fetch_wiki_summary)

    settings = _settings()
    cache = MemoryCache()
    cached_data = {
        "title": "Python",
        "summary": "Python is a programming language.",
        "url": "https://vi.wikipedia.org/wiki/Python",
    }
    cache.set(wiki_cache_key("Python"), cached_data)

    result = wiki_agent.run_wiki_agent(
        {
            "query": "Python la gi?",
            "intent": {"wiki_topic": "Python"},
        },
        cache=cache,
        settings=settings,
        client=FakeClient(),
    )

    assert result["wiki_data"] == cached_data
    assert result["wiki_answer"] == "cached wiki answer"
    assert result["cache_stats"]["wiki"]["hits"] == 1
    assert result["cache_stats"]["wiki"]["misses"] == 0
    assert result["timings"]["wiki"] >= 0


def test_wiki_agent_not_found_returns_clear_message_without_gemini(monkeypatch) -> None:
    def fake_fetch_wiki_summary(*args, **kwargs):
        return {
            "ok": False,
            "error": {
                "source": "wiki",
                "message": "Wikipedia topic was not found: KhongCoChuDeNay",
                "status_code": None,
            },
        }

    monkeypatch.setattr(wiki_agent, "fetch_wiki_summary", fake_fetch_wiki_summary)

    result = wiki_agent.run_wiki_agent(
        {
            "query": "KhongCoChuDeNay là gì?",
            "intent": {"wiki_topic": "KhongCoChuDeNay"},
        },
        cache=MemoryCache(),
        settings=_settings(),
        client=FailingClient(),
    )

    assert result["wiki_data"]["topic"] == "KhongCoChuDeNay"
    assert result["wiki_data"]["error"]["message"] == "Wikipedia topic was not found: KhongCoChuDeNay"
    assert "không tìm thấy" in result["wiki_answer"]
    assert "KhongCoChuDeNay" in result["wiki_answer"]
    assert "từ khóa cụ thể hơn" in result["wiki_answer"]
    assert result["cache_stats"]["wiki"]["hits"] == 0
    assert result["cache_stats"]["wiki"]["misses"] == 1
    assert result["timings"]["wiki"] >= 0


def test_wiki_agent_temporary_error_is_not_cached(monkeypatch) -> None:
    calls = 0

    def fake_fetch_wiki_summary(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "ok": False,
            "error": {
                "source": "wiki",
                "message": "Wikipedia request timed out.",
                "status_code": None,
            },
        }

    monkeypatch.setattr(wiki_agent, "fetch_wiki_summary", fake_fetch_wiki_summary)

    cache = MemoryCache()
    state = {
        "query": "Python là gì?",
        "intent": {"wiki_topic": "Python"},
    }

    first_result = wiki_agent.run_wiki_agent(
        state,
        cache=cache,
        settings=_settings(),
        client=FakeClient(),
    )
    second_result = wiki_agent.run_wiki_agent(
        state,
        cache=cache,
        settings=_settings(),
        client=FakeClient(),
    )

    assert calls == 2
    assert first_result["wiki_data"]["error"]["message"] == "Wikipedia request timed out."
    assert second_result["wiki_data"]["error"]["message"] == "Wikipedia request timed out."
    assert second_result["cache_stats"]["wiki"]["size"] == 0
