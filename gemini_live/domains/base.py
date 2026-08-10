"""Contracts shared by all Gemini Live domains.

The Live session core never needs to understand Weather, Music, or another
business domain.  It routes an approved Gemini tool call to this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DomainRequest:
    """Bounded, server-owned context supplied to one domain tool execution."""

    query: str
    history: tuple[dict[str, str], ...] = ()


@dataclass
class DomainResult:
    """Server-owned result returned by a domain after one Live tool call.

    Domains report only their verified outcome and state.  The shared
    orchestrator is the single place that turns this result into the final
    JSON function response sent to Gemini Live.
    """

    status: str
    context: dict[str, Any] = field(default_factory=dict)
    presentation: Any | None = None
    detail: str | None = None


class LiveDomain(ABC):
    """Extension point implemented once per business domain."""

    @property
    @abstractmethod
    def domain_id(self) -> str:
        """Stable identifier, for example ``weather`` or ``music``."""

    @property
    @abstractmethod
    def tool_declarations(self) -> tuple[dict[str, Any], ...]:
        """Gemini function declarations owned by this domain."""

    @property
    @abstractmethod
    def prompt_guidance(self) -> str:
        """Domain-specific guidance appended to the shared Live prompt."""

    @abstractmethod
    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        request: DomainRequest,
        context: dict[str, Any],
    ) -> DomainResult:
        """Validate and execute one tool call owned by this domain."""
