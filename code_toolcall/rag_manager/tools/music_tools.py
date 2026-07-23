from __future__ import annotations

from typing import Any

from rag_manager.config import Settings
from rag_manager.services.music_embedding_service import OllamaMusicEmbeddingService
from rag_manager.services.music_player_payload import build_music_player_payload
from rag_manager.services.music_repository import MusicChromaRepository
from rag_manager.services.music_search_service import MusicSearchService, normalize_music_text
from rag_manager.tools.registry import declarations

MUSIC_DECLARATIONS = declarations("music")


class MusicTools:
    def __init__(self, settings: Settings) -> None:
        repository = MusicChromaRepository(
            path=settings.music_chroma_path,
            collection_name=settings.music_chroma_collection,
            embedding_dimensions=settings.music_embedding_dimensions,
        )
        embedding = OllamaMusicEmbeddingService(
            base_url=settings.ollama_base_url,
            model=settings.music_embedding_model,
            dimensions=settings.music_embedding_dimensions,
            timeout_seconds=settings.music_embedding_timeout_seconds,
        )
        self.search = MusicSearchService(repository=repository, embedding_service=embedding)
        self.allowed: dict[str, dict[str, Any]] = {}

    def search_music(self, args: dict[str, Any]) -> dict[str, Any]:
        request_kind = str(args.get("request_kind", "")).strip()
        extraction = {
            key: str(args[key]).strip()
            for key in ("title", "artist", "genre", "mood", "language")
            if isinstance(args.get(key), str) and args[key].strip()
        }
        if isinstance(args.get("query"), str) and args["query"].strip():
            extraction["search_query"] = args["query"].strip()
        if request_kind == "exact_track" and not extraction.get("title"):
            return {
                "status": "completed",
                "data": {
                    "status": "needs_details",
                    "requested_title": "",
                    "artist": extraction.get("artist", ""),
                    "candidates": [],
                    "query": extraction.get("search_query", ""),
                    "validation_error": "title_required_for_exact_track",
                },
            }
        if not extraction.get("search_query") and not extraction.get("title"):
            return {
                "status": "completed",
                "data": {
                    "status": "needs_details",
                    "requested_title": "",
                    "artist": "",
                    "candidates": [],
                    "query": "",
                },
            }

        requested_title = extraction.get("title", "")
        artist = extraction.get("artist", "")
        # An explicitly named title and artist is a deterministic catalog lookup.
        # Do not spend an embedding call merely to confirm an exact record.
        if request_kind == "exact_track" and requested_title and artist:
            candidates = self.search.find_exact_track(
                title=requested_title,
                artist=artist,
            )
            result = {"query": extraction.get("search_query", "")}
            if not candidates:
                # The requested track is unavailable. These are only verified
                # same-artist alternatives; they remain candidates so session
                # selection and subsequent play use one consistent list.
                candidates = self.search.find_by_artist(artist, top_k=3)
                self.allowed = {
                    str(item.get("record_id")): item
                    for item in candidates
                    if item.get("record_id")
                }
                return {
                    "status": "completed",
                    "data": {
                        "status": "not_found",
                        "requested_title": requested_title,
                        "artist": artist,
                        "candidates": candidates,
                        "query": result["query"],
                    },
                }
        else:
            result = self.search.search(extraction)
            candidates = result.get("candidates", [])
            # A named title without an artist is deliberately only a retrieval
            # fallback. It may be a similar title, so do not mark it as the
            # requested track or allow automatic playback.
            if request_kind == "exact_track" and requested_title and not artist:
                self.allowed = {
                    str(item.get("record_id")): item
                    for item in candidates
                    if item.get("record_id")
                }
                return {
                    "status": "completed",
                    "data": {
                        "status": "not_found",
                        "requested_title": requested_title,
                        "artist": "",
                        "candidates": candidates,
                        "query": result.get("query", ""),
                    },
                }
        self.allowed = {
            str(item.get("record_id")): item
            for item in candidates
            if item.get("record_id")
        }
        exact_matches = self._exact_title_matches(candidates, requested_title)
        if not candidates:
            return {
                "status": "completed",
                "data": {
                    "status": "not_found",
                    "requested_title": requested_title,
                    "artist": artist,
                    "candidates": [],
                    "query": result.get("query", ""),
                },
            }
        return {
            "status": "completed",
            "data": {
                "status": "found",
                "requested_title": requested_title,
                "artist": artist,
                "candidates": candidates,
                "exact_matches": exact_matches,
                "query": result.get("query", ""),
            },
        }

    def play_music(
        self,
        args: dict[str, Any],
        *,
        allowed_candidates: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        record_id = str(args.get("record_id", ""))
        allowed = allowed_candidates if allowed_candidates is not None else self.allowed
        candidate = allowed.get(record_id)
        if not candidate:
            return {"status": "error", "error": {"code": "candidate_not_allowed"}}
        return {"status": "completed", "data": build_music_player_payload(candidate)}

    @staticmethod
    def _exact_title_matches(candidates: list[dict[str, Any]], title: str) -> list[dict[str, Any]]:
        requested = normalize_music_text(title)
        if not requested:
            return []
        return [
            candidate
            for candidate in candidates
            if requested in {
                normalize_music_text(str(alias))
                for alias in candidate.get("title_aliases", [])
                if isinstance(alias, str)
            }
        ]
