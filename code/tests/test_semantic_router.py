from rag_manager.semantic_router import (
    analyze_input,
    is_explicit_visualization_query,
    is_high_confidence_domain_query,
)
from rag_manager.graph import input_router_node
from rag_manager.visualization.orchestrator import (
    VisualizationOrchestrator,
    VisualizationRequest,
)


def test_high_confidence_domain_detector_is_conservative() -> None:
    assert is_high_confidence_domain_query("Thời tiết Hà Nội hôm nay") is True
    assert is_high_confidence_domain_query(
        "Dùng giao diện hiện tại nhưng đổi nền hồng"
    ) is False


def test_semantic_router_returns_validated_design_result() -> None:
    class FakeClient:
        def chat_json(self, system_prompt, user_message):
            return {
                "status": "ready",
                "route": "visualize",
                "domain_request": None,
                "template": {
                    "action": "design_template",
                    "source": "current",
                    "template_id": None,
                    "selection_index": None,
                    "requirements": {"background_color": "light_pink"},
                    "extracted_keywords": ["light_pink"],
                },
                "missing_information": [],
                "clarifying_question": None,
            }

    result = analyze_input(FakeClient(), query="đổi nền hiện tại sang hồng nhạt")

    assert result["route"] == "visualize"
    assert result["template"]["action"] == "design_template"
    assert result["template"]["requirements"]["background_color"] == "light_pink"


def test_orchestrator_consumes_semantic_result_without_reinterpreting() -> None:
    class FakeTemplateAgent:
        llm = object()

        def __init__(self):
            self.requests = []

        def run(self, request):
            self.requests.append(request)
            return {
                "ok": True,
                "mode": request.mode,
                "template_id": "generated_template",
                "message": "Generated.",
            }

    workflow = FakeTemplateAgent()
    semantic_result = {
        "status": "ready",
        "route": "visualize",
        "template": {
            "action": "design_template",
            "source": "current",
            "template_id": None,
            "selection_index": None,
            "requirements": {"background_color": "light_pink"},
            "extracted_keywords": ["light_pink"],
        },
        "missing_information": [],
        "clarifying_question": None,
    }

    result = VisualizationOrchestrator(template_agent_workflow=workflow).run(
        VisualizationRequest(
            domain_result={"weather_data": {"domain": "weather"}},
            user_request="đổi nền hồng",
            semantic_result=semantic_result,
        )
    )

    assert result.ok is True
    assert workflow.requests[0].requirements == {"background_color": "light_pink"}
    assert workflow.requests[0].semantic_result == semantic_result


def test_input_router_persists_pending_template_state() -> None:
    class FakeClient:
        def chat_json(self, system_prompt, user_message):
            return {
                "status": "needs_clarification",
                "route": "visualize",
                "template": {
                    "action": "design_template",
                    "source": "none",
                    "template_id": None,
                    "selection_index": None,
                    "requirements": {"background_color": "pink"},
                    "extracted_keywords": ["pink"],
                },
                "missing_information": ["base_template"],
                "clarifying_question": "Bạn muốn dùng template nào?",
            }

    result = input_router_node(
        {
            "query": "đổi nền sang hồng",
            "history": [],
            "semantic_router_client": FakeClient(),
            "available_templates": [],
        }
    )

    pending = result["pending_template_state"]
    assert pending["status"] == "collecting_requirements"
    assert pending["requirements"] == {"background_color": "pink"}
    assert pending["missing_information"] == ["base_template"]
    assert result["template_requirements"] == {"background_color": "pink"}


def test_input_router_merges_pending_requirements_and_clears_when_ready() -> None:
    class FakeClient:
        def chat_json(self, system_prompt, user_message):
            return {
                "status": "ready",
                "route": "visualize",
                "template": {
                    "action": "design_template",
                    "source": "current",
                    "template_id": None,
                    "selection_index": None,
                    "requirements": {"base_template": "current"},
                    "extracted_keywords": ["current"],
                },
                "missing_information": [],
                "clarifying_question": None,
            }

    result = input_router_node(
        {
            "query": "dùng template hiện tại",
            "history": [],
            "semantic_router_client": FakeClient(),
            "pending_template_state": {
                "status": "collecting_requirements",
                "requirements": {"background_color": "pink"},
                "missing_information": ["base_template"],
                "clarification_round": 1,
            },
            "available_templates": [],
        }
    )

    assert result["pending_template_state"] == {}
    assert result["template_requirements"] == {}
    assert result["semantic_result"]["template"]["requirements"] == {
        "background_color": "pink",
        "base_template": "current",
    }


def test_domain_tokens_alone_are_not_high_confidence() -> None:
    for query in ("weather", "news", "forecast", "wikipedia"):
        assert is_high_confidence_domain_query(query) is False


def test_domain_ui_requests_always_go_to_semantic_router() -> None:
    queries = (
        "create weather template",
        "change news dashboard layout",
        "use current weather dashboard",
        "change forecast to pink layout",
    )
    assert all(not is_high_confidence_domain_query(query) for query in queries)


def test_complete_domain_patterns_can_bypass_llm() -> None:
    queries = (
        "weather in Hanoi today",
        "weather forecast for Hanoi",
        "latest news about technology",
        "who is Albert Einstein",
        "Bật nhạc Sơn Tùng",
        "Cho tôi nghe nhạc thư giãn",
        "Mở bài Chúng ta của tương lai",
        "Play song Shape of You",
    )
    assert all(is_high_confidence_domain_query(query) for query in queries)


def test_active_weather_followup_bypasses_semantic_visualization_router() -> None:
    class UnexpectedClient:
        def chat_json(self, system_prompt, user_message):
            raise AssertionError("Semantic Router must not run for a Weather follow-up")

    result = input_router_node(
        {
            "query": "cả tuần đi",
            "history": [
                {
                    "role": "assistant",
                    "content": "Ngày mai tại Đà Nẵng có mưa phùn.",
                    "domain": "weather",
                    "workflow_id": "weather_1",
                },
                {"role": "user", "content": "cả tuần đi"},
            ],
            "weather_session": {
                "active": True,
                "workflow_id": "weather_1",
            },
            "semantic_router_client": UnexpectedClient(),
        }
    )

    assert result["input_route"] == "domain"
    assert result["visualization_request"] == {
        "mode": "auto",
        "action": "auto_render",
    }
    assert result["semantic_result"] == {}


def test_active_weather_does_not_hide_explicit_visualization_request() -> None:
    assert is_explicit_visualization_query("đổi giao diện thời tiết") is True

    class FakeClient:
        def chat_json(self, system_prompt, user_message):
            return {
                "status": "ready",
                "route": "visualize",
                "domain_request": None,
                "template": {
                    "action": "design_template",
                    "source": "current",
                    "template_id": None,
                    "selection_index": None,
                    "requirements": {},
                    "extracted_keywords": [],
                },
                "missing_information": [],
                "clarifying_question": None,
            }

    result = input_router_node(
        {
            "query": "đổi giao diện thời tiết",
            "history": [],
            "weather_session": {"active": True},
            "semantic_router_client": FakeClient(),
        }
    )

    assert result["input_route"] == "visualize"
    assert result["semantic_result"]["template"]["action"] == "design_template"
