"""Extension interface for the shared presentation pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .planner_schemas import GroundedFact, PresentationPlan
from .schemas import RenderedPanel


class PresentationRenderer(ABC):
    """Shared renderer interface; independent from any domain fact contract."""

    @abstractmethod
    def render(self, *, domain_id: str, template_id: str, data: dict[str, Any]) -> RenderedPanel:
        """Render normalized domain data into a trusted presentation panel."""


class DomainPresentationAdapter(ABC):
    """Domain facts and target resolution used by the shared pipeline.

    Rendering, Planner invocation, schema validation and compilation remain
    domain-neutral responsibilities of PresentationPipeline.
    """

    @property
    @abstractmethod
    def domain_id(self) -> str:
        """Stable domain identifier."""

    @abstractmethod
    def build_candidate_facts(
        self,
        domain_data: dict[str, Any],
        *,
        compact_data: dict[str, Any],
        presentation_capabilities: dict[str, Any],
    ) -> list[GroundedFact]:
        """Produce verified facts with their permitted visual evidence."""

    @abstractmethod
    def planner_guidance(self) -> str:
        """Domain-specific presentation guidance for the shared Planner LLM."""

    def planner_context(self) -> dict[str, Any]:
        """Optional domain-specific context for the shared Planner input.

        Most domains need no additional context, so they inherit an empty
        object. Domains with an interaction mode can override this without
        changing the shared Planner envelope.
        """
        return {}

    def live_presentation_instruction(self) -> str:
        """Return optional guidance sent with this domain's verified fact pack.

        This is deliberately separate from the connection-level Live guidance.
        A domain can describe how Gemini should turn its facts into narration at
        the moment a tool response makes a rendered presentation available.
        Domains that do not need extra presentation guidance keep the shared
        behavior by returning an empty string.
        """

        return ""

    def live_presentation_context(self) -> dict[str, Any]:
        """Return optional domain context that accompanies a Live fact pack."""

        return {}

    def live_visual_stage_context(
        self,
        *,
        domain_data: dict[str, Any],
        compact_data: dict[str, Any],
        view_model: dict[str, Any],
    ) -> dict[str, Any]:
        """Return trusted state used to render an optional template stage map.

        The default is intentionally empty: templates without a visual stage
        map continue to use the shared Live fact-pack contract unchanged.
        """

        return {}

    @abstractmethod
    def fallback_plan(
        self,
        domain_data: dict[str, Any],
        capabilities: dict[str, Any],
        grounded_facts: list[GroundedFact],
    ) -> PresentationPlan:
        """Return a safe minimal plan if the Planner response is unusable."""

    @abstractmethod
    def resolve_target(
        self,
        capability: dict[str, Any] | None,
        entity: dict[str, Any],
        compact_data: dict[str, Any],
    ) -> str | None:
        """Resolve semantic evidence to a concrete, validated DOM target."""
