"""Application composition root for registered Gemini Live domains."""

from __future__ import annotations

from gemini_live.domains import LiveDomainRegistry
from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.weather import WeatherLiveDomain
from gemini_live.presentation import PresentationPipeline
from gemini_live.settings import Settings


def create_presentation_pipeline(settings: Settings) -> PresentationPipeline:
    """Create the shared render-and-fact-preparation pipeline.

    ``settings`` remains an argument so the composition-root interface stays
    stable while narration is handled by Gemini Live.
    """
    del settings
    return PresentationPipeline()


def create_domain_registry(settings: Settings) -> LiveDomainRegistry:
    """Register domains without exposing shared presentation infrastructure."""
    registry = LiveDomainRegistry()
    registry.register(WeatherLiveDomain(settings))
    registry.register(EducationLiveDomain())
    return registry
