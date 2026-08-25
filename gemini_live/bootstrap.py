"""Application composition root for registered Gemini Live domains."""

from __future__ import annotations

from gemini_live.domains import LiveDomainRegistry
from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.weather import WeatherLiveDomain
from gemini_live.presentation import PresentationPipeline
from gemini_live.presentation.request_domain import PresentationRequestLiveDomain
from gemini_live.settings import Settings
from gemini_live.template_engine.template_manager import TemplateManager


def create_presentation_pipeline(settings: Settings) -> PresentationPipeline:
    """Create the shared renderer with its domain-neutral Template Manager."""

    return PresentationPipeline(template_manager=TemplateManager(settings))


def create_domain_registry(settings: Settings) -> LiveDomainRegistry:
    """Register domains without exposing shared presentation infrastructure."""
    registry = LiveDomainRegistry()
    registry.register(WeatherLiveDomain(settings))
    registry.register(EducationLiveDomain())
    registry.register(PresentationRequestLiveDomain(
        supported_domain_ids=registry.domain_ids,
        presentation_instruction_for=registry.presentation_instruction_for,
    ))
    return registry
