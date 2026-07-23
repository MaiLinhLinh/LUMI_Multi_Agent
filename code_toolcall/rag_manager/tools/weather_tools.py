from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from rag_manager.config import Settings
from rag_manager.services.weather_location_resolver import get_weather_location_resolver
from rag_manager.services.weather_redis import RedisWeatherStore
from rag_manager.tools.registry import declarations

WEATHER_DECLARATION = declarations("weather")[0]

class WeatherTools:
    def __init__(self, settings: Settings) -> None:
        self.store = RedisWeatherStore.from_settings(settings)
        self.resolver = get_weather_location_resolver()

    def get_weather(self, args: dict[str, Any], *, weather_context: dict[str, Any] | None = None) -> dict[str, Any]:
        location_text = str(args.get("location_text", "")).strip()
        context = weather_context if isinstance(weather_context, dict) else {}
        if not location_text:
            location_text = str(context.get("last_location_name", "")).strip()
        resolved = self.resolver.resolve(location_text)
        if not resolved.get("ok"):
            return {"status":"needs_clarification", "clarification":{"field":"location", "question":"Bạn muốn xem thời tiết ở tỉnh/thành nào?"}, "data": resolved}
        # The schema requires request_type. This fallback only protects an
        # already-running session against a malformed model/API response.
        request_type = str(args.get("request_type", "")).strip().lower()
        if not request_type:
            request_type = "forecast" if args.get("date_text") or args.get("days") else "current"
        requested = self._date(str(args.get("date_text", "")))
        requested_days = self._days(args.get("days", 1))
        requested_time = self._time(str(args.get("time_text", "")))
        if request_type == "hourly" and requested_time is None:
            return {
                "status": "needs_clarification",
                "clarification": {"field": "time", "question": "Bạn muốn xem thời tiết vào mấy giờ?"},
                "data": {},
            }
        if request_type == "current" and not args.get("date_text"):
            raw = self.store.get_current(resolved["location_id"])
        else:
            raw = self.store.get_forecast(resolved["location_id"], days=requested_days, start_date=requested)
        if not raw.get("ok"):
            return {"status":"unavailable", "error":{"code":raw.get("code", "weather_unavailable"), "message":raw.get("message", "Không có snapshot thời tiết khả dụng.")}, "data":raw}
        weather_payload = raw.get("data", raw)
        if request_type == "hourly":
            weather_payload = self._select_hourly_forecast(weather_payload, requested_time)
            if weather_payload is None:
                return {
                    "status": "unavailable",
                    "error": {"code": "forecast_time_unavailable", "message": "Dữ liệu không có dự báo cho khung giờ đã chọn."},
                    "data": {},
                }
        data = {
            "location": resolved.get("canonical_name", ""),
            "location_id": resolved["location_id"],
            "request_type": request_type,
            "requested_date": requested,
            "requested_days": requested_days,
            "weather": weather_payload,
            "source": "redis_weather_snapshot",
        }
        # `data` remains available to the graph/Visual node.  Gemini receives
        # only daily facts, never the 24 hourly records per day.
        return {"status": "completed", "data": data, "_llm_response": {"status": "completed", "weather_facts": self._llm_facts(data)}}

    @staticmethod
    def _date(value: str) -> str:
        text = value.strip().casefold()
        if text in {"", "hôm nay", "hom nay", "today", "hiện tại", "hien tai"}: return date.today().isoformat()
        if text in {"ngày mai", "ngay mai", "tomorrow"}: return (date.today()+timedelta(days=1)).isoformat()
        try: return date.fromisoformat(text).isoformat()
        except ValueError: return date.today().isoformat()

    @staticmethod
    def _days(value: Any) -> int:
        try:
            return max(1, min(int(value), 8))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _time(value: str) -> str | None:
        try:
            return datetime.strptime(value.strip(), "%H:%M").strftime("%H:%M")
        except ValueError:
            return None

    @staticmethod
    def _select_hourly_forecast(value: Any, requested_time: str | None) -> dict[str, Any] | None:
        """Return one verified Redis hourly interval for the requested clock time."""
        if not isinstance(value, dict) or not requested_time:
            return None
        requested_hour, requested_minute = (int(part) for part in requested_time.split(":"))
        for raw_day in value.get("days", []) if isinstance(value.get("days"), list) else []:
            if not isinstance(raw_day, dict):
                continue
            for interval in raw_day.get("intervals", []) if isinstance(raw_day.get("intervals"), list) else []:
                if not isinstance(interval, dict):
                    continue
                timestamp = interval.get("forecast_at_local")
                if not isinstance(timestamp, str):
                    continue
                try:
                    interval_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if interval_time.hour != requested_hour:
                    continue
                selected_day = dict(raw_day)
                selected_day["intervals"] = [interval]
                selected_day["is_partial_day"] = True
                selected = dict(value)
                selected["days"] = [selected_day]
                selected["hourly_selection"] = {
                    "requested_time_of_day": requested_time,
                    "matched_interval_start_time": interval_time.strftime("%H:%M"),
                    "resolution_minutes": 60,
                    "minute_offset_within_interval": requested_minute,
                }
                return selected
        return None

    @staticmethod
    def _llm_facts(data: dict[str, Any]) -> dict[str, Any]:
        """Port of the old weather agent's compact daily-facts contract."""
        weather = data.get("weather") if isinstance(data.get("weather"), dict) else {}
        selection = weather.get("hourly_selection")
        if isinstance(selection, dict):
            day = next((item for item in weather.get("days", []) if isinstance(item, dict)), {})
            interval = next((item for item in day.get("intervals", []) if isinstance(item, dict)), {})
            condition = interval.get("condition") if isinstance(interval.get("condition"), dict) else {}
            return {
                "kind": "hourly_forecast",
                "place": data.get("location") or weather.get("location"),
                "date": day.get("date"),
                "requested_time": selection.get("requested_time_of_day"),
                "matched_interval_start": selection.get("matched_interval_start_time"),
                "condition": condition.get("description") or condition.get("main"),
                "temp_c": interval.get("temperature_celsius"),
                "feels_c": interval.get("feels_like_celsius"),
                "humidity_pct": interval.get("humidity_percent"),
                "rain_pct": interval.get("rain_probability"),
                "rain_mm": interval.get("rain_1h_mm"),
                "wind_ms": interval.get("wind_speed_mps"),
            }
        days: list[dict[str, Any]] = []
        for raw_day in weather.get("days", []) if isinstance(weather.get("days"), list) else []:
            if not isinstance(raw_day, dict):
                continue
            temperature = raw_day.get("temperature") if isinstance(raw_day.get("temperature"), dict) else {}
            condition = raw_day.get("condition") if isinstance(raw_day.get("condition"), dict) else {}
            probability = raw_day.get("max_rain_probability")
            if isinstance(probability, (int, float)) and not isinstance(probability, bool):
                probability = round(probability * 100 if 0 <= probability <= 1 else probability, 1)
            # A multi-day overview needs only the fields that let the model
            # accurately compare days.  Preserve the richer one-day contract
            # for questions about a specific day.
            if int(data.get("requested_days", 1) or 1) > 1:
                facts = {
                    "date": raw_day.get("date"),
                    "condition": condition.get("description") or condition.get("main"),
                    "min_c": temperature.get("min_celsius"),
                    "max_c": temperature.get("max_celsius"),
                    "rain_max_pct": probability,
                    "rain_total_mm": raw_day.get("total_rain_mm"),
                }
            else:
                facts = {
                "date": raw_day.get("date"),
                "condition": condition.get("description") or condition.get("main"),
                "conditions": raw_day.get("common_conditions"),
                "min_c": temperature.get("min_celsius"),
                "max_c": temperature.get("max_celsius"),
                "max_feels_c": raw_day.get("temperature_feels_like_celsius"),
                "rain_max_pct": probability,
                "rain_total_mm": raw_day.get("total_rain_mm"),
                "humidity_avg_pct": raw_day.get("humidity_percent"),
                "pressure_avg_hpa": raw_day.get("pressure_hpa"),
                "wind_avg_ms": raw_day.get("wind_speed_mps"),
                "partial_day": raw_day.get("is_partial_day"),
                }
            days.append({key: value for key, value in facts.items() if value not in (None, "", [], {})})
        return {
            "kind": "daily_forecast" if days else "current_weather",
            "place": data.get("location") or weather.get("location"),
            "requested_start_date": data.get("requested_date"),
            "requested_days": data.get("requested_days"),
            "days": days,
        }
