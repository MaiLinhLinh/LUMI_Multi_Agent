"""Domain capability factories for backend-owned Brave Search calls."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from gemini_live_2.gateway import (
    CapabilityDescriptor,
    CapabilityExecutionContext,
    DomainCapability,
    GatewayExecutionError,
)
from gemini_live_2.panel import DataBundle

from .brave import BraveImageResult, BraveSearchError, BraveWebResult
from .result_store import SearchResultStore


class SearchClient(Protocol):
    def search_web(self, query: str) -> BraveWebResult: ...
    def search_image(self, query: str) -> BraveImageResult: ...


def build_search_capabilities(
    *,
    domain_id: str,
    client_factory: Callable[[], SearchClient],
    quota: Any,
    result_store: SearchResultStore,
) -> tuple[DomainCapability, DomainCapability]:
    """Build two purpose-specific capabilities without exposing provider URLs for images."""

    def search_web(arguments: Mapping[str, Any], context: CapabilityExecutionContext) -> DataBundle:
        query = _query(arguments)
        try:
            quota.consume(context.session_id)
            result = client_factory().search_web(query)
            result_id = result_store.put_web(
                session_id=context.session_id,
                title=result.title,
                snippet=result.snippet,
                source_url=result.source_url,
            )
        except BraveSearchError as error:
            raise GatewayExecutionError(str(error)) from error
        return DataBundle(domain_id=domain_id, data={
            "search_results": [{
                "kind": "web",
                "result_id": result_id,
                "title": result.title,
                "snippet": result.snippet,
                "source_url": result.source_url,
            }],
        })

    def search_image(arguments: Mapping[str, Any], context: CapabilityExecutionContext) -> DataBundle:
        query = _query(arguments)
        try:
            quota.consume(context.session_id)
            result = client_factory().search_image(query)
            result_id = result_store.put_image(
                session_id=context.session_id,
                remote_url=result.remote_url,
                source_url=result.source_url,
                caption=result.caption,
            )
        except BraveSearchError as error:
            raise GatewayExecutionError(str(error)) from error
        return DataBundle(domain_id=domain_id, data={
            "search_results": [{
                "kind": "image",
                "result_id": result_id,
                "caption": result.caption,
                "source_url": result.source_url,
            }],
        })

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 400}},
    }
    return (
        DomainCapability(
            domain_id=domain_id,
            descriptor=CapabilityDescriptor(
                id="search_web",
                description="Tìm một nguồn web khi cần fact, giải thích hoặc nội dung cập nhật; trả title, snippet và source URL.",
                input_schema=schema,
            ),
            handler=search_web,
            requires_execution_context=True,
        ),
        DomainCapability(
            domain_id=domain_id,
            descriptor=CapabilityDescriptor(
                id="search_image",
                description="Tìm một ảnh khi Asset Catalog chưa có ảnh phù hợp; trả result_id và caption, không trả URL ảnh.",
                input_schema=schema,
            ),
            handler=search_image,
            requires_execution_context=True,
        ),
    )


def _query(arguments: Mapping[str, Any]) -> str:
    if set(arguments) != {"query"}:
        raise GatewayExecutionError("search arguments must contain exactly query.")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise GatewayExecutionError("search query must be a non-empty string.")
    return query.strip()
