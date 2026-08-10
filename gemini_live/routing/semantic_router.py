"""Two-stage semantic routing before a domain-specific Gemini Live session.

The local rules deliberately decide only explicit, high-confidence requests.
Every other request is a normal Manager-LLM routing case, with recent history
as context for conversational follow-ups such as ``bằng năm`` or ``còn mai``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_WEATHER_MARKER = re.compile(r"thời\s*tiết|thoi\s*tiet|dự\s*báo\s*thời\s*tiết|du\s*bao\s*thoi\s*tiet")
_EDUCATION_MARKER = re.compile(
    r"phép\s*(?:cộng|trừ)|phep\s*(?:cong|tru)|học\s*toán|hoc\s*toan|bài\s*toán|bai\s*toan"
)
_RECENT_HISTORY_LIMIT = 4


class SemanticRoutingError(RuntimeError):
    """Raised when Manager routing cannot return one registered domain."""


@dataclass(frozen=True)
class RouteDecision:
    """The only routing result needed by the caller."""

    domain_id: str


class SemanticRouter:
    """Use explicit local rules first, then Gemma for all ambiguous requests."""

    def __init__(self, *, runtime: Any, domain_ids: tuple[str, ...]) -> None:
        if not domain_ids:
            raise ValueError("SemanticRouter requires at least one domain.")
        self._runtime = runtime
        self._domain_ids = tuple(domain_id.strip() for domain_id in domain_ids if domain_id.strip())
        if not self._domain_ids:
            raise ValueError("SemanticRouter requires non-empty domain IDs.")

    def route(self, *, query: str, history: list[dict[str, Any]] | None = None) -> RouteDecision:
        """Return a domain for one user request without guessing on LLM failure."""

        normalized_query = query.strip()
        if not normalized_query:
            raise SemanticRoutingError("Cannot route an empty user query.")

        rule_domain = self._route_by_rules(normalized_query)
        if rule_domain is not None:
            return RouteDecision(domain_id=rule_domain)

        return self._route_by_manager(normalized_query, history or [])

    def _route_by_rules(self, query: str) -> str | None:
        """Handle only clear domain markers; never infer conversational context."""

        text = query.casefold()
        candidates: list[str] = []
        if "weather" in self._domain_ids and _WEATHER_MARKER.search(text):
            candidates.append("weather")
        if "education" in self._domain_ids and _EDUCATION_MARKER.search(text):
            candidates.append("education")
        # A request mentioning two explicit domains belongs to Manager, which
        # can resolve it using its wording and the recent conversation.
        return candidates[0] if len(candidates) == 1 else None

    def _route_by_manager(self, query: str, history: list[dict[str, Any]]) -> RouteDecision:
        recent_history = [
            {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
            for item in history[-_RECENT_HISTORY_LIMIT:]
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and item.get("content")
        ]
        system_instruction = (
            "You are the domain routing manager for a Vietnamese assistant. "
            "Select exactly one domain for the latest user message. Use recent "
            "conversation only to resolve an ambiguous or short follow-up. "
            f"Valid domain IDs: {', '.join(self._domain_ids)}. "
            "Return only JSON matching the supplied schema."
        )
        user_text = json.dumps(
            {"query": query, "recent_history": recent_history},
            ensure_ascii=False,
        )
        result = self._runtime.generate_structured(
            system_instruction=system_instruction,
            user_text=user_text,
            json_schema={
                "type": "object",
                "properties": {"domain_id": {"type": "string", "enum": list(self._domain_ids)}},
                "required": ["domain_id"],
                "additionalProperties": False,
            },
        )
        payload = result.get("data") if isinstance(result, dict) else None
        domain_id = str(payload.get("domain_id", "")).strip() if isinstance(payload, dict) else ""
        if domain_id not in self._domain_ids:
            error = result.get("error") if isinstance(result, dict) else None
            raise SemanticRoutingError(f"Manager did not return a valid domain: {error or domain_id!r}")
        return RouteDecision(domain_id=domain_id)
