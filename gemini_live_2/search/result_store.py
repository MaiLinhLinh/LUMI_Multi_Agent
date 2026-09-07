"""Ephemeral, session-scoped results returned by backend search providers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from secrets import token_urlsafe
from threading import Lock
from time import monotonic


class SearchResultStoreError(ValueError):
    """A search result cannot safely be used by the current session."""


@dataclass(frozen=True, slots=True)
class StoredWebResult:
    title: str
    snippet: str
    source_url: str


@dataclass(frozen=True, slots=True)
class StoredImageResult:
    remote_url: str
    source_url: str
    caption: str


@dataclass(frozen=True, slots=True)
class _StoredResult:
    session_id: str
    kind: str
    value: StoredWebResult | StoredImageResult
    expires_at: float


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SearchResultStoreError(f"{field} must be a non-empty string.")
    return value.strip()


class SearchResultStore:
    """Keep provider results in RAM only for one Live session and a bounded TTL."""

    def __init__(self, *, ttl_seconds: float = 30 * 60, clock: Callable[[], float] = monotonic) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive number.")
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._results: dict[str, _StoredResult] = {}
        self._lock = Lock()

    def put_web(self, *, session_id: str, title: str, snippet: str, source_url: str) -> str:
        return self._put(session_id=session_id, result_id_prefix="web", kind="web", value=StoredWebResult(
            title=_text(title, "title"), snippet=_text(snippet, "snippet"), source_url=_text(source_url, "source_url"),
        ))

    def put_image(self, *, session_id: str, remote_url: str, source_url: str, caption: str) -> str:
        return self._put(session_id=session_id, result_id_prefix="img", kind="image", value=StoredImageResult(
            remote_url=_text(remote_url, "remote_url"), source_url=_text(source_url, "source_url"), caption=_text(caption, "caption"),
        ))

    def get_web(self, *, session_id: str, result_id: str) -> StoredWebResult:
        value = self._get(session_id=session_id, result_id=result_id, expected_kind="web")
        assert isinstance(value, StoredWebResult)
        return value

    def get_image(self, *, session_id: str, result_id: str) -> StoredImageResult:
        value = self._get(session_id=session_id, result_id=result_id, expected_kind="image")
        assert isinstance(value, StoredImageResult)
        return value

    def clear_session(self, session_id: str) -> None:
        safe_session_id = _text(session_id, "session_id")
        with self._lock:
            self._purge_expired_locked()
            for result_id, stored in tuple(self._results.items()):
                if stored.session_id == safe_session_id:
                    del self._results[result_id]

    def _put(self, *, session_id: str, result_id_prefix: str, kind: str, value: StoredWebResult | StoredImageResult) -> str:
        safe_session_id = _text(session_id, "session_id")
        with self._lock:
            self._purge_expired_locked()
            result_id = f"{result_id_prefix}_{token_urlsafe(9)}"
            while result_id in self._results:
                result_id = f"{result_id_prefix}_{token_urlsafe(9)}"
            self._results[result_id] = _StoredResult(
                session_id=safe_session_id, kind=kind, value=value, expires_at=self._clock() + self._ttl_seconds,
            )
            return result_id

    def _get(self, *, session_id: str, result_id: str, expected_kind: str) -> StoredWebResult | StoredImageResult:
        safe_session_id = _text(session_id, "session_id")
        safe_result_id = _text(result_id, "result_id")
        with self._lock:
            self._purge_expired_locked()
            stored = self._results.get(safe_result_id)
            if stored is None or stored.session_id != safe_session_id or stored.kind != expected_kind:
                raise SearchResultStoreError("search result is unavailable for this session; search again.")
            return stored.value

    def _purge_expired_locked(self) -> None:
        now = self._clock()
        for result_id, stored in tuple(self._results.items()):
            if stored.expires_at <= now:
                del self._results[result_id]
