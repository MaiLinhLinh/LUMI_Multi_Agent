"""Unit tests for the independent multi-domain registry."""

from __future__ import annotations

import unittest
from typing import Any

from gemini_live.domains.base import DomainRequest, DomainResult, LiveDomain
from gemini_live.domains.registry import LiveDomainRegistry


class _DemoDomain(LiveDomain):
    def __init__(self, domain_id: str, tool_name: str) -> None:
        self._domain_id = domain_id
        self._tool_name = tool_name

    @property
    def domain_id(self) -> str:
        return self._domain_id

    @property
    def tool_declarations(self) -> tuple[dict[str, Any], ...]:
        return ({"name": self._tool_name, "parameters": {"type": "object"}},)

    @property
    def prompt_guidance(self) -> str:
        return f"Guidance for {self._domain_id}."

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any], *, request: DomainRequest, context: dict[str, Any]
    ) -> DomainResult:
        return DomainResult(status="completed")


class LiveDomainRegistryTests(unittest.TestCase):
    def test_registers_distinct_domains_and_routes_tools(self) -> None:
        registry = LiveDomainRegistry()
        weather = _DemoDomain("weather", "get_weather")
        music = _DemoDomain("music", "search_music")
        registry.register(weather)
        registry.register(music)

        self.assertEqual(registry.domain_for_tool("get_weather"), weather)
        self.assertEqual(registry.domain_for_tool("search_music"), music)
        self.assertEqual(registry.domain_ids, ("weather", "music"))
        self.assertIn("Guidance for weather.", registry.prompt_guidance())

    def test_rejects_duplicate_tool_owner(self) -> None:
        registry = LiveDomainRegistry()
        registry.register(_DemoDomain("weather", "get_weather"))
        with self.assertRaises(ValueError):
            registry.register(_DemoDomain("other", "get_weather"))


if __name__ == "__main__":
    unittest.main()
