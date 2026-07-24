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
            return self._search_response({
                "status": "needs_details",
                "requested_title": "",
                "artist": extraction.get("artist", ""),
                "candidates": [],
                "query": extraction.get("search_query", ""),
                "validation_error": "title_required_for_exact_track",
            })
        if not extraction.get("search_query") and not extraction.get("title"):
            return self._search_response({
                "status": "needs_details",
                "requested_title": "",
                "artist": "",
                "candidates": [],
                "query": "",
            })

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
                return self._search_response({
                    "status": "not_found",
                    "requested_title": requested_title,
                    "artist": artist,
                    "candidates": candidates,
                    "query": result["query"],
                })
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
                return self._search_response({
                    "status": "not_found",
                    "requested_title": requested_title,
                    "artist": "",
                    "candidates": candidates,
                    "query": result.get("query", ""),
                })
        self.allowed = {
            str(item.get("record_id")): item
            for item in candidates
            if item.get("record_id")
        }
        exact_matches = self._exact_title_matches(candidates, requested_title)
        if not candidates:
            return self._search_response({
                "status": "not_found",
                "requested_title": requested_title,
                "artist": artist,
                "candidates": [],
                "query": result.get("query", ""),
            })
        return self._search_response({
            "status": "found",
            "requested_title": requested_title,
            "artist": artist,
            "candidates": candidates,
            "exact_matches": exact_matches,
            "query": result.get("query", ""),
        })

    @staticmethod
    def _search_response(data: dict[str, Any]) -> dict[str, Any]:
        """Keep full search data for UI/session while minimizing the LLM turn."""

        return {
            "status": "completed",
            "data": data,
            "_llm_response": MusicTools._llm_search_payload(data),
        }

    @staticmethod
    def _llm_search_payload(data: dict[str, Any]) -> dict[str, Any]:
        candidates = data.get("candidates")
        compact_candidates = []
        if isinstance(candidates, list):
            for candidate in candidates[:5]:
                if not isinstance(candidate, dict):
                    continue
                artists = candidate.get("artists")
                compact_candidates.append({
                    "record_id": str(candidate.get("record_id") or ""),
                    "title": str(candidate.get("title") or ""),
                    "artists": [str(item) for item in artists if isinstance(item, str)] if isinstance(artists, list) else [],
                    "version": str(candidate.get("version") or ""),
                    "content_type": str(candidate.get("content_type") or ""),
                })
        exact_matches = data.get("exact_matches")
        exact_match_record_ids = [
            str(item.get("record_id"))
            for item in exact_matches
            if isinstance(item, dict) and item.get("record_id")
        ] if isinstance(exact_matches, list) else []
        return {
            "status": str(data.get("status") or ""),
            "requested_title": str(data.get("requested_title") or ""),
            "requested_artist": str(data.get("artist") or ""),
            "candidates": compact_candidates,
            "exact_match_record_ids": exact_match_record_ids,
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
        canonical_matches = [
            candidate
            for candidate in candidates
            if MusicTools._is_canonical_exact_match(candidate, requested)
        ]
        # A catalog item can retain the song's short title as an alias even when
        # its canonical title identifies a different version (for example, a
        # stage performance).  Prefer a canonical-title match so an explicit
        # request for the song does not become an artificial disambiguation.
        if canonical_matches:
            return canonical_matches
        return [
            candidate
            for candidate in candidates
            if requested in {
                normalize_music_text(str(alias))
                for alias in candidate.get("title_aliases", [])
                if isinstance(alias, str)
            }
        ]

    @staticmethod
    def _is_canonical_exact_match(candidate: dict[str, Any], requested_title: str) -> bool:
        canonical_title = normalize_music_text(str(candidate.get("title") or ""))
        if canonical_title == requested_title:
            return True
        # Imported catalog titles sometimes include an artist/collaborator
        # prefix.  Treat the remainder as exact only when the prefix is one of
        # the verified candidate artists; this does not make a stage/version
        # title an exact match merely because it shares an alias.
        artists = candidate.get("artists")
        return isinstance(artists, list) and any(
            canonical_title.startswith(normalize_music_text(str(artist)))
            and canonical_title.endswith(requested_title)
            for artist in artists
            if isinstance(artist, str) and normalize_music_text(artist)
        )
