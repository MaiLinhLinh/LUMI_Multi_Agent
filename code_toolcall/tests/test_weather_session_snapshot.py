from __future__ import annotations

import time

from rag_manager.tools.weather_tools import WeatherTools


class FakeResolver:
    def resolve(self, _: str):
        return {"ok": True, "location_id": "ha-noi", "canonical_name": "Hà Nội"}


class FakeStore:
    def __init__(self, weather):
        self.weather = weather
        self.forecast_calls = []
        self.current_calls = []

    def get_forecast(self, location_id, *, days, start_date):
        self.forecast_calls.append((location_id, days, start_date))
        return {"ok": True, "data": self.weather}

    def get_current(self, location_id):
        self.current_calls.append(location_id)
        return {"ok": True, "data": {"current": {}}}


def _weather(days=3):
    values = []
    for index in range(days):
        day = index + 1
        values.append({
            "date": f"2026-08-0{day}",
            "condition": {"description": "Mưa rào"},
            "temperature": {"min_celsius": 25, "max_celsius": 31},
            "max_rain_probability": 80,
            "intervals": [
                {"forecast_at_local": f"2026-08-0{day}T12:00:00+07:00", "temperature_celsius": 30},
                {"forecast_at_local": f"2026-08-0{day}T13:00:00+07:00", "temperature_celsius": 31},
            ],
        })
    return {"location": "Hà Nội", "days": values}


def _tools(weather):
    tools = WeatherTools.__new__(WeatherTools)
    tools.store = FakeStore(weather)
    tools.resolver = FakeResolver()
    tools.session_snapshot_ttl_seconds = 600
    return tools


def test_follow_up_uses_covered_session_snapshot_without_redis():
    tools = _tools(_weather())
    first = tools.get_weather({
        "location_text": "Hà Nội", "request_type": "forecast", "date_text": "2026-08-01", "days": 3,
    })

    second = tools.get_weather({
        "location_text": "Hà Nội", "request_type": "forecast", "date_text": "2026-08-02", "days": 1,
    }, weather_context={"session_snapshot": first["_session_snapshot"]})

    assert len(tools.store.forecast_calls) == 1
    assert second["status"] == "completed"
    assert second["data"]["source"] == "session_weather_snapshot"
    assert [day["date"] for day in second["data"]["weather"]["days"]] == ["2026-08-02"]
    assert len(second["_session_snapshot"]["weather"]["days"]) == 3


def test_snapshot_miss_for_uncovered_or_expired_range_uses_redis():
    tools = _tools(_weather())
    first = tools.get_weather({
        "location_text": "Hà Nội", "request_type": "forecast", "date_text": "2026-08-01", "days": 3,
    })
    snapshot = first["_session_snapshot"]

    tools.get_weather({
        "location_text": "Hà Nội", "request_type": "forecast", "date_text": "2026-08-04", "days": 1,
    }, weather_context={"session_snapshot": snapshot})
    assert len(tools.store.forecast_calls) == 2

    snapshot["expires_at_epoch"] = time.time() - 1
    tools.get_weather({
        "location_text": "Hà Nội", "request_type": "forecast", "date_text": "2026-08-02", "days": 1,
    }, weather_context={"session_snapshot": snapshot})
    assert len(tools.store.forecast_calls) == 3


def test_hourly_follow_up_keeps_full_day_intervals_in_snapshot():
    tools = _tools(_weather())
    first = tools.get_weather({
        "location_text": "Hà Nội", "request_type": "forecast", "date_text": "2026-08-01", "days": 3,
    })

    hourly = tools.get_weather({
        "location_text": "Hà Nội", "request_type": "hourly", "date_text": "2026-08-02", "days": 1, "time_text": "12:00",
    }, weather_context={"session_snapshot": first["_session_snapshot"]})

    assert len(tools.store.forecast_calls) == 1
    assert hourly["data"]["source"] == "session_weather_snapshot"
    assert len(hourly["data"]["weather"]["days"][0]["intervals"]) == 1
    assert len(hourly["_session_snapshot"]["weather"]["days"][0]["intervals"]) == 2
