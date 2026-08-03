from __future__ import annotations

import json

from starlette.testclient import TestClient

import web_app


def test_chat_stream_emits_panel_before_presentation_contract(monkeypatch):
    def fake_execute(_key, _query, text_callback, presentation_callback):
        presentation_callback("panel_ready", {
            "ui_type": "weather", "template_id": "weather_basic", "html": "<main>weather</main>",
        })
        presentation_callback("presentation_contract", {
            "schema_version": "lumi.presentation_live_ctc.v1",
            "prebuffer_ms": 8000,
            "scenes": [{
                "narration": "Thoi tiet hom nay co mua.",
                "target_id": "weather.overview",
                "effect": "highlight",
                "gesture": "explain",
            }],
        })
        # The completed contract replaces legacy per-scene audio events. Keep
        # the text callback for clarification and non-presentation responses.
        assert callable(text_callback)
        return {
            "ok": True,
            "session_id": "stream-test",
            "messages": [{"role": "assistant", "content": "Thoi tiet hom nay co mua."}],
        }

    monkeypatch.setattr(web_app, "execute", fake_execute)
    with TestClient(web_app.app) as client:
        response = client.post("/api/chat/stream", json={"session_id": "stream-test", "query": "weather"})

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    event_types = [event["type"] for event in events]
    assert event_types == ["timing", "panel_ready", "timing", "presentation_contract", "final"]
    assert events[1]["panel"]["template_id"] == "weather_basic"
    assert events[2]["marker"] == "first_text_delta_sent"
    assert events[3]["contract"]["scenes"][0]["target_id"] == "weather.overview"
