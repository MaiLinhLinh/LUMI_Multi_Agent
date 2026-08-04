"""Shared presentation orchestration for every business domain.

Domains provide a normalized view model, selected template ID, and an adapter.
This module owns the common rendering, capability discovery, planning, and
Compiler validation sequence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .base import DomainPresentationAdapter
from .capabilities import load_template_metadata, presentation_capabilities
from .contract_compiler import CompiledPresentationPlan
from .contract_compiler import compile_presentation_plan
from .planner_runtime import plan_presentation
from .planner_schemas import GroundedFact
from .renderer import JinjaPresentationRenderer
from .schemas import RenderedPanel


_PRESENT_ID = re.compile(r'data-present-id\s*=\s*["\']([^"\']+)["\']')


@dataclass(frozen=True)
class PreparedPresentation:
    """Trusted output of the shared presentation pipeline."""

    panel: RenderedPanel
    template_metadata: dict[str, Any]
    declared_capabilities: dict[str, dict[str, Any]]
    grounded_facts: list[GroundedFact]
    compiled_plan: CompiledPresentationPlan
    concrete_animation_capabilities: dict[str, list[str]]


@dataclass(frozen=True)
class PresentationRequest:
    """Domain output consumed by the shared pipeline after a successful tool call."""

    domain_id: str
    template_id: str
    view_model: dict[str, Any]
    adapter: DomainPresentationAdapter
    domain_data: dict[str, Any]
    compact_data: dict[str, Any]


class PresentationPipeline:
    """Run presentation stages shared by all business domains.

    Domains provide a view model and DomainPresentationAdapter. This class owns
    renderer, metadata, Planner and Compiler implementation details.
    """

    def __init__(
        self,
        *,
        planner_runtime: Any,
        renderer: JinjaPresentationRenderer | None = None,
    ) -> None:
        self._planner_runtime = planner_runtime
        self._renderer = renderer or JinjaPresentationRenderer()

    def prepare(
        self,
        *,
        request: PresentationRequest,
        query: str,
        history: list[dict[str, Any]] | None,
    ) -> PreparedPresentation:
        if request.adapter.domain_id != request.domain_id:
            raise ValueError("Presentation adapter does not belong to the requested domain.")
        if self._planner_runtime is None:
            raise RuntimeError("Presentation planner runtime is unavailable.")

        panel = self._renderer.render(
            domain_id=request.domain_id,
            template_id=request.template_id,
            data=request.view_model,
        )
        metadata = load_template_metadata(request.domain_id, request.template_id)
        declared = presentation_capabilities(metadata)
        facts = request.adapter.build_candidate_facts(
            request.domain_data,
            compact_data=request.compact_data,
            presentation_capabilities=declared,
        )
        planned = plan_presentation(
            self._planner_runtime,
            query=query,
            history=history,
            template_id=request.template_id,
            capabilities=declared,
            grounded_facts=facts,
            system_instruction=request.adapter.planner_guidance(),
            fallback_plan=lambda: request.adapter.fallback_plan(
                request.domain_data, declared, facts
            ),
        )
        compiled = compile_presentation_plan(
            planned["plan"],
            template_metadata=metadata,
            compact_data=request.compact_data,
            target_resolver=request.adapter.resolve_target,
            grounded_facts=facts,
        )
        return PreparedPresentation(
            panel=panel,
            template_metadata=metadata,
            declared_capabilities=declared,
            grounded_facts=facts,
            compiled_plan=compiled,
            concrete_animation_capabilities=concrete_animation_capabilities(panel.html, declared),
        )

    @staticmethod
    def live_fact_pack(
        request: PresentationRequest,
        prepared: PreparedPresentation,
    ) -> list[dict[str, Any]]:
        """Expose only verified facts and their rendered visual evidence to Live."""
        packed: list[dict[str, Any]] = []
        for fact in prepared.grounded_facts:
            item = fact.model_dump(mode="json", exclude_none=True)
            target_id = request.adapter.resolve_target(
                prepared.declared_capabilities.get(fact.focus),
                fact.entity,
                request.compact_data,
            )
            allowed_effects = prepared.concrete_animation_capabilities.get(target_id or "", [])
            if target_id and allowed_effects:
                item["visual_cue"] = {
                    "target_id": target_id,
                    "allowed_effects": allowed_effects,
                }
            packed.append(item)
        return packed


def concrete_animation_capabilities(
    html: str,
    declared_capabilities: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Intersect rendered DOM anchors with template-declared legal effects."""
    target_ids = sorted({match.group(1) for match in _PRESENT_ID.finditer(html)})
    allowed: dict[str, list[str]] = {}
    for target_id in target_ids:
        effects: set[str] = set()
        for capability in declared_capabilities.values():
            if not isinstance(capability, dict):
                continue
            if target_id == capability.get("target_id") or _matches_pattern(
                target_id, capability.get("target_pattern")
            ):
                effects.update(
                    effect
                    for effect in capability.get("allowed_effects", [])
                    if isinstance(effect, str)
                )
        if effects:
            allowed[target_id] = sorted(effects)
    return allowed


def _matches_pattern(target_id: str, pattern: Any) -> bool:
    if not isinstance(pattern, str) or not pattern:
        return False
    escaped = re.escape(pattern)
    escaped = re.sub(r"\\\{[a-z_]+\\\}", r"[0-9]+", escaped)
    return re.fullmatch(escaped, target_id) is not None
