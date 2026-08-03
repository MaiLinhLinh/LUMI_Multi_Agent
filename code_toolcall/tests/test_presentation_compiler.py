from rag_manager.presentation.compiler import compile_presentation_plan
from rag_manager.presentation.domains.weather import WeatherPresentationAdapter
from rag_manager.presentation.schemas import GroundedFact, PresentationPlan


def test_compiler_derives_target_only_from_selected_fact():
    fact = GroundedFact(id="rain", metric="rain_probability", operation="argmax", value=96, unit="%", entity={"day_index": 1}, focus="rain_risk", effect_hint="draw_circle")
    plan = PresentationPlan.model_validate({"steps": [{"narration": "Rain.", "fact_id": "rain", "effect": "draw_circle"}]})
    compiled = compile_presentation_plan(
        plan,
        template_metadata={"presentation_capabilities": {"overview": {"target_id": "weather.overview", "allowed_effects": ["highlight"]}, "rain_risk": {"target_pattern": "weather.day.{day_index}.rain_risk", "entity_fields": ["day_index"], "allowed_effects": ["draw_circle"]}}},
        compact_data={"weather": {"days": [{}, {}]}}, target_resolver=WeatherPresentationAdapter().resolve_target, grounded_facts=[fact],
    )
    assert compiled.steps[0].target_id == "weather.day.1.rain_risk"
