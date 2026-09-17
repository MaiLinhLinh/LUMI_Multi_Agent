"""Shared search backend used by the Plan Agent's native search tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from gemini_live_2.panel import DataBundle

from .brave import BraveImageResult, BraveSearchError, BraveWebResult
from .result_store import SearchResultStore


class SearchExecutionError(RuntimeError):
    """A shared Plan Agent search tool could not return verified data."""


class SearchClient(Protocol):
    def search_web(self, query: str) -> BraveWebResult: ...
    def search_image(self, query: str) -> BraveImageResult: ...


class PlanAgentSearchService:
    """Execute global ``search_web`` and ``search_image`` tools for any domain."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], SearchClient],
        result_store: SearchResultStore,
    ) -> None:
        self._client_factory = client_factory
        self._result_store = result_store

    def search_web(
        self, *, query: object, domain_id: str, session_id: str | None
    ) -> DataBundle:
        safe_query = _query(query)
        safe_session_id = _session_id(session_id)
        try:
            result = self._client_factory().search_web(safe_query)
            result_id = self._result_store.put_web(
                session_id=safe_session_id,
                title=result.title,
                snippet=result.snippet,
                source_url=result.source_url,
            )
        except BraveSearchError as exc:
            raise SearchExecutionError(str(exc)) from exc
        return DataBundle(domain_id=domain_id, data={
            "search_results": [{
                "kind": "web",
                "result_id": result_id,
                "title": result.title,
                "snippet": result.snippet,
                "source_url": result.source_url,
            }],
        })

    def search_image(
        self, *, query: object, domain_id: str, session_id: str | None
    ) -> DataBundle:
        safe_query = _query(query)
        safe_session_id = _session_id(session_id)
        try:
            result = self._client_factory().search_image(safe_query)
            result_id = self._result_store.put_image(
                session_id=safe_session_id,
                remote_url=result.remote_url,
                source_url=result.source_url,
                caption=result.caption,
            )
        except BraveSearchError as exc:
            raise SearchExecutionError(str(exc)) from exc
        return DataBundle(domain_id=domain_id, data={
            "search_results": [{
                "kind": "image",
                "result_id": result_id,
                "caption": result.caption,
                "source_url": result.source_url,
            }],
        })


def _query(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SearchExecutionError("search query must be a non-empty string.")
    if len(value.strip()) > 400:
        raise SearchExecutionError("search query must not exceed 400 characters.")
    return value.strip()


def _session_id(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SearchExecutionError("search requires an active session.")
    return value.strip()
