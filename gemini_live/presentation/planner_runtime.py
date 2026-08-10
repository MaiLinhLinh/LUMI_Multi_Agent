"""Gemini-backed, schema-validated presentation planning."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from gemini_live.llm.function_calling_runtime import GeminiFunctionCallingRuntime

from .planner_schemas import GroundedFact, PresentationPlan, PresentationStep
from gemini_live.trace import trace, warning


logger = logging.getLogger("lumi.presentation")


_PLAN_QUALITY_INSTRUCTION = """Plan quality rules:
- Create from 3 to 6 steps, choosing the exact number needed to explain useful grounded facts without filler.
- Every step must contain one complete, non-empty Vietnamese sentence in narration.
- Never emit an empty string, a placeholder, or a step with no clear fact to present.
- If a fact does not need narration, omit that step rather than creating an incomplete one."""


def presentation_plan_json_schema(
    capabilities: dict[str, Any],
    grounded_facts: list[GroundedFact] | None = None,
) -> dict[str, Any]:
    """Build the planner contract from the selected template's capabilities.

    Pydantic supplies the stable plan shape. The Planner may select only a
    candidate fact and an allowed effect; it never writes a target or entity.
    """
    schema = deepcopy(PresentationPlan.model_json_schema())
    # This is a backend contract version, not a creative model decision.  Do
    # not ask the Planner to produce it; Pydantic supplies the fixed default.
    properties_root = schema.get("properties")
    if isinstance(properties_root, dict):
        properties_root.pop("schema_version", None)
    required_root = schema.get("required")
    if isinstance(required_root, list):
        schema["required"] = [item for item in required_root if item != "schema_version"]
    step_schema = schema["$defs"]["PresentationStep"]
    properties = step_schema["properties"]
    if grounded_facts:
        properties["fact_id"] = {
            "type": "string",
            "enum": [fact.id for fact in grounded_facts],
            "description": "Exact id of the grounded fact this step presents.",
        }
        required = step_schema.setdefault("required", [])
        if "fact_id" not in required:
            required.append("fact_id")
        rules = []
        for fact in grounded_facts:
            capability = capabilities.get(fact.focus) if isinstance(capabilities, dict) else None
            allowed = capability.get("allowed_effects") if isinstance(capability, dict) else None
            if isinstance(allowed, list) and allowed:
                rules.append({
                    "if": {"properties": {"fact_id": {"const": fact.id}}, "required": ["fact_id"]},
                    "then": {"properties": {"effect": {"enum": allowed}}},
                })
        if rules:
            step_schema["allOf"] = rules
    return schema


def fact_error(step: PresentationStep, capabilities: dict[str, Any], facts: list[GroundedFact]) -> str | None:
    """Validate a Planner choice against trusted fact/template metadata."""
    fact = next((item for item in facts if item.id == step.fact_id), None)
    if fact is None:
        return "unknown_fact"
    capability = capabilities.get(fact.focus) if isinstance(capabilities, dict) else None
    if not isinstance(capability, dict):
        return "fact_focus_not_supported"
    allowed_effects = capability.get("allowed_effects")
    if not isinstance(allowed_effects, list) or step.effect not in allowed_effects:
        return "effect_not_allowed"
    return None


def validate_plan_capabilities(
    plan: PresentationPlan,
    capabilities: dict[str, Any],
    grounded_facts: list[GroundedFact] | None = None,
    max_scenes: int | None = None,
) -> list[str]:
    """Validate the complete final plan, not only streamed objects."""
    errors: list[str] = []
    if max_scenes is not None and len(plan.steps) > max_scenes:
        errors.append("answer_budget_exceeded")
    for index, step in enumerate(plan.steps):
        reason = fact_error(step, capabilities, grounded_facts or [])
        if reason:
            errors.append(f"step[{index}]={reason}")
            continue
    return errors


def plan_presentation(
    runtime: GeminiFunctionCallingRuntime,
    *,
    query: str,
    history: list[dict[str, Any]] | None,
    template_id: str,
    capabilities: dict[str, Any],
    grounded_facts: list[GroundedFact] | None = None,
    domain_context: dict[str, Any] | None = None,
    system_instruction: str,
    fallback_plan: Callable[[], PresentationPlan],
) -> dict[str, Any]:
    """Generate one native structured plan, then validate it before compiling."""

    recent_history = [item for item in (history or [])[-6:] if isinstance(item, dict)]
    user_text = json.dumps(
        {
            "query": query,
            "recent_history": recent_history,
            "grounded_facts": [fact.model_dump(mode="json") for fact in (grounded_facts or [])],
            "template_id": template_id,
            "template_capabilities": capabilities,
            "domain_context": domain_context or {},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    runtime_schema = presentation_plan_json_schema(capabilities, grounded_facts)
    trace("PLANNER_INPUT_READY payload_chars=%s facts=%s", len(user_text), len(grounded_facts or []))

    def generate(instruction: str) -> dict[str, Any]:
        trace("PLANNER_REQUEST_SENT provider=%s model=%s", getattr(runtime, "provider", "gemini"), getattr(runtime, "model", "unknown"))
        result = runtime.generate_structured(
            system_instruction=f"{instruction}\n\n{_PLAN_QUALITY_INSTRUCTION}",
            user_text=user_text,
            json_schema=runtime_schema,
        )
        # Ignore a legacy/model-supplied version if one appears. Versioning is
        # owned by this backend, so the Planner never controls this field.
        data = result.get("data")
        if isinstance(data, dict):
            data = dict(data)
            data["schema_version"] = "presentation_plan.v1"
            result = {**result, "data": data}
        usage = result.get("usage") if isinstance(result, dict) else {}
        trace("PLANNER_RESPONSE_RECEIVED input_tokens=%s output_tokens=%s", (usage or {}).get("input_tokens"), (usage or {}).get("output_tokens"))
        return result

    result = generate(system_instruction)
    try:
        plan = PresentationPlan.model_validate(result.get("data"))
        errors = validate_plan_capabilities(
            plan,
            capabilities,
            grounded_facts,
            6,
        )
        if not errors:
            trace("PLANNER_PYDANTIC_VALIDATED steps=%s", len(plan.steps))
            return {"plan": plan, "usage": result.get("usage", {}), "fallback": False}
        raise ValueError("; ".join(errors))
    except (ValidationError, ValueError) as exc:
        warning("PLANNER_SCHEMA_REJECTED reason=%s", exc)
        retry_instruction = (
            system_instruction
            + "\n\nYour previous draft was rejected by the template validator: "
            + str(exc)
            + ". Return a complete replacement plan. Use an exact fact_id and an allowed effect from the runtime schema."
        )
        retry = generate(retry_instruction)
        try:
            retry_plan = PresentationPlan.model_validate(retry.get("data"))
            retry_errors = validate_plan_capabilities(
                retry_plan,
                capabilities,
                grounded_facts,
                6,
            )
            if not retry_errors:
                trace("PLANNER_PYDANTIC_VALIDATED source=retry steps=%s", len(retry_plan.steps))
                return {"plan": retry_plan, "usage": retry.get("usage", {}), "fallback": False, "retried": True}
            warning("PLANNER_SCHEMA_REJECTED source=retry reason=%s", "; ".join(retry_errors))
        except ValidationError as retry_exc:
            warning("PLANNER_SCHEMA_REJECTED source=retry reason=%s", retry_exc)
        fallback = fallback_plan()
        warning("PLANNER_FALLBACK fact_ids=%s error=%s", [step.fact_id for step in fallback.steps], retry.get("error") or result.get("error"))
        return {
            "plan": fallback,
            "usage": retry.get("usage", {}) or result.get("usage", {}),
            "fallback": True,
            "error": retry.get("error") or result.get("error") or {"type": "invalid_presentation_plan"},
        }


def fallback_presentation_plan(
    *,
    capabilities: dict[str, Any],
    grounded_facts: list[GroundedFact] | None = None,
    fallback_narration: str,
) -> PresentationPlan:
    """Build a domain-neutral minimal plan from an adapter-provided narration."""
    if grounded_facts:
        fact = grounded_facts[0]
        capability = capabilities.get(fact.focus, {}) if isinstance(capabilities, dict) else {}
        effects = capability.get("allowed_effects") if isinstance(capability, dict) else []
        effect = fact.effect_hint if fact.effect_hint in effects else (effects[0] if effects else "highlight")
        return PresentationPlan(steps=[PresentationStep(
            narration=fallback_narration,
            fact_id=fact.id,
            effect=effect,
            gesture="explain",
        )])
    raise ValueError("a presentation fallback requires at least one grounded fact")
