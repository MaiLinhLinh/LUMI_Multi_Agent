"""Shared state management around a future concrete Gemini Live transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gemini_live.domains import DomainRequest
from gemini_live.presentation import PresentationPipeline, PresentationRequest

from .dispatcher import LiveToolDispatcher
from .memory import SessionMemoryStore
from .visual_presentation import FactPresentationState, RenderedPresentation
from .session_protocol import LiveSessionState, can_transition
from gemini_live.trace import trace, warning


@dataclass
class OrchestratedToolResult:
    """Final result of one tool call after shared presentation processing."""

    response: dict[str, Any]
    presentation: RenderedPresentation | None = None


class LiveSessionOrchestrator:
    """Own session memory and domain dispatch; transport remains replaceable."""

    def __init__(
        self,
        dispatcher: LiveToolDispatcher,
        presentation_pipeline: PresentationPipeline,
        memory_store: SessionMemoryStore | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._presentation_pipeline = presentation_pipeline
        self._memory_store = memory_store or SessionMemoryStore()
        self._technical_states: dict[str, LiveSessionState] = {}
        self._fact_presentations: dict[str, FactPresentationState] = {}

    def session_state(self, session_id: str) -> LiveSessionState:
        """Return shared technical state; domain business state is separate."""

        return self._technical_states.setdefault(session_id, LiveSessionState.IDLE)

    def transition_session(self, *, session_id: str, target: LiveSessionState) -> LiveSessionState:
        """Apply only a CP-01-approved transition for a persistent session."""

        current = self.session_state(session_id)
        if current == target:
            return current
        if not can_transition(current, target):
            raise RuntimeError(f"Invalid Live session transition: {current} -> {target}")
        self._technical_states[session_id] = target
        return target

    def reset_session_state(self, session_id: str) -> None:
        """Use after a closed/recovered transport; keep domain memory intact."""

        self._technical_states[session_id] = LiveSessionState.IDLE

    async def execute_tool_call(
        self,
        *,
        session_id: str,
        query: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self.execute_tool_call_result(
            session_id=session_id,
            query=query,
            tool_name=tool_name,
            arguments=arguments,
        )
        return result.response

    async def execute_tool_call_result(
        self,
        *,
        session_id: str,
        query: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> OrchestratedToolResult:
        """Execute a domain tool and build its sole Gemini function response."""
        memory = self._memory_store.get(session_id)
        domain = self._dispatcher._registry.domain_for_tool(tool_name)
        trace("TOOL_DISPATCH_START domain=%s tool=%s", getattr(domain, "domain_id", "unknown"), tool_name)
        request = DomainRequest(query=query, history=tuple(memory.history))
        result = await self._dispatcher.execute(
            tool_name=tool_name,
            arguments=arguments,
            request=request,
            domain_contexts=memory.domain_contexts,
        )
        trace(
            "TOOL_DISPATCH_DONE status=%s correct=%s attempts=%s",
            result.status,
            result.status == "correct",
            0,
        )
        response: dict[str, Any] = {
            "status": result.status,
            "domain_id": domain.domain_id,
        }
        if result.detail:
            response["detail"] = result.detail
        presentation_request = result.presentation
        if isinstance(presentation_request, PresentationRequest):
            try:
                trace("PRESENTATION_PIPELINE_START domain=%s", presentation_request.domain_id)
                prepared = self._presentation_pipeline.prepare(
                    request=presentation_request,
                )
            except Exception as exc:
                warning("PRESENTATION_PIPELINE_FAILED reason=%s", exc)
                return OrchestratedToolResult(
                    response={
                        "status": "error",
                        "domain_id": domain.domain_id,
                        "detail": f"Presentation could not be prepared: {exc}",
                    },
                )
            live_fact_pack = self._presentation_pipeline.build_live_fact_pack(
                presentation_request,
                prepared,
            )
            # The alias-to-target map is intentionally not included in the
            # Gemini tool response. CP-04 will retain it per Live session.
            response["facts"] = live_fact_pack.facts_for_live
            response["visual_effects"] = live_fact_pack.supported_effects
            if prepared.visual_stage_map:
                response["visual_stage_map"] = prepared.visual_stage_map
            presentation_instruction = presentation_request.adapter.live_presentation_instruction()
            if presentation_instruction:
                response["presentation_instruction"] = presentation_instruction
            presentation_context = presentation_request.adapter.live_presentation_context()
            if presentation_context:
                response.update(presentation_context)
            self._fact_presentations[session_id] = FactPresentationState.from_fact_pack(
                template_id=presentation_request.template_id,
                pack=live_fact_pack,
            )
            response["presentation"] = {
                "template_id": presentation_request.template_id,
                "mode": "fact_pack",
            }
            trace(
                "LIVE_PRESENTATION_INSTRUCTION_READY domain=%s chars=%s",
                presentation_request.domain_id,
                len(presentation_instruction),
            )
            rendered_presentation = RenderedPresentation(
                panel={
                    "ui_type": presentation_request.domain_id,
                    "template_id": presentation_request.template_id,
                    "html": prepared.panel.html,
                },
            )
            return OrchestratedToolResult(
                response=response,
                presentation=rendered_presentation,
            )
        return OrchestratedToolResult(response=response)

    def present_visual(
        self,
        *,
        session_id: str,
        anchor_id: str,
        effect_id: str,
    ) -> dict[str, str]:
        """Resolve one Gemini Live visual call without exposing DOM IDs to it."""

        presentation = self._fact_presentations.get(session_id)
        if presentation is None:
            raise ValueError("no active presentation is available")
        return presentation.resolve(anchor_id=anchor_id, effect_id=effect_id)

    def session_memory(self, session_id: str) -> Any:
        """Return the server-owned memory used by a reconnect-safe Live turn."""
        return self._memory_store.get(session_id)

    def remember_turn(self, *, session_id: str, user_text: str, assistant_text: str) -> None:
        memory = self._memory_store.get(session_id)
        memory.append("user", user_text)
        memory.append("assistant", assistant_text)
