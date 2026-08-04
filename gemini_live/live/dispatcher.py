"""Domain-neutral dispatch of Gemini Live function calls."""

from __future__ import annotations

from typing import Any

from gemini_live.domains import DomainRequest, DomainToolResult, LiveDomainRegistry


class UnknownLiveToolError(ValueError):
    pass


class LiveToolDispatcher:
    def __init__(self, registry: LiveDomainRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        request: DomainRequest,
        domain_contexts: dict[str, dict[str, Any]],
    ) -> DomainToolResult:
        domain = self._registry.domain_for_tool(tool_name)
        if domain is None:
            raise UnknownLiveToolError(f"No registered domain owns tool {tool_name!r}.")
        current_context = dict(domain_contexts.get(domain.domain_id, {}))
        result = await domain.execute_tool(
            tool_name,
            arguments,
            request=request,
            context=current_context,
        )
        domain_contexts[domain.domain_id] = dict(result.context)
        return result
