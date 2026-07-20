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


class StreamingFakeWorkflow:
    def invoke(self, state: dict) -> dict:
        callback = state["response_stream_callback"]
        callback("weather", "Hà Nội ")
        callback("weather", "30°C")
        return {
            "weather_status": "completed",
            "weather_answer": "Hà Nội 30°C",
            "final_response": "Hà Nội 30°C",
            "selected_agents": ["weather"],
            "timings": {},
            "llm_usage": {},
        }


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
            "final_response": "Hà Nội hôm nay có mây, nhiệt độ 30°C.",
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
        {
            "role": "user",
            "content": "Thời tiết Hà Nội hôm nay?",
            "domain": "weather",
        },
        {
            "role": "assistant",
            "content": "Hà Nội hôm nay có mây, nhiệt độ 30°C.",
            "domain": "weather",
        },
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


def _music_player(video_id: str = "FN7ALfpGxiI") -> dict:
    return {
        "schema_version": "music.youtube-player.v1",
        "status": "completed",
        "ui_type": "youtube_player",
        "player_action": "play",
        "music": {
            "source_id": f"youtube_{video_id}",
            "track_id": "track_noi_nay_co_anh",
            "title": "Nơi Này Có Anh",
            "artist": "Sơn Tùng M-TP",
            "artists": ["Sơn Tùng M-TP"],
            "video_id": video_id,
            "content_type": "official_mv",
            "version": "official MV",
        },
    }


def test_music_result_uses_shared_left_panel_and_keeps_bot_answer() -> None:
    player = _music_player()
    service, _ = _service(
        {
            "music_status": "completed",
            "music_answer": "Đây là bài “Nơi Này Có Anh” của Sơn Tùng M-TP.",
            "final_response": "Đây là bài “Nơi Này Có Anh” của Sơn Tùng M-TP.",
            "music_player": player,
            "selected_agents": ["music"],
            "timings": {},
            "llm_usage": {},
        }
    )

    payload = service.chat("music-panel", "Bật bài Nơi Này Có Anh")

    assert payload["has_active_panel"] is True
    assert payload["active_panel"] == player
    assert payload["active_panel_revision"] == 1
    assert payload["visualization_html"] == ""
    assert payload["messages"][-1]["content"] == (
        "Đây là bài “Nơi Này Có Anh” của Sơn Tùng M-TP."
    )


def test_music_panel_is_preserved_after_social_turn() -> None:
    player = _music_player()
    service, _ = _service(
        {
            "music_status": "completed",
            "music_answer": "Đây là bài hát bạn yêu cầu.",
            "final_response": "Đây là bài hát bạn yêu cầu.",
            "music_player": player,
            "selected_agents": ["music"],
            "timings": {},
            "llm_usage": {},
        },
        {
            "final_response": "Rất vui được giúp bạn!",
            "selected_agents": ["wiki"],
            "timings": {},
            "llm_usage": {},
        },
    )

    first = service.chat("music-social", "Bật nhạc")
    social = service.chat("music-social", "Cảm ơn bạn")

    assert social["active_panel"] == first["active_panel"]
    assert social["active_panel_revision"] == first["active_panel_revision"]
    assert social["messages"][-1]["content"] == "Rất vui được giúp bạn!"


def test_music_clarification_keeps_player_despite_stale_weather_output(
    tmp_path: Path,
) -> None:
    stale_weather_path = tmp_path / "stale-weather.html"
    stale_weather_path.write_text("<html>Old weather</html>", encoding="utf-8")
    player = _music_player()
    service, _ = _service(
        {
            "music_status": "completed",
            "music_answer": "Đây là bài hát bạn yêu cầu.",
            "final_response": "Đây là bài hát bạn yêu cầu.",
            "music_player": player,
            "selected_agents": ["music"],
            "timings": {},
            "llm_usage": {},
        },
        {
            "music_status": "needs_clarification",
            "music_answer": "Bạn muốn nghe bài nào tiếp theo?",
            "final_response": "Bạn muốn nghe bài nào tiếp theo?",
            "selected_agents": ["music"],
            "visualization_output": {
                "ok": True,
                "html_path": str(stale_weather_path),
            },
            "timings": {},
            "llm_usage": {},
        },
    )

    first = service.chat("music-clarification-panel", "Bật nhạc")
    clarification = service.chat(
        "music-clarification-panel",
        "Chuyển bài tiếp theo",
    )

    assert clarification["active_panel"] == first["active_panel"] == player
    assert clarification["active_panel_revision"] == 1


def test_music_replaces_weather_in_the_same_left_panel(tmp_path: Path) -> None:
    html_path = tmp_path / "weather.html"
    html_path.write_text("<html>Weather</html>", encoding="utf-8")
    player = _music_player()
    service, _ = _service(
        {
            "weather_status": "completed",
            "visualization_html_path": str(html_path),
            "selected_agents": ["weather"],
            "timings": {},
            "llm_usage": {},
        },
        {
            "music_status": "completed",
            "music_answer": "Đây là bài hát bạn yêu cầu.",
            "final_response": "Đây là bài hát bạn yêu cầu.",
            "music_player": player,
            "selected_agents": ["music"],
            "timings": {},
            "llm_usage": {},
        },
    )

    weather = service.chat("shared-panel", "Thời tiết Hà Nội")
    music = service.chat("shared-panel", "Bật nhạc")

    assert weather["active_panel"]["ui_type"] == "weather"
    assert music["active_panel"]["ui_type"] == "youtube_player"
    assert music["active_panel_revision"] == 2
    assert music["visualization_html"] == ""


def test_weather_replaces_music_in_the_same_left_panel(tmp_path: Path) -> None:
    html_path = tmp_path / "weather.html"
    html_path.write_text("<html>Hà Nội 30°C</html>", encoding="utf-8")
    player = _music_player()
    service, _ = _service(
        {
            "music_status": "completed",
            "music_answer": "Đây là bài hát bạn yêu cầu.",
            "final_response": "Đây là bài hát bạn yêu cầu.",
            "music_player": player,
            "selected_agents": ["music"],
            "timings": {},
            "llm_usage": {},
        },
        {
            "weather_status": "completed",
            "visualization_html_path": str(html_path),
            "selected_agents": ["weather"],
            "timings": {},
            "llm_usage": {},
        },
    )

    service.chat("reverse-shared-panel", "Bật nhạc")
    weather = service.chat("reverse-shared-panel", "Thời tiết Hà Nội")

    assert weather["active_panel"]["ui_type"] == "weather"
    assert "Hà Nội 30°C" in weather["active_panel"]["html"]
    assert weather["active_panel_revision"] == 2


def test_clear_session_removes_shared_panel_and_player_state() -> None:
    player = _music_player()
    service, _ = _service(
        {
            "music_status": "completed",
            "music_answer": "Đây là bài hát bạn yêu cầu.",
            "final_response": "Đây là bài hát bạn yêu cầu.",
            "music_player": player,
            "music_session": {"current_source_id": "youtube_FN7ALfpGxiI"},
            "selected_agents": ["music"],
            "timings": {},
            "llm_usage": {},
        }
    )

    service.chat("clear-music-panel", "Bật nhạc")
    cleared = service.clear("clear-music-panel")

    assert cleared["messages"] == []
    assert cleared["has_active_panel"] is False
    assert cleared["active_panel"] == {}
    assert cleared["active_panel_revision"] == 0


def test_web_rejects_malformed_music_player_update() -> None:
    player = _music_player(video_id="invalid")
    service, _ = _service(
        {
            "music_status": "completed",
            "final_response": "Không thể phát.",
            "music_player": player,
            "selected_agents": ["music"],
            "timings": {},
            "llm_usage": {},
        }
    )

    payload = service.chat("invalid-player", "Bật nhạc")

    assert payload["has_active_panel"] is False
    assert payload["active_panel"] == {}


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


def test_web_session_carries_music_state_to_follow_up_turn() -> None:
    music_session = {
        "schema_version": "music.session.v1",
        "last_candidate_ids": ["youtube_1", "youtube_2"],
        "last_candidates": [
            {"record_id": "youtube_1", "video_id": "abcdefghijk"},
            {"record_id": "youtube_2", "video_id": "lmnopqrstuv"},
        ],
    }
    music_player = {
        "schema_version": "music.youtube-player.v1",
        "status": "completed",
        "ui_type": "youtube_player",
        "player_action": "play",
        "music": {"video_id": "abcdefghijk"},
    }
    service, workflow = _service(
        {
            "music_status": "needs_clarification",
            "music_answer": "Bạn muốn chọn bài nào?",
            "final_response": "Bạn muốn chọn bài nào?",
            "music_session": music_session,
            "music_player": music_player,
            "selected_agents": ["music"],
            "timings": {},
            "llm_usage": {},
        },
        {
            "music_status": "completed",
            "music_answer": "Đây là bài thứ hai.",
            "final_response": "Đây là bài thứ hai.",
            "music_session": music_session,
            "music_player": music_player,
            "selected_agents": ["music"],
            "timings": {},
            "llm_usage": {},
        },
    )

    service.chat("music-session", "Bật nhạc Sơn Tùng")
    service.chat("music-session", "Bài thứ hai")

    assert workflow.states[1]["music_session"] == music_session
    assert workflow.states[1]["music_player"] == music_player


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
        script = await client.get("/assets/app.js")
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
    assert script.status_code == 200
    assert 'id="contentPanel"' in homepage.text
    assert 'id="weatherView"' in homepage.text
    assert 'id="weatherFrame"' in homepage.text
    assert 'scrolling="no"' in homepage.text
    assert 'id="musicView"' in homepage.text
    assert 'id="musicFrame"' in homepage.text
    assert "https://www.youtube-nocookie.com" in script.text
    assert "https://www.youtube.com/embed" not in script.text
    assert ".src = youtubeEmbedUrl" in script.text
    assert ".innerHTML" not in script.text
    assert "height: 100dvh" in stylesheet.text
    assert "#weatherFrame { width: 100%; height: 100%; min-height: 0" in stylesheet.text
    assert ".workspace.has-dashboard {\n    flex: 1 1 0;\n    height: 100%;" in stylesheet.text
    assert "grid-template-rows: minmax(0, 1fr);" in stylesheet.text
    assert ".has-dashboard .dashboard-panel,\n  .has-dashboard .chat-panel {" in stylesheet.text
    assert "height: 100%;" in stylesheet.text
    assert "max-height: none;" in stylesheet.text
    assert ".has-dashboard .dashboard-panel {\n    height: calc(100% + 110px);" in stylesheet.text
    assert "overflow-y: auto;" in stylesheet.text
    assert "weatherFrame.srcdoc = panel.html;" in script.text
    assert chat.json()["messages"][-1]["content"] == "Xin chào!"
    assert cleared.json()["messages"] == []


@pytest.mark.asyncio
async def test_web_chat_stream_emits_deltas_before_final_session(monkeypatch) -> None:
    service = web_app.WebChatService(
        settings=SimpleNamespace(has_gemini_key=True),
        workflow=StreamingFakeWorkflow(),
    )
    monkeypatch.setattr(web_app, "_service", service)

    transport = httpx.ASGITransport(app=web_app.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={"session_id": "stream-session", "query": "Thời tiết Hà Nội"},
        )

    events = [web_app.json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == [
        "text_delta",
        "text_delta",
        "final",
    ]
    assert "".join(event["delta"] for event in events[:-1]) == "Hà Nội 30°C"
    assert events[-1]["payload"]["messages"][-1]["content"] == "Hà Nội 30°C"
