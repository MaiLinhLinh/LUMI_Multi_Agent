from __future__ import annotations

from gemini_live.routing import SemanticRouter


class _ManagerRuntimeStub:
    def __init__(self, domain_id: str) -> None:
        self.domain_id = domain_id
        self.calls: list[dict[str, object]] = []

    def generate_structured(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"data": {"domain_id": self.domain_id}}


def test_clear_weather_marker_bypasses_manager() -> None:
    runtime = _ManagerRuntimeStub("education")
    router = SemanticRouter(runtime=runtime, domain_ids=("weather", "education"))

    result = router.route(query="Thời tiết Hà Nội ngày mai thế nào?")

    assert result.domain_id == "weather"
    assert runtime.calls == []


def test_clear_education_marker_bypasses_manager() -> None:
    runtime = _ManagerRuntimeStub("weather")
    router = SemanticRouter(runtime=runtime, domain_ids=("weather", "education"))

    result = router.route(query="Cho con một phép cộng trong phạm vi 10")

    assert result.domain_id == "education"
    assert runtime.calls == []


def test_ambiguous_follow_up_is_sent_to_manager_with_four_recent_turns() -> None:
    runtime = _ManagerRuntimeStub("education")
    router = SemanticRouter(runtime=runtime, domain_ids=("weather", "education"))
    history = [
        {"role": "user", "content": f"turn {index}"}
        for index in range(6)
    ]

    result = router.route(query="bằng năm", history=history)

    assert result.domain_id == "education"
    assert len(runtime.calls) == 1
    user_text = str(runtime.calls[0]["user_text"])
    assert "turn 0" not in user_text
    assert "turn 2" in user_text
