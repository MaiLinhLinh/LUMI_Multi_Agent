"""Extension interface for the shared presentation pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .schemas import RenderedPanel


class PresentationRenderer(ABC):
    """Shared renderer interface; independent from any domain fact contract."""

    @abstractmethod
    def render(self, *, domain_id: str, template_id: str, data: dict[str, Any]) -> RenderedPanel:
        """Render normalized domain data into a trusted presentation panel."""


class DomainPresentationAdapter(ABC):
    """Domain-owned narration guidance and trusted stage state."""

    @property
    @abstractmethod
    def domain_id(self) -> str:
        """Stable domain identifier."""

    @abstractmethod
    def live_presentation_instruction(self) -> str:
        """Return this domain's presentation prompt from its prompt module."""

        return ""

    def live_visual_stage_context(
        self,
        *,
        render_data: dict[str, Any],
        template_id: str,
    ) -> dict[str, Any]:
        """Return trusted state used to render an optional template stage map.

        The default is intentionally empty for templates without dynamic stage
        state.
        """

        return {}
