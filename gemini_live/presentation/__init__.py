"""Reusable rendering and verified Live fact-pack contracts."""

from .base import DomainPresentationAdapter, PresentationRenderer
from .pipeline import LiveFactPack, PreparedPresentation, PresentationPipeline, PresentationRequest
from .renderer import JinjaPresentationRenderer
from .schemas import RenderedPanel, TemplateCapabilities

__all__ = [
    "DomainPresentationAdapter",
    "JinjaPresentationRenderer",
    "LiveFactPack",
    "PresentationPipeline",
    "PresentationRequest",
    "PresentationRenderer",
    "PreparedPresentation",
    "RenderedPanel",
    "TemplateCapabilities",
]
