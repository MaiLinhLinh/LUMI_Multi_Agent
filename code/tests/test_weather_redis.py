import json

from rag_manager.config import Settings
from rag_manager.services import weather_snapshot_worker
from rag_manager.services.weather_redis import (
    RedisWeatherStore,
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


def _settings() -> Settings:
    return Settings(
        gemini_api_key="",
        gemini_base_url="",
        gemini_model="",
        openweather_api_key="weather-key",
        gnews_api_key="",
        weather_cache_ttl_seconds=3600,
        news_cache_ttl_seconds=900,
        wiki_cache_ttl_seconds=None,
        request_timeout_seconds=8,
        debug_routing=False,
    )


def test_redis_store_publishes_and_reads_active_snapshot() -> None:
    client = FakeRedis()
    store = RedisWeatherStore(client, prefix="test-weather")
    current = {"location": "Hà Nội", "temperature": {"current_celsius": 30}}
    forecast = {
        "location": "Hà Nội",
        "requested_days": 5,
        "days": [{"date": f"2026-07-{day:02d}"} for day in range(13, 18)],
    }

    store.save_snapshot(
        "snapshot-1",
        [
            WeatherSnapshotEntry(
                location_id="ha_noi",
                location="Hà Nội",
                current=current,
                forecast=forecast,
            )
        ],
        metadata={"generated_at_utc": "2026-07-13T00:00:00+00:00"},
        ttl_seconds=14400,
    )

    assert client.transaction is True
    assert client.values["test-weather:snapshot:active"] == "snapshot-1"
    assert json.loads(
        client.values["test-weather:snapshot:snapshot-1:metadata"]
    )["generated_at_utc"] == "2026-07-13T00:00:00+00:00"
    location_key = "test-weather:snapshot:snapshot-1:location:ha_noi"
    assert location_key in client.values
    assert json.loads(client.values[location_key])["location_id"] == "ha_noi"
    assert (
        json.loads(client.values[location_key])["schema_version"]
        == "weather.snapshot.v3"
    )
    assert store.get_current("ha_noi")["data"] == current
    result = store.get_forecast("ha_noi", days=3)
    assert result["data"]["requested_days"] == 3
    assert len(result["data"]["days"]) == 3
    assert store.stats()["hits"] == 2


def test_redis_store_does_not_fallback_when_active_snapshot_is_missing() -> None:
    store = RedisWeatherStore(FakeRedis())

    result = store.get_current("ha_noi")

    assert result["ok"] is False
    assert result["error"]["source"] == "weather_redis"
    assert "No active weather snapshot" in result["error"]["message"]
    assert store.stats()["misses"] == 1


def test_redis_store_rejects_free_text_location_lookup() -> None:
    store = RedisWeatherStore(FakeRedis())

    result = store.get_current("Hà Nội")

    assert result["ok"] is False
    assert "Invalid weather location_id" in result["error"]["message"]


def test_redis_forecast_filters_from_requested_start_date() -> None:
    client = FakeRedis()
    store = RedisWeatherStore(client, prefix="test-weather")
    forecast = {
        "location": "Hà Nội",
        "days": [{"date": f"2026-07-{day:02d}"} for day in range(13, 18)],
    }
    store.save_snapshot(
        "snapshot-1",
        [
            WeatherSnapshotEntry(
                location_id="ha_noi",
                location="Hà Nội",
                current={"location": "Hà Nội"},
                forecast=forecast,
            )
        ],
        metadata={"schema_version": "weather.snapshot.v3"},
        ttl_seconds=14400,
    )

    result = store.get_forecast(
        "ha_noi",
        start_date="2026-07-15",
        days=2,
    )

    assert result["data"]["requested_start_date"] == "2026-07-15"
    assert result["data"]["days"] == [
        {"date": "2026-07-15"},
        {"date": "2026-07-16"},
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


def test_worker_activates_snapshot_only_after_complete_fetch(monkeypatch) -> None:
    store = RecordingStore()
    locations = [
        WeatherLocation(
            "Hà Nội",
            "Hanoi,VN",
            ("Hanoi",),
            location_id="ha_noi",
        )
    ]

    monkeypatch.setattr(
        weather_snapshot_worker,
        "fetch_weather",
        lambda *args, **kwargs: {"ok": True, "data": {"location": "Hà Nội"}},
    )
    monkeypatch.setattr(
        weather_snapshot_worker,
        "fetch_weather_forecast",
        lambda *args, **kwargs: {
            "ok": True,
            "data": {"location": "Hà Nội", "days": []},
        },
    )

    result = weather_snapshot_worker.refresh_weather_snapshot(
        settings=_settings(),
        store=store,
        locations=locations,
    )

    assert result["ok"] is True
    assert store.saved is not None
    assert store.saved["ttl_seconds"] == 14400
    assert store.saved["entries"][0].location_id == "ha_noi"
    assert store.saved["metadata"]["schema_version"] == "weather.snapshot.v3"
    assert store.saved["metadata"]["day_grouping"] == "location_local_date"


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

    def fake_current(location, **kwargs):
        calls.append(("current", location, kwargs))
        return {"ok": True, "data": {"location": "Vinh"}}

    def fake_forecast(location, **kwargs):
        calls.append(("forecast", location, kwargs))
        return {"ok": True, "data": {"location": "Vinh", "days": []}}

    monkeypatch.setattr(weather_snapshot_worker, "fetch_weather", fake_current)
    monkeypatch.setattr(
        weather_snapshot_worker,
        "fetch_weather_forecast",
        fake_forecast,
    )

    result = weather_snapshot_worker.refresh_weather_snapshot(
        settings=_settings(),
        store=store,
        locations=locations,
    )

    assert result["ok"] is True
    assert calls[0][2]["latitude"] == 18.676346
    assert calls[0][2]["longitude"] == 105.676548
    entry = store.saved["entries"][0]
    assert entry.current["location"] == "Nghệ An"
    assert entry.current["provider_location"] == "Vinh"
    assert entry.current["location_id"] == "nghe_an"
    assert entry.location_id == "nghe_an"


def test_worker_keeps_old_snapshot_when_one_location_fails(monkeypatch) -> None:
    store = RecordingStore()
    locations = [WeatherLocation("Hà Nội", "Hanoi,VN")]
    monkeypatch.setattr(
        weather_snapshot_worker,
        "fetch_weather",
        lambda *args, **kwargs: {
            "ok": False,
            "error": {"source": "weather", "message": "failed", "status_code": 500},
        },
    )
    monkeypatch.setattr(
        weather_snapshot_worker,
        "fetch_weather_forecast",
        lambda *args, **kwargs: {"ok": True, "data": {"days": []}},
    )

    result = weather_snapshot_worker.refresh_weather_snapshot(
        settings=_settings(),
        store=store,
        locations=locations,
    )

    assert result["ok"] is False
    assert store.saved is None
    assert result["failed_locations"][0]["location"] == "Hà Nội"
