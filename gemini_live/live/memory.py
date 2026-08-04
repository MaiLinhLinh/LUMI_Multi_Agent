"""Server-owned bounded memory for reconnect-safe Live sessions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionMemory:
    history: list[dict[str, str]] = field(default_factory=list)
    domain_contexts: dict[str, dict[str, object]] = field(default_factory=dict)

    def append(self, role: str, content: str) -> None:
        if role not in {"user", "assistant"} or not content.strip():
            return
        self.history.append({"role": role, "content": content.strip()[:4_000]})
        del self.history[:-6]


class SessionMemoryStore:
    """In-memory store for the first migration stage; persistence is replaceable."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionMemory] = {}

    def get(self, session_id: str) -> SessionMemory:
        return self._sessions.setdefault(session_id, SessionMemory())
