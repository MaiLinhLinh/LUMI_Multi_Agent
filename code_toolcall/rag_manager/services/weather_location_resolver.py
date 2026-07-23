"""Resolve user-provided Vietnamese place names to stable weather location IDs."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_LOCATIONS_FILE = Path(__file__).with_name("weather_locations_vn.json")
LOCATION_RESOLVER_SOURCE = "weather_location_resolver"


@dataclass(frozen=True)
class WeatherLocationRecord:
    """One searchable location from the static Vietnam weather catalog."""

    location_id: str
    name: str
    aliases: tuple[str, ...]
    reference_name: str = ""

    @property
    def search_names(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                value
                for value in (
                    self.location_id,
                    self.name,
                    *self.aliases,
                    self.reference_name,
                )
                if value
            )
        )


class WeatherLocationResolver:
    """In-memory exact and fuzzy lookup over the fixed location catalog."""

    def __init__(
        self,
        locations: list[WeatherLocationRecord],
        *,
        fuzzy_threshold: float = 0.82,
        ambiguity_margin: float = 0.05,
    ) -> None:
        if not locations:
            raise ValueError("Weather location catalog must not be empty")
        if any(
            not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", location.location_id)
            for location in locations
        ):
            raise ValueError("Weather location IDs must use lowercase snake_case")
        self._locations = {location.location_id: location for location in locations}
        if len(self._locations) != len(locations):
            raise ValueError("Weather location IDs must be unique")
        self._fuzzy_threshold = fuzzy_threshold
        self._ambiguity_margin = ambiguity_margin
        self._exact_index: dict[str, set[str]] = {}
        self._compact_index: dict[str, set[str]] = {}
        self._search_values: dict[str, list[tuple[str, str]]] = {}
        for location in locations:
            searchable: list[tuple[str, str]] = []
            for display_name in location.search_names:
                normalized = normalize_location_text(display_name)
                if not normalized:
                    continue
                compact = normalized.replace(" ", "")
                self._exact_index.setdefault(normalized, set()).add(location.location_id)
                self._compact_index.setdefault(compact, set()).add(location.location_id)
                searchable.append((display_name, compact))
            self._search_values[location.location_id] = searchable

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> WeatherLocationResolver:
        source = Path(path) if path else DEFAULT_LOCATIONS_FILE
        payload = json.loads(source.read_text(encoding="utf-8"))
        raw_locations = payload.get("locations") if isinstance(payload, dict) else payload
        if not isinstance(raw_locations, list):
            raise ValueError("Weather locations catalog must contain a JSON list")

        locations: list[WeatherLocationRecord] = []
        for item in raw_locations:
            if not isinstance(item, dict) or item.get("active") is False:
                continue
            location_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            if not location_id or not name:
                raise ValueError("Every active weather location requires id and name")
            raw_aliases = item.get("aliases", [])
            aliases = tuple(
                str(alias).strip()
                for alias in raw_aliases
                if isinstance(alias, str) and alias.strip()
            ) if isinstance(raw_aliases, list) else ()
            locations.append(
                WeatherLocationRecord(
                    location_id=location_id,
                    name=name,
                    aliases=aliases,
                    reference_name=str(item.get("reference_name", "")).strip(),
                )
            )
        return cls(locations)

    def resolve(self, location_text: str) -> dict[str, Any]:
        """Return a stable location ID or a structured ambiguity/not-found error."""

        requested = location_text.strip() if isinstance(location_text, str) else ""
        normalized = normalize_location_text(requested)
        if not normalized:
            return _resolver_error(
                "missing_location",
                "Không tìm thấy cụm địa danh trong yêu cầu.",
            )

        exact_ids = self._exact_index.get(normalized, set())
        if not exact_ids:
            exact_ids = self._compact_index.get(normalized.replace(" ", ""), set())
        if len(exact_ids) == 1:
            location_id = next(iter(exact_ids))
            return self._success(
                self._locations[location_id],
                requested=requested,
                match_type="exact",
                confidence=1.0,
            )
        if len(exact_ids) > 1:
            return self._ambiguous(requested, [(location_id, 1.0) for location_id in exact_ids])

        compact_request = normalized.replace(" ", "")
        scores: list[tuple[str, float]] = []
        matched_names: dict[str, str] = {}
        for location_id, searchable in self._search_values.items():
            best_name = ""
            best_score = 0.0
            for display_name, compact_name in searchable:
                score = SequenceMatcher(None, compact_request, compact_name).ratio()
                if score > best_score:
                    best_name = display_name
                    best_score = score
            scores.append((location_id, best_score))
            matched_names[location_id] = best_name
        scores.sort(key=lambda item: (-item[1], item[0]))
        best_id, best_score = scores[0]
        if best_score < self._fuzzy_threshold:
            return _resolver_error(
                "location_not_found",
                f"Không tìm thấy địa danh phù hợp với {requested!r} trong danh mục.",
                candidates=self._candidate_payload(scores[:3]),
            )
        if len(scores) > 1 and scores[1][1] >= best_score - self._ambiguity_margin:
            return self._ambiguous(requested, scores[:3])
        return self._success(
            self._locations[best_id],
            requested=requested,
            match_type="fuzzy",
            confidence=best_score,
            matched_name=matched_names[best_id],
        )

    def _success(
        self,
        location: WeatherLocationRecord,
        *,
        requested: str,
        match_type: str,
        confidence: float,
        matched_name: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "location_id": location.location_id,
            "canonical_name": location.name,
            "requested_text": requested,
            "matched_name": matched_name or location.name,
            "match_type": match_type,
            "confidence": round(confidence, 4),
        }

    def _ambiguous(
        self,
        requested: str,
        scores: list[tuple[str, float]],
    ) -> dict[str, Any]:
        return _resolver_error(
            "ambiguous_location",
            f"Địa danh {requested!r} chưa đủ rõ ràng.",
            candidates=self._candidate_payload(scores),
        )

    def _candidate_payload(
        self,
        scores: list[tuple[str, float]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "location_id": location_id,
                "canonical_name": self._locations[location_id].name,
                "confidence": round(score, 4),
            }
            for location_id, score in scores
        ]


def normalize_location_text(value: str) -> str:
    """Normalize Vietnamese spelling variants for deterministic lookup."""

    normalized = value.casefold().replace("đ", "d")
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFD", normalized)
        if unicodedata.category(character) != "Mn"
    )
    normalized = re.sub(r"\b(?:thanh pho|tinh|tp)\b", " ", normalized)
    normalized = re.sub(r"\b(?:viet nam|vietnam|vn)\b", " ", normalized)
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


@lru_cache(maxsize=8)
def _cached_resolver(source_path: str) -> WeatherLocationResolver:
    return WeatherLocationResolver.from_file(source_path)


def get_weather_location_resolver(
    path: str | Path | None = None,
) -> WeatherLocationResolver:
    """Load and cache one resolver per catalog path for the process lifetime."""

    source = Path(path) if path else DEFAULT_LOCATIONS_FILE
    return _cached_resolver(str(source.resolve()))


def _resolver_error(
    code: str,
    message: str,
    *,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "error": {
            "source": LOCATION_RESOLVER_SOURCE,
            "code": code,
            "message": message,
            "status_code": None,
        },
    }
    if candidates:
        result["candidates"] = candidates
    return result
