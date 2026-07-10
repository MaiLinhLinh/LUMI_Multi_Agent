import json

from rag_manager.agents.aggregator import run_aggregator_agent
from rag_manager.config import Settings
from rag_manager.llm.prompts import AGGREGATOR_SYSTEM_PROMPT


class FakeClient:
    def __init__(self) -> None:
        self.system_prompt = ""
        self.payload = {}

    def chat_text(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.2,
    ) -> str:
        self.system_prompt = system_prompt
        assert temperature == 0.2
        assert "Aggregator JSON:" in user_message
        payload_text = user_message.split("Aggregator JSON:\n", 1)[1]
        self.payload = json.loads(payload_text)
        return "combined final answer"


def test_aggregator_combines_multiple_agent_outputs() -> None:
    settings = Settings(
        gemini_api_key="",
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
    client = FakeClient()
    state = {
        "query": "Tell me about Hanoi weather, background, and news",
        "execution_mode": "parallel",
        "selected_agents": ["weather", "wiki", "news"],
        "weather_answer": "Weather answer",
        "weather_data": {"location": "Ha Noi", "temperature": {"current_celsius": 30}},
        "wiki_answer": "Wiki answer",
        "wiki_data": {"title": "Hanoi", "summary": "Capital of Vietnam"},
        "news_answer": "News answer",
        "news_data": {"articles": [{"title": "Hanoi update"}]},
        "cache_stats": {"weather": {"hits": 1}, "news": {"misses": 1}},
        "timings": {"weather": 0.1, "news": 0.2, "wiki": 0.3},
    }

    result = run_aggregator_agent(state, settings=settings, client=client)

    assert result["final_response"] == "combined final answer"
    assert result["timings"]["aggregate"] >= 0
    assert client.system_prompt == AGGREGATOR_SYSTEM_PROMPT
    assert set(client.payload["agent_outputs"]) == {"weather", "news", "wiki"}
    assert client.payload["agent_outputs"]["weather"]["answer"] == "Weather answer"
    assert client.payload["agent_outputs"]["news"]["data"]["articles"][0]["title"] == "Hanoi update"
    assert client.payload["successful_agents"] == ["weather", "news", "wiki"]
    assert client.payload["cache_stats"]["weather"]["hits"] == 1
    assert client.payload["timings"]["wiki"] == 0.3
