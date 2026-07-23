from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from rag_manager.tools.registry import declarations

ASSETS = Path(__file__).resolve().parents[1] / "visualization" / "assets" / "templates"
VISUAL_DECLARATION = declarations("visual")[0]
_YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class VisualTools:
    """Code-only render tools for existing, trusted UI templates."""

    def select_weather_template(self, data: dict[str, Any]) -> str:
        # Same value-aware rule as the original project's visual orchestrator:
        # a one-day forecast uses the hourly timeline card; an hourly point or
        # current conditions use the basic card; several days use the forecast
        # template.  The code_toolcall weather payload stores forecast fields
        # directly under `weather`, while the older envelope nests them under
        # `weather.forecast`, so accept both contracts.
        weather = data.get("weather", {})
        weather = weather if isinstance(weather, dict) else {}
        forecast = weather.get("forecast", weather)
        forecast = forecast if isinstance(forecast, dict) else {}
        if isinstance(forecast.get("hourly_selection"), dict):
            return "weather_basic"
        days = forecast.get("days", [])
        if isinstance(days, list) and len(days) == 1:
            return "weather_single_day"
        if isinstance(days, list) and len(days) > 1:
            return "weather_forecast"
        return "weather_basic"

    def compact_weather_data(self, value: dict[str, Any]) -> dict[str, Any]:
        """Keep only the requested weather day; never forward a full snapshot."""
        value = value if isinstance(value, dict) else {}
        raw = value.get("weather", value)
        raw = raw if isinstance(raw, dict) else {}
        requested_date = value.get("requested_date")
        requested_days = value.get("requested_days", 1)
        try:
            requested_days = max(1, min(int(requested_days), 8))
        except (TypeError, ValueError):
            requested_days = 1
        forecast = dict(raw)
        source_days = raw.get("days", [])
        selected_days: list[dict[str, Any]] = []
        if isinstance(source_days, list):
            valid_days = [day for day in source_days if isinstance(day, dict)]
            selected_days = [
                day for day in valid_days
                if not requested_date or day.get("date") == requested_date
            ][:requested_days]
            if not selected_days:
                selected_days = valid_days[:requested_days]
            elif requested_days > 1:
                start_index = next(
                    (index for index, day in enumerate(valid_days) if day.get("date") == selected_days[0].get("date")),
                    0,
                )
                selected_days = valid_days[start_index:start_index + requested_days]

        compact_days = [self._compact_day(day) for day in selected_days]
        forecast["days"] = compact_days
        forecast.pop("interval_count", None)
        forecast.pop("available_day_count", None)
        forecast.pop("raw", None)
        return {
            "location": value.get("location") or raw.get("location") or "Thời tiết",
            "location_id": value.get("location_id"),
            "request_type": value.get("request_type"),
            "requested_date": requested_date,
            "weather": forecast,
            "source": value.get("source", "redis_weather_snapshot"),
        }

    @staticmethod
    def _compact_day(day: dict[str, Any]) -> dict[str, Any]:
        intervals = day.get("intervals", [])
        safe_intervals = [item for item in intervals if isinstance(item, dict)][:24] if isinstance(intervals, list) else []
        return {
            key: day.get(key)
            for key in (
                "date", "condition", "temperature", "temperature_feels_like_celsius",
                "total_rain_mm", "max_rain_probability", "humidity_percent",
                "wind_speed_mps", "pressure_hpa", "source_granularity",
            )
        } | {"intervals": safe_intervals}

    def render_visualization(self, args: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        template_id = str(args.get("template_id", "weather_basic"))
        path = next(iter(ASSETS.glob(f"weather/{template_id}/template.html")), None)
        if path is None:
            return {"status": "error", "error": {"code": "template_not_registered"}}
        try:
            contract = _weather_contract(data)
            environment = Environment(
                loader=FileSystemLoader(str(path.parent)),
                undefined=StrictUndefined,
                autoescape=True,
            )
            html = environment.get_template("template.html").render(
                data=contract, weather=contract, payload=contract, answer=""
            )
        except Exception as exc:
            return {"status": "error", "error": {"code": "render_failed", "message": str(exc)}}
        return {
            "status": "completed",
            "data": {"ui_type": "weather", "template_id": template_id, "html": html},
        }

    def render_music_player(self, player_payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a tool-produced video id and hand it to the fixed frontend iframe."""
        music = player_payload.get("music") if isinstance(player_payload, dict) else None
        video_id = music.get("video_id") if isinstance(music, dict) else None
        if (
            not isinstance(player_payload, dict)
            or player_payload.get("ui_type") != "youtube_player"
            or player_payload.get("player_action") not in {"play", "replay", "stop"}
            or not isinstance(video_id, str)
            or not _YOUTUBE_VIDEO_ID.fullmatch(video_id)
        ):
            return {"status": "error", "error": {"code": "invalid_music_player_payload"}}
        return {"status": "completed", "data": player_payload}


def _weather_contract(value: dict[str, Any]) -> dict[str, Any]:
    raw = value.get("weather", value) if isinstance(value, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    current = raw.get("current", raw)
    forecast = raw.get("forecast", raw) if isinstance(raw, dict) else {}
    selection = forecast.get("hourly_selection") if isinstance(forecast, dict) else None
    presentation = {"mode": "weather", "time_label": value.get("requested_date", ""), "interval_notice": ""}
    if isinstance(selection, dict):
        selected_day = next((item for item in forecast.get("days", []) if isinstance(item, dict)), {})
        interval = next((item for item in selected_day.get("intervals", []) if isinstance(item, dict)), {})
        current = {
            "condition": interval.get("condition", {"main": "Chưa rõ", "description": ""}),
            "temperature": {
                "current_celsius": interval.get("temperature_celsius", "—"),
                "feels_like_celsius": interval.get("feels_like_celsius"),
            },
            "humidity_percent": interval.get("humidity_percent", "—"),
            "pressure_hpa": interval.get("pressure_hpa", "—"),
            "wind": {"speed_mps": interval.get("wind_speed_mps", "—")},
            "cloudiness_percent": interval.get("cloudiness_percent", "—"),
        }
        presentation = {
            "mode": "hourly_forecast",
            "time_label": f"Dự báo lúc {selection.get('requested_time_of_day', '')} ngày {selected_day.get('date', value.get('requested_date', ''))}",
            "interval_notice": "Dữ liệu theo khung giờ đã chọn.",
        }
    location = {
        "name": value.get("location") or raw.get("location") or "Thời tiết",
        "country": raw.get("country", "Việt Nam"),
    }
    return {
        "location": location,
        "current": {
            "condition": current.get("condition", {"main": "Chưa rõ", "description": ""}),
            "temperature": current.get("temperature", {"current_celsius": current.get("temperature_celsius", "—"), "feels_like_celsius": None}),
            "humidity_percent": current.get("humidity_percent", "—"),
            "pressure_hpa": current.get("pressure_hpa", "—"),
            "wind": current.get("wind", {"speed_mps": current.get("wind_speed_mps", "—")}),
            "cloudiness_percent": current.get("cloudiness_percent", "—"),
        },
        "forecast": {"source_granularity": forecast.get("source_granularity", "snapshot"), "days": forecast.get("days", [])},
        "presentation": presentation,
        "source": {"provider": "Redis weather snapshot"},
    }
