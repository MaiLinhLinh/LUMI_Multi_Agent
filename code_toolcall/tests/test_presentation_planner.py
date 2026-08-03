import json

from rag_manager.presentation.planner import plan_presentation, presentation_plan_json_schema
from rag_manager.presentation.schemas import GroundedFact, PresentationPlan, PresentationStep


FACT = GroundedFact(id="rain_peak", metric="rain_probability", operation="argmax", value=96, unit="%", entity={"day_index": 0}, focus="rain_risk", effect_hint="draw_circle")
CAPABILITIES = {"overview": {"target_id": "weather.overview", "allowed_effects": ["highlight"]}, "rain_risk": {"target_id": "weather.day.0.rain_risk", "allowed_effects": ["draw_circle"]}}


class Runtime:
    def __init__(self, payloads): self.payloads = list(payloads); self.calls = []
    def generate_structured(self, **kwargs):
        self.calls.append(kwargs); payload = self.payloads.pop(0)
        callback = kwargs.get("on_json_chunk")
        if callback: callback(json.dumps(payload))
        return {"data": payload, "usage": {"stage": "planner"}}


def call(runtime, received=None):
    return plan_presentation(
        runtime, query="Khi nao mua nhieu nhat?", history=[], template_id="weather_forecast", capabilities=CAPABILITIES,
        grounded_facts=[FACT], system_instruction="json", on_valid_step=(received.append if received is not None else None),
        fallback_plan=lambda: PresentationPlan(steps=[PresentationStep(narration="Fallback.", fact_id="rain_peak", effect="draw_circle")]),
    )


def test_runtime_schema_allows_only_fact_ids_and_not_dom_fields():
    step = presentation_plan_json_schema(CAPABILITIES, [FACT])["$defs"]["PresentationStep"]
    assert step["properties"]["fact_id"]["enum"] == ["rain_peak"]
    assert "focus" not in step["properties"]
    assert "entity" not in step["properties"]


def test_planner_streams_a_valid_fact_selection():
    received = []
    result = call(Runtime([{"steps": [{"narration": "Mua cao nhat la 96 phan tram.", "fact_id": "rain_peak", "effect": "draw_circle"}]}]), received)
    assert result["fallback"] is False
    assert [step.fact_id for step in received] == ["rain_peak"]


def test_planner_retries_unknown_fact_and_uses_valid_replacement():
    runtime = Runtime([
        {"steps": [{"narration": "Sai.", "fact_id": "other", "effect": "draw_circle"}]},
        {"steps": [{"narration": "Dung.", "fact_id": "rain_peak", "effect": "draw_circle"}]},
    ])
    result = call(runtime)
    assert result["retried"] is True
    assert result["plan"].steps[0].fact_id == "rain_peak"
