"""Cerebras implementation of the Planner structured-output interface.

This runtime is deliberately limited to ``generate_structured``.  Gemini
continues to own Live voice, domain tool calling, and every existing backend
workflow; the shared PresentationPipeline only needs this one method from its
Planner runtime.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from cerebras.cloud.sdk import Cerebras
from gemini_live.trace import error as trace_error


logger = logging.getLogger("lumi.presentation")


def _strict_schema(schema: Any) -> Any:
    """Return a Cerebras strict-output compatible copy of a JSON schema.

    Cerebras requires ``additionalProperties: false`` at every object level
    and does not support array ``minItems`` / ``maxItems``, string
    ``minLength`` / ``maxLength``, or conditional ``allOf`` rules in strict
    mode.
    This is only a transport adaptation: the source Planner schema and the
    downstream Pydantic/Compiler validation remain unchanged.
    """

    if isinstance(schema, list):
        return [_strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    result = {key: _strict_schema(value) for key, value in schema.items()}
    # Fact-to-effect conditional rules remain enforced by the Compiler after
    # generation, but Cerebras does not accept ``allOf`` in strict schemas.
    result.pop("allOf", None)
    # Keep the scene-count guard in Pydantic after generation, but do not send
    # unsupported array constraints to Cerebras' strict-schema endpoint.
    if result.get("type") == "array":
        result.pop("minItems", None)
        result.pop("maxItems", None)
    if result.get("type") == "string":
        result.pop("minLength", None)
        result.pop("maxLength", None)
    if result.get("type") == "object":
        result["additionalProperties"] = False
    return result


def _value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


class CerebrasStructuredRuntime:
    """Native Cerebras Structured Output client used only by the Planner."""

    provider = "cerebras"

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("CEREBRAS_API_KEY is required when PLANNER_PROVIDER=cerebras.")
        if not model:
            raise ValueError("CEREBRAS_PLANNER_MODEL is required when PLANNER_PROVIDER=cerebras.")
        self.client = Cerebras(api_key=api_key)
        self.model = model

    def generate_structured(
        self,
        *,
        system_instruction: str,
        user_text: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate exactly one Planner object through Cerebras strict JSON."""

        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_text},
                ],
                temperature=0.1,
                max_completion_tokens=1024,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "presentation_plan",
                        "strict": True,
                        "schema": _strict_schema(json_schema),
                    },
                },
            )
            choices = getattr(response, "choices", None) or []
            message = getattr(choices[0], "message", None) if choices else None
            text = getattr(message, "content", None)
            if not isinstance(text, str) or not text:
                raise ValueError("Cerebras structured response has no message content.")
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError("Cerebras structured response must be a JSON object.")

            usage_raw = getattr(response, "usage", None)
            time_raw = getattr(response, "time_info", None)
            usage = {
                "stage": "presentation_planner",
                "mode": "structured_output",
                "provider": self.provider,
                "model": self.model,
                "inference_ms": round((time.perf_counter() - started) * 1000, 2),
                "input_tokens": _value(usage_raw, "prompt_tokens"),
                "output_tokens": _value(usage_raw, "completion_tokens"),
                "total_tokens": _value(usage_raw, "total_tokens"),
                "queue_ms": _milliseconds(_value(time_raw, "queue_time")),
                "prompt_ms": _milliseconds(_value(time_raw, "prompt_time")),
                "completion_ms": _milliseconds(_value(time_raw, "completion_time")),
                "server_total_ms": _milliseconds(_value(time_raw, "total_time")),
            }
            return {"data": payload, "usage": usage}
        except Exception as exc:
            trace_error("PLANNER_REQUEST_FAILED type=%s message=%s", exc.__class__.__name__, exc)
            return {
                "data": None,
                "usage": {
                    "stage": "presentation_planner",
                    "mode": "failed",
                    "provider": self.provider,
                    "model": self.model,
                    "inference_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
            }


def _milliseconds(seconds: Any) -> float | None:
    return round(float(seconds) * 1000, 2) if isinstance(seconds, (int, float)) else None
