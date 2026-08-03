from rag_manager.presentation.domains.weather import WeatherPresentationAdapter
from rag_manager.presentation.registry import PresentationRegistry


def test_registry_returns_only_registered_domain_adapter():
    adapter = PresentationRegistry.with_weather().get("weather")
    assert isinstance(adapter, WeatherPresentationAdapter)
    assert PresentationRegistry.with_weather().get("music") is None


def test_weather_adapter_creates_multi_day_facts_with_visual_evidence():
    facts = WeatherPresentationAdapter().build_candidate_facts(
        {"days": [
            {"date": "2026-08-01", "condition": "rain", "min_c": 24, "max_c": 28, "rain_max_pct": 60},
            {"date": "2026-08-02", "condition": "rain", "min_c": 25, "max_c": 30, "rain_max_pct": 70},
            {"date": "2026-08-03", "condition": "storm", "min_c": 25, "max_c": 31, "rain_max_pct": 80},
        ]}, compact_data={}, presentation_capabilities={},
    )
    by_id = {fact.id: fact for fact in facts}
    assert {"period_rain_probability_peak", "period_temperature_peak", "period_condition_groups"} <= set(by_id)
    assert by_id["period_condition_groups"].visual_evidence["groups"][0]["day_indices"] == [0, 1]
    assert by_id["period_temperature_trend"].visual_evidence["kind"] == "chart_segment"


def test_weather_adapter_creates_hourly_candidates_for_a_single_day():
    facts = WeatherPresentationAdapter().build_candidate_facts(
        {"days": [{"date": "2026-08-01", "condition": "Rain", "min_c": 24, "max_c": 31, "rain_max_pct": 80}]},
        compact_data={"weather": {"days": [{"intervals": [
            {"time": "09:00", "temperature_celsius": 27, "rain_probability": .4},
            {"time": "14:00", "temperature_celsius": 31, "rain_probability": .8},
        ]}]}},
        presentation_capabilities={"day_summary": {}, "temperature": {}, "rain_risk": {}, "hourly_rain_risk": {}, "hourly_temperature": {}},
    )
    by_id = {fact.id: fact for fact in facts}
    assert by_id["hourly_rain_probability_peak"].entity["interval_index"] == 1
    assert by_id["hourly_temperature_peak"].value == 31.0
