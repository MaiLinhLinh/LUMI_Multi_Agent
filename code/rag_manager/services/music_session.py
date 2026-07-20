"""Backend-owned Music candidate and playback session state."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from rag_manager.services.music_search_service import normalize_music_text


MUSIC_SESSION_VERSION = "music.session.v1"
MAX_SESSION_CANDIDATES = 5
MAX_PLAYBACK_HISTORY = 20
_CANDIDATE_FIELDS = (
    "record_id",
    "track_id",
    "title",
    "artists",
    "video_id",
    "content_type",
    "version",
    "thumbnail_url",
    "duration_seconds",
    "release_date",
    "release_date_origin",
    "view_count",
)
_ORDINALS = {
    "1": 1,
    "mot": 1,
    "nhat": 1,
    "2": 2,
    "hai": 2,
    "nhi": 2,
    "3": 3,
    "ba": 3,
    "4": 4,
    "bon": 4,
    "tu": 4,
    "5": 5,
    "nam": 5,
}
_SELECTION_PATTERN = re.compile(
    r"^(?:(?:toi|minh)\s+)?(?:(?:chon|muon|bat|mo|phat|nghe)\s+)?"
    r"(?:bai\s+)?(?:(?:so|thu)\s+)?(1|2|3|4|5|mot|nhat|hai|nhi|ba|bon|tu|nam)$"
)
_REPLAY_COMMANDS = {
    "phat lai",
    "bat lai",
    "mo lai",
    "nghe lai",
    "phat lai bai do",
    "bat lai bai do",
}
_NEXT_COMMANDS = {
    "bai tiep theo",
    "bai ke tiep",
    "bai khac",
    "phat bai khac",
    "bat bai khac",
    "chuyen bai",
    "next",
}
_STOP_COMMANDS = {
    "dung",
    "dung nhac",
    "tat nhac",
    "ngung phat",
    "dung phat nhac",
    "stop",
}
_CURRENT_REFERENCE_COMMANDS = {
    "bai do",
    "bat bai do",
    "mo bai do",
    "phat bai do",
    "nghe bai do",
}
_TITLE_PREFIX_PATTERN = re.compile(
    r"^(?:(?:toi|minh)\s+)?(?:(?:chon|muon|bat|mo|phat|nghe)\s+)?(?:bai\s+)?"
)


class MusicSessionManager:
    """Resolve trusted session actions without querying Chroma again."""

    def normalize(self, raw: Any) -> dict[str, Any]:
        session = dict(raw) if isinstance(raw, Mapping) else {}
        candidates = _candidate_list(session.get("last_candidates"))
        current = _candidate(session.get("current_candidate"))
        history = session.get("playback_history", [])
        return {
            "schema_version": MUSIC_SESSION_VERSION,
            "last_music_request": (
                dict(session.get("last_music_request", {}))
                if isinstance(session.get("last_music_request"), Mapping)
                else {}
            ),
            "last_candidates": candidates,
            "last_candidate_ids": [
                candidate["record_id"]
                for candidate in candidates
                if candidate.get("record_id")
            ],
            "selected_track_id": _text(session.get("selected_track_id")),
            "current_artist": _text(session.get("current_artist")),
            "current_track_id": _text(session.get("current_track_id")),
            "current_source_id": _text(session.get("current_source_id")),
            "current_candidate_index": _positive_int(
                session.get("current_candidate_index")
            ),
            "current_candidate": current,
            "playback_status": (
                session.get("playback_status")
                if session.get("playback_status") in {"idle", "playing", "stopped"}
                else ("playing" if current else "idle")
            ),
            "playback_history": [
                str(value)
                for value in history[-MAX_PLAYBACK_HISTORY:]
                if isinstance(value, str) and value.strip()
            ] if isinstance(history, list) else [],
        }

    def resolve_query(
        self,
        query: str,
        session: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        normalized = normalize_music_text(query)
        if not normalized:
            return None

        selection = _selection_index(normalized)
        if selection is not None:
            return self._select_index(session, selection, source="query_shortcut")
        if normalized in _REPLAY_COMMANDS:
            return self._replay(session, source="query_shortcut")
        if normalized in _NEXT_COMMANDS:
            return self._next(session, source="query_shortcut")
        if normalized in _STOP_COMMANDS:
            return self._stop(session, source="query_shortcut")
        if normalized in _CURRENT_REFERENCE_COMMANDS:
            return self._replay(session, source="query_shortcut")

        named = self._match_named_candidate(normalized, session)
        if named is not None:
            return self._select_index(
                session,
                named,
                source="saved_candidate_title",
            )
        return None

    def resolve_extraction(
        self,
        extraction: Mapping[str, Any],
        session: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        selection_index = _positive_int(extraction.get("selection_index"))
        if selection_index is not None:
            return self._select_index(
                session,
                selection_index,
                source="llm1_selection",
            )

        action = extraction.get("action")
        if action == "replay":
            return self._replay(session, source="llm1_action")
        if action == "next":
            return self._next(session, source="llm1_action")
        if action == "stop":
            return self._stop(session, source="llm1_action")

        title = normalize_music_text(_text(extraction.get("title")))
        if action in {"play", "search"} and title and not any(
            extraction.get(field)
            for field in ("genre", "mood", "language", "version", "sort_by")
        ):
            index = self._candidate_index_by_title(
                title,
                _text(extraction.get("artist")),
                session,
            )
            if index is not None:
                return self._select_index(
                    session,
                    index,
                    source="saved_candidate_title",
                )
        return None

    def apply_pipeline_result(
        self,
        session: Mapping[str, Any],
        *,
        extraction: Mapping[str, Any],
        search_result: Mapping[str, Any],
        decision: Mapping[str, Any],
        status: str,
    ) -> dict[str, Any]:
        updated = self.normalize(session)
        if extraction:
            updated["last_music_request"] = dict(extraction)

        raw_candidates = search_result.get("candidates")
        if isinstance(raw_candidates, list):
            candidates = _candidate_list(raw_candidates)
            updated["last_candidates"] = candidates
            updated["last_candidate_ids"] = [
                candidate["record_id"]
                for candidate in candidates
                if candidate.get("record_id")
            ]
            updated["current_candidate_index"] = _index_of_source(
                candidates,
                updated.get("current_source_id"),
            )

        selected = _candidate(decision.get("selected_candidate"))
        if status == "completed" and selected:
            self._set_current(updated, selected, decision.get("selected_index"))
        elif decision.get("code") == "playback_stopped":
            updated["playback_status"] = "stopped"
        return updated

    def _select_index(
        self,
        session: Mapping[str, Any],
        index: int,
        *,
        source: str,
    ) -> dict[str, Any]:
        candidates = _candidate_list(session.get("last_candidates"))
        if not candidates:
            return _session_issue(
                "selection_context_required",
                "There is no saved Music candidate list.",
                field="selection_index",
                source=source,
            )
        if index < 1 or index > len(candidates):
            return _session_issue(
                "selection_index_out_of_range",
                "The selected Music candidate index is outside the saved list.",
                field="selection_index",
                source=source,
                details={"requested_index": index, "candidate_count": len(candidates)},
            )
        return _session_completed(
            candidates[index - 1],
            code="saved_candidate_selected",
            source=source,
            selected_index=index,
        )

    def _replay(
        self,
        session: Mapping[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        current = _candidate(session.get("current_candidate"))
        if not current:
            return _session_issue(
                "player_context_required",
                "There is no current Music candidate to replay.",
                field="action",
                source=source,
            )
        return _session_completed(
            current,
            code="current_candidate_replayed",
            source=source,
            selected_index=_positive_int(session.get("current_candidate_index")),
        )

    def _next(
        self,
        session: Mapping[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        candidates = _candidate_list(session.get("last_candidates"))
        if not candidates:
            return _session_issue(
                "selection_context_required",
                "There is no saved Music candidate list for a next track.",
                field="action",
                source=source,
            )
        current_index = _index_of_source(
            candidates,
            session.get("current_source_id"),
        )
        next_index = 1 if current_index is None else current_index + 1
        if next_index > len(candidates):
            return _session_issue(
                "no_next_music_candidate",
                "The current track is the last saved Music candidate.",
                field="action",
                source=source,
                details={"candidate_count": len(candidates)},
            )
        return _session_completed(
            candidates[next_index - 1],
            code="next_candidate_selected",
            source=source,
            selected_index=next_index,
        )

    def _stop(
        self,
        session: Mapping[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        if not _candidate(session.get("current_candidate")):
            return _session_issue(
                "player_context_required",
                "There is no current Music candidate to stop.",
                field="action",
                source=source,
            )
        return {
            "handled": True,
            "status": "completed",
            "code": "playback_stopped",
            "source": source,
        }

    def _match_named_candidate(
        self,
        normalized_query: str,
        session: Mapping[str, Any],
    ) -> int | None:
        stripped = _TITLE_PREFIX_PATTERN.sub("", normalized_query).strip()
        if not stripped:
            return None
        matches = []
        for index, candidate in enumerate(
            _candidate_list(session.get("last_candidates")),
            start=1,
        ):
            title = normalize_music_text(_text(candidate.get("title")))
            if title and (stripped == title or title == normalized_query):
                matches.append(index)
        return matches[0] if len(matches) == 1 else None

    def _candidate_index_by_title(
        self,
        title: str,
        artist: str,
        session: Mapping[str, Any],
    ) -> int | None:
        requested_artist = normalize_music_text(artist)
        matches: list[int] = []
        for index, candidate in enumerate(
            _candidate_list(session.get("last_candidates")),
            start=1,
        ):
            if normalize_music_text(_text(candidate.get("title"))) != title:
                continue
            artists = candidate.get("artists", [])
            if requested_artist and not any(
                requested_artist in normalize_music_text(str(value))
                or normalize_music_text(str(value)) in requested_artist
                for value in artists
            ):
                continue
            matches.append(index)
        return matches[0] if len(matches) == 1 else None

    def _set_current(
        self,
        session: dict[str, Any],
        candidate: dict[str, Any],
        selected_index: Any,
    ) -> None:
        source_id = _text(candidate.get("record_id"))
        artists = candidate.get("artists", [])
        session["current_candidate"] = candidate
        session["selected_track_id"] = _text(candidate.get("track_id"))
        session["current_track_id"] = _text(candidate.get("track_id"))
        session["current_source_id"] = source_id
        session["current_artist"] = (
            _text(artists[0]) if isinstance(artists, list) and artists else ""
        )
        session["current_candidate_index"] = (
            _positive_int(selected_index)
            or _index_of_source(session.get("last_candidates", []), source_id)
        )
        session["playback_status"] = "playing"
        history = list(session.get("playback_history", []))
        if source_id and (not history or history[-1] != source_id):
            history.append(source_id)
        session["playback_history"] = history[-MAX_PLAYBACK_HISTORY:]


def _selection_index(normalized_query: str) -> int | None:
    match = _SELECTION_PATTERN.fullmatch(normalized_query)
    return _ORDINALS.get(match.group(1)) if match else None


def _candidate_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    candidates: list[dict[str, Any]] = []
    for item in raw[:MAX_SESSION_CANDIDATES]:
        candidate = _candidate(item)
        if candidate and candidate.get("record_id") and candidate.get("video_id"):
            candidates.append(candidate)
    return candidates


def _candidate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    candidate = {field: raw.get(field) for field in _CANDIDATE_FIELDS}
    artists = raw.get("artists")
    candidate["artists"] = [
        str(value) for value in artists if isinstance(value, str) and value.strip()
    ] if isinstance(artists, list) else []
    if not _text(candidate.get("record_id")) or not _text(candidate.get("video_id")):
        return {}
    return candidate


def _index_of_source(candidates: Any, source_id: Any) -> int | None:
    requested = _text(source_id)
    if not requested:
        return None
    for index, candidate in enumerate(_candidate_list(candidates), start=1):
        if candidate.get("record_id") == requested:
            return index
    return None


def _session_completed(
    candidate: Mapping[str, Any],
    *,
    code: str,
    source: str,
    selected_index: int | None,
) -> dict[str, Any]:
    return {
        "handled": True,
        "status": "completed",
        "code": code,
        "source": source,
        "selected_candidate": dict(candidate),
        "selected_index": selected_index,
    }


def _session_issue(
    code: str,
    message: str,
    *,
    field: str,
    source: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "handled": True,
        "status": "needs_clarification",
        "code": code,
        "message": message,
        "field": field,
        "source": source,
    }
    if details:
        result["details"] = dict(details)
    return result


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
