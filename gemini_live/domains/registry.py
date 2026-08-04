"""Tool-to-domain registry used by the Gemini Live session core."""

from __future__ import annotations

from typing import Any

from .base import LiveDomain


class LiveDomainRegistry:
    """Register independent domains without modifying the Live session core."""

    def __init__(self) -> None:
        self._domains: dict[str, LiveDomain] = {}
        self._tool_domains: dict[str, LiveDomain] = {}

    def register(self, domain: LiveDomain) -> None:
        domain_id = domain.domain_id.strip()
        if not domain_id:
            raise ValueError("Live domain_id must not be empty.")
        if domain_id in self._domains:
            raise ValueError(f"Live domain already registered: {domain_id}")

        declared_names: list[str] = []
        for declaration in domain.tool_declarations:
            name = declaration.get("name") if isinstance(declaration, dict) else None
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"Domain {domain_id} has a declaration without a tool name.")
            if name in self._tool_domains:
                owner = self._tool_domains[name].domain_id
                raise ValueError(f"Tool {name} is already owned by domain {owner}.")
            declared_names.append(name)

        self._domains[domain_id] = domain
        for name in declared_names:
            self._tool_domains[name] = domain

    def domain(self, domain_id: str) -> LiveDomain | None:
        return self._domains.get(domain_id)

    def domain_for_tool(self, tool_name: str) -> LiveDomain | None:
        return self._tool_domains.get(tool_name)

    def tool_declarations(self) -> list[dict[str, Any]]:
        """Return fresh declarations safe for the Gemini SDK to consume."""
        return [dict(item) for domain in self._domains.values() for item in domain.tool_declarations]

    def prompt_guidance(self) -> str:
        guidance = [domain.prompt_guidance.strip() for domain in self._domains.values()]
        return "\n\n".join(item for item in guidance if item)

    @property
    def domain_ids(self) -> tuple[str, ...]:
        return tuple(self._domains)
