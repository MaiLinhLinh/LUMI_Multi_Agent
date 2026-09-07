"""A domain-neutral native-tool agent that plans one replacement panel."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types
from openai import AsyncOpenAI

from gemini_live_2.catalogs.domains import DomainRegistry, ManifestError
from gemini_live_2.panel import ActiveSurfaceSummary
from gemini_live_2.catalogs.templates import TemplateCatalogError
from gemini_live_2.gateway import (
    CapabilityDescriptor,
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
from gemini_live_2.widgets import WidgetPropsError, WidgetRegistry
from .prompts import SurfacePlanPromptBuilder


logger = logging.getLogger("lumi.plan_agent")
_MAX_TOOL_STEPS = 10
_CALL_CAPABILITY_NAME = "call_capability"
_DESCRIBE_WIDGETS_NAME = "describe_widgets"
_DESCRIBE_TEMPLATE_NAME = "describe_template"



class PlanAgentError(RuntimeError):
    """Raised for configuration, model, tool-loop, or decision failures."""


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


def _bundle_summary(bundle: DataBundle) -> dict[str, Any]:
    data = dict(bundle.data)
    search_results = data.get("search_results")
    return {
        "data_keys": sorted(data),
        "alias_ids": [alias.id for alias in bundle.alias_catalog],
        "search_result_count": len(search_results) if isinstance(search_results, list) else 0,
    }


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


def _bundle_for_agent(bundle: DataBundle) -> dict[str, Any]:
    return {"data": dict(bundle.data), "aliases": [alias.to_dict() for alias in bundle.alias_catalog]}


def _merge_bundles(current: DataBundle, update: DataBundle) -> DataBundle:
    """Keep verified data, with an append-only journal for search results."""

    if current.domain_id != update.domain_id:
        raise PlanAgentError("domain capability returned data for another domain.")
    merged_data = dict(current.data)
    update_data = dict(update.data)
    current_search_results = merged_data.pop("search_results", None)
    update_search_results = update_data.pop("search_results", None)
    if current_search_results is not None or update_search_results is not None:
        existing = _search_results(current_search_results, "existing search_results")
        incoming = _search_results(update_search_results, "capability search_results")
        merged_data["search_results"] = [*existing, *incoming]

    duplicate_keys = set(merged_data).intersection(update_data)
    if duplicate_keys:
        raise PlanAgentError(
            "capability result conflicts with existing data keys: " + ", ".join(sorted(duplicate_keys)) + "."
        )
    aliases: tuple[DataAlias, ...] = current.alias_catalog + update.alias_catalog
    alias_ids = [alias.id for alias in aliases]
    if len(alias_ids) != len(set(alias_ids)):
        raise PlanAgentError("capability result conflicts with an existing data alias.")
    return DataBundle(domain_id=current.domain_id, data={**merged_data, **update_data}, aliases=aliases)


def _search_results(value: object, field_name: str) -> list[dict[str, Any]]:
    """Validate the agreed append-only search journal shape at the merge boundary."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise PlanAgentError(f"{field_name} must be an array.")
    results: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise PlanAgentError(f"{field_name}[{index}] must be an object.")
        kind = item.get("kind")
        result_id = item.get("result_id")
        if kind not in {"web", "image"} or not isinstance(result_id, str) or not result_id.strip():
            raise PlanAgentError(
                f"{field_name}[{index}] requires kind 'web' or 'image' and a non-empty result_id."
            )
        results.append(dict(item))
    return results


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


@dataclass(frozen=True, slots=True)
class PlanAgentResult:
    """A lifecycle command together with the verified data used to plan it."""

    command: SurfacePlanCommand
    data_bundle: DataBundle


ClientFactory = Callable[..., Any]
CerebrasClientFactory = Callable[..., Any]


def _parse_command(value: object) -> SurfacePlanCommand:
    try:
        return surface_plan_command_from_dict(value)
    except ContractValidationError as exc:
        raise PlanAgentError(str(exc)) from exc


def _native_tools(capabilities: tuple[CapabilityDescriptor, ...]) -> types.Tool:
    """Expose shared widget discovery and only the capabilities granted by the domain."""

    declarations = [
        types.FunctionDeclaration(
            name=_DESCRIBE_WIDGETS_NAME,
            description=(
                "Lấy contract đầy đủ (props, initial_state, children và interaction) "
                "cho các widget được phép dùng trong surface mới."
            ),
            parametersJsonSchema={
                "type": "object",
                "additionalProperties": False,
                "required": ["widget_ids"],
                "properties": {
                    "widget_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                },
            },
        ),
        types.FunctionDeclaration(
            name=_DESCRIBE_TEMPLATE_NAME,
            description="Return the binding contract for one reusable layout template.",
            parametersJsonSchema={
                "type": "object",
                "additionalProperties": False,
                "required": ["template_id"],
                "properties": {"template_id": {"type": "string"}},
            },
        ),
    ]
    if capabilities:
        declarations.append(types.FunctionDeclaration(
            name=_CALL_CAPABILITY_NAME,
            description=(
                "Gọi một capability đã được cấp quyền để lấy hoặc tạo dữ liệu tin cậy "
                "cần cho Presentation Plan. Chỉ dùng capability_id trong danh sách được cấp."
            ),
            parametersJsonSchema={
                "type": "object",
                "additionalProperties": False,
                "required": ["capability_id", "arguments"],
                "properties": {
                    "capability_id": {"type": "string", "enum": [item.id for item in capabilities]},
                    "arguments": {"type": "object"},
                },
            },
        ))
    return types.Tool(functionDeclarations=declarations)


def _cerebras_tools(capabilities: tuple[CapabilityDescriptor, ...]) -> list[dict[str, Any]]:
    """Return the same native tools in Cerebras/OpenAI chat-completions form."""

    declarations: list[dict[str, Any]] = [{
        "type": "function",
        "function": {
            "name": _DESCRIBE_WIDGETS_NAME,
            "description": (
                "Lấy contract đầy đủ (props, initial_state, children và interaction) "
                "cho các widget được phép dùng trong surface mới."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["widget_ids"],
                "properties": {
                    "widget_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                },
            },
        },
    }, {
        "type": "function",
        "function": {
            "name": _DESCRIBE_TEMPLATE_NAME,
            "description": "Return the binding contract for one reusable layout template.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["template_id"],
                "properties": {"template_id": {"type": "string"}},
            },
        },
    }]
    if capabilities:
        declarations.append({
            "type": "function",
            "function": {
                "name": _CALL_CAPABILITY_NAME,
                "description": (
                    "Gọi một capability đã được cấp quyền để lấy hoặc tạo dữ liệu tin cậy "
                    "cần cho Presentation Plan. Chỉ dùng capability_id trong danh sách được cấp."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["capability_id", "arguments"],
                    "properties": {
                        "capability_id": {"type": "string", "enum": [item.id for item in capabilities]},
                        "arguments": {"type": "object"},
                    },
                },
            },
        })
    return declarations


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
        try:
            resources = self._domain_registry.load(request.domain_id)
            capabilities = self._domain_gateway.capability_catalog(request.domain_id)
        except (ManifestError, GatewayConfigurationError, GatewayPermissionError) as exc:
            raise PlanAgentError(str(exc)) from exc

        bundle = request.initial_bundle or self._domain_gateway.empty_bundle(request.domain_id)
        logger.info(
            "[PLAN_AGENT_CONTEXT] provider=gemini assets=%d templates=%d widgets=%d capabilities=%s verified=%s",
            len(resources.assets.assets),
            len(resources.templates.for_plan_agent()),
            len(resources.manifest.allowed_widget_ids),
            [capability.id for capability in capabilities],
            _log_payload(_bundle_summary(bundle)),
        )
        payload = {
            "domain": resources.manifest.for_plan_agent(),
            "intent": request.intent,
            "recent_history": list(request.recent_history),
            "canvas": {"columns": 16, "rows": 10},
            "assets": resources.assets.plan_agent_catalog(),
            "template_catalog": resources.templates.for_plan_agent(),
            "widget_index": self._widget_registry.widget_index(
                resources.manifest.allowed_widget_ids
            ),
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
        messages: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=json.dumps(payload, ensure_ascii=False))])
        ]
        client = self._client_factory(api_key=self._settings.plan_agent_api_key)
        config = types.GenerateContentConfig(
            system_instruction=self._prompt_builder.build(domain_instruction=resources.plan_instruction),
            tools=[_native_tools(capabilities)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        tool_call_count = 0
        tool_limit_feedback_sent = False
        described_widget_ids: set[str] = set()
        described_template_ids: set[str] = set()

        while True:
            response = await self._generate_response(client, messages, config)
            calls = _function_calls(response)
            logger.info(
                "[PLAN_AGENT_MODEL_STEP] provider=gemini tool_calls_used=%d/%d calls=%s",
                tool_call_count,
                self._max_tool_steps,
                [getattr(call, "name", None) for call in calls],
            )
            if not calls:
                return self._final_result(
                    response,
                    request.domain_id,
                    resources,
                    bundle,
                    described_widget_ids=described_widget_ids,
                    described_template_ids=described_template_ids,
                )
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
                if name == _DESCRIBE_WIDGETS_NAME:
                    response_data = self._describe_widgets(
                        widget_ids=arguments.get("widget_ids"),
                        allowed_widget_ids=resources.manifest.allowed_widget_ids,
                    )
                    described_widget_ids.update(item["id"] for item in response_data["widgets"])
                elif name == _DESCRIBE_TEMPLATE_NAME:
                    response_data = self._describe_template(
                        template_id=arguments.get("template_id"),
                        resources=resources,
                    )
                    described_template_ids.add(response_data["template_id"])
                elif name == _CALL_CAPABILITY_NAME:
                    capability_id = _text(arguments.get("capability_id"), "capability_id")
                    capability_arguments = _mapping(arguments.get("arguments"), "capability arguments")
                    try:
                        update = self._domain_gateway.execute(
                            domain_id=request.domain_id,
                            capability_id=capability_id,
                            arguments=capability_arguments,
                            execution_context=(
                                CapabilityExecutionContext(session_id=request.session_id)
                                if request.session_id is not None else None
                            ),
                        )
                    except GatewayExecutionError as exc:
                        response_data = {
                            "capability_id": capability_id,
                            "error": {"message": str(exc)},
                        }
                    except (GatewayConfigurationError, GatewayPermissionError) as exc:
                        raise PlanAgentError(str(exc)) from exc
                    else:
                        bundle = _merge_bundles(bundle, update)
                        response_data = {
                            "capability_id": capability_id,
                            "verified_data": _bundle_for_agent(update),
                        }
                else:
                    raise PlanAgentError("Plan Agent called an unsupported native function.")
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

    async def _plan_with_cerebras(self, request: PlanAgentRequest) -> PlanAgentResult:
        """Run the same agent loop through Cerebras' OpenAI-compatible API."""

        if not self._settings.cerebras_api_key:
            raise PlanAgentError("CEREBRAS_API_KEY is not configured for the Plan Agent.")
        try:
            resources = self._domain_registry.load(request.domain_id)
            capabilities = self._domain_gateway.capability_catalog(request.domain_id)
        except (ManifestError, GatewayConfigurationError, GatewayPermissionError) as exc:
            raise PlanAgentError(str(exc)) from exc

        bundle = request.initial_bundle or self._domain_gateway.empty_bundle(request.domain_id)
        logger.info(
            "[PLAN_AGENT_CONTEXT] provider=cerebras assets=%d templates=%d widgets=%d capabilities=%s verified=%s",
            len(resources.assets.assets),
            len(resources.templates.for_plan_agent()),
            len(resources.manifest.allowed_widget_ids),
            [capability.id for capability in capabilities],
            _log_payload(_bundle_summary(bundle)),
        )
        payload = {
            "domain": resources.manifest.for_plan_agent(),
            "intent": request.intent,
            "recent_history": list(request.recent_history),
            "canvas": {"columns": 16, "rows": 10},
            "assets": resources.assets.plan_agent_catalog(),
            "template_catalog": resources.templates.for_plan_agent(),
            "widget_index": self._widget_registry.widget_index(resources.manifest.allowed_widget_ids),
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
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._prompt_builder.build(domain_instruction=resources.plan_instruction),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        client = self._cerebras_client_factory(
            api_key=self._settings.cerebras_api_key,
            base_url="https://api.cerebras.ai/v1",
        )
        tool_call_count = 0
        tool_limit_feedback_sent = False
        described_widget_ids: set[str] = set()
        described_template_ids: set[str] = set()

        while True:
            try:
                completion = await client.chat.completions.create(
                    model=self._settings.cerebras_planner_model,
                    messages=messages,
                    tools=_cerebras_tools(capabilities),
                    parallel_tool_calls=False,
                )
            except Exception as exc:
                logger.warning(
                    "[PLAN_AGENT_REQUEST_FAILED] provider=cerebras error_type=%s detail=%s",
                    type(exc).__name__, str(exc)[:500],
                )
                raise PlanAgentError("Plan Agent did not return a planning response.") from exc

            choices = getattr(completion, "choices", None) or ()
            if not choices:
                raise PlanAgentError("Cerebras returned no planning choice.")
            message = choices[0].message
            calls = tuple(getattr(message, "tool_calls", None) or ())
            logger.info(
                "[PLAN_AGENT_MODEL_STEP] provider=cerebras tool_calls_used=%d/%d calls=%s",
                tool_call_count,
                self._max_tool_steps,
                [getattr(getattr(call, "function", None), "name", None) for call in calls],
            )
            if not calls:
                return self._final_result_from_text(
                    getattr(message, "content", None),
                    request.domain_id,
                    resources,
                    bundle,
                    described_widget_ids=described_widget_ids,
                    described_template_ids=described_template_ids,
                )
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
                if name == _DESCRIBE_WIDGETS_NAME:
                    response_data = self._describe_widgets(
                        widget_ids=arguments.get("widget_ids"),
                        allowed_widget_ids=resources.manifest.allowed_widget_ids,
                    )
                    described_widget_ids.update(item["id"] for item in response_data["widgets"])
                elif name == _DESCRIBE_TEMPLATE_NAME:
                    response_data = self._describe_template(
                        template_id=arguments.get("template_id"),
                        resources=resources,
                    )
                    described_template_ids.add(response_data["template_id"])
                elif name == _CALL_CAPABILITY_NAME:
                    capability_id = _text(arguments.get("capability_id"), "capability_id")
                    capability_arguments = _mapping(arguments.get("arguments"), "capability arguments")
                    try:
                        update = self._domain_gateway.execute(
                            domain_id=request.domain_id,
                            capability_id=capability_id,
                            arguments=capability_arguments,
                            execution_context=(
                                CapabilityExecutionContext(session_id=request.session_id)
                                if request.session_id is not None else None
                            ),
                        )
                    except GatewayExecutionError as exc:
                        response_data = {
                            "capability_id": capability_id,
                            "error": {"message": str(exc)},
                        }
                    except (GatewayConfigurationError, GatewayPermissionError) as exc:
                        raise PlanAgentError(str(exc)) from exc
                    else:
                        bundle = _merge_bundles(bundle, update)
                        response_data = {
                            "capability_id": capability_id,
                            "verified_data": _bundle_for_agent(update),
                        }
                else:
                    raise PlanAgentError("Plan Agent called an unsupported native function.")
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
            tool_call_count += min(len(calls), max(0, remaining_tool_calls))

    def _describe_widgets(
        self,
        *,
        widget_ids: object,
        allowed_widget_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        if not isinstance(widget_ids, list) or not widget_ids:
            raise PlanAgentError("describe_widgets.widget_ids must be a non-empty array.")
        normalized_ids = tuple(_text(item, "widget_id") for item in widget_ids)
        if len(normalized_ids) != len(set(normalized_ids)):
            raise PlanAgentError("describe_widgets.widget_ids must not contain duplicates.")
        allowed = set(allowed_widget_ids)
        widgets: list[dict[str, Any]] = []
        for widget_id in normalized_ids:
            if widget_id not in allowed:
                raise PlanAgentError(f"widget_id '{widget_id}' is not allowed by the active domain.")
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
    def _describe_template(*, template_id: object, resources: Any) -> dict[str, Any]:
        requested_id = _text(template_id, "template_id")
        try:
            template = resources.templates.load_layout_template(requested_id)
        except TemplateCatalogError as exc:
            raise PlanAgentError(str(exc)) from exc
        return {
            "template_id": template.template_id,
            "description": template.description,
            "semantic_spec": template.semantic_spec.to_dict(),
            "blocks": [block.to_dict() for block in template.blocks],
            "bindings": [binding.to_dict() for binding in template.bindings],
        }

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
        except (json.JSONDecodeError, PlanAgentError) as exc:
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
