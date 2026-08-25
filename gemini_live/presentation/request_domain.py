"""Shared Live tool for requesting a presentation before a template is chosen."""

from __future__ import annotations

from typing import Any, Callable

from gemini_live.domains.base import DomainRequest, DomainResult, LiveDomain

from .pipeline import PresentationRequest


CREATE_PRESENTATION_REQUEST_DECLARATION: dict[str, Any] = {
    "name": "create_presentation_request",
    "description": (
        "Create a presentation request for a domain when no business tool is needed. "
        "Use it to describe what the user should see; it does not create data or a layout."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "domain_id": {
                "type": "string",
                "description": "The registered domain that owns the requested presentation.",
            },
            "presentation_brief": {
                "type": "string",
                "description": "One concise Vietnamese sentence describing what the user wants to see.",
            },
        },
        "required": ["domain_id", "presentation_brief"],
        "additionalProperties": False,
    },
}


class PresentationRequestLiveDomain(LiveDomain):
    """Technical shared-tool owner; it has no business-domain narration."""

    def __init__(
        self,
        *,
        supported_domain_ids: tuple[str, ...],
        presentation_instruction_for: Callable[[str], str] | None = None,
    ) -> None:
        self._supported_domain_ids = frozenset(supported_domain_ids)
        self._presentation_instruction_for = presentation_instruction_for or (lambda _domain_id: "")

    @property
    def domain_id(self) -> str:
        return "presentation_request"

    @property
    def tool_declarations(self) -> tuple[dict[str, Any], ...]:
        """Expose only registered business domains to Gemini Live.

        The declaration is built per registry instance because the available
        domains are configured at startup, not fixed in this shared module.
        """
        parameters = CREATE_PRESENTATION_REQUEST_DECLARATION["parameters"]
        properties = parameters["properties"]
        domain_property = properties["domain_id"]
        return ({
            **CREATE_PRESENTATION_REQUEST_DECLARATION,
            "parameters": {
                **parameters,
                "properties": {
                    **properties,
                    "domain_id": {
                        **domain_property,
                        "enum": sorted(self._supported_domain_ids),
                    },
                },
            },
        },)

    @property
    def prompt_guidance(self) -> str:
        return (
            "Khi người dùng cần một nội dung trực quan nhưng không có hoặc không cần tool nghiệp vụ "
            "phù hợp, hãy gọi create_presentation_request. Truyền domain_id của domain phù hợp và "
            "presentation_brief là đúng một câu tiếng Việt mô tả điều người dùng muốn thấy."
        )

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        request: DomainRequest,
        context: dict[str, Any],
    ) -> DomainResult:
        del request
        if tool_name != "create_presentation_request":
            raise ValueError(f"Shared presentation domain does not own tool {tool_name!r}.")

        domain_id = arguments.get("domain_id")
        presentation_brief = arguments.get("presentation_brief")
        if not isinstance(domain_id, str) or domain_id not in self._supported_domain_ids:
            return DomainResult(
                status="invalid_arguments",
                context=dict(context),
                detail="domain_id must be a registered business domain.",
            )
        if not isinstance(presentation_brief, str) or not presentation_brief.strip():
            return DomainResult(
                status="invalid_arguments",
                context=dict(context),
                detail="presentation_brief must be a non-empty string.",
            )
        return DomainResult(
            status="completed",
            context=dict(context),
            presentation=PresentationRequest(
                domain_id=domain_id,
                presentation_brief=presentation_brief.strip(),
                presentation_instruction=self._presentation_instruction_for(domain_id),
            ),
        )
