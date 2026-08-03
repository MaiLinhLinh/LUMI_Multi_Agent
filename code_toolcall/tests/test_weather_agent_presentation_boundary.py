from rag_manager.agents.weather_agent import run_weather


class FakeWeatherRuntime:
    def __init__(self):
        self.kwargs = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        return {
            "text": "",
            "tool_trace": [{
                "tool": "get_weather",
                "result": {
                    "status": "completed",
                    "data": {"location_id": "ha-noi", "location": "Hà Nội", "requested_days": 1},
                    "_llm_response": {"weather_facts": {"place": "Hà Nội"}},
                },
            }],
            "usage": [],
            "stream_timings": {},
            "completed_after_tool": "get_weather",
        }


def test_weather_agent_stops_after_completed_weather_tool_for_planner_handoff():
    runtime = FakeWeatherRuntime()
    result = run_weather(runtime, object(), "Thời tiết Hà Nội hôm nay")

    assert runtime.kwargs["stop_after_completed_tools"] == {"get_weather"}
    assert result["status"] == "completed"
    assert result["presentation_deferred"] is True
    assert result["weather_facts"] == {"place": "Hà Nội"}
    assert result["answer"].strip()


def test_weather_agent_keeps_legacy_final_text_turn_when_presentation_is_disabled():
    runtime = FakeWeatherRuntime()
    result = run_weather(runtime, object(), "weather", presentation_enabled=False)

    assert runtime.kwargs["stop_after_completed_tools"] is None
    assert result["presentation_deferred"] is False


def test_weather_agent_keeps_private_snapshot_in_session_context():
    runtime = FakeWeatherRuntime()
    runtime.run = lambda **_: {
        "text": "",
        "tool_trace": [{
            "tool": "get_weather",
            "result": {
                "status": "completed",
                "data": {
                    "location_id": "ha-noi",
                    "location": "HÃ  Ná»™i",
                    "requested_date": "2026-08-01",
                    "requested_days": 3,
                },
                "_session_snapshot": {"location_id": "ha-noi", "weather": {"days": []}},
                "_llm_response": {"weather_facts": {}},
            },
        }],
        "usage": [],
        "stream_timings": {},
    }

    result = run_weather(runtime, object(), "weather")

    assert result["weather_context"]["session_snapshot"]["location_id"] == "ha-noi"
