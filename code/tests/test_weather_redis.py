import json
from datetime import datetime, timezone

import pytest

from rag_manager.config import Settings
from rag_manager.services import weather_snapshot_worker
from rag_manager.services.weather_redis import (
    RedisWeatherStore,
    WEATHER_SNAPSHOT_SCHEMA_VERSION,
    WeatherSnapshotEntry,
    location_slug,
)
from rag_manager.services.weather_snapshot_worker import WeatherLocation


class FakeRedisPipeline:
    def __init__(self, client) -> None:
        self.client = client
        self.commands = []

    def setex(self, key, ttl, value):
        self.commands.append((key, ttl, value))
        return self

    def execute(self):
        for key, _ttl, value in self.commands:
            self.client.values[key] = value
        return [True] * len(self.commands)


class FakeRedis:
    def __init__(self) -> None:
        self.values = {}
        self.transaction = None

    def get(self, key):
        return self.values.get(key)

    def pipeline(self, *, transaction):
        self.transaction = transaction
        return FakeRedisPipeline(self)


class RecordingStore:
    def __init__(self) -> None:
        self.saved = None

    def save_snapshot(self, snapshot_id, entries, *, metadata, ttl_seconds):
        self.saved = {
            "snapshot_id": snapshot_id,
            "entries": entries,
            "metadata": metadata,
            "ttl_seconds": ttl_seconds,
        }


REFERENCE_NOW = datetime(2026, 7, 16, 5, tzinfo=timezone.utc)


def _store(client: FakeRedis, *, max_age_seconds: int = 14400) -> RedisWeatherStore:
    return RedisWeatherStore(
        client,
        prefix="test-weather",
        max_age_seconds=max_age_seconds,
        now_provider=lambda: REFERENCE_NOW,
    )


def _metadata(
    *,
    snapshot_id: str = "snapshot-1",
    generated_at_utc: str = "2026-07-16T04:00:00+00:00",
) -> dict:
    return {
        "schema_version": WEATHER_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "generated_at_utc": generated_at_utc,
        "provider": "openweathermap",
        "location_count": 1,
    }


def _forecast_day(day_text: str) -> dict:
    forecast_at_local = f"{day_text}T12:00:00+07:00"
    return {
        "date": day_text,
        "interval_count": 1,
        "coverage_start_local": forecast_at_local,
        "coverage_end_local": forecast_at_local,
        "intervals": [
            {
                "local_date": day_text,
                "forecast_at_local": forecast_at_local,
                "temperature_celsius": 30,
                "humidity_percent": 70,
                "pressure_hpa": 1005,
                "wind_speed_mps": 2.5,
                "rain_probability": 0.2,
            }
        ],
    }


def _forecast(
    dates: list[str] | None = None,
    *,
    location: str = "Hà Nội",
    location_id: str = "ha_noi",
) -> dict:
    resolved_dates = dates or ["2026-07-16", "2026-07-17", "2026-07-18"]
    days = [_forecast_day(value) for value in resolved_dates]
    intervals = [interval for day in days for interval in day["intervals"]]
    return {
        "location": location,
        "location_id": location_id,
        "timezone_offset_seconds": 25200,
        "requested_days": None,
        "available_day_count": len(days),
        "interval_count": len(intervals),
        "coverage_start_local": intervals[0]["forecast_at_local"] if intervals else None,
        "coverage_end_local": intervals[-1]["forecast_at_local"] if intervals else None,
        "days": days,
    }


def _current(*, location: str = "Hà Nội", location_id: str = "ha_noi") -> dict:
    return {
        "location": location,
        "location_id": location_id,
        "timezone_offset_seconds": 25200,
        "temperature": {"current_celsius": 30},
    }


def _entry(dates: list[str] | None = None) -> WeatherSnapshotEntry:
    return WeatherSnapshotEntry(
        location_id="ha_noi",
        location="Hà Nội",
        current=_current(),
        forecast=_forecast(dates),
    )


def _save_valid_snapshot(
    client: FakeRedis,
    *,
    dates: list[str] | None = None,
) -> RedisWeatherStore:
    store = _store(client)
    store.save_snapshot(
        "snapshot-1",
        [_entry(dates)],
        metadata=_metadata(),
        ttl_seconds=14400,
    )
    return store


def _settings(*, openweather_api_key: str = "weather-key") -> Settings:
    return Settings(
        gemini_api_key="",
        gemini_base_url="",
        gemini_model="",
        openweather_api_key=openweather_api_key,
        gnews_api_key="",
        weather_cache_ttl_seconds=3600,
        news_cache_ttl_seconds=900,
        wiki_cache_ttl_seconds=None,
        request_timeout_seconds=8,
        debug_routing=False,
    )


def test_redis_store_publishes_and_reads_active_snapshot() -> None:
    client = FakeRedis()
    store = _store(client)
    current = _current()
    forecast = _forecast(
        ["2026-07-16", "2026-07-17", "2026-07-18", "2026-07-19", "2026-07-20"]
    )

    store.save_snapshot(
        "snapshot-1",
        [
            WeatherSnapshotEntry(
                location_id="ha_noi", location="Hà Nội", current=current, forecast=forecast
            )
        ],
        metadata=_metadata(),
        ttl_seconds=14400,
    )

    assert client.transaction is True
    assert client.values["test-weather:snapshot:active"] == "snapshot-1"
    assert json.loads(
        client.values["test-weather:snapshot:snapshot-1:metadata"]
    )["generated_at_utc"] == "2026-07-16T04:00:00+00:00"
    location_key = "test-weather:snapshot:snapshot-1:location:ha_noi"
    assert location_key in client.values
    assert json.loads(client.values[location_key])["location_id"] == "ha_noi"
    assert (
        json.loads(client.values[location_key])["schema_version"]
        == "weather.snapshot.v4"
    )
    current_result = store.get_current("ha_noi")
    assert current_result["data"] == current
    assert "raw" not in current_result
    assert current_result["snapshot"]["schema_version"] == "weather.snapshot.v4"
    assert current_result["snapshot"]["age_seconds"] == 3600
    result = store.get_forecast("ha_noi", days=3)
    assert result["data"]["requested_days"] == 3
    assert len(result["data"]["days"]) == 3
    assert store.stats()["hits"] == 2


def test_redis_store_returns_eight_forecast_days() -> None:
    client = FakeRedis()
    dates = [f"2026-07-{day:02d}" for day in range(16, 24)]
    store = _save_valid_snapshot(client, dates=dates)

    result = store.get_forecast(
        "ha_noi",
        start_date="2026-07-16",
        days=8,
    )

    assert result["ok"] is True
    assert result["data"]["requested_days"] == 8
    assert [day["date"] for day in result["data"]["days"]] == dates


def test_redis_store_does_not_fallback_when_active_snapshot_is_missing() -> None:
    store = RedisWeatherStore(FakeRedis())

    result = store.get_current("ha_noi")

    assert result["ok"] is False
    assert result["error"]["source"] == "weather_redis"
    assert result["error"]["code"] == "snapshot_unavailable"
    assert "No active weather snapshot" in result["error"]["message"]
    assert store.stats()["misses"] == 1


def test_redis_store_rejects_free_text_location_lookup() -> None:
    store = RedisWeatherStore(FakeRedis())

    result = store.get_current("Hà Nội")

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_location_id"
    assert "Invalid weather location_id" in result["error"]["message"]


def test_redis_forecast_filters_from_requested_start_date() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(
        client,
        dates=["2026-07-16", "2026-07-17", "2026-07-18", "2026-07-19"],
    )

    result = store.get_forecast(
        "ha_noi",
        start_date="2026-07-17",
        days=2,
    )

    assert result["data"]["requested_start_date"] == "2026-07-17"
    assert result["data"]["days"] == [
        _forecast_day("2026-07-17"),
        _forecast_day("2026-07-18"),
    ]


def test_redis_input_errors_have_machine_readable_codes() -> None:
    store = _store(FakeRedis())

    assert store.get_current("")["error"]["code"] == "missing_location_id"
    assert store.get_current("Hà Nội")["error"]["code"] == "invalid_location_id"


def test_redis_connection_error_has_machine_readable_code() -> None:
    class FailingRedis(FakeRedis):
        def get(self, key):
            raise ConnectionError("connection refused")

    result = _store(FailingRedis()).get_current("ha_noi")

    assert result["error"]["code"] == "redis_connection_error"
    assert result["error"]["retryable"] is True


def test_missing_snapshot_metadata_is_rejected() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    del client.values["test-weather:snapshot:snapshot-1:metadata"]

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == "snapshot_metadata_missing"


@pytest.mark.parametrize("raw_metadata", ["not-json", "[]"])
def test_invalid_snapshot_metadata_json_is_rejected(raw_metadata: str) -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    client.values["test-weather:snapshot:snapshot-1:metadata"] = raw_metadata

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == "snapshot_metadata_invalid_json"


def test_unsupported_snapshot_schema_is_rejected() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    key = "test-weather:snapshot:snapshot-1:metadata"
    metadata = json.loads(client.values[key])
    metadata["schema_version"] = "weather.snapshot.v3"
    client.values[key] = json.dumps(metadata)

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == "snapshot_schema_unsupported"


def test_snapshot_id_mismatch_is_rejected() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    key = "test-weather:snapshot:snapshot-1:metadata"
    metadata = json.loads(client.values[key])
    metadata["snapshot_id"] = "snapshot-other"
    client.values[key] = json.dumps(metadata)

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == "snapshot_id_mismatch"


@pytest.mark.parametrize(
    ("generated_at_utc", "expected_code"),
    [
        ("2026-07-16T04:00:00", "snapshot_generated_at_invalid"),
        ("not-a-date", "snapshot_generated_at_invalid"),
        ("2026-07-16T06:00:00+00:00", "snapshot_generated_at_invalid"),
        ("2026-07-15T00:00:00+00:00", "snapshot_stale"),
    ],
)
def test_snapshot_freshness_is_enforced(
    generated_at_utc: str,
    expected_code: str,
) -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    key = "test-weather:snapshot:snapshot-1:metadata"
    metadata = json.loads(client.values[key])
    metadata["generated_at_utc"] = generated_at_utc
    client.values[key] = json.dumps(metadata)

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == expected_code


def test_snapshot_metadata_required_types_are_enforced() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    key = "test-weather:snapshot:snapshot-1:metadata"
    metadata = json.loads(client.values[key])
    metadata["location_count"] = "1"
    client.values[key] = json.dumps(metadata)

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == "snapshot_metadata_invalid"


def test_snapshot_change_during_read_retries_once_then_fails() -> None:
    class ChangingActiveRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.change_active = False
            self.active_reads = 0

        def get(self, key):
            if self.change_active and key == "test-weather:snapshot:active":
                self.active_reads += 1
                return "snapshot-1" if self.active_reads % 2 else "snapshot-2"
            return super().get(key)

    client = ChangingActiveRedis()
    store = _save_valid_snapshot(client)
    client.change_active = True

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == "snapshot_changed_during_read"
    assert client.active_reads == 4


def test_snapshot_change_during_read_retries_with_new_active_snapshot() -> None:
    class SequencedActiveRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.active_sequence: list[str] = []

        def get(self, key):
            if key == "test-weather:snapshot:active" and self.active_sequence:
                return self.active_sequence.pop(0)
            return super().get(key)

    client = SequencedActiveRedis()
    store = _save_valid_snapshot(client)
    store.save_snapshot(
        "snapshot-2",
        [_entry()],
        metadata=_metadata(snapshot_id="snapshot-2"),
        ttl_seconds=14400,
    )
    client.active_sequence = [
        "snapshot-1",
        "snapshot-2",
        "snapshot-2",
        "snapshot-2",
    ]

    result = store.get_current("ha_noi")

    assert result["ok"] is True
    assert result["snapshot_id"] == "snapshot-2"


def test_location_not_in_snapshot_has_unavailable_code() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    del client.values[
        "test-weather:snapshot:snapshot-1:location:ha_noi"
    ]

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == "location_not_in_snapshot"


@pytest.mark.parametrize(
    ("raw_payload", "expected_code"),
    [
        ("not-json", "location_payload_invalid_json"),
        ("[]", "location_payload_invalid"),
    ],
)
def test_invalid_location_payload_is_rejected(
    raw_payload: str,
    expected_code: str,
) -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    key = "test-weather:snapshot:snapshot-1:location:ha_noi"
    client.values[key] = raw_payload

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == expected_code


def test_location_id_mismatch_is_rejected() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    key = "test-weather:snapshot:snapshot-1:location:ha_noi"
    payload = json.loads(client.values[key])
    payload["location_id"] = "yen_bai"
    client.values[key] = json.dumps(payload)

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == "location_id_mismatch"


def test_missing_weather_section_is_rejected() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    key = "test-weather:snapshot:snapshot-1:location:ha_noi"
    payload = json.loads(client.values[key])
    del payload["current"]
    client.values[key] = json.dumps(payload)

    result = store.get_current("ha_noi")

    assert result["error"]["code"] == "weather_section_missing"


def test_forecast_consistency_is_enforced() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)
    key = "test-weather:snapshot:snapshot-1:location:ha_noi"
    payload = json.loads(client.values[key])
    payload["forecast"]["days"][1]["date"] = "2026-07-16"
    client.values[key] = json.dumps(payload)

    result = store.get_forecast(
        "ha_noi", days=1, start_date="2026-07-16"
    )

    assert result["error"]["code"] == "location_payload_invalid"


def test_forecast_start_date_invalid_has_code() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(client)

    result = store.get_forecast("ha_noi", days=2, start_date="16/07/2026")

    assert result["error"]["code"] == "forecast_start_date_invalid"


def test_forecast_exact_range_is_never_silently_shortened() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(
        client,
        dates=["2026-07-16", "2026-07-17"],
    )

    result = store.get_forecast(
        "ha_noi", days=2, start_date="2026-07-17"
    )

    assert result["error"]["code"] == "forecast_date_unavailable"
    assert result["error"]["details"]["missing_dates"] == ["2026-07-18"]


def test_five_future_days_are_selected_after_partial_current_day() -> None:
    client = FakeRedis()
    store = _save_valid_snapshot(
        client,
        dates=[
            "2026-07-16",
            "2026-07-17",
            "2026-07-18",
            "2026-07-19",
            "2026-07-20",
            "2026-07-21",
        ],
    )

    result = store.get_forecast(
        "ha_noi", days=5, start_date="2026-07-17"
    )

    assert result["ok"] is True
    assert [day["date"] for day in result["data"]["days"]] == [
        "2026-07-17",
        "2026-07-18",
        "2026-07-19",
        "2026-07-20",
        "2026-07-21",
    ]


def test_location_slug_matches_accented_and_ascii_names() -> None:
    assert location_slug("Thành phố Hà Nội") == location_slug("Ha Noi")
    assert location_slug("Hà Nội, Việt Nam") == location_slug("Ha Noi")
    assert location_slug("Đà Nẵng") == "da-nang"


def test_default_weather_location_file_contains_63_locations() -> None:
    locations = weather_snapshot_worker.load_weather_locations()

    assert len(locations) == 63
    assert len({location.location_id for location in locations}) == 63
    assert all(location.has_coordinates for location in locations)
    assert all(8 <= location.latitude <= 24 for location in locations)
    assert all(102 <= location.longitude <= 110 for location in locations)

    nghe_an = next(location for location in locations if location.location_id == "nghe_an")
    assert nghe_an.reference_name == "Vinh"
    assert nghe_an.latitude == 18.676346


def test_weather_location_preflight_separates_resolved_suspicious_and_failed(
    monkeypatch,
) -> None:
    locations = [
        WeatherLocation("Hà Nội", "Hanoi,VN", ("Hanoi",)),
        WeatherLocation("Hải Phòng", "Hai Phong,VN", ("Hai Phong",)),
        WeatherLocation("Bắc Kạn", "Bắc Kạn,VN"),
    ]

    def fake_geocode(query, *, api_key, timeout_seconds, limit):
        assert api_key == "weather-key"
        assert timeout_seconds == 8
        assert limit == 5
        if query == "Hanoi,VN":
            return {
                "ok": True,
                "data": {
                    "candidates": [
                        {
                            "name": "Hanoi",
                            "local_names": {"vi": "Hà Nội"},
                            "state": "Hà Nội",
                            "country": "VN",
                            "lat": 21.0,
                            "lon": 105.8,
                        }
                    ]
                },
            }
        if query == "Hai Phong,VN":
            return {
                "ok": True,
                "data": {
                    "candidates": [
                        {
                            "name": "Hải Phòng",
                            "local_names": {"vi": "Hải Phòng"},
                            "state": "Somewhere",
                            "country": "VN",
                            "lat": 20.8,
                            "lon": 106.7,
                        }
                    ]
                },
            }
        return {"ok": True, "data": {"candidates": []}}

    monkeypatch.setattr(
        weather_snapshot_worker,
        "geocode_weather_location",
        fake_geocode,
    )

    result = weather_snapshot_worker.preflight_weather_locations(
        settings=_settings(),
        locations=locations,
    )

    assert result["ok"] is False
    assert result["checked_locations"] == 3
    assert result["resolved_count"] == 1
    assert result["suspicious_count"] == 1
    assert result["failed_count"] == 1
    assert result["resolved_locations"][0]["location"] == "Hà Nội"
    assert result["suspicious_locations"][0]["location"] == "Hải Phòng"
    assert (
        result["suspicious_locations"][0]["reason"]
        == "candidate_state_does_not_match_configuration"
    )
    assert result["failed_locations"][0]["reason"] == "no_geocoding_results"


def test_worker_activates_snapshot_only_after_complete_fetch(
    monkeypatch,
    capsys,
) -> None:
    store = RecordingStore()
    locations = [
        WeatherLocation(
            "Hà Nội",
            "Hanoi,VN",
            ("Hanoi",),
            location_id="ha_noi",
            latitude=21.0283334,
            longitude=105.854041,
        )
    ]

    monkeypatch.setattr(
        weather_snapshot_worker,
        "fetch_open_meteo_weather",
        lambda **kwargs: {
            "ok": True,
            "data": {
                "current": {"location": ""},
                "forecast": {"location": "", "days": []},
            },
        },
    )

    result = weather_snapshot_worker.refresh_weather_snapshot(
        settings=_settings(openweather_api_key=""),
        store=store,
        locations=locations,
    )

    assert result["ok"] is True
    assert store.saved is not None
    assert store.saved["ttl_seconds"] == 14400
    assert store.saved["entries"][0].location_id == "ha_noi"
    assert store.saved["metadata"]["schema_version"] == "weather.snapshot.v4"
    assert store.saved["metadata"]["provider"] == "open-meteo"
    assert "forecast_days" not in store.saved["metadata"]
    assert store.saved["metadata"]["day_grouping"] == "location_local_date"
    terminal_output = capsys.readouterr().out
    assert "[Open-Meteo][1/1][FETCH_START]" in terminal_output
    assert "[Open-Meteo][1/1][FETCH_OK]" in terminal_output


def test_worker_fetches_rich_catalog_location_by_coordinates(monkeypatch) -> None:
    store = RecordingStore()
    locations = [
        WeatherLocation(
            name="Nghệ An",
            query="Vinh,VN",
            aliases=("Vinh",),
            location_id="nghe_an",
            latitude=18.676346,
            longitude=105.676548,
            reference_name="Vinh",
        )
    ]
    calls = []

    def fake_weather(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "data": {
                "current": {"location": ""},
                "forecast": {"location": "", "days": []},
            },
            "raw_forecast": {"hourly": {"time": ["2026-07-16T00:00"]}},
        }

    monkeypatch.setattr(
        weather_snapshot_worker,
        "fetch_open_meteo_weather",
        fake_weather,
    )

    result = weather_snapshot_worker.refresh_weather_snapshot(
        settings=_settings(),
        store=store,
        locations=locations,
    )

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["latitude"] == 18.676346
    assert calls[0]["longitude"] == 105.676548
    entry = store.saved["entries"][0]
    assert entry.current["location"] == "Nghệ An"
    assert entry.current["location_id"] == "nghe_an"
    assert entry.location_id == "nghe_an"
    assert entry.raw_forecast == {
        "hourly": {"time": ["2026-07-16T00:00"]}
    }


def test_worker_keeps_old_snapshot_when_one_location_fails(monkeypatch) -> None:
    store = RecordingStore()
    locations = [
        WeatherLocation(
            "Hà Nội",
            "Hanoi,VN",
            latitude=21.0283334,
            longitude=105.854041,
        )
    ]
    monkeypatch.setattr(
        weather_snapshot_worker,
        "fetch_open_meteo_weather",
        lambda **kwargs: {
            "ok": False,
            "error": {
                "source": "open-meteo",
                "message": "failed",
                "status_code": 500,
            },
        },
    )

    result = weather_snapshot_worker.refresh_weather_snapshot(
        settings=_settings(),
        store=store,
        locations=locations,
    )

    assert result["ok"] is False
    assert store.saved is None
    assert result["failed_locations"][0]["location"] == "Hà Nội"
    assert result["error"]["code"] == "snapshot_fetch_incomplete"


def test_worker_publish_failure_has_machine_readable_code(monkeypatch) -> None:
    class FailingStore(RecordingStore):
        def save_snapshot(self, snapshot_id, entries, *, metadata, ttl_seconds):
            raise ConnectionError("connection refused")

    monkeypatch.setattr(
        weather_snapshot_worker,
        "fetch_open_meteo_weather",
        lambda **kwargs: {
            "ok": True,
            "data": {
                "current": {"location": ""},
                "forecast": {"location": "", "days": []},
            },
        },
    )

    result = weather_snapshot_worker.refresh_weather_snapshot(
        settings=_settings(),
        store=FailingStore(),
        locations=[
            WeatherLocation(
                "Hà Nội",
                "Hanoi,VN",
                location_id="ha_noi",
                latitude=21.0283334,
                longitude=105.854041,
            )
        ],
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "redis_publish_failed"
    assert result["error"]["retryable"] is True
