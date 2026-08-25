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
    ActivePanelState,
    PanelCompilationError,
    PanelCompiler,
    RouteRequest,
    panel_client_payload,
    render_visual_stage_map,
)
from gemini_live_2.plan_agent import (
    CreatePlanDecision,
    PlanAgent,
    PlanAgentError,
    PlanAgentRequest,
    UseExistingPlanDecision,
)
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
        layout_template_materializer: LayoutTemplateMaterializer | None = None,
        template_extractor: TemplateExtractor | None = None,
    ) -> None:
        self._memory_store = memory_store or SessionMemoryStore()
        self._domain_registry = domain_registry
        self._plan_agent = plan_agent
        self._panel_compiler = panel_compiler
        self._layout_template_materializer = layout_template_materializer or LayoutTemplateMaterializer()
        self._template_extractor = template_extractor
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

    def active_panel_presentation_context(self, session_id: str) -> dict[str, Any] | None:
        """Return the trusted context needed to resume one rendered panel."""

        state = self._active_panels.get(session_id)
        if state is None or self._domain_registry is None:
            return None
        resources = self._domain_registry.load(state.panel_ir.domain_id)
        return {
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
            for repair_attempt in range(self._MAX_PLAN_REPAIR_ATTEMPTS + 1):
                planned = await self._plan_agent.plan(PlanAgentRequest(
                    domain_id=route.domain_id,
                    intent=route.intent,
                    recent_history=history,
                    validation_feedback=validation_feedback,
                ))
                decision = planned.decision
                if isinstance(decision, UseExistingPlanDecision):
                    layout_template = resources.templates.load_layout_template(decision.template_id)
                    plan = self._layout_template_materializer.materialize(
                        template=layout_template,
                        bindings=decision.bindings,
                    )
                elif isinstance(decision, CreatePlanDecision):
                    plan = decision.plan
                else:  # Defensive: PlanAgentResult is intentionally closed today.
                    raise PlanAgentError("Plan Agent returned an unsupported decision.")
                try:
                    panel = self._panel_compiler.compile(
                        plan=plan,
                        data_bundle=planned.data_bundle,
                        domain_resources=resources,
                    )
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
            if isinstance(decision, CreatePlanDecision):
                extractor = self._template_extractor or TemplateExtractor(
                    self._panel_compiler.widget_registry
                )
                layout_template = extractor.extract(
                    plan=plan,
                    template_id=resources.templates.next_generated_template_id(),
                    description=decision.template_description,
                )
                resources.templates.save_layout_template(layout_template)
        except (
            ManifestError,
            TemplateCatalogError,
            LayoutTemplateError,
            PanelCompilationError,
            PlanAgentError,
            ValueError,
        ) as exc:
            return OrchestratedToolResult({"status": "error", "detail": str(exc)})

        previous = self._active_panels.get(session_id)
        state = ActivePanelState(panel_ir=panel) if previous is None else previous.replace(panel)
        self._active_panels[session_id] = state
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
            "target_id": anchor.target_id,
            "effect_id": effect_id,
            "effect": effect_id,
            "panel_id": state.panel_ir.panel_id,
            "panel_revision": state.revision,
        }

    def panel_action(
        self,
        *,
        session_id: str,
        action_id: str,
        anchor_ids: list[str],
    ) -> PanelActionResult:
        """Validate and atomically apply a state change to the active panel."""

        if action_id != "reveal":
            raise ValueError("unsupported panel action")
        if not anchor_ids or not all(isinstance(anchor_id, str) and anchor_id for anchor_id in anchor_ids):
            raise ValueError("anchor_ids must contain at least one non-empty string")
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("anchor_ids must not contain duplicates")

        state = self._active_panels.get(session_id)
        if state is None:
            raise ValueError("no active panel")
        anchor_map = state.panel_ir.anchor_map
        block_map = state.panel_ir.block_map
        anchors = []
        for anchor_id in anchor_ids:
            anchor = anchor_map.get(anchor_id)
            if anchor is None:
                raise ValueError("unknown anchor_id for the active panel")
            anchors.append(anchor)
        block_ids = {anchor.block_id for anchor in anchors}
        if any(block_map[block_id].visibility != "hidden" for block_id in block_ids):
            raise ValueError("reveal is allowed only for blocks that are currently hidden")
        if self._domain_registry is None:
            raise RuntimeError("domain registry is required for panel updates")
        resources = self._domain_registry.load(state.panel_ir.domain_id)
        trace(
            "PANEL_ACTION_STAGE_MAP_BEFORE action=%s:\n%s",
            action_id,
            render_visual_stage_map(state.panel_ir),
        )

        updated_panel = state.panel_ir.with_block_visibility(block_ids=block_ids, visibility="visible")
        updated_state = state.replace(updated_panel)
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
            "PANEL_ACTION_STAGE_MAP_AFTER action=%s:\n%s",
            action_id,
            visual_stage_map,
        )
        response = {
            "status": "completed",
            "action_id": action_id,
            "anchor_ids": anchor_ids,
            "panel_id": updated_panel.panel_id,
            "revision": updated_state.revision,
            "visual_stage_map": visual_stage_map,
            "visual_effects": _visual_effects(updated_panel),
        }
        return PanelActionResult(response=response, panel_update=payload)

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
