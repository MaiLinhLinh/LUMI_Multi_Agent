from __future__ import annotations

import rag_manager.graph as graph_module
from rag_manager.config import Settings
from rag_manager.presentation.domains.weather import WeatherPresentationAdapter
from rag_manager.presentation.registry import PresentationRegistry
from rag_manager.presentation.schemas import PresentationPlan, PresentationStep


class FakeAppRuntime:
    def __init__(self, settings):
        self.llm = object(); self.weather = object(); self.visual = object(); self.music = object()
        self.presentation_enabled = settings.presentation_enabled
        self.presentation_registry = PresentationRegistry.with_weather()


def test_graph_compiles_fact_selected_by_planner(monkeypatch):
    monkeypatch.setattr(graph_module, "AppRuntime", FakeAppRuntime)
    monkeypatch.setattr(graph_module, "router_node", lambda _state: {"route": "domain", "selected_agent": "weather"})
    monkeypatch.setattr(graph_module, "run_weather", lambda *_args, **_kwargs: {"answer": "fallback", "status": "completed", "weather_facts": {"days": [{"date": "2026-08-01", "condition": "rain", "min_c": 24, "max_c": 30, "rain_max_pct": 80}]}, "weather_context": {}, "llm_usage": [], "stream_timings": {}, "tool_trace": []})
    monkeypatch.setattr(graph_module, "run_visual", lambda *_args, **_kwargs: {"payload": {}, "tool_trace": [], "presentation_context": {"domain_id": "weather", "template_id": "weather_single_day", "compact_data": {"weather": {"days": [{"intervals": []}]}}}})
    def planner(_adapter, _runtime, **kwargs):
        fact = kwargs["grounded_facts"][0]
        step = PresentationStep(narration="Rain today.", fact_id=fact.id, effect="highlight")
        kwargs["on_valid_step"](step)
        return {"plan": PresentationPlan(steps=[step]), "usage": {}, "fallback": False}
    monkeypatch.setattr(WeatherPresentationAdapter, "plan", planner)
    result = graph_module.build_workflow(Settings(gemini_api_key="test", gemini_model="test")).invoke({"query": "weather", "history": []})
    assert result["final_answer"] == "Rain today."
    assert result["compiled_presentation_plan"]["steps"][0]["target_id"] == "weather.day.0.summary"
