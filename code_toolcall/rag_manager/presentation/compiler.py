"""Compile a semantic presentation plan into frontend-safe presentation steps."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .capabilities import presentation_capabilities
from .schemas import (
    CompiledPresentationPlan,
    CompiledPresentationAction,
    CompiledPresentationStep,
    PresentationEffect,
    PresentationGesture,
    PresentationPlan,
    PresentationStep,
    GroundedFact,
)
from .speech_text import derive_speech_text


_ALLOWED_EFFECTS = frozenset(PresentationEffect.__args__)
_ALLOWED_GESTURES = frozenset(PresentationGesture.__args__)
TargetResolver = Callable[[dict[str, Any] | None, dict[str, Any], dict[str, Any]], str | None]


class PresentationCompileError(ValueError):
    """The trusted template metadata does not offer a safe presentation target."""


def compile_presentation_plan(
    plan: PresentationPlan,
    *,
    template_metadata: dict[str, Any],
    compact_data: dict[str, Any],
    target_resolver: TargetResolver,
    grounded_facts: list[GroundedFact] | None = None,
) -> CompiledPresentationPlan:
    """Map semantic focus names to known template anchors with safe fallbacks."""
    capabilities = presentation_capabilities(template_metadata)
    overview = capabilities.get("overview")
    if overview is None:
        raise PresentationCompileError("template has no safe overview capability")

    compiled_steps = [
        _compile_step(step, capabilities=capabilities, overview=overview, compact_data=compact_data, target_resolver=target_resolver, grounded_facts=grounded_facts or [])
        for step in plan.steps
    ]
    return CompiledPresentationPlan(steps=compiled_steps)


def _compile_step(
    step: PresentationStep,
    *,
    capabilities: dict[str, dict[str, Any]],
    overview: dict[str, Any],
    compact_data: dict[str, Any],
    target_resolver: TargetResolver,
    grounded_facts: list[GroundedFact],
) -> CompiledPresentationStep:
    fact = next((item for item in grounded_facts if item.id == step.fact_id), None)
    if fact is None:
        raise PresentationCompileError(f"unknown grounded fact: {step.fact_id}")
    capability = capabilities.get(fact.focus)
    target_id = target_resolver(capability, fact.entity, compact_data) if capability else None
    if target_id is None:
        capability = overview
        target_id = target_resolver(overview, {}, compact_data)
    if target_id is None:
        raise PresentationCompileError("overview capability has no safe target")

    effect = _safe_effect(step.effect, capability)
    actions = _evidence_actions(
        fact,
        capabilities=capabilities,
        compact_data=compact_data,
        target_resolver=target_resolver,
        fallback_target_id=target_id,
        fallback_effect=effect,
    )
    spoken_text, alignment_text = derive_speech_text(step.narration)
    return CompiledPresentationStep(
        narration=step.narration,
        spoken_text=spoken_text,
        alignment_text=alignment_text,
        target_id=target_id,
        effect=effect,
        gesture=_safe_gesture(step.gesture),
        actions=actions,
    )


def _evidence_actions(
    fact: GroundedFact | None,
    *,
    capabilities: dict[str, dict[str, Any]],
    compact_data: dict[str, Any],
    target_resolver: TargetResolver,
    fallback_target_id: str,
    fallback_effect: str,
) -> list[CompiledPresentationAction]:
    """Compile adapter-calculated evidence; never trust visual choices from LLM."""
    evidence = fact.visual_evidence if fact is not None else {}
    kind = evidence.get("kind") if isinstance(evidence, dict) else None
    if kind == "day_groups":
        capability = capabilities.get("day_summary")
        groups = evidence.get("groups", [])
        actions: list[CompiledPresentationAction] = []
        if capability and isinstance(groups, list):
            for group_index, group in enumerate(groups):
                indices = group.get("day_indices", []) if isinstance(group, dict) else []
                targets = [
                    target_resolver(capability, {"day_index": index}, compact_data)
                    for index in indices if isinstance(index, int) and not isinstance(index, bool)
                ]
                targets = [target for target in targets if target]
                if targets:
                    actions.append(CompiledPresentationAction(
                        target_ids=targets,
                        effect="draw_group_bracket",
                        start_ms=200 + group_index * 2300,
                        duration_ms=900,
                        payload={"label": group.get("label", ""), "timeline_ratio": 0.05 + group_index * 0.5},
                    ))
        if actions:
            return actions
    if kind == "chart_segment":
        chart = capabilities.get("temperature_trend")
        target = target_resolver(chart, {}, compact_data) if chart else None
        if target:
            return [CompiledPresentationAction(
                target_ids=[target], effect="trace_chart_segment", start_ms=180, duration_ms=1300,
                payload={"point_indices": evidence.get("point_indices", []), "timeline_ratio": 0.05},
            )]
    if kind == "temperature_range":
        chart = capabilities.get("temperature_trend")
        target = target_resolver(chart, {}, compact_data) if chart else None
        if target:
            return [CompiledPresentationAction(
                target_ids=[target], effect="draw_temperature_range", start_ms=180, duration_ms=1100,
                payload={"max_range_c": evidence.get("max_range_c"), "min_range_c": evidence.get("min_range_c"), "timeline_ratio": 0.05},
            )]
    return [CompiledPresentationAction(target_ids=[fallback_target_id], effect=fallback_effect)]


def _safe_effect(requested: str, capability: dict[str, Any]) -> str:
    allowed = capability.get("allowed_effects", [])
    allowed = [effect for effect in allowed if effect in _ALLOWED_EFFECTS]
    if not allowed:
        raise PresentationCompileError("capability has no supported effect")
    if requested in allowed:
        return requested
    if "highlight" in allowed:
        return "highlight"
    if "reveal" in allowed:
        return "reveal"
    return allowed[0]


def _safe_gesture(requested: str) -> str:
    return requested if requested in _ALLOWED_GESTURES else "explain"
