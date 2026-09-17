"""A domain-neutral native-tool agent that plans one replacement panel."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types
from openai import AsyncOpenAI

from gemini_live_2.catalogs.domains import DomainRegistry, ManifestError
from gemini_live_2.catalogs.resources import SharedResourceError, SharedResourceRegistry, SharedResources
from gemini_live_2.catalogs.templates import TemplateCatalogError
from gemini_live_2.panel import ActiveSurfaceSummary
from gemini_live_2.gateway import (
    CapabilityExecutionContext,
    DomainGateway,
    GatewayConfigurationError,
    GatewayExecutionError,
    GatewayPermissionError,
)
from gemini_live_2.panel.contracts import (
    ContractValidationError,
    CreateSurfacePlan,
    DataAlias,
    DataBundle,
    PatchSurfacePlan,
    SurfacePlanCommand,
    UseExistingSurfaceTemplate,
    surface_plan_command_from_dict,
)
from gemini_live_2.settings import Settings
from gemini_live_2.search.capabilities import PlanAgentSearchService, SearchExecutionError
from gemini_live_2.widgets import WidgetPropsError, WidgetRegistry
from .prompts import SurfacePlanPromptBuilder
from .tools import (
    CALL_CAPABILITY,
    DESCRIBE_TEMPLATE,
    DESCRIBE_WIDGETS,
    SEARCH_IMAGE,
    SEARCH_WEB,
    cerebras_tools,
    gemini_tools,
)


logger = logging.getLogger("lumi.plan_agent")
_MAX_TOOL_STEPS = 6
_MAX_INVALID_FINAL_JSON_RETRIES = 1
PlanTelemetryCallback = Callable[[dict[str, Any]], Awaitable[None]]



class PlanAgentError(RuntimeError):
    """Raised for configuration, model, tool-loop, or decision failures."""


class InvalidFinalJsonError(PlanAgentError):
    """The model returned a final decision that is not syntactically valid JSON."""


def _invalid_final_json_feedback(error: InvalidFinalJsonError) -> str:
    return (
        "FINAL JSON PARSE ERROR\n"
        "Your immediately preceding final response was not valid JSON. It was not accepted, "
        "and no Surface has been created.\n"
        f"Parser error: {error}\n"
        "Do not call any tool. Keep all verified data and prior tool results unchanged. "
        "Return the corrected final decision now as exactly one valid JSON object, with no markdown or other text."
    )


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanAgentError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanAgentError(f"{field_name} must be an object.")
    return value


def _log_payload(value: object, *, limit: int = 2_000) -> str:
    """Make planning diagnostics readable without dumping unbounded model data."""

    try:
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):  # pragma: no cover - defensive logging only.
        text = repr(value)
    return text if len(text) <= limit else f"{text[:limit]}…<truncated>"


def _payload_bytes(value: object) -> int:
    """Report serialized context/result size without logging its content."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):  # pragma: no cover - telemetry only.
        encoded = repr(value).encode("utf-8", errors="replace")
    return len(encoded)


def _bundle_summary(bundle: DataBundle) -> dict[str, Any]:
    data = dict(bundle.data)
    search_results = data.get("search_results")
    return {
        "data_keys": sorted(data),
        "alias_ids": [alias.id for alias in bundle.alias_catalog],
        "search_result_count": len(search_results) if isinstance(search_results, list) else 0,
    }


def _bundle_for_agent(bundle: DataBundle) -> dict[str, Any]:
    return {"data": dict(bundle.data), "aliases": [alias.to_dict() for alias in bundle.alias_catalog]}


def _search_results(value: object, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PlanAgentError(f"{field_name} must be an array.")
    results: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise PlanAgentError(f"{field_name}[{index}] must be an object.")
        if item.get("kind") not in {"web", "image"} or not isinstance(item.get("result_id"), str):
            raise PlanAgentError(
                f"{field_name}[{index}] requires kind 'web' or 'image' and a non-empty result_id."
            )
        results.append(dict(item))
    return results


def _merge_bundles(current: DataBundle, update: DataBundle) -> DataBundle:
    if current.domain_id != update.domain_id:
        raise PlanAgentError("domain capability returned data for another domain.")
    merged_data = dict(current.data)
    update_data = dict(update.data)
    current_results = merged_data.pop("search_results", None)
    update_results = update_data.pop("search_results", None)
    if current_results is not None or update_results is not None:
        merged_data["search_results"] = [
            *_search_results(current_results, "existing search_results"),
            *_search_results(update_results, "capability search_results"),
        ]
    duplicate_keys = set(merged_data).intersection(update_data)
    if duplicate_keys:
        raise PlanAgentError(
            "capability result conflicts with existing data keys: " + ", ".join(sorted(duplicate_keys)) + "."
        )
    aliases: tuple[DataAlias, ...] = current.alias_catalog + update.alias_catalog
    if len({alias.id for alias in aliases}) != len(aliases):
        raise PlanAgentError("capability result conflicts with an existing data alias.")
    return DataBundle(domain_id=current.domain_id, data={**merged_data, **update_data}, aliases=aliases)


def _usage_value(usage: object, *names: str) -> int | None:
    """Read one non-negative token counter from an SDK object or mapping."""

    for name in names:
        value = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _usage_child(usage: object, name: str) -> object | None:
    return usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)


def _usage_for_telemetry(usage: object) -> dict[str, int] | None:
    """Normalize provider usage without ever estimating tokens locally."""

    if usage is None:
        return None
    completion_details = _usage_child(usage, "completion_tokens_details")
    prompt_details = _usage_child(usage, "prompt_tokens_details")
    cached_input_tokens = _usage_value(
        usage, "cached_input_tokens", "cached_tokens", "cache_read_input_tokens"
    )
    if cached_input_tokens is None:
        cached_input_tokens = _usage_value(
            prompt_details, "cached_tokens", "cached_input_tokens", "cache_read_input_tokens"
        )
    fields = {
        "prompt_tokens": _usage_value(usage, "prompt_tokens", "prompt_token_count"),
        "output_tokens": _usage_value(
            usage, "completion_tokens", "response_token_count", "candidates_token_count"
        ),
        "reasoning_tokens": _usage_value(
            completion_details, "reasoning_tokens", "thoughts_token_count"
        ),
        "total_tokens": _usage_value(usage, "total_tokens", "total_token_count"),
        # Cache usage is provider-owned.  Keep it absent when the provider
        # does not report it; callers must never infer a cache hit locally.
        "cached_input_tokens": cached_input_tokens,
    }
    normalized = {name: value for name, value in fields.items() if value is not None}
    return normalized or None


async def _emit_plan_event(
    request: "PlanAgentRequest",
    event: str,
    **payload: Any,
) -> None:
    """Forward one bounded lifecycle event to the route owner's telemetry UI."""

    callback = request.telemetry_callback
    if callback is None:
        return
    await callback({
        "source": "plan_agent",
        "event": event,
        "plan_run_id": request.plan_run_id,
        "plan_attempt": request.plan_attempt,
        **payload,
    })


async def _emit_plan_telemetry(
    request: "PlanAgentRequest",
    *,
    provider: str,
    step: int,
    output_kind: str,
    tool_names: list[str],
    usage: object,
    model_elapsed_ms: int,
) -> None:
    """Forward one real model-output usage record to the request owner."""

    await _emit_plan_event(
        request,
        "plan_model_response_received",
        provider=provider,
        step=step,
        output_kind=output_kind,
        tool_names=tool_names,
        usage=_usage_for_telemetry(usage),
        model_elapsed_ms=model_elapsed_ms,
    )


async def _emit_plan_context_built(
    request: "PlanAgentRequest",
    *,
    provider: str,
    payload: Mapping[str, Any],
    system_instruction: str,
    context_build_ms: int,
) -> None:
    await _emit_plan_event(
        request,
        "plan_context_built",
        provider=provider,
        context_build_ms=context_build_ms,
        payload_bytes=_payload_bytes(payload),
        system_prompt_bytes=len(system_instruction.encode("utf-8")),
        history_items=len(request.recent_history),
        assets=len(payload.get("assets", [])),
        templates=len(payload.get("template_catalog", [])),
        widgets=len(payload.get("widget_index", [])),
        capabilities=len(payload.get("capabilities", [])),
        prior_search_results=_bundle_summary(request.initial_bundle).get("search_result_count", 0)
        if request.initial_bundle is not None
        else 0,
    )


def _safe_history(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, tuple):
        raise PlanAgentError("recent_history must be a tuple.")
    safe: list[dict[str, str]] = []
    for item in value[-6:]:
        if not isinstance(item, Mapping):
            raise PlanAgentError("each history item must be an object.")
        role = item.get("role")
        text = item.get("text")
        if role not in {"user", "assistant"} or not isinstance(text, str) or not text.strip():
            raise PlanAgentError("history entries require role user/assistant and non-empty text.")
        safe.append({"role": role, "text": text.strip()[:1200]})
    return tuple(safe)


@dataclass(frozen=True, slots=True)
class PlanAgentRequest:
    """Backend-owned context for one request that must create/replace a panel."""

    domain_id: str
    intent: str
    recent_history: tuple[dict[str, str], ...] = ()
    initial_bundle: DataBundle | None = None
    active_surface_summary: ActiveSurfaceSummary | None = None
    validation_feedback: Mapping[str, Any] | None = None
    runtime_feedback: Mapping[str, Any] | None = None
    session_id: str | None = None
    telemetry_callback: PlanTelemetryCallback | None = None
    plan_run_id: str | None = None
    plan_attempt: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _text(self.domain_id, "domain_id"))
        object.__setattr__(self, "intent", _text(self.intent, "intent"))
        object.__setattr__(self, "recent_history", _safe_history(self.recent_history))
        if self.initial_bundle is not None:
            if not isinstance(self.initial_bundle, DataBundle):
                raise PlanAgentError("initial_bundle must be a DataBundle.")
            if self.initial_bundle.domain_id != self.domain_id:
                raise PlanAgentError("initial_bundle must match domain_id.")
        if self.active_surface_summary is not None:
            if not isinstance(self.active_surface_summary, ActiveSurfaceSummary):
                raise PlanAgentError("active_surface_summary must be an ActiveSurfaceSummary.")
            if self.active_surface_summary.domain_id != self.domain_id:
                raise PlanAgentError("active_surface_summary must match domain_id.")
        if self.validation_feedback is not None:
            if not isinstance(self.validation_feedback, Mapping):
                raise PlanAgentError("validation_feedback must be an object.")
            object.__setattr__(self, "validation_feedback", dict(self.validation_feedback))
        if self.runtime_feedback is not None:
            if not isinstance(self.runtime_feedback, Mapping):
                raise PlanAgentError("runtime_feedback must be an object.")
            object.__setattr__(self, "runtime_feedback", dict(self.runtime_feedback))
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        if self.telemetry_callback is not None and not callable(self.telemetry_callback):
            raise PlanAgentError("telemetry_callback must be callable.")
        if self.plan_run_id is not None:
            object.__setattr__(self, "plan_run_id", _text(self.plan_run_id, "plan_run_id"))
        if isinstance(self.plan_attempt, bool) or not isinstance(self.plan_attempt, int) or self.plan_attempt < 1:
            raise PlanAgentError("plan_attempt must be a positive integer.")


@dataclass(frozen=True, slots=True)
class PlanAgentResult:
    """A lifecycle command together with the verified data used to plan it."""

    command: SurfacePlanCommand
    data_bundle: DataBundle


@dataclass(frozen=True, slots=True)
class _ToolExecution:
    """Backend result of one Plan Agent native-tool invocation."""

    response: dict[str, Any]
    data_bundle: DataBundle
    described_widget_ids: tuple[str, ...] = ()
    described_template_id: str | None = None


ClientFactory = Callable[..., Any]
CerebrasClientFactory = Callable[..., Any]


def _parse_command(value: object) -> SurfacePlanCommand:
    try:
        return surface_plan_command_from_dict(value)
    except ContractValidationError as exc:
        raise PlanAgentError(str(exc)) from exc


def _function_calls(response: Any) -> tuple[Any, ...]:
    """Support the SDK convenience field and the underlying candidate parts."""

    direct = getattr(response, "function_calls", None)
    if direct:
        return tuple(direct)
    candidates = getattr(response, "candidates", None) or ()
    if candidates:
        parts = getattr(getattr(candidates[0], "content", None), "parts", None) or ()
        return tuple(part.function_call for part in parts if getattr(part, "function_call", None) is not None)
    return ()


def _model_content(response: Any, calls: tuple[Any, ...]) -> types.Content:
    candidates = getattr(response, "candidates", None) or ()
    content = getattr(candidates[0], "content", None) if candidates else None
    if isinstance(content, types.Content):
        return content
    return types.Content(role="model", parts=[types.Part(function_call=call) for call in calls])


class PlanAgent:
    """Run a constrained native-function-call loop for one replacement panel."""

    def __init__(
        self,
        settings: Settings,
        *,
        domain_registry: DomainRegistry,
        domain_gateway: DomainGateway,
        widget_registry: WidgetRegistry,
        shared_resource_registry: SharedResourceRegistry,
        search_service: PlanAgentSearchService | None = None,
        client_factory: ClientFactory = genai.Client,
        cerebras_client_factory: CerebrasClientFactory = AsyncOpenAI,
        max_tool_steps: int = _MAX_TOOL_STEPS,
    ) -> None:
        if max_tool_steps < 0:
            raise PlanAgentError("max_tool_steps must not be negative.")
        self._settings = settings
        self._domain_registry = domain_registry
        self._domain_gateway = domain_gateway
        self._widget_registry = widget_registry
        self._shared_resource_registry = shared_resource_registry
        self._search_service = search_service
        self._client_factory = client_factory
        self._cerebras_client_factory = cerebras_client_factory
        self._max_tool_steps = max_tool_steps
        self._prompt_builder = SurfacePlanPromptBuilder()

    async def plan(self, request: PlanAgentRequest) -> PlanAgentResult:
        """Return the final decision plus the trusted bundle for the Compiler."""

        logger.info(
            "[PLAN_AGENT_START] provider=%s domain=%s intent=%r history=%d active_surface=%s "
            "compiler_feedback=%s runtime_feedback=%s tool_budget=%d",
            self._settings.planner_provider,
            request.domain_id,
            request.intent,
            len(request.recent_history),
            request.active_surface_summary is not None,
            request.validation_feedback is not None,
            request.runtime_feedback is not None,
            self._max_tool_steps,
        )

        if self._settings.planner_provider == "cerebras":
            return await self._plan_with_cerebras(request)
        if self._settings.planner_provider != "gemini":
            raise PlanAgentError("PLANNER_PROVIDER must be 'gemini' or 'cerebras'.")

        if not self._settings.plan_agent_api_key:
            raise PlanAgentError("GEMINI_API_KEY is not configured for the Plan Agent.")
        context_started = time.perf_counter()
        try:
            resources = self._domain_registry.load(request.domain_id)
            shared_resources = self._shared_resource_registry.load()
            capabilities = self._domain_gateway.capability_catalog(request.domain_id)
        except (ManifestError, SharedResourceError, GatewayConfigurationError, GatewayPermissionError) as exc:
            raise PlanAgentError(str(exc)) from exc

        bundle = request.initial_bundle or self._domain_gateway.empty_bundle(request.domain_id)
        logger.info(
            "[PLAN_AGENT_CONTEXT] provider=gemini assets=%d templates=%d widgets=%d capabilities=%s verified=%s",
            len(shared_resources.assets.assets),
            len(shared_resources.templates.for_plan_agent()),
            len(self._widget_registry.widget_ids()),
            [capability.id for capability in capabilities],
            _log_payload(_bundle_summary(bundle)),
        )
        payload = {
            "domain": resources.manifest.for_plan_agent(),
            "intent": request.intent,
            "recent_history": list(request.recent_history),
            "canvas": {"columns": 16, "rows": 10},
            "assets": shared_resources.assets.plan_agent_catalog(),
            "template_catalog": shared_resources.templates.for_plan_agent(),
            "widget_index": self._widget_registry.widget_index(),
            "capabilities": [capability.for_plan_agent() for capability in capabilities],
            "tool_budget": {"max_native_calls": self._max_tool_steps},
        "verified_data": _bundle_for_agent(bundle),
        "active_surface_summary": (
            request.active_surface_summary.to_dict()
            if request.active_surface_summary is not None
            else None
        ),
        }
        if request.validation_feedback is not None:
            payload["compiler_feedback"] = dict(request.validation_feedback)
        if request.runtime_feedback is not None:
            payload["runtime_feedback"] = dict(request.runtime_feedback)
        system_instruction = self._prompt_builder.build(domain_instruction=resources.plan_instruction)
        await _emit_plan_context_built(
            request,
            provider="gemini",
            payload=payload,
            system_instruction=system_instruction,
            context_build_ms=round((time.perf_counter() - context_started) * 1000),
        )
        messages: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=json.dumps(payload, ensure_ascii=False))])
        ]
        client = self._client_factory(api_key=self._settings.plan_agent_api_key)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[gemini_tools(capabilities)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        tool_call_count = 0
        tool_limit_feedback_sent = False
        invalid_final_json_retries = 0
        described_widget_ids: set[str] = set()
        described_template_ids: set[str] = set()

        while True:
            step = tool_call_count + 1
            await _emit_plan_event(
                request,
                "plan_model_request_sent",
                provider="gemini",
                step=step,
                model_messages=len(messages),
                prior_tool_results=tool_call_count,
            )
            model_started = time.perf_counter()
            response = await self._generate_response(client, messages, config)
            model_elapsed_ms = round((time.perf_counter() - model_started) * 1000)
            calls = _function_calls(response)
            await _emit_plan_telemetry(
                request,
                provider="gemini",
                step=step,
                output_kind="tool_call" if calls else "final_json",
                tool_names=[str(getattr(call, "name", "")) for call in calls],
                usage=getattr(response, "usage_metadata", None),
                model_elapsed_ms=model_elapsed_ms,
            )
            logger.info(
                "[PLAN_AGENT_MODEL_STEP] provider=gemini tool_calls_used=%d/%d calls=%s",
                tool_call_count,
                self._max_tool_steps,
                [getattr(call, "name", None) for call in calls],
            )
            if not calls:
                final_parse_started = time.perf_counter()
                try:
                    result = self._final_result(
                        response,
                        request.domain_id,
                        resources,
                        bundle,
                        described_widget_ids=described_widget_ids,
                        described_template_ids=described_template_ids,
                    )
                except InvalidFinalJsonError as exc:
                    if invalid_final_json_retries >= _MAX_INVALID_FINAL_JSON_RETRIES:
                        raise
                    invalid_final_json_retries += 1
                    logger.warning(
                        "[PLAN_AGENT_FINAL_JSON_RETRY] provider=gemini retry=%d detail=%s",
                        invalid_final_json_retries,
                        str(exc)[:500],
                    )
                    messages.append(_model_content(response, calls))
                    messages.append(types.Content(
                        role="user",
                        parts=[types.Part(text=_invalid_final_json_feedback(exc))],
                    ))
                    continue
                await _emit_plan_event(
                    request,
                    "plan_final_json_parsed",
                    provider="gemini",
                    step=step,
                    parse_ms=round((time.perf_counter() - final_parse_started) * 1000),
                    action=result.command.to_dict()["action"],
                )
                return result
            if tool_limit_feedback_sent:
                raise PlanAgentError("Plan Agent called a tool after receiving tool_limit_reached.")

            messages.append(_model_content(response, calls))
            function_responses: list[types.Part] = []
            remaining_tool_calls = self._max_tool_steps - tool_call_count
            for call_index, call in enumerate(calls, start=1):
                name = getattr(call, "name", None)
                arguments = _mapping(getattr(call, "args", None), f"{name} arguments")
                logger.info(
                    "[PLAN_AGENT_TOOL_CALL] provider=gemini step=%d/%d name=%s arguments=%s",
                    tool_call_count + call_index,
                    self._max_tool_steps,
                    name,
                    _log_payload(arguments),
                )
                if call_index > remaining_tool_calls:
                    response_data = {
                        "error": {
                            "code": "tool_limit_reached",
                            "message": (
                                "Native tool budget reached. Do not call another tool; use verified_data "
                                "already received to return the final surface plan."
                            ),
                        },
                    }
                    tool_limit_feedback_sent = True
                    function_responses.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=name,
                            id=getattr(call, "id", None),
                            response=response_data,
                        )
                    ))
                    continue
                await _emit_plan_event(
                    request,
                    "plan_tool_execution_started",
                    provider="gemini",
                    step=tool_call_count + call_index,
                    tool_name=str(name),
                    arguments_bytes=_payload_bytes(arguments),
                )
                tool_started = time.perf_counter()
                tool_result = self._execute_tool(
                    name=name,
                    arguments=arguments,
                    domain_id=request.domain_id,
                    shared_resources=shared_resources,
                    data_bundle=bundle,
                    session_id=request.session_id,
                )
                response_data = tool_result.response
                bundle = tool_result.data_bundle
                described_widget_ids.update(tool_result.described_widget_ids)
                if tool_result.described_template_id is not None:
                    described_template_ids.add(tool_result.described_template_id)
                await _emit_plan_event(
                    request,
                    "plan_tool_result_received",
                    provider="gemini",
                    step=tool_call_count + call_index,
                    tool_name=str(name),
                    tool_elapsed_ms=round((time.perf_counter() - tool_started) * 1000),
                    result_bytes=_payload_bytes(response_data),
                    bundle=_bundle_summary(bundle),
                )
                if isinstance(response_data, Mapping) and "error" not in response_data:
                    await _emit_plan_event(
                        request,
                        "tool_result_available",
                        tool_name=str(name),
                        arguments=dict(arguments),
                        response=dict(response_data),
                    )
                logger.info(
                    "[PLAN_AGENT_TOOL_RESPONSE] provider=gemini step=%d name=%s response=%s bundle=%s",
                    tool_call_count + call_index,
                    name,
                    _log_payload(response_data),
                    _log_payload(_bundle_summary(bundle)),
                )
                function_responses.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=name,
                        id=getattr(call, "id", None),
                        response=response_data,
                    )
                ))
            tool_call_count += min(len(calls), max(0, remaining_tool_calls))
            messages.append(types.Content(role="user", parts=function_responses))
            await _emit_plan_event(
                request,
                "plan_tool_results_appended",
                provider="gemini",
                step=tool_call_count,
                appended_results=len(function_responses),
                next_step=tool_call_count + 1,
            )

    async def _plan_with_cerebras(self, request: PlanAgentRequest) -> PlanAgentResult:
        """Run the same agent loop through Cerebras' OpenAI-compatible API."""

        if not self._settings.cerebras_api_key:
            raise PlanAgentError("CEREBRAS_API_KEY is not configured for the Plan Agent.")
        context_started = time.perf_counter()
        try:
            resources = self._domain_registry.load(request.domain_id)
            shared_resources = self._shared_resource_registry.load()
            capabilities = self._domain_gateway.capability_catalog(request.domain_id)
        except (ManifestError, SharedResourceError, GatewayConfigurationError, GatewayPermissionError) as exc:
            raise PlanAgentError(str(exc)) from exc

        bundle = request.initial_bundle or self._domain_gateway.empty_bundle(request.domain_id)
        logger.info(
            "[PLAN_AGENT_CONTEXT] provider=cerebras assets=%d templates=%d widgets=%d capabilities=%s verified=%s",
            len(shared_resources.assets.assets),
            len(shared_resources.templates.for_plan_agent()),
            len(self._widget_registry.widget_ids()),
            [capability.id for capability in capabilities],
            _log_payload(_bundle_summary(bundle)),
        )
        payload = {
            "domain": resources.manifest.for_plan_agent(),
            "intent": request.intent,
            "recent_history": list(request.recent_history),
            "canvas": {"columns": 16, "rows": 10},
            "assets": shared_resources.assets.plan_agent_catalog(),
            "template_catalog": shared_resources.templates.for_plan_agent(),
            "widget_index": self._widget_registry.widget_index(),
            "capabilities": [capability.for_plan_agent() for capability in capabilities],
            "tool_budget": {"max_native_calls": self._max_tool_steps},
            "verified_data": _bundle_for_agent(bundle),
            "active_surface_summary": (
                request.active_surface_summary.to_dict()
                if request.active_surface_summary is not None
                else None
            ),
        }
        if request.validation_feedback is not None:
            payload["compiler_feedback"] = dict(request.validation_feedback)
        if request.runtime_feedback is not None:
            payload["runtime_feedback"] = dict(request.runtime_feedback)
        system_instruction = self._prompt_builder.build(domain_instruction=resources.plan_instruction)
        await _emit_plan_context_built(
            request,
            provider="cerebras",
            payload=payload,
            system_instruction=system_instruction,
            context_build_ms=round((time.perf_counter() - context_started) * 1000),
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_instruction,
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        client = self._cerebras_client_factory(
            api_key=self._settings.cerebras_api_key,
            base_url="https://api.cerebras.ai/v1",
        )
        tool_call_count = 0
        tool_limit_feedback_sent = False
        invalid_final_json_retries = 0
        described_widget_ids: set[str] = set()
        described_template_ids: set[str] = set()

        while True:
            step = tool_call_count + 1
            await _emit_plan_event(
                request,
                "plan_model_request_sent",
                provider="cerebras",
                step=step,
                model_messages=len(messages),
                prior_tool_results=tool_call_count,
            )
            model_started = time.perf_counter()
            try:
                completion = await client.chat.completions.create(
                    model=self._settings.cerebras_planner_model,
                    messages=messages,
                    tools=cerebras_tools(capabilities),
                    parallel_tool_calls=False,
                    reasoning_effort="low"
                )
            except Exception as exc:
                logger.warning(
                    "[PLAN_AGENT_REQUEST_FAILED] provider=cerebras error_type=%s detail=%s",
                    type(exc).__name__, str(exc)[:500],
                )
                raise PlanAgentError("Plan Agent did not return a planning response.") from exc

            model_elapsed_ms = round((time.perf_counter() - model_started) * 1000)

            choices = getattr(completion, "choices", None) or ()
            if not choices:
                raise PlanAgentError("Cerebras returned no planning choice.")
            message = choices[0].message
            calls = tuple(getattr(message, "tool_calls", None) or ())
            await _emit_plan_telemetry(
                request,
                provider="cerebras",
                step=step,
                output_kind="tool_call" if calls else "final_json",
                tool_names=[
                    str(getattr(getattr(call, "function", None), "name", ""))
                    for call in calls
                ],
                usage=getattr(completion, "usage", None),
                model_elapsed_ms=model_elapsed_ms,
            )
            logger.info(
                "[PLAN_AGENT_MODEL_STEP] provider=cerebras tool_calls_used=%d/%d calls=%s",
                tool_call_count,
                self._max_tool_steps,
                [getattr(getattr(call, "function", None), "name", None) for call in calls],
            )
            if not calls:
                final_parse_started = time.perf_counter()
                try:
                    result = self._final_result_from_text(
                        getattr(message, "content", None),
                        request.domain_id,
                        resources,
                        bundle,
                        described_widget_ids=described_widget_ids,
                        described_template_ids=described_template_ids,
                    )
                except InvalidFinalJsonError as exc:
                    if invalid_final_json_retries >= _MAX_INVALID_FINAL_JSON_RETRIES:
                        raise
                    invalid_final_json_retries += 1
                    logger.warning(
                        "[PLAN_AGENT_FINAL_JSON_RETRY] provider=cerebras retry=%d detail=%s",
                        invalid_final_json_retries,
                        str(exc)[:500],
                    )
                    messages.append(message.model_dump(exclude_none=True))
                    messages.append({"role": "user", "content": _invalid_final_json_feedback(exc)})
                    continue
                await _emit_plan_event(
                    request,
                    "plan_final_json_parsed",
                    provider="cerebras",
                    step=step,
                    parse_ms=round((time.perf_counter() - final_parse_started) * 1000),
                    action=result.command.to_dict()["action"],
                )
                return result
            if tool_limit_feedback_sent:
                raise PlanAgentError("Plan Agent called a tool after receiving tool_limit_reached.")

            messages.append(message.model_dump(exclude_none=True))
            remaining_tool_calls = self._max_tool_steps - tool_call_count
            for call_index, call in enumerate(calls, start=1):
                function = getattr(call, "function", None)
                name = getattr(function, "name", None)
                raw_arguments = getattr(function, "arguments", None)
                try:
                    arguments = _mapping(json.loads(raw_arguments), f"{name} arguments")
                except (TypeError, json.JSONDecodeError, PlanAgentError) as exc:
                    raise PlanAgentError(f"Cerebras returned invalid arguments for {name}.") from exc
                logger.info(
                    "[PLAN_AGENT_TOOL_CALL] provider=cerebras step=%d/%d name=%s arguments=%s",
                    tool_call_count + call_index,
                    self._max_tool_steps,
                    name,
                    _log_payload(arguments),
                )
                if call_index > remaining_tool_calls:
                    response_data = {
                        "error": {
                            "code": "tool_limit_reached",
                            "message": (
                                "Native tool budget reached. Do not call another tool; use verified_data "
                                "already received to return the final surface plan."
                            ),
                        },
                    }
                    tool_limit_feedback_sent = True
                    messages.append({
                        "role": "tool",
                        "tool_call_id": getattr(call, "id", None),
                        "content": json.dumps(response_data, ensure_ascii=False),
                    })
                    continue
                await _emit_plan_event(
                    request,
                    "plan_tool_execution_started",
                    provider="cerebras",
                    step=tool_call_count + call_index,
                    tool_name=str(name),
                    arguments_bytes=_payload_bytes(arguments),
                )
                tool_started = time.perf_counter()
                tool_result = self._execute_tool(
                    name=name,
                    arguments=arguments,
                    domain_id=request.domain_id,
                    shared_resources=shared_resources,
                    data_bundle=bundle,
                    session_id=request.session_id,
                )
                response_data = tool_result.response
                bundle = tool_result.data_bundle
                described_widget_ids.update(tool_result.described_widget_ids)
                if tool_result.described_template_id is not None:
                    described_template_ids.add(tool_result.described_template_id)
                await _emit_plan_event(
                    request,
                    "plan_tool_result_received",
                    provider="cerebras",
                    step=tool_call_count + call_index,
                    tool_name=str(name),
                    tool_elapsed_ms=round((time.perf_counter() - tool_started) * 1000),
                    result_bytes=_payload_bytes(response_data),
                    bundle=_bundle_summary(bundle),
                )
                if isinstance(response_data, Mapping) and "error" not in response_data:
                    await _emit_plan_event(
                        request,
                        "tool_result_available",
                        tool_name=str(name),
                        arguments=dict(arguments),
                        response=dict(response_data),
                    )
                logger.info(
                    "[PLAN_AGENT_TOOL_RESPONSE] provider=cerebras step=%d name=%s response=%s bundle=%s",
                    tool_call_count + call_index,
                    name,
                    _log_payload(response_data),
                    _log_payload(_bundle_summary(bundle)),
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": getattr(call, "id", None),
                    "content": json.dumps(response_data, ensure_ascii=False),
                })
                await _emit_plan_event(
                    request,
                    "plan_tool_results_appended",
                    provider="cerebras",
                    step=tool_call_count + call_index,
                    appended_results=1,
                    next_step=tool_call_count + call_index + 1,
                )
            tool_call_count += min(len(calls), max(0, remaining_tool_calls))

    def _describe_widgets(
        self,
        *,
        widget_ids: object,
    ) -> dict[str, Any]:
        if not isinstance(widget_ids, list) or not widget_ids:
            raise PlanAgentError("describe_widgets.widget_ids must be a non-empty array.")
        normalized_ids = tuple(_text(item, "widget_id") for item in widget_ids)
        if len(normalized_ids) != len(set(normalized_ids)):
            raise PlanAgentError("describe_widgets.widget_ids must not contain duplicates.")
        widgets: list[dict[str, Any]] = []
        for widget_id in normalized_ids:
            try:
                widget = self._widget_registry.get(widget_id)
            except WidgetPropsError as exc:
                raise PlanAgentError(str(exc)) from exc
            public_contract = widget.public_contract()
            state_fields = public_contract.pop("state_fields", {})
            widget_description: dict[str, Any] = {
                "id": widget.widget_id,
                "purpose": widget.purpose,
                "props": public_contract.pop("props"),
                "initial_state": {
                    "type": "object",
                    "required": False,
                    "default": widget.default_state,
                    "fields": state_fields,
                    "description": (
                        "State khởi tạo của block. Chỉ dùng field do widget khai báo; "
                        "Gemini Live quyết định các state update tiếp theo."
                    ),
                },
            }
            for field_name in ("allowed_child_widget_ids", "interactions"):
                if field_name in public_contract:
                    widget_description[field_name] = public_contract[field_name]
            widgets.append(widget_description)
        return {"widgets": widgets}

    @staticmethod
    def _describe_template(*, template_id: object, shared_resources: SharedResources) -> dict[str, Any]:
        requested_id = _text(template_id, "template_id")
        try:
            template = shared_resources.templates.load_layout_template(requested_id)
        except TemplateCatalogError as exc:
            raise PlanAgentError(str(exc)) from exc
        return {
            "template_id": template.template_id,
            "description": template.description,
            "semantic_spec": template.semantic_spec.to_dict(),
            "blocks": [block.to_dict() for block in template.blocks],
            "bindings": [binding.to_dict() for binding in template.bindings],
        }

    def _execute_tool(
        self,
        *,
        name: object,
        arguments: Mapping[str, Any],
        domain_id: str,
        shared_resources: SharedResources,
        data_bundle: DataBundle,
        session_id: str | None,
    ) -> _ToolExecution:
        """Dispatch a declared tool to the backend service that implements it."""

        if name == DESCRIBE_WIDGETS:
            response = self._describe_widgets(
                widget_ids=arguments.get("widget_ids"),
            )
            return _ToolExecution(
                response=response,
                data_bundle=data_bundle,
                described_widget_ids=tuple(widget["id"] for widget in response["widgets"]),
            )
        if name == DESCRIBE_TEMPLATE:
            response = self._describe_template(
                template_id=arguments.get("template_id"), shared_resources=shared_resources
            )
            return _ToolExecution(
                response=response,
                data_bundle=data_bundle,
                described_template_id=response["template_id"],
            )
        if name == SEARCH_WEB:
            if set(arguments) != {"query"}:
                return _ToolExecution(
                    response={"error": {"message": "search arguments must contain exactly query."}},
                    data_bundle=data_bundle,
                )
            return self._execute_search(
                tool_name=SEARCH_WEB,
                query=arguments.get("query"),
                domain_id=domain_id,
                data_bundle=data_bundle,
                session_id=session_id,
            )
        if name == SEARCH_IMAGE:
            if set(arguments) != {"query"}:
                return _ToolExecution(
                    response={"error": {"message": "search arguments must contain exactly query."}},
                    data_bundle=data_bundle,
                )
            return self._execute_search(
                tool_name=SEARCH_IMAGE,
                query=arguments.get("query"),
                domain_id=domain_id,
                data_bundle=data_bundle,
                session_id=session_id,
            )
        if name != CALL_CAPABILITY:
            raise PlanAgentError("Plan Agent called an unsupported native function.")

        capability_id = _text(arguments.get("capability_id"), "capability_id")
        capability_arguments = _mapping(arguments.get("arguments"), "capability arguments")
        try:
            update = self._domain_gateway.execute(
                domain_id=domain_id,
                capability_id=capability_id,
                arguments=capability_arguments,
                execution_context=(
                    CapabilityExecutionContext(session_id=session_id) if session_id else None
                ),
            )
        except GatewayExecutionError as exc:
            return _ToolExecution(
                response={"capability_id": capability_id, "error": {"message": str(exc)}},
                data_bundle=data_bundle,
            )
        except (GatewayConfigurationError, GatewayPermissionError) as exc:
            raise PlanAgentError(str(exc)) from exc
        merged_bundle = _merge_bundles(data_bundle, update)
        return _ToolExecution(
            response={"capability_id": capability_id, "verified_data": _bundle_for_agent(update)},
            data_bundle=merged_bundle,
        )

    def _execute_search(
        self,
        *,
        tool_name: str,
        query: object,
        domain_id: str,
        data_bundle: DataBundle,
        session_id: str | None,
    ) -> _ToolExecution:
        if self._search_service is None:
            return _ToolExecution(
                response={"error": {"message": "Plan Agent search service is not configured."}},
                data_bundle=data_bundle,
            )
        try:
            update = (
                self._search_service.search_web(
                    query=query, domain_id=domain_id, session_id=session_id
                )
                if tool_name == SEARCH_WEB
                else self._search_service.search_image(
                    query=query, domain_id=domain_id, session_id=session_id
                )
            )
        except SearchExecutionError as exc:
            return _ToolExecution(
                response={"error": {"message": str(exc)}},
                data_bundle=data_bundle,
            )
        merged_bundle = _merge_bundles(data_bundle, update)
        return _ToolExecution(
            response={"verified_data": _bundle_for_agent(update)},
            data_bundle=merged_bundle,
        )

    async def _generate_response(
        self,
        client: Any,
        messages: list[types.Content],
        config: types.GenerateContentConfig,
    ) -> Any:
        try:
            return await client.aio.models.generate_content(
                model=self._settings.plan_agent_model,
                contents=messages,
                config=config,
            )
        except Exception as exc:
            logger.warning("[PLAN_AGENT_REQUEST_FAILED] error_type=%s detail=%s", type(exc).__name__, str(exc)[:500])
            raise PlanAgentError("Plan Agent did not return a planning response.") from exc

    @staticmethod
    def _final_result(
        response: Any,
        domain_id: str,
        resources: Any,
        bundle: DataBundle,
        *,
        described_widget_ids: set[str],
        described_template_ids: set[str],
    ) -> PlanAgentResult:
        response_text = getattr(response, "text", None)
        return PlanAgent._final_result_from_text(
            response_text,
            domain_id,
            resources,
            bundle,
            described_widget_ids=described_widget_ids,
            described_template_ids=described_template_ids,
        )

    @staticmethod
    def _final_result_from_text(
        response_text: object,
        domain_id: str,
        resources: Any,
        bundle: DataBundle,
        *,
        described_widget_ids: set[str],
        described_template_ids: set[str],
    ) -> PlanAgentResult:
        if not isinstance(response_text, str) or not response_text.strip():
            raise PlanAgentError("Plan Agent returned neither a function call nor a final JSON decision.")
        logger.info("[PLAN_AGENT_RAW_DECISION] chars=%d output=%s", len(response_text), response_text)
        try:
            command = _parse_command(json.loads(response_text))
        except json.JSONDecodeError as exc:
            logger.warning("[PLAN_AGENT_INVALID_DECISION] error_type=%s detail=%s", type(exc).__name__, str(exc)[:500])
            raise InvalidFinalJsonError(str(exc)) from exc
        except PlanAgentError as exc:
            logger.warning("[PLAN_AGENT_INVALID_DECISION] error_type=%s detail=%s", type(exc).__name__, str(exc)[:500])
            raise PlanAgentError("Plan Agent returned an invalid final decision.") from exc
        if isinstance(command, UseExistingSurfaceTemplate):
            if command.template_id not in described_template_ids:
                raise PlanAgentError(
                    "Plan Agent must call describe_template before using: " + command.template_id
                )
        logger.info(
            "[PLAN_AGENT_FINAL_DECISION] action=%s verified=%s described_widgets=%s described_templates=%s",
            command.to_dict()["action"],
            _log_payload(_bundle_summary(bundle)),
            sorted(described_widget_ids),
            sorted(described_template_ids),
        )
        return PlanAgentResult(command=command, data_bundle=bundle)
