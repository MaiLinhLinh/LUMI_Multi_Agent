"""Shared state management around a future concrete Gemini Live transport."""

from __future__ import annotations

from typing import Any

from gemini_live.domains import DomainRequest
from gemini_live.presentation import PresentationPipeline, PresentationRequest

from .dispatcher import LiveToolDispatcher
from .memory import SessionMemoryStore
from .scene_state import LivePresentation, active_scenes_from_compiled_plan, scene_instruction
from .session_protocol import LiveSessionState, can_transition


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
        return result.tool_response

    async def execute_tool_call_result(
        self,
        *,
        session_id: str,
        query: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Execute a domain tool and retain its server-only presentation result."""
        memory = self._memory_store.get(session_id)
        request = DomainRequest(query=query, history=tuple(memory.history))
        result = await self._dispatcher.execute(
            tool_name=tool_name,
            arguments=arguments,
            request=request,
            domain_contexts=memory.domain_contexts,
        )
        presentation_request = result.presentation
        if isinstance(presentation_request, PresentationRequest):
            try:
                prepared = self._presentation_pipeline.prepare(
                    request=presentation_request,
                    query=query,
                    history=list(memory.history),
                )
            except Exception as exc:
                return type(result)(
                    tool_response={
                        "status": "error",
                        "message": f"Presentation could not be prepared: {exc}",
                    },
                    context=result.context,
                )
            scenes = active_scenes_from_compiled_plan(
                domain_id=presentation_request.domain_id,
                template_id=presentation_request.template_id,
                compiled_plan=prepared.compiled_plan,
            )
            response = dict(result.tool_response)
            response["facts"] = self._presentation_pipeline.live_fact_pack(
                presentation_request,
                prepared,
            )
            response["presentation"] = {
                "template_id": presentation_request.template_id,
                "presentation_plan": {
                    "schema_version": "lumi.live_scene_plan.v1",
                    "scene_count": len(scenes.scenes),
                    "current_scene": scene_instruction(scenes),
                },
            }
            result.tool_response = response
            result.presentation = LivePresentation(
                panel={
                    "ui_type": presentation_request.domain_id,
                    "template_id": presentation_request.template_id,
                    "html": prepared.panel.html,
                },
                scenes=scenes,
            )
        return result

    def session_memory(self, session_id: str) -> Any:
        """Return the server-owned memory used by a reconnect-safe Live turn."""
        return self._memory_store.get(session_id)

    def remember_turn(self, *, session_id: str, user_text: str, assistant_text: str) -> None:
        memory = self._memory_store.get(session_id)
        memory.append("user", user_text)
        memory.append("assistant", assistant_text)
