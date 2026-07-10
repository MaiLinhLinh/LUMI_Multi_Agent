"""Simple in-memory cache used by data agents."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any


INFINITE_TTL: None = None


@dataclass
class CacheEntry:
    """Stored cache value."""

    value: Any
    expires_at: float | None


class MemoryCache:
    """Small dictionary-backed cache for local agent data.

    Passing ``ttl_seconds=None`` stores an entry without expiration.
    """

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0

    def get(self, key: str, default: Any = None) -> Any:
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return default
        if self._is_expired(entry):
            self.delete(key)
            self._misses += 1
            return default
        self._hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        expires_at = None
        if ttl_seconds is not INFINITE_TTL:
            expires_at = monotonic() + ttl_seconds
        self._entries[key] = CacheEntry(value=value, expires_at=expires_at)

    def delete(self, key: str) -> bool:
        if key not in self._entries:
            return False
        del self._entries[key]
        return True

    def clear(self) -> None:
        self._entries.clear()

    def contains(self, key: str) -> bool:
        if key not in self._entries:
            return False
        entry = self._entries[key]
        if self._is_expired(entry):
            self.delete(key)
        return key in self._entries

    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._entries),
        }

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _is_expired(entry: CacheEntry) -> bool:
        return entry.expires_at is not None and monotonic() >= entry.expires_at
