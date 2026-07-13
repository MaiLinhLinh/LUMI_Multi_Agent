"""Integration coverage for Semantic Router -> graph -> visualization flow."""

from __future__ import annotations

from rag_manager import graph
from rag_manager.visualization.orchestrator import VisualizationResult


DOMAIN_RESULT = {"weather_data": {"domain": "weather"}}


class FakeSemanticClient:
    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.messages = []

    def chat_json(self, system_prompt, user_message):
        self.messages.append(user_message)
        return self.responses.pop(0)


class FakeVisualizationOrchestrator:
    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        semantic = request.semantic_result or {}
        action = semantic.get("template", {}).get("action")
        needs_clarification = semantic.get("status") == "needs_clarification"
        return VisualizationResult(
            ok=not needs_clarification
            and (action == "design_template" or action == "select_existing"),
            mode=request.mode,
            template_id=request.template_id or "generated_template",
            html_path="D:/tmp/integration.html",
            available_templates=[{"id": "weather_basic"}],
            message="ok",
            errors=["missing_template_requirements"] if needs_clarification else [],
        )


def _semantic_result(
    action,
    *,
    status="ready",
    requirements=None,
    missing_information=None,
    clarifying_question=None,
    **template,
):
    return {
        "status": status,
        "route": "visualize",
        "domain_request": None,
        "template": {
            "action": action,
            "source": "none",
            "template_id": None,
            "selection_index": None,
            "requirements": requirements or {},
            "extracted_keywords": [],
            **template,
        },
        "missing_information": missing_information or [],
        "clarifying_question": clarifying_question
        or ("Bạn muốn dùng template nào?" if status == "needs_clarification" else None),
    }


def test_domain_semantic_route_runs_domain_workflow(monkeypatch):
    client = FakeSemanticClient(
        {
            "status": "ready",
            "route": "domain",
            "domain_request": "Thời tiết Hà Nội hôm nay",
            "template": {
                "action": None,
                "source": "none",
                "template_id": None,
                "selection_index": None,
                "requirements": {},
                "extracted_keywords": [],
            },
            "missing_information": [],
            "clarifying_question": None,
        }
    )
    manager_queries = []
    orchestrator = FakeVisualizationOrchestrator()

    def fake_manager(client, query):
        manager_queries.append(query)
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

    monkeypatch.setattr(graph, "classify_intent", fake_manager)
    monkeypatch.setattr(
        graph,
        "run_weather_agent",
        lambda state, **kwargs: {"weather_data": {"domain": "weather"}},
    )
    monkeypatch.setattr(
        graph,
        "run_aggregator_agent",
        lambda state, **kwargs: {"final_response": "answer"},
    )

    result = graph.build_workflow().invoke(
        {
            "query": "Cho tôi biết tình hình ở Hà Nội",
            "semantic_router_client": client,
            "manager_client": object(),
            "visualization_orchestrator": orchestrator,
        }
    )

    assert result["input_route"] == "domain"
    assert manager_queries == ["Thời tiết Hà Nội hôm nay"]
    assert orchestrator.requests[0].action == "auto_render"
    assert orchestrator.requests[0].semantic_result is None


def test_visualize_show_options_uses_orchestrator(monkeypatch):
    orchestrator = FakeVisualizationOrchestrator()
    result = graph.build_workflow().invoke(
        {
            "query": "Có những template nào?",
            "semantic_router_client": FakeSemanticClient(
                _semantic_result("show_options")
            ),
            "visualization_orchestrator": orchestrator,
            "last_domain_result": DOMAIN_RESULT,
        }
    )

    assert result["input_route"] == "visualize"
    assert orchestrator.requests[0].semantic_result["template"]["action"] == "show_options"


def test_visualize_select_existing_uses_orchestrator(monkeypatch):
    semantic = _semantic_result("select_existing", template_id="weather_basic")
    orchestrator = FakeVisualizationOrchestrator()
    graph.build_workflow().invoke(
        {
            "query": "Dùng weather_basic",
            "semantic_router_client": FakeSemanticClient(semantic),
            "visualization_orchestrator": orchestrator,
            "last_domain_result": DOMAIN_RESULT,
        }
    )

    assert orchestrator.requests[0].semantic_result["template"]["action"] == "select_existing"


def test_visualize_design_template_forwards_requirements(monkeypatch):
    semantic = _semantic_result(
        "design_template",
        requirements={"background_color": "pink"},
    )
    orchestrator = FakeVisualizationOrchestrator()
    graph.build_workflow().invoke(
        {
            "query": "Đổi nền template thành hồng",
            "semantic_router_client": FakeSemanticClient(semantic),
            "visualization_orchestrator": orchestrator,
            "last_domain_result": DOMAIN_RESULT,
        }
    )

    assert orchestrator.requests[0].semantic_result["template"]["requirements"] == {
        "background_color": "pink"
    }


def test_clarification_state_merges_across_turns():
    client = FakeSemanticClient(
        _semantic_result(
            "design_template",
            status="needs_clarification",
            requirements={"background_color": "pink"},
            missing_information=["base_template"],
        ),
        _semantic_result(
            "design_template",
            requirements={"base_template": "current"},
            source="current",
        ),
    )
    orchestrator = FakeVisualizationOrchestrator()
    workflow = graph.build_workflow()
    first = workflow.invoke(
        {
            "query": "Đổi nền thành hồng",
            "semantic_router_client": client,
            "visualization_orchestrator": orchestrator,
            "last_domain_result": DOMAIN_RESULT,
        }
    )

    assert first["pending_template_state"]["requirements"] == {
        "background_color": "pink"
    }

    second = workflow.invoke(
        {
            **first,
            "query": "Dùng template hiện tại",
        }
    )

    assert second["pending_template_state"] == {}
    assert orchestrator.requests[-1].semantic_result["template"]["requirements"] == {
        "background_color": "pink",
        "base_template": "current",
    }
