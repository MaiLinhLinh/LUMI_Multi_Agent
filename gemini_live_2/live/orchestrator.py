"""Runtime boundary between Gemini Live routing and SurfaceDocument mutation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Any, Mapping

from gemini_live_2.catalogs.domains import DomainRegistry, ManifestError
from gemini_live_2.catalogs.layout_templates import (
    LayoutTemplateError,
    LayoutTemplateMaterializer,
    TemplateExtractor,
)
from gemini_live_2.catalogs.templates import TemplateCatalogError
from gemini_live_2.panel import (
    AddBlockOperation,
    ActivePanelState,
    ActiveSurfaceSummary,
    ChoiceChild,
    CreateSurfacePlan,
    DataBundle,
    DeleteSurface,
    MoveBlockOperation,
    PanelCompilationError,
    PanelCompiler,
    PatchSurfacePlan,
    PresentationPlan,
    PlanBlock,
    RemoveBlockOperation,
    ReplaceBlockOperation,
    ReplaceChildrenOperation,
    RouteRequest,
    UseExistingSurfaceTemplate,
    UpdatePropsOperation,
    render_visual_stage_map,
    surface_document_client_payload,
)
from gemini_live_2.plan_agent import PlanAgent, PlanAgentError, PlanAgentRequest
from gemini_live_2.trace import trace
from gemini_live_2.extension_loader import EffectRegistry

from .memory import SessionMemoryStore
from .session_protocol import LiveSessionState, can_transition
from .visual_presentation import RenderedPresentation


@dataclass
class OrchestratedToolResult:
    response: dict[str, Any]
    presentation: RenderedPresentation | None = None


@dataclass
class PanelActionResult:
    """A validated in-place panel state transition and its browser payload."""

    response: dict[str, Any]
    panel_update: dict[str, Any]


@dataclass
class PanelInteractionResult:
    """A trusted browser interaction, optionally with a committed state snapshot."""

    interaction: dict[str, Any]
    panel_update: dict[str, Any] | None = None


@dataclass
class SurfaceDeleteResult:
    """A validated surface close and the response returned to Gemini Live."""

    response: dict[str, Any]


async def _emit_route_telemetry(callback: Any, event: str, **payload: Any) -> None:
    """Emit timing-only route lifecycle events when a caller requested telemetry."""

    if callback is not None:
        await callback({"source": "orchestrator", "event": event, **payload})


@dataclass(frozen=True, slots=True)
class PendingTemplateCandidate:
    """A compiled create plan awaiting browser confirmation before curation."""

    surface_id: str
    revision: int
    domain_id: str
    command: CreateSurfacePlan


class LiveSessionOrchestrator:
    """Create a new SurfaceDocument only when Gemini Live explicitly routes a request."""

    _MAX_PLAN_REPAIR_ATTEMPTS = 2
    _RUNTIME_REPAIR_ERROR_TYPES = frozenset({
        "image_load_failed",
        "renderer_missing",
        "widget_render_failed",
        "state_apply_failed",
    })

    def __init__(
        self,
        *,
        memory_store: SessionMemoryStore | None = None,
        domain_registry: DomainRegistry | None = None,
        plan_agent: PlanAgent | None = None,
        panel_compiler: PanelCompiler | None = None,
        effect_registry: EffectRegistry | None = None,
    ) -> None:
        self._memory_store = memory_store or SessionMemoryStore()
        self._domain_registry = domain_registry
        self._plan_agent = plan_agent
        self._panel_compiler = panel_compiler
        self._effect_registry = effect_registry
        self._technical_states: dict[str, LiveSessionState] = {}
        self._active_panels: dict[str, ActivePanelState] = {}
        self._pending_template_candidates: dict[str, PendingTemplateCandidate] = {}

    def session_memory(self, session_id: str):
        return self._memory_store.get(session_id)

    def session_state(self, session_id: str) -> LiveSessionState:
        return self._technical_states.setdefault(session_id, LiveSessionState.IDLE)

    def transition_session(self, *, session_id: str, target: LiveSessionState) -> LiveSessionState:
        current = self.session_state(session_id)
        if current != target and not can_transition(current, target):
            raise RuntimeError(f"Invalid Live session transition: {current} -> {target}")
        self._technical_states[session_id] = target
        return target

    def reset_session_state(self, session_id: str) -> None:
        self._technical_states[session_id] = LiveSessionState.IDLE
        if self._panel_compiler is not None:
            clear_search_results = getattr(self._panel_compiler, "clear_search_results", None)
            if callable(clear_search_results):
                clear_search_results(session_id)
        self._pending_template_candidates.pop(session_id, None)

    def active_panel(self, session_id: str) -> ActivePanelState | None:
        return self._active_panels.get(session_id)

    def active_surface_summary(self, session_id: str) -> ActiveSurfaceSummary | None:
        """Return Plan-Agent context for the currently active surface, if any."""

        state = self._active_panels.get(session_id)
        return ActiveSurfaceSummary.from_active_panel(state) if state is not None else None

    def active_panel_presentation_context(self, session_id: str) -> dict[str, Any] | None:
        """Return the trusted context needed to resume one rendered panel."""

        state = self._active_panels.get(session_id)
        if state is None or self._domain_registry is None:
            return None
        resources = self._domain_registry.load(state.document.domain_id)
        return {
            "surface_id": state.document.surface_id,
            "revision": state.revision,
            "presentation_instruction": resources.presentation_instruction,
            "visual_stage_map": render_visual_stage_map(
                state.document,
                widget_registry=self._panel_compiler.widget_registry,
                asset_catalog=resources.assets,
            ),
            "visual_effects": _visual_effects(self._effect_registry),
        }

    def domain_presentation_instruction(self, domain_id: object) -> str | None:
        """Return the one domain-level speaking/style instruction for Live."""

        if not isinstance(domain_id, str) or self._domain_registry is None:
            return None
        try:
            return self._domain_registry.load(domain_id).presentation_instruction
        except ManifestError:
            return None

    def runtime_repair_presentation_context(
        self, *, session_id: str, surface_id: str, revision: object
    ) -> dict[str, Any]:
        """Return a map only after the browser confirms the active repair revision."""

        state = self._active_panels.get(session_id)
        if state is None:
            raise ValueError("no active surface")
        if surface_id != state.document.surface_id or revision != state.revision:
            raise ValueError("surface_id or revision is not active")
        context = self.active_panel_presentation_context(session_id)
        if context is None:  # pragma: no cover - guarded by the active state above.
            raise ValueError("active surface context is unavailable")
        return context

    async def execute_tool_call_result(
        self,
        *,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        telemetry_callback: Any = None,
        plan_run_id: str | None = None,
        **_: Any,
    ) -> OrchestratedToolResult:
        if tool_name != "route_request":
            return OrchestratedToolResult({"status": "unsupported", "detail": "Unknown Live tool."})
        if self._domain_registry is None or self._plan_agent is None or self._panel_compiler is None:
            return OrchestratedToolResult({"status": "error", "detail": "Panel routing is not configured."})
        run_started = time.perf_counter()
        try:
            route = RouteRequest.from_dict(arguments)
            resources = self._domain_registry.load(route.domain_id)
            history = tuple(
                {"role": item["role"], "text": item["content"]}
                for item in self.session_memory(session_id).history
                if item.get("role") in {"user", "assistant"} and item.get("content")
            )
            validation_feedback: dict[str, Any] | None = None
            # A compiler repair must reuse the verified data collected by the
            # failed plan.  Remote result IDs are valid within this session and
            # re-searching only wastes the next tool budget.
            repair_bundle = None
            active_surface_summary = self.active_surface_summary(session_id)
            for repair_attempt in range(self._MAX_PLAN_REPAIR_ATTEMPTS + 1):
                planned = await self._plan_agent.plan(PlanAgentRequest(
                    domain_id=route.domain_id,
                    intent=route.intent,
                    recent_history=history,
                    initial_bundle=repair_bundle,
                    active_surface_summary=active_surface_summary,
                    validation_feedback=validation_feedback,
                    session_id=session_id,
                    telemetry_callback=telemetry_callback,
                    plan_run_id=plan_run_id,
                    plan_attempt=repair_attempt + 1,
                ))
                repair_bundle = planned.data_bundle
                try:
                    compile_started = time.perf_counter()
                    await _emit_route_telemetry(
                        telemetry_callback,
                        "compiler_started",
                        plan_run_id=plan_run_id,
                        plan_attempt=repair_attempt + 1,
                    )
                    state = self._apply_surface_command(
                        session_id=session_id,
                        route=route,
                        command=planned.command,
                        data_bundle=planned.data_bundle,
                        domain_resources=resources,
                    )
                    await _emit_route_telemetry(
                        telemetry_callback,
                        "compiler_completed",
                        plan_run_id=plan_run_id,
                        plan_attempt=repair_attempt + 1,
                        compiler_elapsed_ms=round((time.perf_counter() - compile_started) * 1000),
                        revision=state.revision,
                        component_count=len(state.document.components),
                        anchor_count=len(state.document.anchors),
                    )
                    if repair_attempt:
                        trace("PLAN_COMPILE_REPAIR_SUCCEEDED attempt=%s", repair_attempt + 1)
                    break
                except PanelCompilationError as exc:
                    await _emit_route_telemetry(
                        telemetry_callback,
                        "compiler_rejected",
                        plan_run_id=plan_run_id,
                        plan_attempt=repair_attempt + 1,
                        compiler_elapsed_ms=round((time.perf_counter() - compile_started) * 1000),
                        code=exc.code,
                    )
                    if repair_attempt >= self._MAX_PLAN_REPAIR_ATTEMPTS:
                        raise
                    validation_feedback = exc.for_plan_agent()
                    await _emit_route_telemetry(
                        telemetry_callback,
                        "plan_repair_started",
                        plan_run_id=plan_run_id,
                        next_plan_attempt=repair_attempt + 2,
                        reason_code=exc.code,
                    )
                    trace(
                        "PLAN_COMPILE_REPAIR_REQUIRED attempt=%s code=%s details=%s",
                        repair_attempt + 1,
                        exc.code,
                        validation_feedback["details"],
                    )
            else:  # pragma: no cover - loop always breaks or raises.
                raise PlanAgentError("Plan Agent did not produce a compilable plan.")
        except (
            ManifestError,
            PanelCompilationError,
            PlanAgentError,
            ValueError,
        ) as exc:
            await _emit_route_telemetry(
                telemetry_callback,
                "plan_run_failed",
                plan_run_id=plan_run_id,
                total_elapsed_ms=round((time.perf_counter() - run_started) * 1000),
                error_type=type(exc).__name__,
            )
            return OrchestratedToolResult({"status": "error", "detail": str(exc)})

        self._active_panels[session_id] = state
        self._track_template_candidate(session_id=session_id, command=planned.command, state=state)
        payload = surface_document_client_payload(
            state.document,
            asset_urls={
                asset.id: f"/assets/domains/{state.document.domain_id}/{asset.id}"
                for asset in resources.assets.assets
            },
        )
        response = {
            "status": "completed",
            "domain_id": state.document.domain_id,
            "panel_id": state.document.surface_id,
            "revision": state.revision,
            "presentation_instruction": resources.presentation_instruction,
            "visual_stage_map": render_visual_stage_map(
                state.document,
                widget_registry=self._panel_compiler.widget_registry,
                asset_catalog=resources.assets,
            ),
            "visual_effects": _visual_effects(self._effect_registry),
        }
        await _emit_route_telemetry(
            telemetry_callback,
            "plan_run_completed",
            plan_run_id=plan_run_id,
            total_elapsed_ms=round((time.perf_counter() - run_started) * 1000),
            revision=state.revision,
        )
        return OrchestratedToolResult(response=response, presentation=RenderedPresentation(panel=payload))

    async def repair_runtime_diagnostic(
        self,
        *,
        session_id: str,
        diagnostic: Mapping[str, Any],
    ) -> OrchestratedToolResult:
        """Repair an active surface only after a browser-observed fatal error.

        This is deliberately separate from Gemini Live routing: a browser cannot
        supply a plan, content, or replacement asset.  It can only identify the
        current compiler-owned component and the bounded observed failure.
        """

        try:
            active, runtime_feedback = self._validate_runtime_diagnostic(
                session_id=session_id,
                diagnostic=diagnostic,
            )
        except ValueError as exc:
            trace("RUNTIME_DIAGNOSTIC_IGNORED reason=%s", exc)
            return OrchestratedToolResult({"status": "ignored", "detail": str(exc)})
        if self._domain_registry is None or self._plan_agent is None or self._panel_compiler is None:
            return OrchestratedToolResult({"status": "error", "detail": "Panel routing is not configured."})

        try:
            route = RouteRequest.from_dict({
                "domain_id": active.document.domain_id,
                "intent": active.purpose,
            })
            resources = self._domain_registry.load(route.domain_id)
            history = tuple(
                {"role": item["role"], "text": item["content"]}
                for item in self.session_memory(session_id).history
                if item.get("role") in {"user", "assistant"} and item.get("content")
            )
            validation_feedback: dict[str, Any] | None = None
            # Keep all verified data from a failed runtime-repair plan for its
            # compiler retry; do not force the Agent to search again.
            repair_bundle = None
            active_surface_summary = self.active_surface_summary(session_id)
            for repair_attempt in range(self._MAX_PLAN_REPAIR_ATTEMPTS + 1):
                planned = await self._plan_agent.plan(PlanAgentRequest(
                    domain_id=route.domain_id,
                    intent=route.intent,
                    recent_history=history,
                    initial_bundle=repair_bundle,
                    active_surface_summary=active_surface_summary,
                    validation_feedback=validation_feedback,
                    runtime_feedback=runtime_feedback,
                    session_id=session_id,
                ))
                repair_bundle = planned.data_bundle
                try:
                    state = self._apply_surface_command(
                        session_id=session_id,
                        route=route,
                        command=planned.command,
                        data_bundle=planned.data_bundle,
                        domain_resources=resources,
                    )
                    break
                except PanelCompilationError as exc:
                    if repair_attempt >= self._MAX_PLAN_REPAIR_ATTEMPTS:
                        raise
                    validation_feedback = exc.for_plan_agent()
            else:  # pragma: no cover - loop always breaks or raises.
                raise PlanAgentError("Plan Agent did not produce a compilable runtime repair.")
        except (ManifestError, PanelCompilationError, PlanAgentError, ValueError) as exc:
            return OrchestratedToolResult({"status": "error", "detail": str(exc)})

        self._active_panels[session_id] = state
        self._track_template_candidate(session_id=session_id, command=planned.command, state=state)
        payload = surface_document_client_payload(
            state.document,
            asset_urls={
                asset.id: f"/assets/domains/{state.document.domain_id}/{asset.id}"
                for asset in resources.assets.assets
            },
        )
        response = {
            "status": "completed",
            "kind": "runtime_repair",
            "domain_id": state.document.domain_id,
            "panel_id": state.document.surface_id,
            "revision": state.revision,
            "visual_stage_map": render_visual_stage_map(
                state.document,
                widget_registry=self._panel_compiler.widget_registry,
                asset_catalog=resources.assets,
            ),
            "visual_effects": _visual_effects(self._effect_registry),
        }
        return OrchestratedToolResult(response=response, presentation=RenderedPresentation(panel=payload))

    def _validate_runtime_diagnostic(
        self,
        *,
        session_id: str,
        diagnostic: Mapping[str, Any],
    ) -> tuple[ActivePanelState, dict[str, Any]]:
        if not isinstance(diagnostic, Mapping):
            raise ValueError("runtime diagnostic must be an object")
        active = self._active_panels.get(session_id)
        if active is None:
            raise ValueError("no active surface")
        document = active.document
        surface_id = diagnostic.get("surface_id")
        revision = diagnostic.get("revision")
        component_id = diagnostic.get("component_id")
        anchor_id = diagnostic.get("anchor_id")
        error_type = diagnostic.get("error_type")
        repair_scope = diagnostic.get("repair_scope")
        observed = diagnostic.get("observed")
        if surface_id != document.surface_id or revision != document.revision:
            raise ValueError("surface_id or revision is not active")
        if not isinstance(component_id, str) or component_id not in document.component_map:
            raise ValueError("component_id is not in the active surface")
        anchor = document.anchor_map.get(anchor_id) if isinstance(anchor_id, str) else None
        if anchor is None or anchor.component_id != component_id:
            raise ValueError("anchor_id does not belong to the active component")
        if error_type not in self._RUNTIME_REPAIR_ERROR_TYPES:
            raise ValueError("runtime diagnostic error_type is not repairable")
        if repair_scope != "surface_plan":
            raise ValueError("runtime diagnostic repair_scope is not supported")
        if not isinstance(observed, Mapping) or len(observed) > 12:
            raise ValueError("runtime diagnostic observed must be a small object")
        normalized_observed: dict[str, str | int | float | bool | None] = {}
        for key, value in observed.items():
            if not isinstance(key, str) or not key or len(key) > 80:
                raise ValueError("runtime diagnostic observed contains an invalid key")
            if isinstance(value, str):
                if len(value) > 240:
                    raise ValueError("runtime diagnostic observed string is too long")
            elif not isinstance(value, (int, float, bool, type(None))):
                raise ValueError("runtime diagnostic observed values must be scalar")
            normalized_observed[key] = value
        return active, {
            "kind": "runtime_feedback",
            "surface_id": document.surface_id,
            "revision": document.revision,
            "component_id": component_id,
            "anchor_id": anchor_id,
            "error_type": error_type,
            "observed": normalized_observed,
            "repair_scope": repair_scope,
        }

    def _apply_surface_command(
        self,
        *,
        session_id: str,
        route: RouteRequest,
        command: CreateSurfacePlan | PatchSurfacePlan | UseExistingSurfaceTemplate,
        data_bundle: DataBundle,
        domain_resources: Any,
    ) -> ActivePanelState:
        """Materialize one agent command without mutating the active surface.

        The caller persists the returned state only after this method has
        compiled the complete candidate.  A bad patch therefore leaves the
        currently rendered surface and its revision untouched.
        """

        if self._panel_compiler is None:  # pragma: no cover - guarded by caller.
            raise RuntimeError("panel compiler is required for surface commands")
        if isinstance(command, CreateSurfacePlan):
            document = self._panel_compiler.compile_surface_document(
                plan=PresentationPlan(domain_id=route.domain_id, blocks=command.blocks),
                data_bundle=data_bundle,
                domain_resources=domain_resources,
                search_session_id=session_id,
            )
            previous = self._active_panels.get(session_id)
            if previous is not None:
                document = replace(document, revision=previous.revision + 1)
            return ActivePanelState(
                document=document,
                purpose=route.intent,
            )
        if isinstance(command, UseExistingSurfaceTemplate):
            try:
                template = domain_resources.templates.load_layout_template(command.template_id)
                plan = LayoutTemplateMaterializer().materialize(template=template, bindings=command.bindings)
            except (TemplateCatalogError, LayoutTemplateError) as exc:
                raise PanelCompilationError(str(exc), code="invalid_template_bindings") from exc
            document = self._panel_compiler.compile_surface_document(
                plan=plan,
                data_bundle=data_bundle,
                domain_resources=domain_resources,
                search_session_id=session_id,
            )
            previous = self._active_panels.get(session_id)
            if previous is not None:
                document = replace(document, revision=previous.revision + 1)
            return ActivePanelState(
                document=document,
                purpose=route.intent,
            )
        return self._apply_patch_surface_plan(
            session_id=session_id,
            route=route,
            command=command,
            data_bundle=data_bundle,
            domain_resources=domain_resources,
        )

    def confirm_template_candidate_rendered(
        self, *, session_id: str, surface_id: str, revision: object
    ) -> dict[str, Any]:
        """Persist a new reusable template only after its exact browser render succeeds."""

        candidate = self._pending_template_candidates.get(session_id)
        active = self._active_panels.get(session_id)
        if candidate is None:
            return {"status": "ignored", "detail": "no pending template candidate"}
        if (
            not isinstance(revision, int)
            or surface_id != candidate.surface_id
            or revision != candidate.revision
            or active is None
            or active.document.surface_id != candidate.surface_id
            or active.revision != candidate.revision
        ):
            return {"status": "ignored", "detail": "template candidate revision is not active"}

        self._pending_template_candidates.pop(session_id, None)
        if self._domain_registry is None:
            return {"status": "error", "detail": "domain registry is not configured"}
        try:
            resources = self._domain_registry.load(candidate.domain_id)
            template_id = self._persist_reusable_template(
                command=candidate.command,
                domain_resources=resources,
            )
        except (ManifestError, ValueError) as exc:
            trace("TEMPLATE_SAVE_SKIPPED reason=%s", str(exc)[:300])
            return {"status": "error", "detail": str(exc)}
        if template_id is None:
            return {"status": "ignored", "detail": "template could not be curated"}
        return {"status": "completed", "template_id": template_id}

    def _track_template_candidate(
        self,
        *,
        session_id: str,
        command: CreateSurfacePlan | PatchSurfacePlan | UseExistingSurfaceTemplate,
        state: ActivePanelState,
    ) -> None:
        """Only newly created, explicitly reusable surfaces can become templates."""

        self._pending_template_candidates.pop(session_id, None)
        if not isinstance(command, CreateSurfacePlan) or not command.template_description:
            return
        self._pending_template_candidates[session_id] = PendingTemplateCandidate(
            surface_id=state.document.surface_id,
            revision=state.revision,
            domain_id=state.document.domain_id,
            command=command,
        )
        trace("TEMPLATE_CANDIDATE_PENDING surface=%s revision=%s", state.document.surface_id, state.revision)

    def _persist_reusable_template(self, *, command: CreateSurfacePlan, domain_resources: Any) -> str | None:
        """Curate a browser-confirmed plan into a data-free reusable TemplateSpec."""

        assert self._panel_compiler is not None
        try:
            catalog = domain_resources.templates
            template = TemplateExtractor(self._panel_compiler.widget_registry).extract(
                plan=PresentationPlan(
                    domain_id=domain_resources.manifest.domain_id,
                    blocks=command.blocks,
                ),
                template_id=catalog.next_generated_template_id(),
                description=command.template_description or "",
            )
            catalog.save_layout_template(template)
            trace("TEMPLATE_SAVED id=%s", template.template_id)
            return template.template_id
        except (LayoutTemplateError, TemplateCatalogError, OSError, ValueError) as exc:
            trace("TEMPLATE_SAVE_SKIPPED reason=%s", str(exc)[:300])
            return None

    def _apply_patch_surface_plan(
        self,
        *,
        session_id: str,
        route: RouteRequest,
        command: PatchSurfacePlan,
        data_bundle: DataBundle,
        domain_resources: Any,
    ) -> ActivePanelState:
        """Apply a structural patch atomically while retaining stable IDs."""

        active = self._active_panels.get(session_id)
        if active is None:
            raise PanelCompilationError("patch_surface_plan requires an active surface.", code="missing_surface")
        if command.surface_id != active.document.surface_id:
            raise PanelCompilationError("patch_surface_plan targets a different active surface.", code="stale_surface")
        if command.base_revision != active.revision:
            raise PanelCompilationError("patch_surface_plan.base_revision is stale.", code="stale_revision")
        if route.domain_id != active.document.domain_id:
            raise PanelCompilationError("patch_surface_plan cannot change the active surface domain.", code="domain_mismatch")

        planned_blocks: list[tuple[str, PlanBlock]] = [
            (
                component.id,
                PlanBlock(
                    widget_id=component.type,
                    grid=component.layout,
                    props=_plan_props_from_component(component.props),
                    initial_visibility=str(component.state["visibility"]),
                    initial_state=component.state,
                    children=tuple(
                        ChoiceChild(widget_id=child.type, props=_plan_props_from_component(child.props))
                        for child in component.children
                    ),
                ),
            )
            for component in active.document.components
        ]
        existing_anchors = {anchor.anchor_id: anchor.component_id for anchor in active.document.anchors}
        next_id = _next_runtime_component_id(component_id for component_id, _ in planned_blocks)

        def find_index(anchor_id: str) -> int:
            component_id = existing_anchors.get(anchor_id)
            if component_id is None:
                raise PanelCompilationError(
                    f"patch operation references unknown anchor_id '{anchor_id}'.",
                    code="unknown_anchor",
                )
            for index, (candidate_id, _) in enumerate(planned_blocks):
                if candidate_id == component_id:
                    return index
            raise PanelCompilationError(
                f"patch operation references a component already removed through anchor_id '{anchor_id}'.",
                code="removed_component",
            )

        for operation in command.operations:
            if isinstance(operation, AddBlockOperation):
                component_id = str(next_id)
                next_id += 1
                planned_blocks.append((component_id, operation.block))
                continue
            if isinstance(operation, RemoveBlockOperation):
                planned_blocks.pop(find_index(operation.anchor_id))
                continue
            if isinstance(operation, ReplaceBlockOperation):
                index = find_index(operation.anchor_id)
                component_id, _ = planned_blocks[index]
                planned_blocks[index] = (component_id, operation.block)
                continue
            if isinstance(operation, MoveBlockOperation):
                index = find_index(operation.anchor_id)
                component_id, block = planned_blocks[index]
                planned_blocks[index] = (component_id, PlanBlock(
                    widget_id=block.widget_id,
                    grid=operation.grid,
                    props=block.props,
                    initial_visibility=block.initial_visibility,
                    initial_state=block.initial_state,
                    children=block.children,
                ))
                continue
            if isinstance(operation, UpdatePropsOperation):
                index = find_index(operation.anchor_id)
                component_id, block = planned_blocks[index]
                planned_blocks[index] = (component_id, PlanBlock(
                    widget_id=block.widget_id,
                    grid=block.grid,
                    props={**block.props, **operation.changes},
                    initial_visibility=block.initial_visibility,
                    initial_state=block.initial_state,
                    children=block.children,
                ))
                continue
            if isinstance(operation, ReplaceChildrenOperation):
                index = find_index(operation.anchor_id)
                component_id, block = planned_blocks[index]
                planned_blocks[index] = (component_id, PlanBlock(
                    widget_id=block.widget_id,
                    grid=block.grid,
                    props=block.props,
                    initial_visibility=block.initial_visibility,
                    initial_state=block.initial_state,
                    children=operation.children,
                ))
                continue
            raise PanelCompilationError("patch_surface_plan contains an unsupported operation.")

        if not planned_blocks:
            raise PanelCompilationError("patch_surface_plan cannot remove every block.", code="empty_surface")

        retained_ids = {component_id for component_id, _ in planned_blocks}
        anchor_ids_by_component_key = {
            (anchor.component_id, anchor.anchor_key): anchor.anchor_id
            for anchor in active.document.anchors
            if anchor.component_id in retained_ids
        }
        document = self._panel_compiler.compile_surface_document(
            plan=PresentationPlan(domain_id=route.domain_id, blocks=tuple(block for _, block in planned_blocks)),
            data_bundle=data_bundle,
            domain_resources=domain_resources,
            surface_id=active.document.surface_id,
            component_ids=tuple(component_id for component_id, _ in planned_blocks),
            anchor_ids_by_component_key=anchor_ids_by_component_key,
            search_session_id=session_id,
        )
        return ActivePanelState(
            document=replace(document, revision=active.revision + 1),
            purpose=route.intent,
        )

    def present_visual(self, *, session_id: str, anchor_id: str, effect_id: str) -> dict[str, Any]:
        """Resolve a temporary visual cue from the active SurfaceDocument."""

        state = self._active_panels.get(session_id)
        if state is None:
            raise ValueError("no active panel")
        anchor = state.document.anchor_map.get(anchor_id)
        if anchor is None:
            raise ValueError("unknown anchor_id for the active panel")
        if self._effect_registry is None:
            raise RuntimeError("effect registry is not configured")
        try:
            self._effect_registry.get(effect_id)
        except ValueError as exc:
            raise ValueError("unknown effect_id") from exc
        return {
            "anchor_id": anchor.anchor_id,
            "effect_id": effect_id,
            "effect": effect_id,
            "panel_id": state.document.surface_id,
            "panel_revision": state.document.revision,
        }

    def resolve_panel_interaction(
        self,
        *,
        session_id: str,
        surface_id: str,
        revision: object,
        anchor_id: str,
        action: str,
    ) -> dict[str, Any]:
        """Validate a browser interaction and return trusted panel data for Live.

        The browser may identify only an active surface revision, one
        compiler-owned anchor and an action. It never supplies content or a
        correctness claim. The owning widget registry decides whether that
        action is allowed; this keeps the boundary ready for later click, drag
        and drop widgets without giving the browser authority over semantics.
        """

        state = self._active_panels.get(session_id)
        if state is None:
            raise ValueError("no active panel")
        document = state.document
        if surface_id != document.surface_id:
            raise ValueError("surface_id does not match the active panel")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("revision must be a positive integer")
        if revision != document.revision:
            raise ValueError("revision does not match the active surface")
        anchor = document.anchor_map.get(anchor_id)
        if anchor is None:
            raise ValueError("unknown anchor_id for the active panel")
        component = document.component_map.get(anchor.component_id)
        widget = self._panel_compiler.widget_registry.get(component.type) if component and self._panel_compiler else None
        if component is None or widget is None or not widget.allows_interaction(action):
            raise ValueError("action is not allowed for this anchor")
        return {
            "event": "surface_interaction",
            "surface_id": document.surface_id,
            "revision": document.revision,
            "anchor_id": anchor.anchor_id,
            "widget_id": component.type,
            "action": action,
            "content": [child.to_dict() for child in component.children],
        }

    def apply_panel_interaction(
        self,
        *,
        session_id: str,
        surface_id: str,
        revision: object,
        anchor_id: str,
        action: str,
    ) -> PanelInteractionResult:
        """Validate one browser action and apply its declared generic state rule.

        Widgets without a state rule (currently ``choice.select``) preserve
        SD7 behaviour: Runtime only forwards the trusted event.  A widget such
        as flashcard declares its own rule in the Registry; the Runtime merely
        validates and commits the resulting state through the existing atomic
        ``update_surface_state`` path.
        """

        interaction = self.resolve_panel_interaction(
            session_id=session_id,
            surface_id=surface_id,
            revision=revision,
            anchor_id=anchor_id,
            action=action,
        )
        state = self._active_panels.get(session_id)
        if state is None or self._panel_compiler is None:  # defensive; resolve already checked active state.
            raise RuntimeError("panel compiler and active panel are required for interactions")
        document = state.document
        anchor = document.anchor_map[anchor_id]
        component = document.component_map[anchor.component_id]
        widget = self._panel_compiler.widget_registry.get(component.type)
        changes = widget.interaction_state_changes(action=action, current_state=component.state)
        if not changes:
            return PanelInteractionResult(interaction=interaction)

        mutation = self.update_surface_state(
            session_id=session_id,
            surface_id=document.surface_id,
            base_revision=document.revision,
            updates=[{"anchor_id": anchor.anchor_id, "changes": changes}],
        )
        interaction = {
            **interaction,
            "revision": mutation.response["revision"],
            "visual_stage_map": mutation.response["visual_stage_map"],
            "visual_effects": mutation.response["visual_effects"],
        }
        return PanelInteractionResult(interaction=interaction, panel_update=mutation.panel_update)

    def update_surface_state(
        self,
        *,
        session_id: str,
        surface_id: str,
        base_revision: int,
        updates: list[dict[str, Any]],
    ) -> PanelActionResult:
        """Validate and atomically apply registered widget-state transitions."""

        if not isinstance(surface_id, str) or not surface_id:
            raise ValueError("surface_id must be a non-empty string")
        if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
            raise ValueError("base_revision must be a positive integer")
        if not isinstance(updates, list) or not updates:
            raise ValueError("updates must contain at least one update")

        state = self._active_panels.get(session_id)
        if state is None:
            raise ValueError("no active panel")
        document = state.document
        if surface_id != document.surface_id:
            raise ValueError("surface_id does not match the active panel")
        if base_revision != document.revision:
            raise ValueError("base_revision does not match the active panel revision")
        anchor_map = document.anchor_map
        component_map = document.component_map
        component_replacements: dict[str, dict[str, Any]] = {}
        updated_anchor_ids: list[str] = []
        for update in updates:
            if not isinstance(update, dict):
                raise ValueError("each update must be an object")
            anchor_id = update.get("anchor_id")
            changes = update.get("changes")
            if not isinstance(anchor_id, str) or not anchor_id:
                raise ValueError("update.anchor_id must be a non-empty string")
            if not isinstance(changes, dict) or not changes:
                raise ValueError("update.changes must be a non-empty object")
            anchor = anchor_map.get(anchor_id)
            if anchor is None:
                raise ValueError("unknown anchor_id for the active panel")
            if anchor.component_id in component_replacements:
                raise ValueError("updates must not target the same component more than once")
            component = component_map[anchor.component_id]
            try:
                widget = self._panel_compiler.widget_registry.get(component.type) if self._panel_compiler else None
                if widget is None:
                    raise RuntimeError("panel compiler is required for state updates")
                next_values = widget.validate_state_changes(
                    current_state=component.state,
                    changes=changes,
                )
                component_replacements[component.id] = next_values
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            updated_anchor_ids.append(anchor_id)
        if self._domain_registry is None:
            raise RuntimeError("domain registry is required for panel updates")
        resources = self._domain_registry.load(document.domain_id)
        trace(
            "UPDATE_SURFACE_STATE_MAP_BEFORE:\n%s",
            render_visual_stage_map(
                state.document,
                widget_registry=self._panel_compiler.widget_registry,
                asset_catalog=resources.assets,
            ),
        )

        updated_document = replace(
            document,
            revision=document.revision + 1,
            components=tuple(
                replace(component, state=component_replacements.get(component.id, component.state))
                for component in document.components
            ),
        )
        updated_state = state.replace(updated_document)
        self._active_panels[session_id] = updated_state
        payload = surface_document_client_payload(
            updated_state.document,
            asset_urls={
                asset.id: f"/assets/domains/{updated_document.domain_id}/{asset.id}"
                for asset in resources.assets.assets
            },
        )
        visual_stage_map = render_visual_stage_map(
            updated_state.document,
            widget_registry=self._panel_compiler.widget_registry,
            asset_catalog=resources.assets,
        )
        trace(
            "UPDATE_SURFACE_STATE_MAP_AFTER:\n%s",
            visual_stage_map,
        )
        response = {
            "status": "completed",
            "updated_anchor_ids": updated_anchor_ids,
            "panel_id": updated_document.surface_id,
            "surface_id": updated_document.surface_id,
            "revision": updated_document.revision,
            "visual_stage_map": visual_stage_map,
            "visual_effects": _visual_effects(self._effect_registry),
        }
        return PanelActionResult(response=response, panel_update=payload)

    def delete_surface(
        self,
        *,
        session_id: str,
        surface_id: str,
        base_revision: int,
    ) -> SurfaceDeleteResult:
        """Close exactly the active surface revision requested by Gemini."""

        command = DeleteSurface(surface_id=surface_id, base_revision=base_revision)
        state = self._active_panels.get(session_id)
        if state is None:
            raise ValueError("no active panel")
        if command.surface_id != state.document.surface_id:
            raise ValueError("surface_id does not match the active panel")
        if command.base_revision != state.document.revision:
            raise ValueError("base_revision does not match the active panel revision")

        del self._active_panels[session_id]
        next_revision = state.revision + 1
        trace("DELETE_SURFACE_ACCEPTED surface=%s revision=%s", command.surface_id, next_revision)
        return SurfaceDeleteResult(response={
            "status": "completed",
            "surface_id": command.surface_id,
            "revision": next_revision,
            "visual_stage_map": "VISUAL STAGE MAP — KHÔNG CÓ PANEL ĐANG MỞ.",
            "visual_effects": [],
        })

    def remember_turn(self, *, session_id: str, user_text: str, assistant_text: str) -> None:
        memory = self._memory_store.get(session_id)
        memory.append("user", user_text)
        memory.append("assistant", assistant_text)


def _visual_effects(effect_registry: EffectRegistry | None = None) -> list[dict[str, str]]:
    """Expose every installed target-agnostic effect to Gemini Live."""

    if effect_registry is None:
        return []
    effects: list[dict[str, str]] = []
    for definition in effect_registry.definitions():
        effects.append({
            "id": definition.effect_id,
            "description": definition.description,
            "usage_guidance": definition.usage_guidance,
        })
    return effects


def _next_runtime_component_id(component_ids: Any) -> int:
    """Allocate the next opaque numeric component ID without exposing it to agents."""

    numeric_ids = [int(item) for item in component_ids if isinstance(item, str) and item.isdigit()]
    return max(numeric_ids, default=0) + 1


def _plan_props_from_component(props: Any) -> dict[str, Any]:
    """Remove compiler-owned remote source metadata before a structural patch.

    The stored ``remote_image_result_id`` remains in plan props.  A fresh
    Compiler pass resolves it again for the current session, while raw provider
    URLs never become legal Plan-Agent input.
    """

    if not isinstance(props, dict):
        return dict(props)
    return {key: value for key, value in props.items() if key != "source"}
