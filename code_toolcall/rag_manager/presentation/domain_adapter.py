"""Shared contract for a presentation-capable domain."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .capabilities import load_template_metadata
from .compiler import compile_presentation_plan
from .planner import plan_presentation
from .schemas import CompiledPresentationPlan, GroundedFact, PresentationPlan


class PresentationDomainAdapter(ABC):
    """Translate one validated domain dataset into reusable presentation facts."""

    domain_id: str
    system_instruction: str

    def load_template_metadata(self, template_id: str) -> dict[str, Any]:
        return load_template_metadata(self.domain_id, template_id)

    @abstractmethod
    def build_candidate_facts(
        self,
        domain_data: dict[str, Any],
        *,
        compact_data: dict[str, Any],
        presentation_capabilities: dict[str, Any],
    ) -> list[GroundedFact]:
        """Return factual candidates calculated only from validated domain data."""

    @abstractmethod
    def fallback_plan(
        self,
        domain_data: dict[str, Any],
        capabilities: dict[str, Any],
        grounded_facts: list[GroundedFact],
    ) -> PresentationPlan:
        """Return a smallest truthful plan when the LLM output is unusable."""

    @abstractmethod
    def resolve_target(
        self,
        capability: dict[str, Any] | None,
        entity: dict[str, Any],
        compact_data: dict[str, Any],
    ) -> str | None:
        """Resolve a semantic target while validating this domain's entities."""

    def plan(
        self,
        runtime: Any,
        *,
        query: str,
        history: list[dict[str, Any]] | None,
        domain_data: dict[str, Any],
        template_id: str,
        capabilities: dict[str, Any],
        grounded_facts: list[GroundedFact],
        on_valid_step: Any = None,
    ) -> dict[str, Any]:
        return plan_presentation(
            runtime,
            query=query,
            history=history,
            template_id=template_id,
            capabilities=capabilities,
            grounded_facts=grounded_facts,
            system_instruction=self.system_instruction,
            fallback_plan=lambda: self.fallback_plan(domain_data, capabilities, grounded_facts),
            on_valid_step=on_valid_step,
        )

    def compile(
        self,
        plan: PresentationPlan,
        *,
        template_metadata: dict[str, Any],
        compact_data: dict[str, Any],
        grounded_facts: list[GroundedFact],
    ) -> CompiledPresentationPlan:
        return compile_presentation_plan(
            plan,
            template_metadata=template_metadata,
            compact_data=compact_data,
            target_resolver=self.resolve_target,
            grounded_facts=grounded_facts,
        )
