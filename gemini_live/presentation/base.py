"""Extension interface for the shared presentation pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .planner_schemas import GroundedFact
from .schemas import RenderedPanel


class PresentationRenderer(ABC):
    """Shared renderer interface; independent from any domain fact contract."""

    @abstractmethod
    def render(self, *, domain_id: str, template_id: str, data: dict[str, Any]) -> RenderedPanel:
        """Render normalized domain data into a trusted presentation panel."""


class DomainPresentationAdapter(ABC):
    """Domain facts and target resolution used by the shared Live pipeline."""

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
