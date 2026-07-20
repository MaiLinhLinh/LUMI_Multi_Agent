from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import web_app


class FakeWorkflow:
    def __init__(self, results: list[dict]) -> None:
        self.results = list(results)
        self.states: list[dict] = []

    def invoke(self, state: dict) -> dict:
        self.states.append(state)
        return self.results.pop(0)


def _service(*results: dict) -> tuple[web_app.WebChatService, FakeWorkflow]:
    workflow = FakeWorkflow(list(results))
    service = web_app.WebChatService(
        settings=SimpleNamespace(has_gemini_key=True),
        workflow=workflow,
    )
    return service, workflow


def test_weather_dashboard_is_preserved_after_social_turn(tmp_path: Path) -> None:
    html_path = tmp_path / "weather.html"
    html_path.write_text("<html><body>Hà Nội 30°C</body></html>", encoding="utf-8")
    service, workflow = _service(
        {
            "weather_status": "completed",
            "selected_agents": ["weather"],
            "visualization_html_path": str(html_path),
            "timings": {},
            "llm_usage": {},
        },
        {
            "final_response": "Xin chào! Tôi vẫn ở đây để hỗ trợ bạn.",
            # A carried session path must not be mistaken for a new render.
            "visualization_html_path": str(tmp_path / "stale.html"),
            "selected_agents": [],
            "timings": {},
            "llm_usage": {},
        },
    )

    weather_payload = service.chat("session-1", "Thời tiết Hà Nội hôm nay?")
    social_payload = service.chat("session-1", "Cảm ơn bạn")

    assert weather_payload["has_visualization"] is True
    assert "Hà Nội 30°C" in weather_payload["visualization_html"]
    assert social_payload["visualization_html"] == weather_payload["visualization_html"]
    assert social_payload["messages"][-1]["content"] == (
        "Xin chào! Tôi vẫn ở đây để hỗ trợ bạn."
    )
    assert workflow.states[1]["history"] == [
        {"role": "user", "content": "Thời tiết Hà Nội hôm nay?"},
        {"role": "user", "content": "Cảm ơn bạn"},
    ]


def test_new_weather_result_replaces_previous_dashboard(tmp_path: Path) -> None:
    first_path = tmp_path / "first.html"
    second_path = tmp_path / "second.html"
    first_path.write_text("<html>Hà Nội</html>", encoding="utf-8")
    second_path.write_text("<html>Đà Nẵng</html>", encoding="utf-8")
    service, _ = _service(
        {"weather_status": "completed", "visualization_html_path": str(first_path)},
        {"weather_status": "completed", "visualization_html_path": str(second_path)},
    )

    service.chat("session-2", "Thời tiết Hà Nội?")
    payload = service.chat("session-2", "Thế thì Đà Nẵng?")

    assert "Đà Nẵng" in payload["visualization_html"]
    assert "Hà Nội" not in payload["visualization_html"]


def test_clarification_uses_text_reply_and_keeps_dashboard(tmp_path: Path) -> None:
    html_path = tmp_path / "weather.html"
    html_path.write_text("<html>Weather</html>", encoding="utf-8")
    service, _ = _service(
        {"weather_status": "completed", "visualization_html_path": str(html_path)},
        {
            "weather_status": "needs_clarification",
            "final_response": "Bạn muốn xem thời tiết vào ngày nào?",
        },
    )

    service.chat("session-3", "Thời tiết Hà Nội")
    payload = service.chat("session-3", "Tôi chưa rõ")

    assert payload["has_visualization"] is True
    assert payload["messages"][-1]["content"] == "Bạn muốn xem thời tiết vào ngày nào?"


def test_terminal_metrics_include_topics_timings_and_usage(capsys) -> None:
    web_app._print_terminal_metrics(
        {
            "selected_agents": ["weather"],
            "timings": {"manager": 3.9},
            "llm_usage": {
                "manager": {
                    "model": "gemma-4-26b-a4b-it",
                    "prompt_tokens": 378,
                    "completion_tokens": 38,
                    "total_tokens": 416,
                    "time_to_first_token": 0.915768,
                    "time_to_first_visible": 0.915768,
                    "time_to_last_visible": 1.589584,
                    "visible_generation_duration": 0.673816,
                    "total_request_time": 2.448752,
                }
            },
        }
    )

    output = capsys.readouterr().out
    assert "[WEB][WORKFLOW_METRICS]" in output
    assert "Topics: ['weather']" in output
    assert "Timings: {'manager': 3.9}" in output
    assert "LLM usage [manager]" in output
    assert "prompt_tokens: 378" in output
    assert "time_to_first_token: 0.915768s" in output


@pytest.mark.asyncio
async def test_web_routes_serve_interface_and_clear_session(monkeypatch) -> None:
    service, _ = _service({"final_response": "Xin chào!"})
    monkeypatch.setattr(web_app, "_service", service)

    transport = httpx.ASGITransport(app=web_app.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        homepage = await client.get("/")
        stylesheet = await client.get("/assets/app.css")
        chat = await client.post(
            "/api/chat",
            json={"session_id": "browser-session", "query": "Xin chào"},
        )
        cleared = await client.post(
            "/api/session/clear",
            json={"session_id": "browser-session"},
        )

    assert homepage.status_code == 200
    assert "Trợ lí ảo chatbot" in homepage.text
    assert stylesheet.status_code == 200
    assert chat.json()["messages"][-1]["content"] == "Xin chào!"
    assert cleared.json()["messages"] == []
