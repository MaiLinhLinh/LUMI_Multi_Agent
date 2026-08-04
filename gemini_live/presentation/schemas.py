"""Domain-neutral presentation data contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateCapabilities:
    domain_id: str
    template_id: str
    targets: dict[str, frozenset[str]]


@dataclass(frozen=True)
class RenderedPanel:
    domain_id: str
    template_id: str
    html: str
    capabilities: TemplateCapabilities
