"""Application composition root for registered Gemini Live domains."""

from __future__ import annotations

from gemini_live.domains import LiveDomainRegistry
from gemini_live.domains.weather import WeatherLiveDomain
from gemini_live.llm.function_calling_runtime import GeminiFunctionCallingRuntime
from gemini_live.presentation import PresentationPipeline
from gemini_live.settings import Settings


def create_presentation_pipeline(settings: Settings) -> PresentationPipeline:
    """Create the one shared Planner/render/compiler pipeline for the app."""
    planner_runtime = GeminiFunctionCallingRuntime(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
    )
    return PresentationPipeline(planner_runtime=planner_runtime)


def create_domain_registry(settings: Settings) -> LiveDomainRegistry:
    """Register domains without exposing shared presentation infrastructure."""
    registry = LiveDomainRegistry()
    registry.register(WeatherLiveDomain(settings))
    return registry
