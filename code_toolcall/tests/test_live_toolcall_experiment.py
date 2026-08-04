from rag_manager.live_toolcall_experiment.schemas import (
    ActiveAnimationCapabilities,
    ActivePresentationScenes,
    AnimationCommand,
)
from rag_manager.live_toolcall_experiment.weather_bridge import WeatherLiveBridge
from rag_manager.live_toolcall_experiment.session import _system_instruction
from rag_manager.presentation.schemas import GroundedFact


def test_runtime_capability_expands_template_target_pattern() -> None:
    capabilities = WeatherLiveBridge._capabilities(
        "weather_single_day",
        '<div data-present-id="weather.day.0.temperature"></div>'
        '<div data-present-id="weather.day.0.interval.3.rain_risk"></div>',
    )

    assert capabilities.allows(
        AnimationCommand(target_id="weather.day.0.interval.3.rain_risk", effect="draw_circle")
    )
    assert not capabilities.allows(
        AnimationCommand(target_id="weather.day.0.interval.3.rain_risk", effect="trace_line")
    )


def test_runtime_capability_rejects_undeclared_target() -> None:
    capabilities = ActiveAnimationCapabilities(
        template_id="weather_single_day",
        allowed={"weather.day.0.temperature": ["highlight"]},
    )

    assert not capabilities.allows(
        AnimationCommand(target_id="weather.day.0.rain_risk", effect="highlight")
    )


def test_live_fact_pack_binds_a_grounded_fact_to_its_single_visual_cue() -> None:
    bridge = WeatherLiveBridge(weather=None, visual=None)  # type: ignore[arg-type]
    facts = [
        GroundedFact(
            id="day_temperature_range",
            metric="temperature_max",
            operation="summary",
            value={"min_c": 25.4, "max_c": 31.6},
            unit="°C",
            entity={"day_index": 0},
            focus="temperature",
            effect_hint="draw_circle",
        )
    ]
    packed = bridge._live_fact_pack(
        facts,
        declared_capabilities={
            "temperature": {
                "target_id": "weather.day.0.temperature",
                "allowed_effects": ["highlight", "draw_arrow"],
            }
        },
        compact_data={"weather": {"days": [{}]}},
        capabilities=ActiveAnimationCapabilities(
            template_id="weather_single_day",
            allowed={"weather.day.0.temperature": ["highlight", "draw_arrow"]},
        ),
    )

    assert packed == [{
        "id": "day_temperature_range",
        "metric": "temperature_max",
        "operation": "summary",
        "value": {"min_c": 25.4, "max_c": 31.6},
        "unit": "°C",
        "entity": {"day_index": 0},
        "focus": "temperature",
        "effect_hint": "draw_circle",
        "visual_evidence": {},
        "visual_cue": {
            "target_id": "weather.day.0.temperature",
            "allowed_effects": ["highlight", "draw_arrow"],
        },
    }]


def test_live_scene_trigger_uses_only_compiler_approved_scene() -> None:
    scenes = ActivePresentationScenes(
        template_id="weather_single_day",
        scenes={
            "weather-scene-1": {
                "scene_id": "weather-scene-1",
                "narration": "Khả năng mưa hôm nay khá cao.",
                "target_id": "weather.day.0.rain_risk",
                "effect": "draw_circle",
                "actions": [],
            }
        },
    )

    response, scene = WeatherLiveBridge.trigger_scene(
        {"scene_id": "weather-scene-1"}, scenes, expected_scene_id="weather-scene-1"
    )

    assert response == {"status": "completed", "scene_id": "weather-scene-1"}
    assert scene is not None
    assert scene["target_id"] == "weather.day.0.rain_risk"
    rejected, scene = WeatherLiveBridge.trigger_scene(
        {"scene_id": "invented"}, scenes, expected_scene_id="weather-scene-1"
    )
    assert rejected["status"] == "rejected"
    assert scene is None
    rejected, scene = WeatherLiveBridge.trigger_scene(
        {"scene_id": "weather-scene-1"}, scenes, expected_scene_id=None
    )
    assert rejected["reason"] == "scene_not_next"
    assert scene is None


def test_live_session_memory_is_bounded_and_excludes_raw_snapshot() -> None:
    instruction = _system_instruction(
        [{"role": "user", "content": "Thời tiết Hà Nội một tuần tới thế nào?"}],
        {
            "weather": {
                "last_location_name": "Hà Nội",
                "last_days": 7,
                "session_snapshot": {"raw": "must not enter the Live prompt"},
            }
        },
    )

    assert "Thời tiết Hà Nội một tuần tới thế nào?" in instruction
    assert '"last_location_name":"Hà Nội"' in instruction
    assert "must not enter" not in instruction
