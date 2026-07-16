from datetime import datetime, timedelta

from rag_manager.services import open_meteo_api


def _open_meteo_payload(*, hour_count: int = 24) -> dict:
    start = datetime(2026, 7, 16)
    times = [
        (start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M")
        for index in range(hour_count)
    ]
    return {
        "latitude": 21.03,
        "longitude": 105.88,
        "utc_offset_seconds": 25200,
        "timezone": "Asia/Bangkok",
        "current_units": {},
        "current": {
            "time": "2026-07-16T13:45",
            "interval": 900,
            "temperature_2m": 31.5,
            "apparent_temperature": 36.2,
            "relative_humidity_2m": 70,
            "surface_pressure": 1001.2,
            "weather_code": 61,
            "cloud_cover": 80,
            "wind_speed_10m": 2.5,
            "wind_direction_10m": 135,
        },
        "hourly_units": {},
        "hourly": {
            "time": times,
            "temperature_2m": [30.0] * hour_count,
            "apparent_temperature": [34.0] * hour_count,
            "relative_humidity_2m": [75] * hour_count,
            "surface_pressure": [1002.0] * hour_count,
            "precipitation_probability": [40] * hour_count,
            "rain": [0.1] * hour_count,
            "weather_code": [61] * hour_count,
            "cloud_cover": [85] * hour_count,
            "wind_speed_10m": [3.0] * hour_count,
            "wind_direction_10m": [140] * hour_count,
        },
    }


def test_fetch_open_meteo_weather_requests_hourly_data_without_api_key(
    monkeypatch,
    capsys,
) -> None:
    captured = {}

    def fake_get_json(url, *, source, params, timeout_seconds):
        captured.update(
            {
                "url": url,
                "source": source,
                "params": params,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {"ok": True, "data": _open_meteo_payload()}

    monkeypatch.setattr(open_meteo_api, "get_json", fake_get_json)

    response = open_meteo_api.fetch_open_meteo_weather(
        latitude=21.0283334,
        longitude=105.854041,
        timeout_seconds=9,
    )

    assert response["ok"] is True
    assert captured["url"] == "https://api.open-meteo.com/v1/forecast"
    assert captured["source"] == "open-meteo"
    assert captured["timeout_seconds"] == 9
    assert captured["params"]["timezone"] == "auto"
    assert captured["params"]["forecast_days"] == 6
    assert "appid" not in captured["params"]
    assert "temperature_2m" in captured["params"]["hourly"]

    current = response["data"]["current"]
    forecast = response["data"]["forecast"]
    assert current["observed_at_local"] == "2026-07-16T13:45:00+07:00"
    assert current["observed_at_utc"] == "2026-07-16T06:45:00+00:00"
    assert current["condition"]["description"] == "mưa"
    assert forecast["source_granularity"] == "1-hour forecast intervals"
    assert forecast["interval_count"] == 24
    assert forecast["days"][0]["is_partial_day"] is False
    assert forecast["days"][0]["total_rain_mm"] == 2.4
    first_interval = forecast["days"][0]["intervals"][0]
    assert first_interval["forecast_at_local"] == "2026-07-16T00:00:00+07:00"
    assert first_interval["forecast_at_utc"] == "2026-07-15T17:00:00+00:00"
    assert first_interval["rain_probability"] == 0.4
    assert first_interval["rain_1h_mm"] == 0.1
    terminal_output = capsys.readouterr().out
    assert "[Open-Meteo][RAW_API_RESPONSE]" in terminal_output
    assert "latitude=21.0283334 longitude=105.854041" in terminal_output


def test_open_meteo_rejects_misaligned_hourly_arrays(monkeypatch) -> None:
    payload = _open_meteo_payload()
    payload["hourly"]["rain"] = [0.1]
    monkeypatch.setattr(
        open_meteo_api,
        "get_json",
        lambda *args, **kwargs: {"ok": True, "data": payload},
    )

    response = open_meteo_api.fetch_open_meteo_weather(
        latitude=21.0283334,
        longitude=105.854041,
    )

    assert response["ok"] is False
    assert response["error"]["source"] == "open-meteo"
    assert "hourly.rain" in response["error"]["message"]


def test_open_meteo_rejects_invalid_coordinates_without_http_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        open_meteo_api,
        "get_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP must not be called")
        ),
    )

    response = open_meteo_api.fetch_open_meteo_weather(
        latitude=100,
        longitude=105.854041,
    )

    assert response["ok"] is False
    assert response["error"]["message"] == "Weather latitude is invalid."
