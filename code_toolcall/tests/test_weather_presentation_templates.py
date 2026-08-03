import json
import re
from pathlib import Path

import pytest

from rag_manager.tools.visual_tools import VisualTools


TEMPLATE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "rag_manager"
    / "visualization"
    / "assets"
    / "templates"
    / "weather"
)
PRESENT_ID_RE = re.compile(r'data-present-id="([^"]+)"')


def weather_day(index: int) -> dict:
    return {
        "date": f"2026-08-{index + 1:02d}",
        "condition": {"main": "Rain", "description": "light rain"},
        "temperature": {"min_celsius": 25 + index, "max_celsius": 31 + index},
        "max_rain_probability": 0.6,
        "total_rain_mm": 4.0,
        "humidity_percent": 78,
        "wind_speed_mps": 3.5,
        "pressure_hpa": 1007,
        "intervals": [
            {
                "time": "09:00",
                "temperature_celsius": 28,
                "rain_probability": 0.3,
                "condition": {"main": "Clouds", "description": "cloudy"},
            },
            {
                "time": "15:00",
                "temperature_celsius": 31,
                "rain_probability": 0.6,
                "condition": {"main": "Rain", "description": "light rain"},
            },
        ],
    }


def render_template(template_id: str) -> str:
    day_count = {"weather_basic": 1, "weather_single_day": 1, "weather_forecast": 2}[template_id]
    result = VisualTools().render_visualization(
        {"template_id": template_id},
        {
            "location": "Ha Noi",
            "requested_days": day_count,
            "weather": {
                "current": {
                    "condition": {"main": "Clouds", "description": "cloudy"},
                    "temperature": {"current_celsius": 30, "feels_like_celsius": 33},
                    "humidity_percent": 70,
                    "pressure_hpa": 1008,
                    "wind": {"speed_mps": 3.4},
                },
                "days": [weather_day(index) for index in range(day_count)],
            },
        },
    )
    assert result["status"] == "completed", result
    return result["data"]["html"]


@pytest.mark.parametrize("template_id", ["weather_basic", "weather_single_day", "weather_forecast"])
def test_presentation_capability_targets_exist_after_render(template_id: str):
    metadata = json.loads((TEMPLATE_ROOT / template_id / "metadata.json").read_text(encoding="utf-8"))
    rendered_ids = set(PRESENT_ID_RE.findall(render_template(template_id)))

    assert metadata["presentation_capabilities"]
    for capability in metadata["presentation_capabilities"].values():
        target = (
            capability.get("target_id")
            or capability["target_pattern"]
            .replace("{day_index}", "0")
            .replace("{interval_index}", "0")
        )
        assert target in rendered_ids
        assert capability["allowed_effects"]


def test_forecast_template_declares_chart_trace_and_point_anchors():
    metadata = json.loads((TEMPLATE_ROOT / "weather_forecast" / "metadata.json").read_text(encoding="utf-8"))
    rendered_ids = set(PRESENT_ID_RE.findall(render_template("weather_forecast")))

    assert "trace_line" in metadata["presentation_capabilities"]["temperature_trend"]["allowed_effects"]
    assert "weather.temperature_trend.line" in rendered_ids
    assert "weather.temperature_trend.point.0" in rendered_ids
    assert "weather.week.rain_pattern" in rendered_ids
    assert "weekly_rain_pattern" in metadata["presentation_capabilities"]
