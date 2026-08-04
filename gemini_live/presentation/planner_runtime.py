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


logger = logging.getLogger("lumi.presentation")


def presentation_plan_json_schema(
    capabilities: dict[str, Any],
    grounded_facts: list[GroundedFact] | None = None,
) -> dict[str, Any]:
    """Build the planner contract from the selected template's capabilities.

    Pydantic supplies the stable plan shape. The Planner may select only a
    candidate fact and an allowed effect; it never writes a target or entity.
    """
    schema = deepcopy(PresentationPlan.model_json_schema())
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


class PresentationStepStreamParser:
    """Extract complete objects from the `steps` JSON array without trusting them."""

    def __init__(self) -> None:
        self._buffer = ""
        self._scan_index = 0
        self._array_started = False
        self._object_start: int | None = None
        self._depth = 0
        self._in_string = False
        self._escaped = False

    def feed(self, chunk: str) -> list[dict[str, Any]]:
        self._buffer += chunk
        if not self._array_started:
            marker = '"steps"'
            marker_index = self._buffer.find(marker)
            if marker_index < 0:
                return []
            array_index = self._buffer.find("[", marker_index + len(marker))
            if array_index < 0:
                return []
            self._array_started = True
            self._scan_index = array_index + 1

        completed: list[dict[str, Any]] = []
        for index in range(self._scan_index, len(self._buffer)):
            char = self._buffer[index]
            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif char == "\\":
                    self._escaped = True
                elif char == '"':
                    self._in_string = False
                continue
            if char == '"':
                self._in_string = True
                continue
            if self._object_start is None:
                if char == "{":
                    self._object_start = index
                    self._depth = 1
                continue
            if char == "{":
                self._depth += 1
            elif char == "}":
                self._depth -= 1
                if self._depth == 0:
                    try:
                        value = json.loads(self._buffer[self._object_start:index + 1])
                    except json.JSONDecodeError:
                        value = None
                    if isinstance(value, dict):
                        completed.append(value)
                    self._object_start = None
        self._scan_index = len(self._buffer)
        return completed


def plan_presentation(
    runtime: GeminiFunctionCallingRuntime,
    *,
    query: str,
    history: list[dict[str, Any]] | None,
    template_id: str,
    capabilities: dict[str, Any],
    grounded_facts: list[GroundedFact] | None = None,
    system_instruction: str,
    fallback_plan: Callable[[], PresentationPlan],
    on_valid_step: Callable[[PresentationStep], None] | None = None,
) -> dict[str, Any]:
    """Generate a plan; malformed/model failures become a safe deterministic plan."""
    parser = PresentationStepStreamParser()
    streamed_steps: list[PresentationStep] = []
    def is_allowed_step(step: PresentationStep) -> tuple[bool, str | None]:
        """Check a streaming object before it reaches the frontend."""
        if len(streamed_steps) >= 6:
            return False, "answer_budget_exceeded"
        reason = fact_error(step, capabilities, grounded_facts or [])
        if reason:
            return False, reason
        return True, None

    def receive_chunk(chunk: str) -> None:
        for raw_step in parser.feed(chunk):
            try:
                step = PresentationStep.model_validate(raw_step)
            except ValidationError as exc:
                logger.info(
                    "[PRESENTATION:STEP_REJECTED] source=stream reason=schema_invalid errors=%s",
                    exc.errors(include_url=False),
                )
                continue
            allowed, reason = is_allowed_step(step)
            if not allowed:
                logger.info(
                    "[PRESENTATION:STEP_REJECTED] source=stream reason=%s fact_id=%s effect=%s",
                    reason,
                    step.fact_id,
                    step.effect,
                )
                continue
            streamed_steps.append(step)
            logger.info(
                "[PRESENTATION:STEP_ACCEPTED] source=stream fact_id=%s effect=%s narration_chars=%s",
                step.fact_id,
                step.effect,
                len(step.narration),
            )
            if on_valid_step is not None:
                on_valid_step(step)

    recent_history = [item for item in (history or [])[-6:] if isinstance(item, dict)]
    user_text = json.dumps(
        {
            "query": query,
            "recent_history": recent_history,
            "grounded_facts": [fact.model_dump(mode="json") for fact in (grounded_facts or [])],
            "template_id": template_id,
            "template_capabilities": capabilities,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    runtime_schema = presentation_plan_json_schema(capabilities, grounded_facts)
    logger.info(
        "[PRESENTATION:PLANNER_INPUT] template=%s query_chars=%s history_items=%s facts=%s fact_ids=%s capabilities=%s payload_chars=%s",
        template_id,
        len(query),
        len(recent_history),
        len(grounded_facts or []),
        [fact.id for fact in grounded_facts or []],
        sorted(capabilities),
        len(user_text),
    )

    def generate(instruction: str) -> dict[str, Any]:
        return runtime.generate_structured(
            system_instruction=instruction,
            user_text=user_text,
            json_schema=runtime_schema,
            on_json_chunk=receive_chunk,
        )

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
            logger.info(
                "[PRESENTATION:PLAN_ACCEPTED] source=initial steps=%s fact_ids=%s",
                len(plan.steps),
                [step.fact_id for step in plan.steps],
            )
            return {"plan": plan, "usage": result.get("usage", {}), "fallback": False}
        raise ValueError("; ".join(errors))
    except (ValidationError, ValueError) as exc:
        logger.warning("[PRESENTATION:PLAN_REJECTED] reason=%s", exc)
        # If nothing was emitted, it is safe to ask the model for a corrected
        # complete plan.  Once a step has reached the browser we never rewrite it.
        if not streamed_steps:
            parser = PresentationStepStreamParser()
            retry_instruction = (
                system_instruction
                + "\n\nYour previous draft was rejected by the template validator: "
                + str(exc)
                + ". Return a complete replacement plan. Use an exact fact_id from the runtime schema."
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
                    logger.info(
                        "[PRESENTATION:PLAN_ACCEPTED] source=retry steps=%s fact_ids=%s",
                        len(retry_plan.steps),
                        [step.fact_id for step in retry_plan.steps],
                    )
                    return {"plan": retry_plan, "usage": retry.get("usage", {}), "fallback": False, "retried": True}
                logger.warning("[PRESENTATION:PLAN_REJECTED] source=retry reason=%s", "; ".join(retry_errors))
                result = retry
            except ValidationError as retry_exc:
                logger.warning("[PRESENTATION:PLAN_REJECTED] source=retry reason=%s", retry_exc)
                result = retry
        # Keep already validated streamed steps if a later part of the response
        # is malformed.  They have individually passed the same Pydantic gate
        # and therefore match any narration emitted before the final parse.
        if streamed_steps:
            logger.warning(
                "[PRESENTATION:PLAN_FALLBACK] source=partial_stream steps=%s fact_ids=%s error=%s",
                len(streamed_steps),
                [step.fact_id for step in streamed_steps],
                result.get("error"),
            )
            return {
                "plan": PresentationPlan(steps=streamed_steps[:6]),
                "usage": result.get("usage", {}),
                "fallback": True,
                "error": result.get("error") or {"type": "partial_presentation_plan"},
            }
        fallback = fallback_plan()
        logger.warning(
            "[PRESENTATION:PLAN_FALLBACK] source=deterministic fallback_fact_ids=%s error=%s",
            [step.fact_id for step in fallback.steps],
            result.get("error"),
        )
        return {
            "plan": fallback,
            "usage": result.get("usage", {}),
            "fallback": True,
            "error": result.get("error") or {"type": "invalid_presentation_plan"},
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
