from rag_manager import graph
from rag_manager.config import Settings
from rag_manager.visualization.orchestrator import VisualizationResult


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


def _weather_envelope() -> dict:
    return {
        "domain": "weather",
        "schema_version": "weather.current.v1",
        "data_type": "current",
        "location": "Ha Noi",
        "data": {
            "location": {"name": "Ha Noi", "country": "VN"},
            "current": {"temperature": {"current_celsius": 30}},
            "forecast": None,
        },
        "source": {"provider": "openweathermap", "tools_used": []},
        "available_fields": [
            "location.name",
            "location.country",
            "current.temperature.current_celsius",
        ],
    }


class FakeVisualizationOrchestrator:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return VisualizationResult(
            ok=True,
            mode=request.mode,
            template_id=request.template_id or "weather_basic",
            html="<html>weather</html>",
            html_path="D:/tmp/weather.html",
            available_templates=[
                {"id": "weather_basic", "score": 110},
                {"id": "weather_alt", "score": 105},
            ],
            message="rendered",
        )


def test_domain_question_runs_manager_weather_aggregate_then_visualize(monkeypatch) -> None:
    calls: list[str] = []

    def fake_classify_intent(client, query: str) -> dict:
        calls.append("manager")
        return {
            "topics": ["weather"],
            "execution_mode": "single",
            "primary_intent": "weather",
            "dependencies": [],
            "location": "Ha Noi",
            "news_query": "",
            "wiki_topic": "",
            "reason": "weather",
        }

    def fake_weather_agent(state, *, cache=None, settings=None, client=None) -> dict:
        calls.append("weather")
        return {
            "weather_data": _weather_envelope(),
            "weather_answer": "Weather answer",
        }

    def fake_aggregator_agent(state, *, settings=None, client=None) -> dict:
        calls.append("aggregate")
        return {"final_response": "Final weather answer"}

    orchestrator = FakeVisualizationOrchestrator()
    monkeypatch.setattr(graph, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(graph, "run_weather_agent", fake_weather_agent)
    monkeypatch.setattr(graph, "run_aggregator_agent", fake_aggregator_agent)

    result = graph.build_workflow().invoke(
        {
            "query": "Thoi tiet Ha Noi hom nay",
            "settings": _settings(),
            "manager_client": object(),
            "visualization_orchestrator": orchestrator,
        }
    )

    assert calls == ["manager", "weather", "aggregate"]
    assert len(orchestrator.requests) == 1
    request = orchestrator.requests[0]
    assert request.mode == "auto"
    assert request.domain_result["weather_data"]["schema_version"] == "weather.current.v1"
    assert result["last_domain_result"]["weather_answer"] == "Weather answer"
    assert result["visualization_output"]["template_id"] == "weather_basic"
    assert result["visualization_html_path"] == "D:/tmp/weather.html"


def test_choose_template_followup_routes_directly_to_visualize(monkeypatch) -> None:
    def unexpected_manager(*args, **kwargs):
        raise AssertionError("Visualization follow-up must not go through Manager Agent")

    orchestrator = FakeVisualizationOrchestrator()
    monkeypatch.setattr(graph, "classify_intent", unexpected_manager)

    result = graph.build_workflow().invoke(
        {
            "query": "chọn mẫu 2",
            "settings": _settings(),
            "manager_client": object(),
            "last_domain_result": {
                "weather_data": _weather_envelope(),
                "weather_answer": "Weather answer",
            },
            "available_templates": [
                {"id": "weather_basic", "score": 110},
                {"id": "weather_alt", "score": 105},
            ],
            "visualization_orchestrator": orchestrator,
        }
    )

    assert result["input_route"] == "visualize"
    assert len(orchestrator.requests) == 1
    request = orchestrator.requests[0]
    assert request.mode == "auto"
    assert request.template_id is None
    assert request.action == "semantic_request"
    assert request.domain_result["weather_answer"] == "Weather answer"
    assert result["visualization_output"]["template_id"] == "weather_basic"


def test_change_template_request_enters_visualization_request(monkeypatch) -> None:
    orchestrator = FakeVisualizationOrchestrator()
    result = graph.build_workflow().invoke(
        {
            "query": "Tôi muốn đổi template",
            "settings": _settings(),
            "manager_client": object(),
            "last_domain_result": {"weather_data": _weather_envelope()},
            "available_templates": [{"id": "weather_basic"}],
            "visualization_orchestrator": orchestrator,
        }
    )

    assert result["input_route"] == "visualize"
    assert orchestrator.requests[0].mode == "auto"
    assert orchestrator.requests[0].template_id is None


def test_natural_language_current_template_request_routes_to_visualization(monkeypatch) -> None:
    orchestrator = FakeVisualizationOrchestrator()
    result = graph.build_workflow().invoke(
        {
            "query": "tôi muốn template hiện tại nhưng đổi thành nền màu hồng nhạt",
            "settings": _settings(),
            "manager_client": object(),
            "last_domain_result": {"weather_data": _weather_envelope()},
            "available_templates": [{"id": "weather_basic"}],
            "visualization_orchestrator": orchestrator,
        }
    )

    assert result["input_route"] == "visualize"
    assert orchestrator.requests[0].mode == "auto"
    assert orchestrator.requests[0].template_id is None


def test_bare_number_followup_selects_listed_template_without_manager(monkeypatch) -> None:
    def unexpected_manager(*args, **kwargs):
        raise AssertionError("Template number follow-up must not go through Manager Agent")

    orchestrator = FakeVisualizationOrchestrator()
    monkeypatch.setattr(graph, "classify_intent", unexpected_manager)

    result = graph.build_workflow().invoke(
        {
            "query": "2",
            "settings": _settings(),
            "manager_client": object(),
            "last_domain_result": {"weather_data": _weather_envelope()},
            "available_templates": [
                {"id": "weather_basic", "score": 110},
                {"id": "weather_alt", "score": 105},
            ],
            "visualization_orchestrator": orchestrator,
        }
    )

    assert result["input_route"] == "visualize"
    assert orchestrator.requests[0].mode == "auto"
    assert orchestrator.requests[0].template_id is None
    assert orchestrator.requests[0].action == "semantic_request"


def test_template_list_create_option_routes_to_create_flow(monkeypatch) -> None:
    def unexpected_manager(*args, **kwargs):
        raise AssertionError("Template selection must not go through Manager Agent")

    orchestrator = FakeVisualizationOrchestrator()
    monkeypatch.setattr(graph, "classify_intent", unexpected_manager)

    result = graph.build_workflow().invoke(
        {
            "query": "3",
            "settings": _settings(),
            "manager_client": object(),
            "last_domain_result": {"weather_data": _weather_envelope()},
            "available_templates": [
                {"id": "weather_basic", "score": 110},
                {"id": "weather_alt", "score": 105},
                {"id": "__create_new_template__", "type": "create_new_template"},
            ],
            "visualization_orchestrator": orchestrator,
        }
    )

    assert result["input_route"] == "visualize"
    assert orchestrator.requests[0].mode == "auto"
    assert orchestrator.requests[0].template_id is None


def test_template_selection_with_modification_routes_to_customize(monkeypatch) -> None:
    def unexpected_manager(*args, **kwargs):
        raise AssertionError("Template customization must not go through Manager Agent")

    orchestrator = FakeVisualizationOrchestrator()
    monkeypatch.setattr(graph, "classify_intent", unexpected_manager)

    result = graph.build_workflow().invoke(
        {
            "query": "chon mau 2 doi nen mau hong",
            "settings": _settings(),
            "manager_client": object(),
            "last_domain_result": {"weather_data": _weather_envelope()},
            "available_templates": [
                {"id": "weather_basic", "score": 110},
                {"id": "weather_alt", "score": 105},
            ],
            "visualization_orchestrator": orchestrator,
        }
    )

    assert result["input_route"] == "visualize"
    assert orchestrator.requests[0].mode == "auto"
    assert orchestrator.requests[0].template_id is None
    assert orchestrator.requests[0].action == "semantic_request"


def test_choose_template_without_last_domain_result_returns_helpful_message(monkeypatch) -> None:
    def unexpected_manager(*args, **kwargs):
        raise AssertionError("Visualization follow-up must not go through Manager Agent")

    monkeypatch.setattr(graph, "classify_intent", unexpected_manager)

    result = graph.build_workflow().invoke(
        {
            "query": "chon mau 2",
            "settings": _settings(),
            "manager_client": object(),
        }
    )

    assert result["input_route"] == "visualize"
    assert result["visualization_output"]["ok"] is False
    assert result["visualization_output"]["errors"] == ["missing_template_requirements"]
    assert "dữ liệu" in result["visualization_output"]["message"]
