from rag_manager import graph
from rag_manager.config import Settings


def _settings() -> Settings:
    return Settings(
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


def test_manager_node_passes_history_for_clarification_follow_up(monkeypatch) -> None:
    captured: dict = {}
    history = [
        {"role": "user", "content": "Thời tiết Hà Nội thế nào?"},
        {
            "role": "assistant",
            "content": "Bạn muốn biết thời tiết Hà Nội vào thời điểm nào?",
        },
        {"role": "user", "content": "hôm nay"},
    ]

    def fake_classify_intent(client, query: str, history=None) -> dict:
        captured["query"] = query
        captured["history"] = history
        return {
            "topics": ["weather"],
            "execution_mode": "single",
            "primary_intent": "weather",
            "dependencies": [],
            "news_query": "",
            "wiki_topic": "",
        }

    monkeypatch.setattr(graph, "classify_intent", fake_classify_intent)

    result = graph.manager_classify_node(
        {
            "query": "hôm nay",
            "history": history,
            "semantic_result": {"domain_request": "hôm nay"},
            "manager_client": object(),
            "settings": _settings(),
        }
    )

    assert captured == {"query": "hôm nay", "history": history}
    assert result["selected_agents"] == ["weather"]


def test_graph_single_mode_runs_only_primary_agent(monkeypatch) -> None:
    calls: list[str] = []

    def fake_classify_intent(client, query: str, history=None) -> dict:
        calls.append("manager")
        assert query == "Thời tiết Hà Nội hôm nay thế nào?"
        return {
            "topics": ["weather"],
            "execution_mode": "single",
            "primary_intent": "weather",
            "dependencies": [],
            "news_query": "",
            "wiki_topic": "",
        }

    def fake_weather_agent(state, *, store=None, settings=None, client=None) -> dict:
        calls.append("weather")
        return {
            "weather_data": {"location": "Hà Nội", "temperature": {"current_celsius": 30}},
            "weather_answer": "Hà Nội hôm nay khoảng 30 độ C.",
            "cache_stats": {"weather": {"hits": 0, "misses": 1}},
            "timings": {"weather": 0.01},
        }

    def unexpected_agent(*args, **kwargs) -> dict:
        raise AssertionError("Single weather mode must not run news or wiki")

    monkeypatch.setattr(graph, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(graph, "run_weather_llm_pipeline", fake_weather_agent)
    monkeypatch.setattr(graph, "run_news_agent", unexpected_agent)
    monkeypatch.setattr(graph, "run_wiki_agent", unexpected_agent)

    workflow = graph.build_workflow()
    result = workflow.invoke(
        {
            "query": "Thời tiết Hà Nội hôm nay thế nào?",
            "history": [],
            "settings": _settings(),
            "manager_client": object(),
        }
    )

    assert calls == ["manager", "weather"]
    assert result["execution_mode"] == "single"
    assert result["selected_agents"] == ["weather"]
    assert result["weather_answer"] == "Hà Nội hôm nay khoảng 30 độ C."
    assert "news_answer" not in result
    assert "wiki_answer" not in result
    assert result["final_response"] == "Hà Nội hôm nay khoảng 30 độ C."
    assert result["cache_stats"]["weather"]["misses"] == 1
    assert result["timings"]["manager"] >= 0
    assert result["timings"]["weather"] == 0.01
    assert result["timings"]["aggregate"] >= 0


def test_graph_routes_single_music_request_to_music_agent(monkeypatch) -> None:
    def fake_classify_intent(client, query: str, history=None) -> dict:
        assert query == "Bật nhạc Sơn Tùng"
        return {
            "topics": ["music"],
            "execution_mode": "single",
            "primary_intent": "music",
            "dependencies": [],
            "news_query": "",
            "wiki_topic": "",
        }

    monkeypatch.setattr(graph, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(
        graph,
        "run_music_agent",
        lambda state, **_kwargs: {
            "music_status": "completed",
            "music_answer": "Đây là bài “Lạc Trôi” của Sơn Tùng M-TP.",
            "music_data": {
                "decision": {
                    "selected_candidate": {"video_id": "Llw9Q6akRo4"}
                }
            },
            "timings": {"music": 0.01},
        },
    )

    workflow = graph.build_workflow()
    result = workflow.invoke(
        {
            "query": "Bật nhạc Sơn Tùng",
            "history": [],
            "settings": _settings(),
            "manager_client": object(),
        }
    )

    assert result["selected_agents"] == ["music"]
    assert result["music_status"] == "completed"
    assert result["final_response"] == result["music_answer"]
    assert result["music_answer"].startswith("Đây là bài")


def test_weather_agent_handles_clarification_after_manager_routes(monkeypatch) -> None:
    calls: list[str] = []

    def fake_classify_intent(client, query: str, history=None) -> dict:
        calls.append("manager")
        return {
            "topics": ["weather"],
            "execution_mode": "single",
            "primary_intent": "weather",
            "dependencies": [],
            "news_query": "",
            "wiki_topic": "",
        }

    def fake_weather_agent(state, **kwargs) -> dict:
        calls.append("weather")
        return {
            "weather_status": "needs_clarification",
            "weather_answer": "Bạn muốn xem thời tiết Hà Nội vào thời điểm nào?",
            "final_response": "Bạn muốn xem thời tiết Hà Nội vào thời điểm nào?",
        }

    monkeypatch.setattr(graph, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(graph, "run_weather_llm_pipeline", fake_weather_agent)

    result = graph.build_workflow().invoke(
        {
            "query": "Thời tiết Hà Nội thế nào?",
            "history": [
                {"role": "user", "content": "Thời tiết Hà Nội thế nào?"}
            ],
            "settings": _settings(),
            "manager_client": object(),
        }
    )

    assert calls == ["manager", "weather"]
    assert result["weather_status"] == "needs_clarification"
    assert result["final_response"].endswith("nào?")
    assert "manager_status" not in result


def test_weather_unavailable_ends_before_aggregate_and_visualization(monkeypatch) -> None:
    def fake_classify_intent(client, query: str, history=None) -> dict:
        return {
            "topics": ["weather"],
            "execution_mode": "single",
            "primary_intent": "weather",
            "dependencies": [],
            "news_query": "",
            "wiki_topic": "",
        }

    monkeypatch.setattr(graph, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(
        graph,
        "run_weather_llm_pipeline",
        lambda state, **kwargs: {
            "weather_status": "unavailable",
            "weather_answer": "Không có dữ liệu ngày yêu cầu.",
            "final_response": "Không có dữ liệu ngày yêu cầu.",
        },
    )
    monkeypatch.setattr(
        graph,
        "run_aggregator_agent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Aggregate must not run for unavailable weather")
        ),
    )

    result = graph.build_workflow().invoke(
        {
            "query": "Thời tiết Hà Nội ngày mai",
            "history": [
                {"role": "user", "content": "Thời tiết Hà Nội ngày mai"}
            ],
            "settings": _settings(),
            "manager_client": object(),
        }
    )

    assert result["weather_status"] == "unavailable"
    assert result["final_response"] == "Không có dữ liệu ngày yêu cầu."
    assert "visualization_output" not in result


def test_graph_parallel_mode_runs_selected_agents_and_aggregates(monkeypatch) -> None:
    calls: list[str] = []

    class FakeAggregatorClient:
        def __init__(self) -> None:
            self.user_message = ""

        def chat_text(
            self,
            system_prompt: str,
            user_message: str,
            temperature: float = 0.2,
        ) -> str:
            calls.append("aggregate_llm")
            self.user_message = user_message
            assert temperature == 0.2
            return "Combined weather and news answer"

    def fake_classify_intent(client, query: str, history=None) -> dict:
        calls.append("manager")
        assert query == "Hanoi weather and latest travel news"
        return {
            "topics": ["weather", "news"],
            "execution_mode": "parallel",
            "primary_intent": "weather",
            "dependencies": [],
            "news_query": "Hanoi travel news",
            "wiki_topic": "",
        }

    def fake_weather_agent(state, *, store=None, settings=None, client=None) -> dict:
        calls.append("weather")
        assert state["selected_agents"] == ["weather", "news"]
        return {
            "weather_data": {"location": "Hanoi", "temperature": {"current_celsius": 31}},
            "weather_answer": "Weather answer",
            "cache_stats": {"weather": {"hits": 0, "misses": 1}},
            "timings": {"weather": 0.01},
        }

    def fake_news_agent(state, *, cache=None, settings=None, client=None) -> dict:
        calls.append("news")
        assert state["selected_agents"] == ["weather", "news"]
        return {
            "news_data": {"articles": [{"title": "Travel update"}]},
            "news_answer": "News answer",
            "cache_stats": {"news": {"hits": 0, "misses": 1}},
            "timings": {"news": 0.02},
        }

    def unexpected_wiki_agent(*args, **kwargs) -> dict:
        raise AssertionError("Parallel weather/news mode must not run wiki")

    aggregator_client = FakeAggregatorClient()
    monkeypatch.setattr(graph, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(graph, "run_weather_llm_pipeline", fake_weather_agent)
    monkeypatch.setattr(graph, "run_news_agent", fake_news_agent)
    monkeypatch.setattr(graph, "run_wiki_agent", unexpected_wiki_agent)

    workflow = graph.build_workflow()
    result = workflow.invoke(
        {
            "query": "Hanoi weather and latest travel news",
            "history": [],
            "settings": _settings(),
            "manager_client": object(),
            "aggregator_client": aggregator_client,
        }
    )

    assert calls[0] == "manager"
    assert set(calls[1:3]) == {"weather", "news"}
    assert calls[-1] == "aggregate_llm"
    assert result["execution_mode"] == "parallel"
    assert result["selected_agents"] == ["weather", "news"]
    assert result["weather_answer"] == "Weather answer"
    assert result["news_answer"] == "News answer"
    assert "wiki_answer" not in result
    assert result["final_response"] == "Combined weather and news answer"
    assert result["cache_stats"]["weather"]["misses"] == 1
    assert result["cache_stats"]["news"]["misses"] == 1
    assert result["timings"]["manager"] >= 0
    assert result["timings"]["weather"] == 0.01
    assert result["timings"]["news"] == 0.02
    assert result["timings"]["aggregate"] >= 0
    assert "Weather answer" in aggregator_client.user_message
    assert "News answer" in aggregator_client.user_message


def test_graph_parallel_mode_stops_when_weather_agent_fails(monkeypatch) -> None:
    calls: list[str] = []

    class FakeAggregatorClient:
        def __init__(self) -> None:
            self.user_message = ""

        def chat_text(
            self,
            system_prompt: str,
            user_message: str,
            temperature: float = 0.2,
        ) -> str:
            calls.append("aggregate_llm")
            self.user_message = user_message
            assert temperature == 0.2
            assert "News answer" in user_message
            assert "weather failed" in user_message
            return "Partial combined answer"

    def fake_classify_intent(client, query: str, history=None) -> dict:
        calls.append("manager")
        return {
            "topics": ["weather", "news"],
            "execution_mode": "parallel",
            "primary_intent": "weather",
            "dependencies": [],
            "news_query": "Hanoi travel news",
            "wiki_topic": "",
        }

    def failing_weather_agent(state, *, store=None, settings=None, client=None) -> dict:
        calls.append("weather")
        raise RuntimeError("weather failed")

    def fake_news_agent(state, *, cache=None, settings=None, client=None) -> dict:
        calls.append("news")
        return {
            "news_data": {"articles": [{"title": "Travel update"}]},
            "news_answer": "News answer",
            "cache_stats": {"news": {"hits": 0, "misses": 1}},
            "timings": {"news": 0.02},
        }

    aggregator_client = FakeAggregatorClient()
    monkeypatch.setattr(graph, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(graph, "run_weather_llm_pipeline", failing_weather_agent)
    monkeypatch.setattr(graph, "run_news_agent", fake_news_agent)

    workflow = graph.build_workflow()
    result = workflow.invoke(
        {
            "query": "Hanoi weather and latest travel news",
            "history": [],
            "settings": _settings(),
            "manager_client": object(),
            "aggregator_client": aggregator_client,
        }
    )

    assert calls[0] == "manager"
    assert set(calls[1:3]) == {"weather", "news"}
    assert "aggregate_llm" not in calls
    assert result["weather_status"] == "error"
    assert "chưa thể xử lý yêu cầu thời tiết" in result["final_response"]
    assert result["news_answer"] == "News answer"
    assert "chưa thể xử lý yêu cầu thời tiết" in result["weather_answer"]
    assert result["errors"] == [{"source": "weather", "message": "weather failed"}]
    assert result["cache_stats"]["news"]["misses"] == 1
    assert result["timings"]["news"] == 0.02
    assert aggregator_client.user_message == ""


def test_graph_sequential_mode_passes_context_between_steps(monkeypatch) -> None:
    calls: list[str] = []

    class FakeAggregatorClient:
        def __init__(self) -> None:
            self.user_message = ""

        def chat_text(
            self,
            system_prompt: str,
            user_message: str,
            temperature: float = 0.2,
        ) -> str:
            calls.append("aggregate_llm")
            self.user_message = user_message
            assert temperature == 0.2
            return "Sequential combined answer"

    def fake_classify_intent(client, query: str, history=None) -> dict:
        calls.append("manager")
        assert query == "Use Hanoi weather, then background, then news"
        return {
            "topics": ["weather", "wiki", "news"],
            "execution_mode": "sequential",
            "primary_intent": "weather",
            "dependencies": [
                {
                    "from_topic": "weather",
                    "to_topic": "wiki",
                },
                {
                    "from_topic": "wiki",
                    "to_topic": "news",
                },
            ],
            "news_query": "Hanoi travel news",
            "wiki_topic": "Hanoi",
        }

    def fake_weather_agent(state, *, store=None, settings=None, client=None) -> dict:
        calls.append("weather")
        assert state.get("context", {}) == {}
        return {
            "weather_data": {"location": "Hanoi", "temperature": {"current_celsius": 31}},
            "weather_answer": "Weather answer",
            "cache_stats": {"weather": {"hits": 0, "misses": 1}},
            "timings": {"weather": 0.01},
        }

    def fake_wiki_agent(state, *, cache=None, settings=None, client=None) -> dict:
        calls.append("wiki")
        context = state["context"]
        assert context["weather_answer"] == "Weather answer"
        assert context["weather_data"]["location"] == "Hanoi"
        assert context["last_topic"] == "weather"
        return {
            "wiki_data": {"title": "Hanoi", "summary": "Capital of Vietnam"},
            "wiki_answer": "Wiki answer",
            "cache_stats": {"wiki": {"hits": 0, "misses": 1}},
            "timings": {"wiki": 0.02},
        }

    def fake_news_agent(state, *, cache=None, settings=None, client=None) -> dict:
        calls.append("news")
        context = state["context"]
        assert context["weather_answer"] == "Weather answer"
        assert context["wiki_answer"] == "Wiki answer"
        assert context["wiki_data"]["title"] == "Hanoi"
        assert context["last_topic"] == "wiki"
        return {
            "news_data": {"articles": [{"title": "Sequential update"}]},
            "news_answer": "News answer",
            "cache_stats": {"news": {"hits": 0, "misses": 1}},
            "timings": {"news": 0.03},
        }

    aggregator_client = FakeAggregatorClient()
    monkeypatch.setattr(graph, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(graph, "run_weather_llm_pipeline", fake_weather_agent)
    monkeypatch.setattr(graph, "run_wiki_agent", fake_wiki_agent)
    monkeypatch.setattr(graph, "run_news_agent", fake_news_agent)

    workflow = graph.build_workflow()
    result = workflow.invoke(
        {
            "query": "Use Hanoi weather, then background, then news",
            "history": [],
            "settings": _settings(),
            "manager_client": object(),
            "aggregator_client": aggregator_client,
        }
    )

    assert calls == ["manager", "weather", "wiki", "news", "aggregate_llm"]
    assert result["execution_mode"] == "sequential"
    assert result["selected_agents"] == ["weather", "wiki", "news"]
    assert result["weather_answer"] == "Weather answer"
    assert result["wiki_answer"] == "Wiki answer"
    assert result["news_answer"] == "News answer"
    assert result["context"]["weather_answer"] == "Weather answer"
    assert result["context"]["wiki_answer"] == "Wiki answer"
    assert result["context"]["news_answer"] == "News answer"
    assert result["context"]["last_topic"] == "news"
    assert result["final_response"] == "Sequential combined answer"
    assert result["cache_stats"]["weather"]["misses"] == 1
    assert result["cache_stats"]["wiki"]["misses"] == 1
    assert result["cache_stats"]["news"]["misses"] == 1
    assert result["timings"]["manager"] >= 0
    assert result["timings"]["weather"] == 0.01
    assert result["timings"]["wiki"] == 0.02
    assert result["timings"]["news"] == 0.03
    assert result["timings"]["aggregate"] >= 0
    assert "Weather answer" in aggregator_client.user_message
    assert "Wiki answer" in aggregator_client.user_message
    assert "News answer" in aggregator_client.user_message
