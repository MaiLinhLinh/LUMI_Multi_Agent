"""Deterministic validation and result decisions for the Music Agent."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from rag_manager.services.music_search_service import normalize_music_text


MUSIC_EXTRACTION_FIELDS = {
    "action",
    "search_query",
    "title",
    "artist",
    "genre",
    "mood",
    "language",
    "version",
    "sort_by",
    "sort_order",
    "selection_index",
}
_TEXT_FIELDS = (
    "search_query",
    "title",
    "artist",
    "genre",
    "mood",
    "language",
    "version",
)
_CATALOG_FIELDS = _TEXT_FIELDS
_UNTRUSTED_RETRIEVAL_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"<\s*iframe\b", re.IGNORECASE),
    re.compile(r"\bwhere_document\b", re.IGNORECASE),
    re.compile(r"\$(?:contains|regex|and|or)\b", re.IGNORECASE),
    re.compile(r"\bvideo_id\b", re.IGNORECASE),
)


class MusicResultValidator:
    """Keep LLM extraction separate from trusted backend decisions."""

    def validate_extraction(self, extraction: Mapping[str, Any]) -> dict[str, Any]:
        unexpected = sorted(set(extraction) - MUSIC_EXTRACTION_FIELDS)
        if unexpected:
            return _issue(
                "invalid_extraction_fields",
                "Music extraction contains fields outside the response schema.",
                field="response_schema",
                details={"unexpected_fields": unexpected},
            )

        canonical = {field: extraction.get(field) for field in MUSIC_EXTRACTION_FIELDS}
        for field in _TEXT_FIELDS:
            raw = canonical.get(field)
            canonical[field] = raw.strip() if isinstance(raw, str) and raw.strip() else None
            value = canonical[field]
            if isinstance(value, str):
                if len(value) > 300:
                    return _issue(
                        "music_field_too_long",
                        "A music retrieval field is too long.",
                        field=field,
                    )
                if any(pattern.search(value) for pattern in _UNTRUSTED_RETRIEVAL_PATTERNS):
                    return _issue(
                        "unsafe_music_retrieval_value",
                        "Music retrieval values must not contain URLs, iframe markup, IDs, or database filters.",
                        field=field,
                    )

        action = canonical.get("action")
        if action is None:
            return _issue(
                "missing_music_action",
                "The requested music action is unclear.",
                field="action",
            )

        selection_index = canonical.get("selection_index")
        if selection_index is not None:
            return _issue(
                "selection_context_required",
                "A numbered selection requires a saved candidate list.",
                field="selection_index",
            )

        if action in {"next", "replay", "stop"}:
            return _issue(
                "player_context_required",
                "This playback action requires Music session state.",
                field="action",
                details={"action": action},
            )

        sort_by = canonical.get("sort_by")
        sort_order = canonical.get("sort_order")
        if sort_order is not None and sort_by is None:
            return _issue(
                "sort_field_required",
                "Music sort order cannot be used without a sort field.",
                field="sort_by",
            )
        if sort_by is not None and sort_order is None:
            canonical["sort_order"] = "desc"

        if action in {"play", "search"} and not any(
            canonical.get(field) for field in _CATALOG_FIELDS
        ):
            return _issue(
                "missing_music_requirements",
                "The request does not contain enough information for catalog retrieval.",
                field="search_query",
            )

        return {
            "status": "ready_for_search",
            "code": "ready_for_search",
            "canonical_extraction": canonical,
        }

    def evaluate_search_result(
        self,
        extraction: Mapping[str, Any],
        search_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw_candidates = search_result.get("candidates")
        candidates = (
            [dict(item) for item in raw_candidates if isinstance(item, Mapping)]
            if isinstance(raw_candidates, Sequence)
            and not isinstance(raw_candidates, (str, bytes))
            else []
        )
        if not candidates:
            return _issue(
                "music_not_found",
                "No catalog result matched the validated request.",
                field="search_query",
            )

        if extraction.get("sort_by") in {"release_date", "popularity"}:
            return _completed(candidates[0], "deterministic_structured_sort")

        requested_title = normalize_music_text(_text(extraction.get("title")))
        if requested_title:
            exact = [
                candidate
                for candidate in candidates
                if normalize_music_text(_text(candidate.get("title")))
                == requested_title
            ]
            if len(exact) == 1:
                return _completed(exact[0], "unique_exact_title")
            if len(candidates) == 1:
                return _completed(candidates[0], "single_filtered_title")
            return _issue(
                "multiple_music_matches",
                "Multiple catalog records match the requested title.",
                field="title",
                candidate_count=len(candidates),
            )

        if len(candidates) == 1:
            return _completed(candidates[0], "single_search_result")

        return _issue(
            "multiple_music_matches",
            "The request is broad and returned multiple catalog records.",
            field="title",
            candidate_count=len(candidates),
        )


def _completed(candidate: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "code": "music_candidate_selected",
        "reason": reason,
        "selected_candidate": dict(candidate),
    }


def _issue(
    code: str,
    message: str,
    *,
    field: str,
    details: Mapping[str, Any] | None = None,
    candidate_count: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "needs_clarification",
        "code": code,
        "message": message,
        "field": field,
    }
    if details:
        payload["details"] = dict(details)
    if candidate_count is not None:
        payload["candidate_count"] = candidate_count
    return payload


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
