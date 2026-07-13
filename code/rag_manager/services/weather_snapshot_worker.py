"""Background worker that refreshes Redis weather snapshots from OpenWeather."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag_manager.config import Settings, load_settings
from rag_manager.services.weather_api import (
    fetch_weather,
    fetch_weather_forecast,
    geocode_weather_location,
)
from rag_manager.services.weather_redis import (
    RedisWeatherStore,
    WEATHER_SNAPSHOT_SCHEMA_VERSION,
    WeatherSnapshotEntry,
    location_slug,
)


DEFAULT_LOCATIONS_FILE = Path(__file__).with_name("weather_locations_vn.json")


@dataclass(frozen=True)
class WeatherLocation:
    """One stable weather location and its verified representative point."""

    name: str
    query: str
    aliases: tuple[str, ...] = ()
    location_id: str = ""
    latitude: float | None = None
    longitude: float | None = None
    reference_name: str = ""
    center_type: str = ""
    coordinate_origin: str = ""
    wikidata_id: str = ""

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def load_weather_locations(path: str | Path | None = None) -> list[WeatherLocation]:
    """Load location definitions used to build a nationwide snapshot."""

    source = Path(path) if path else DEFAULT_LOCATIONS_FILE
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw_locations = payload.get("locations")
        if not isinstance(raw_locations, list):
            raise ValueError(
                "Weather locations catalog must contain a 'locations' JSON list"
            )
    elif isinstance(payload, list):
        raw_locations = payload
    else:
        raise ValueError("Weather locations file must contain a JSON list or catalog")

    locations: list[WeatherLocation] = []
    for item in raw_locations:
        if isinstance(item, str):
            name = item.strip()
            query = f"{name},VN"
            aliases: tuple[str, ...] = ()
            location_id = location_slug(name).replace("-", "_")
            latitude = None
            longitude = None
            reference_name = name
            center_type = ""
            coordinate_origin = ""
            wikidata_id = ""
        elif isinstance(item, dict):
            if item.get("active") is False:
                continue
            name = str(item.get("name", "")).strip()
            reference_name = str(item.get("reference_name", "")).strip() or name
            query = (
                str(item.get("query", "")).strip() or f"{reference_name},VN"
            )
            raw_aliases = item.get("aliases", [])
            aliases = tuple(
                str(alias).strip()
                for alias in raw_aliases
                if isinstance(alias, str) and alias.strip()
            )
            location_id = str(item.get("id", "")).strip() or location_slug(
                name
            ).replace("-", "_")
            latitude = _optional_coordinate(item.get("latitude"), field="latitude")
            longitude = _optional_coordinate(
                item.get("longitude"), field="longitude"
            )
            if (latitude is None) != (longitude is None):
                raise ValueError(
                    f"Weather location {name!r} must define both latitude and longitude"
                )
            if latitude is not None and not 8 <= latitude <= 24:
                raise ValueError(
                    f"Weather location {name!r} latitude is outside Vietnam"
                )
            if longitude is not None and not 102 <= longitude <= 110:
                raise ValueError(
                    f"Weather location {name!r} longitude is outside Vietnam"
                )
            center_type = str(item.get("center_type", "")).strip()
            coordinate_origin = str(item.get("coordinate_origin", "")).strip()
            wikidata_id = str(item.get("wikidata_id", "")).strip()
        else:
            raise ValueError("Each weather location must be a string or JSON object")
        if not name:
            raise ValueError("Weather location name must not be empty")
        if not location_id:
            raise ValueError(f"Weather location {name!r} must have a stable id")
        if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", location_id):
            raise ValueError(
                f"Weather location {name!r} has invalid id {location_id!r}"
            )
        locations.append(
            WeatherLocation(
                name=name,
                query=query,
                aliases=aliases,
                location_id=location_id,
                latitude=latitude,
                longitude=longitude,
                reference_name=reference_name,
                center_type=center_type,
                coordinate_origin=coordinate_origin,
                wikidata_id=wikidata_id,
            )
        )

    if not locations:
        raise ValueError("Weather locations file must not be empty")
    _validate_unique_locations(locations)
    return locations


def preflight_weather_locations(
    *,
    settings: Settings,
    locations: list[WeatherLocation] | None = None,
    delay_seconds: float = 0,
) -> dict[str, Any]:
    """Validate configured coordinates; geocode only legacy name-only entries."""

    resolved_locations = locations or load_weather_locations(
        settings.weather_locations_file or None
    )
    if (
        any(not location.has_coordinates for location in resolved_locations)
        and not settings.openweather_api_key.strip()
    ):
        return _refresh_error("Missing OPENWEATHER_API_KEY.")
    resolved: list[dict[str, Any]] = []
    suspicious: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for index, location in enumerate(resolved_locations):
        if location.has_coordinates:
            resolved.append(
                {
                    "location": location.name,
                    "location_id": location.location_id,
                    "query": location.query,
                    "source": "configured_coordinates",
                    "selected": {
                        "name": location.reference_name,
                        "country": "VN",
                        "lat": location.latitude,
                        "lon": location.longitude,
                    },
                }
            )
            continue
        response = geocode_weather_location(
            location.query,
            api_key=settings.openweather_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            limit=5,
        )
        if not response.get("ok"):
            failed.append(
                {
                    "location": location.name,
                    "query": location.query,
                    "reason": "geocoding_api_error",
                    "error": response.get("error"),
                }
            )
        else:
            data = response.get("data", {})
            candidates = data.get("candidates", []) if isinstance(data, dict) else []
            vietnam_candidates = [
                candidate
                for candidate in candidates
                if isinstance(candidate, dict)
                and candidate.get("country") == "VN"
                and _has_valid_coordinates(candidate)
            ]
            if not candidates:
                failed.append(
                    {
                        "location": location.name,
                        "query": location.query,
                        "reason": "no_geocoding_results",
                    }
                )
            elif not vietnam_candidates:
                suspicious.append(
                    {
                        "location": location.name,
                        "query": location.query,
                        "reason": "no_valid_vietnam_candidate",
                        "candidates": _candidate_summaries(candidates),
                    }
                )
            else:
                matching_candidates = [
                    candidate
                    for candidate in vietnam_candidates
                    if _candidate_matches_location(candidate, location)
                ]
                trusted_candidates = [
                    candidate
                    for candidate in matching_candidates
                    if _candidate_state_is_trusted(candidate, location)
                ]
                if trusted_candidates:
                    resolved.append(
                        {
                            "location": location.name,
                            "query": location.query,
                            "selected": _candidate_summary(trusted_candidates[0]),
                        }
                    )
                elif matching_candidates:
                    suspicious.append(
                        {
                            "location": location.name,
                            "query": location.query,
                            "reason": _state_rejection_reason(
                                matching_candidates[0], location
                            ),
                            "candidates": _candidate_summaries(matching_candidates),
                        }
                    )
                else:
                    suspicious.append(
                        {
                            "location": location.name,
                            "query": location.query,
                            "reason": "candidate_name_does_not_match_configuration",
                            "candidates": _candidate_summaries(vietnam_candidates),
                        }
                    )

        if delay_seconds > 0 and index < len(resolved_locations) - 1:
            time.sleep(delay_seconds)

    return {
        "ok": not failed and not suspicious,
        "checked_locations": len(resolved_locations),
        "resolved_count": len(resolved),
        "suspicious_count": len(suspicious),
        "failed_count": len(failed),
        "resolved_locations": resolved,
        "suspicious_locations": suspicious,
        "failed_locations": failed,
    }


def refresh_weather_snapshot(
    *,
    settings: Settings,
    store: RedisWeatherStore | None = None,
    locations: list[WeatherLocation] | None = None,
) -> dict[str, Any]:
    """Fetch a complete snapshot and atomically make it active in Redis."""

    if not settings.openweather_api_key.strip():
        return _refresh_error("Missing OPENWEATHER_API_KEY.")

    resolved_store = store or RedisWeatherStore.from_settings(settings)
    resolved_locations = locations or load_weather_locations(
        settings.weather_locations_file or None
    )
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    entries: list[WeatherSnapshotEntry] = []
    failures: list[dict[str, Any]] = []

    for location in resolved_locations:
        current = fetch_weather(
            location.query,
            api_key=settings.openweather_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            latitude=location.latitude,
            longitude=location.longitude,
        )
        forecast = fetch_weather_forecast(
            location.query,
            api_key=settings.openweather_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            days=5,
            latitude=location.latitude,
            longitude=location.longitude,
        )
        if not current.get("ok") or not forecast.get("ok"):
            failures.append(
                {
                    "location": location.name,
                    "location_id": location.location_id,
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "current_error": current.get("error"),
                    "forecast_error": forecast.get("error"),
                }
            )
            continue
        entries.append(
            WeatherSnapshotEntry(
                location_id=location.location_id,
                location=location.name,
                current=_canonical_weather_data(current["data"], location),
                forecast=_canonical_weather_data(forecast["data"], location),
                raw_current=current.get("raw_data", current["data"]),
                raw_forecast=forecast.get("raw_data", forecast["data"]),
            )
        )

    if failures:
        return {
            "ok": False,
            "snapshot_id": snapshot_id,
            "loaded_locations": len(entries),
            "failed_locations": failures,
            "error": {
                "source": "weather_snapshot_worker",
                "message": "Snapshot was not activated because some locations failed.",
                "status_code": None,
            },
        }

    generated_at = datetime.now(timezone.utc).isoformat()
    metadata = {
        "schema_version": WEATHER_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "generated_at_utc": generated_at,
        "provider": "openweathermap",
        "location_count": len(entries),
        "forecast_days": 5,
        "location_catalog": "vietnam_63_pre_2025_merger",
        "lookup_mode": "verified_coordinates",
        "day_grouping": "location_local_date",
        "interval_time_basis": "location_local_time",
        "timezone_source": "openweather_city_timezone_offset",
    }
    try:
        resolved_store.save_snapshot(
            snapshot_id,
            entries,
            metadata=metadata,
            ttl_seconds=settings.weather_snapshot_ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - Redis is an external boundary
        return _refresh_error(f"Could not publish weather snapshot to Redis: {exc}")
    return {"ok": True, **metadata}


def run_refresh_loop(
    settings: Settings,
    *,
    locations: list[WeatherLocation] | None = None,
) -> None:
    """Refresh immediately and then repeat at the configured interval."""

    store = RedisWeatherStore.from_settings(settings)
    resolved_locations = locations or load_weather_locations(
        settings.weather_locations_file or None
    )
    interval = max(1, settings.weather_refresh_interval_seconds)
    while True:
        result = refresh_weather_snapshot(
            settings=settings,
            store=store,
            locations=resolved_locations,
        )
        _print_json_result(result)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        action="store_true",
        help="Refresh one snapshot and exit instead of running every three hours.",
    )
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="Geocode every configured location and print validation results.",
    )
    parser.add_argument(
        "--locations-file",
        help="Override WEATHER_LOCATIONS_FILE for this worker process.",
    )
    parser.add_argument(
        "--preflight-delay",
        type=float,
        default=1.05,
        help="Seconds between geocoding calls; defaults to 1.05 to protect API quota.",
    )
    args = parser.parse_args()
    settings = load_settings()
    locations = load_weather_locations(
        args.locations_file or settings.weather_locations_file or None
    )
    if args.preflight:
        result = preflight_weather_locations(
            settings=settings,
            locations=locations,
            delay_seconds=max(0, args.preflight_delay),
        )
        _print_json_result(result)
        raise SystemExit(0 if result.get("ok") else 1)
    if args.once:
        result = refresh_weather_snapshot(settings=settings, locations=locations)
        _print_json_result(result)
        raise SystemExit(0 if result.get("ok") else 1)
    run_refresh_loop(settings, locations=locations)


def _refresh_error(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "source": "weather_snapshot_worker",
            "message": message,
            "status_code": None,
        },
    }


def _optional_coordinate(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Weather location {field} must be a number")
    return float(value)


def _validate_unique_locations(locations: list[WeatherLocation]) -> None:
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for location in locations:
        normalized_id = location.location_id.casefold()
        if normalized_id in seen_ids:
            raise ValueError(f"Duplicate weather location id: {location.location_id!r}")
        seen_ids.add(normalized_id)

        normalized_name = location_slug(location.name)
        if normalized_name in seen_names:
            raise ValueError(f"Duplicate weather location name: {location.name!r}")
        seen_names.add(normalized_name)


def _canonical_weather_data(
    data: dict[str, Any],
    location: WeatherLocation,
) -> dict[str, Any]:
    canonical = dict(data)
    provider_location = canonical.get("location")
    if provider_location and provider_location != location.name:
        canonical["provider_location"] = provider_location
    canonical.update(
        {
            "location": location.name,
            "location_id": location.location_id,
            "reference_name": location.reference_name,
            "coordinates": {
                "latitude": location.latitude,
                "longitude": location.longitude,
            },
        }
    )
    return canonical


def _print_json_result(result: dict[str, Any]) -> None:
    message = json.dumps(result, ensure_ascii=False, indent=2)
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            raise
        buffer.write((message + "\n").encode("utf-8"))
        buffer.flush()


def _candidate_matches_location(
    candidate: dict[str, Any],
    location: WeatherLocation,
) -> bool:
    expected = {
        location_slug(name)
        for name in (location.name, location.query, *location.aliases)
        if name.strip()
    }
    local_names = candidate.get("local_names")
    candidate_names = [candidate.get("name"), candidate.get("state")]
    if isinstance(local_names, dict):
        candidate_names.extend(local_names.values())
    observed = {
        location_slug(name)
        for name in candidate_names
        if isinstance(name, str) and name.strip()
    }
    return bool(expected & observed)


def _has_valid_coordinates(candidate: dict[str, Any]) -> bool:
    latitude = candidate.get("lat")
    longitude = candidate.get("lon")
    return (
        isinstance(latitude, (int, float))
        and not isinstance(latitude, bool)
        and -90 <= latitude <= 90
        and isinstance(longitude, (int, float))
        and not isinstance(longitude, bool)
        and -180 <= longitude <= 180
    )


def _candidate_state_is_trusted(
    candidate: dict[str, Any],
    location: WeatherLocation,
) -> bool:
    state = candidate.get("state")
    if isinstance(state, str) and state.strip():
        return _state_slug(state) == location_slug(location.name)
    return _allows_candidate_without_state(location)


def _allows_candidate_without_state(location: WeatherLocation) -> bool:
    municipality_slugs = {
        "ha-noi",
        "ho-chi-minh",
        "hai-phong",
        "da-nang",
        "can-tho",
    }
    canonical_slug = location_slug(location.name)
    default_query_slug = location_slug(f"{location.name},VN")
    return (
        canonical_slug in municipality_slugs
        or location_slug(location.query) != default_query_slug
    )


def _state_rejection_reason(
    candidate: dict[str, Any],
    location: WeatherLocation,
) -> str:
    state = candidate.get("state")
    if not isinstance(state, str) or not state.strip():
        return "candidate_has_no_state_for_province"
    if _state_slug(state) != location_slug(location.name):
        return "candidate_state_does_not_match_configuration"
    return "candidate_requires_manual_review"


def _state_slug(state: str) -> str:
    return location_slug(state).removesuffix("-province")


def _candidate_summaries(candidates: list[Any]) -> list[dict[str, Any]]:
    return [
        _candidate_summary(candidate)
        for candidate in candidates
        if isinstance(candidate, dict)
    ]


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": candidate.get("name", ""),
        "state": candidate.get("state", ""),
        "country": candidate.get("country", ""),
        "lat": candidate.get("lat"),
        "lon": candidate.get("lon"),
    }


if __name__ == "__main__":
    main()
