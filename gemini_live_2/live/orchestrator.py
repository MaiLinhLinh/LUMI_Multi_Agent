"""Runtime boundary between Gemini Live routing and PanelIR materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    BlockState,
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
    RouteRequest,
    SurfaceState,
    SurfaceStructure,
    UseExistingSurfaceTemplate,
    UpdatePropsOperation,
    panel_client_payload,
    render_visual_stage_map,
)
from gemini_live_2.plan_agent import PlanAgent, PlanAgentError, PlanAgentRequest
from gemini_live_2.trace import trace

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
class SurfaceDeleteResult:
    """A validated surface close and the response returned to Gemini Live."""

    response: dict[str, Any]


class LiveSessionOrchestrator:
    """Create a new PanelIR only when Gemini Live explicitly routes a request."""

    _MAX_PLAN_REPAIR_ATTEMPTS = 2

    def __init__(
        self,
        *,
        memory_store: SessionMemoryStore | None = None,
        domain_registry: DomainRegistry | None = None,
        plan_agent: PlanAgent | None = None,
        panel_compiler: PanelCompiler | None = None,
    ) -> None:
        self._memory_store = memory_store or SessionMemoryStore()
        self._domain_registry = domain_registry
        self._plan_agent = plan_agent
        self._panel_compiler = panel_compiler
        self._technical_states: dict[str, LiveSessionState] = {}
        self._active_panels: dict[str, ActivePanelState] = {}

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
        resources = self._domain_registry.load(state.panel_ir.domain_id)
        return {
            "surface_id": state.panel_ir.panel_id,
            "revision": state.revision,
            "presentation_instruction": resources.presentation_instruction,
            "visual_stage_map": render_visual_stage_map(state.panel_ir),
            "visual_effects": _visual_effects(state.panel_ir),
        }

    async def execute_tool_call_result(
        self,
        *,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        **_: Any,
    ) -> OrchestratedToolResult:
        if tool_name != "route_request":
            return OrchestratedToolResult({"status": "unsupported", "detail": "Unknown Live tool."})
        if self._domain_registry is None or self._plan_agent is None or self._panel_compiler is None:
            return OrchestratedToolResult({"status": "error", "detail": "Panel routing is not configured."})
        try:
            route = RouteRequest.from_dict(arguments)
            resources = self._domain_registry.load(route.domain_id)
            history = tuple(
                {"role": item["role"], "text": item["content"]}
                for item in self.session_memory(session_id).history
                if item.get("role") in {"user", "assistant"} and item.get("content")
            )
            validation_feedback: dict[str, Any] | None = None
            active_surface_summary = self.active_surface_summary(session_id)
            for repair_attempt in range(self._MAX_PLAN_REPAIR_ATTEMPTS + 1):
                planned = await self._plan_agent.plan(PlanAgentRequest(
                    domain_id=route.domain_id,
                    intent=route.intent,
                    recent_history=history,
                    active_surface_summary=active_surface_summary,
                    validation_feedback=validation_feedback,
                ))
                try:
                    state = self._apply_surface_command(
                        session_id=session_id,
                        route=route,
                        command=planned.command,
                        data_bundle=planned.data_bundle,
                        domain_resources=resources,
                    )
                    panel = state.panel_ir
                    if repair_attempt:
                        trace("PLAN_COMPILE_REPAIR_SUCCEEDED attempt=%s", repair_attempt + 1)
                    break
                except PanelCompilationError as exc:
                    if repair_attempt >= self._MAX_PLAN_REPAIR_ATTEMPTS:
                        raise
                    validation_feedback = exc.for_plan_agent()
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
            return OrchestratedToolResult({"status": "error", "detail": str(exc)})

        self._active_panels[session_id] = state
        if isinstance(planned.command, CreateSurfacePlan) and planned.command.template_description:
            self._persist_reusable_template(
                command=planned.command,
                domain_resources=resources,
            )
        payload = panel_client_payload(
            panel,
            asset_urls={
                asset.id: f"/assets/domains/{panel.domain_id}/{asset.id}"
                for asset in resources.assets.assets
            },
        )
        # Browser uses this to discard a delayed cue from a replaced panel.
        payload["revision"] = state.revision
        response = {
            "status": "completed",
            "domain_id": panel.domain_id,
            "panel_id": panel.panel_id,
            "revision": state.revision,
            "presentation_instruction": resources.presentation_instruction,
            "visual_stage_map": render_visual_stage_map(panel),
            "visual_effects": _visual_effects(panel),
        }
        return OrchestratedToolResult(response=response, presentation=RenderedPresentation(panel=payload))

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
            panel = self._panel_compiler.compile(
                plan=PresentationPlan(domain_id=route.domain_id, blocks=command.blocks),
                data_bundle=data_bundle,
                domain_resources=domain_resources,
            )
            previous = self._active_panels.get(session_id)
            return ActivePanelState(
                panel_ir=panel,
                purpose=route.intent,
                revision=1 if previous is None else previous.revision + 1,
            )
        if isinstance(command, UseExistingSurfaceTemplate):
            try:
                template = domain_resources.templates.load_layout_template(command.template_id)
                plan = LayoutTemplateMaterializer().materialize(template=template, bindings=command.bindings)
            except (TemplateCatalogError, LayoutTemplateError) as exc:
                raise PanelCompilationError(str(exc), code="invalid_template_bindings") from exc
            panel = self._panel_compiler.compile(
                plan=plan,
                data_bundle=data_bundle,
                domain_resources=domain_resources,
            )
            previous = self._active_panels.get(session_id)
            return ActivePanelState(
                panel_ir=panel,
                purpose=route.intent,
                revision=1 if previous is None else previous.revision + 1,
            )
        return self._apply_patch_surface_plan(
            session_id=session_id,
            route=route,
            command=command,
            data_bundle=data_bundle,
            domain_resources=domain_resources,
        )

    def _persist_reusable_template(self, *, command: CreateSurfacePlan, domain_resources: Any) -> None:
        """Store only a fully compiled new layout; persistence never replaces UI success."""

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
        except (LayoutTemplateError, TemplateCatalogError, OSError, ValueError) as exc:
            trace("TEMPLATE_SAVE_SKIPPED reason=%s", str(exc)[:300])

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
        if command.surface_id != active.structure.surface_id:
            raise PanelCompilationError("patch_surface_plan targets a different active surface.", code="stale_surface")
        if command.base_revision != active.revision:
            raise PanelCompilationError("patch_surface_plan.base_revision is stale.", code="stale_revision")
        if route.domain_id != active.structure.domain_id:
            raise PanelCompilationError("patch_surface_plan cannot change the active surface domain.", code="domain_mismatch")

        blocks: list[tuple[str, PlanBlock]] = [
            (
                block.id,
                PlanBlock(
                    widget_id=block.widget_id,
                    grid=block.grid,
                    props=block.props,
                    initial_visibility=active.state.state_for(block.id).visibility,
                    children=tuple(
                        ChoiceChild(widget_id=child.widget_id, props=child.props)
                        for child in block.children
                    ),
                ),
            )
            for block in active.structure.blocks
        ]
        existing_anchors = {anchor.anchor_id: anchor.block_id for anchor in active.structure.anchors}
        reset_state_ids: set[str] = set()
        next_id = _next_runtime_block_id(block_id for block_id, _ in blocks)

        def find_index(anchor_id: str) -> int:
            block_id = existing_anchors.get(anchor_id)
            if block_id is None:
                raise PanelCompilationError(
                    f"patch operation references unknown anchor_id '{anchor_id}'.",
                    code="unknown_anchor",
                )
            for index, (candidate_id, _) in enumerate(blocks):
                if candidate_id == block_id:
                    return index
            raise PanelCompilationError(
                f"patch operation references a block already removed through anchor_id '{anchor_id}'.",
                code="removed_block",
            )

        for operation in command.operations:
            if isinstance(operation, AddBlockOperation):
                block_id = str(next_id)
                next_id += 1
                blocks.append((block_id, operation.block))
                reset_state_ids.add(block_id)
                continue
            if isinstance(operation, RemoveBlockOperation):
                blocks.pop(find_index(operation.anchor_id))
                continue
            if isinstance(operation, ReplaceBlockOperation):
                index = find_index(operation.anchor_id)
                block_id, _ = blocks[index]
                blocks[index] = (block_id, operation.block)
                reset_state_ids.add(block_id)
                continue
            if isinstance(operation, MoveBlockOperation):
                index = find_index(operation.anchor_id)
                block_id, block = blocks[index]
                blocks[index] = (block_id, PlanBlock(
                    widget_id=block.widget_id,
                    grid=operation.grid,
                    props=block.props,
                    initial_visibility=block.initial_visibility,
                    children=block.children,
                ))
                continue
            if isinstance(operation, UpdatePropsOperation):
                index = find_index(operation.anchor_id)
                block_id, block = blocks[index]
                blocks[index] = (block_id, PlanBlock(
                    widget_id=block.widget_id,
                    grid=block.grid,
                    props={**block.props, **operation.changes},
                    initial_visibility=block.initial_visibility,
                    children=block.children,
                ))
                continue
            raise PanelCompilationError("patch_surface_plan contains an unsupported operation.")

        if not blocks:
            raise PanelCompilationError("patch_surface_plan cannot remove every block.", code="empty_surface")

        retained_ids = {block_id for block_id, _ in blocks}
        anchor_ids_by_block_key = {
            (anchor.block_id, anchor.anchor_key): anchor.anchor_id
            for anchor in active.structure.anchors
            if anchor.block_id in retained_ids
        }
        panel = self._panel_compiler.compile(
            plan=PresentationPlan(domain_id=route.domain_id, blocks=tuple(block for _, block in blocks)),
            data_bundle=data_bundle,
            domain_resources=domain_resources,
            panel_id=active.structure.surface_id,
            block_ids=tuple(block_id for block_id, _ in blocks),
            anchor_ids_by_block_key=anchor_ids_by_block_key,
        )
        next_block_states = {
            block.id: (
                BlockState(visibility=block.visibility)
                if block.id in reset_state_ids or block.id not in active.state.block_states
                else active.state.state_for(block.id)
            )
            for block in panel.blocks
        }
        return ActivePanelState(
            structure=SurfaceStructure.from_panel_ir(panel),
            state=SurfaceState(next_block_states),
            purpose=route.intent,
            revision=active.revision + 1,
        )

    def present_visual(self, *, session_id: str, anchor_id: str, effect_id: str) -> dict[str, Any]:
        """Resolve a visual cue exclusively from the currently active PanelIR."""

        state = self._active_panels.get(session_id)
        if state is None:
            raise ValueError("no active panel")
        anchor = state.panel_ir.anchor_map.get(anchor_id)
        if anchor is None:
            raise ValueError("unknown anchor_id for the active panel")
        if effect_id not in anchor.allowed_effect_ids:
            raise ValueError("effect_id is not allowed for this anchor")
        return {
            "anchor_id": anchor.anchor_id,
            "effect_id": effect_id,
            "effect": effect_id,
            "panel_id": state.panel_ir.panel_id,
            "panel_revision": state.revision,
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
        if surface_id != state.panel_ir.panel_id:
            raise ValueError("surface_id does not match the active panel")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("revision must be a positive integer")
        if revision != state.revision:
            raise ValueError("revision does not match the active surface")
        anchor = state.panel_ir.anchor_map.get(anchor_id)
        if anchor is None:
            raise ValueError("unknown anchor_id for the active panel")
        block = state.panel_ir.block_map.get(anchor.block_id)
        widget = self._panel_compiler.widget_registry.get(block.widget_id) if block and self._panel_compiler else None
        if block is None or widget is None or widget.interaction_event != action:
            raise ValueError("action is not allowed for this anchor")
        return {
            "event": "surface_interaction",
            "surface_id": state.panel_ir.panel_id,
            "revision": state.revision,
            "anchor_id": anchor.anchor_id,
            "widget_id": block.widget_id,
            "action": action,
            "content": [child.to_dict() for child in block.children],
        }

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
        if surface_id != state.panel_ir.panel_id:
            raise ValueError("surface_id does not match the active panel")
        if base_revision != state.revision:
            raise ValueError("base_revision does not match the active panel revision")
        anchor_map = state.panel_ir.anchor_map
        block_map = state.panel_ir.block_map
        block_replacements: dict[str, BlockState] = {}
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
            if anchor.block_id in block_replacements:
                raise ValueError("updates must not target the same block more than once")
            block = block_map[anchor.block_id]
            try:
                widget = self._panel_compiler.widget_registry.get(block.widget_id) if self._panel_compiler else None
                if widget is None:
                    raise RuntimeError("panel compiler is required for state updates")
                next_values = widget.validate_state_changes(
                    current_state=state.state.state_for(block.id).to_dict(),
                    changes=changes,
                )
                block_replacements[block.id] = BlockState(**next_values)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            updated_anchor_ids.append(anchor_id)
        if self._domain_registry is None:
            raise RuntimeError("domain registry is required for panel updates")
        resources = self._domain_registry.load(state.panel_ir.domain_id)
        trace(
            "UPDATE_SURFACE_STATE_MAP_BEFORE:\n%s",
            render_visual_stage_map(state.panel_ir),
        )

        updated_state = state.replace_state(state.state.replace_block_states(block_replacements))
        updated_panel = updated_state.panel_ir
        self._active_panels[session_id] = updated_state
        payload = panel_client_payload(
            updated_panel,
            asset_urls={
                asset.id: f"/assets/domains/{updated_panel.domain_id}/{asset.id}"
                for asset in resources.assets.assets
            },
        )
        payload["revision"] = updated_state.revision
        visual_stage_map = render_visual_stage_map(updated_panel)
        trace(
            "UPDATE_SURFACE_STATE_MAP_AFTER:\n%s",
            visual_stage_map,
        )
        response = {
            "status": "completed",
            "updated_anchor_ids": updated_anchor_ids,
            "panel_id": updated_panel.panel_id,
            "surface_id": updated_panel.panel_id,
            "revision": updated_state.revision,
            "visual_stage_map": visual_stage_map,
            "visual_effects": _visual_effects(updated_panel),
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
        if command.surface_id != state.panel_ir.panel_id:
            raise ValueError("surface_id does not match the active panel")
        if command.base_revision != state.revision:
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


def _visual_effects(panel: Any) -> list[dict[str, str]]:
    """Expose only effect IDs granted by the compiler-owned anchor map."""

    effect_ids = sorted({effect for anchor in panel.anchors for effect in anchor.allowed_effect_ids})
    return [
        {
            "id": effect_id,
            "description": "Làm nổi bật vùng đang nói tới." if effect_id == "highlight" else "Khoanh rõ vùng đang nói tới.",
        }
        for effect_id in effect_ids
    ]


def _next_runtime_block_id(block_ids: Any) -> int:
    """Allocate the next opaque numeric block ID without exposing it to agents."""

    numeric_ids = [int(item) for item in block_ids if isinstance(item, str) and item.isdigit()]
    return max(numeric_ids, default=0) + 1
