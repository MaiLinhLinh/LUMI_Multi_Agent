from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from rag_manager.agents.manager import manager_node
from rag_manager.agents.music_agent import run_music
from rag_manager.agents.router import router_node
from rag_manager.agents.visual_agent import run_visual
from rag_manager.agents.weather_agent import run_weather
from rag_manager.config import Settings
from rag_manager.llm.function_calling_runtime import GeminiFunctionCallingRuntime
from rag_manager.presentation.registry import PresentationRegistry
from rag_manager.presentation.capabilities import presentation_capabilities
from rag_manager.presentation.schemas import CompiledPresentationPlan, PresentationPlan
from rag_manager.state import GraphState
from rag_manager.tools.music_tools import MusicTools
from rag_manager.tools.visual_tools import VisualTools
from rag_manager.tools.weather_tools import WeatherTools

logger = logging.getLogger("lumi.presentation")

class AppRuntime:
    def __init__(self, settings: Settings) -> None:
        self.llm = GeminiFunctionCallingRuntime(api_key=settings.gemini_api_key, model=settings.gemini_model)
        self.weather = WeatherTools(settings)
        self.visual = VisualTools()
        self.presentation_enabled = settings.presentation_enabled
        self.presentation_ctc_prebuffer_ms = settings.presentation_ctc_prebuffer_ms
        self.presentation_registry = PresentationRegistry.with_weather()
        self._settings = settings
        self._music: MusicTools | None = None
    @property
    def music(self) -> MusicTools:
        if self._music is None: self._music = MusicTools(self._settings)
        return self._music

def build_workflow(settings: Settings):
    runtime = AppRuntime(settings)
    def manager_graph_node(state: GraphState) -> dict[str, Any]:
        started = time.perf_counter()
        result = manager_node(state, runtime.llm)
        return {
            **result,
            "timings": {**state.get("timings", {}), "manager_ms": round((time.perf_counter() - started) * 1000, 2)},
        }
    def manager_error_node(state: GraphState) -> dict[str, Any]:
        return {
            "final_answer": "Tôi chưa xác định được yêu cầu thuộc nhóm hỗ trợ nào. Bạn có thể nói rõ hơn về thời tiết, âm nhạc hoặc phần trực quan không?",
            "agent_result": {"status": "error"},
        }
    def weather_node(state: GraphState) -> dict[str, Any]:
        started=time.perf_counter(); callback=state.get("response_stream_callback"); result=run_weather(runtime.llm, runtime.weather, state["query"], state.get("history"), state.get("weather_context"), (lambda text: callback("weather", text)) if callable(callback) else None, presentation_enabled=runtime.presentation_enabled)
        weather_facts = result.get("weather_facts", {})
        return {"agent_result":result,"final_answer":result["answer"],"weather_context":result.get("weather_context", state.get("weather_context", {})),"weather_facts":weather_facts,"presentation_domain_data":weather_facts,"tool_trace":result["tool_trace"],"llm_usage":state.get("llm_usage", [])+result.get("llm_usage", []),"timings":{**state.get("timings", {}),"weather_ms":round((time.perf_counter()-started)*1000,2),**result.get("stream_timings", {})}}
    def music_node(state: GraphState) -> dict[str, Any]:
        started=time.perf_counter(); callback=state.get("response_stream_callback"); result=run_music(runtime.llm, runtime.music, state["query"], state.get("history"), state.get("music_session"), (lambda text: callback("music", text)) if callable(callback) else None)
        return {"agent_result":result,"final_answer":result["answer"],"music_player":result.get("music_player",{}),"music_session":result.get("music_session", state.get("music_session", {})),"tool_trace":result["tool_trace"],"llm_usage":state.get("llm_usage", [])+result.get("llm_usage", []),"timings":{**state.get("timings", {}),"music_ms":round((time.perf_counter()-started)*1000,2),**result.get("stream_timings", {})}}
    def visual_node(state: GraphState) -> dict[str, Any]:
        started=time.perf_counter()
        result=run_visual(
            runtime.visual,
            state.get("agent_result",{}).get("data",{}),
            music_player=state.get("music_player") or None,
        )
        payload = result["payload"]
        presentation_callback = state.get("presentation_stream_callback")
        domain_id = result.get("presentation_context", {}).get("domain_id")
        if runtime.presentation_enabled and runtime.presentation_registry.get(domain_id) and callable(presentation_callback):
            presentation_callback("panel_ready", payload)
        return {"visualization_payload":payload,"presentation_context":result.get("presentation_context", {}),"tool_trace":state.get("tool_trace",[])+result["tool_trace"],"timings":{**state.get("timings",{}),"visual_ms":round((time.perf_counter()-started)*1000,2)}}
    def presentation_planner_node(state: GraphState) -> dict[str, Any]:
        planner_started = time.perf_counter()
        context = state.get("presentation_context", {})
        domain_id = context.get("domain_id") if isinstance(context, dict) else None
        adapter = runtime.presentation_registry.get(domain_id)
        template_id = context.get("template_id") if isinstance(context, dict) else None
        compact_data = context.get("compact_data") if isinstance(context, dict) else None
        if adapter is None or not isinstance(template_id, str) or not isinstance(compact_data, dict):
            return {"final_answer": state.get("final_answer", "")}

        try:
            metadata = adapter.load_template_metadata(template_id)
            capabilities = presentation_capabilities(metadata)
        except (TypeError, ValueError) as exc:
            return {
                "error": f"presentation metadata unavailable: {exc}",
                "timings": {**state.get("timings", {}), "presentation_planner_ms": 0.0, "presentation_compiler_ms": 0.0},
            }
        grounded_facts = adapter.build_candidate_facts(
            state.get("presentation_domain_data", {}),
            compact_data=compact_data,
            presentation_capabilities=capabilities,
        )
        logger.info(
            "[PRESENTATION:CANDIDATE_FACTS] domain=%s template=%s count=%s facts=%s",
            domain_id,
            template_id,
            len(grounded_facts),
            [
                {
                    "id": fact.id,
                    "metric": fact.metric,
                    "operation": fact.operation,
                    "focus": fact.focus,
                    "effect_hint": fact.effect_hint,
                    "entity": fact.entity,
                    "evidence_kind": (fact.visual_evidence or {}).get("kind"),
                }
                for fact in grounded_facts
            ],
        )
        if not grounded_facts:
            logger.warning(
                "[PRESENTATION:NO_CANDIDATE_FACTS] domain=%s template=%s",
                domain_id, template_id,
            )
            return {"final_answer": state.get("final_answer", "")}
        callback = state.get("response_stream_callback")
        presentation_callback = state.get("presentation_stream_callback")
        compiled_streamed_steps = []
        compiler_ms = 0.0

        def publish_compiled_step(compiled: Any) -> None:
            """Preserve text-only callbacks outside the web presentation path.

            Gemini Live + CTC consumes one completed, compiler-validated
            presentation contract below. It never receives per-scene TTS turns.
            """
            if not callable(presentation_callback) and callable(callback):
                callback("weather", compiled.narration)

        def compile_and_stream(step: Any) -> None:
            nonlocal compiler_ms
            started = time.perf_counter()
            compiled = adapter.compile(
                PresentationPlan(steps=[step]),
                template_metadata=metadata,
                compact_data=compact_data,
                grounded_facts=grounded_facts,
            ).steps[0]
            compiler_ms += (time.perf_counter() - started) * 1000
            compiled_streamed_steps.append(compiled)
            logger.info(
                "[PRESENTATION:STEP] source=stream target=%s effect=%s gesture=%s narration_chars=%s",
                compiled.target_id,
                compiled.effect,
                compiled.gesture,
                len(compiled.narration),
            )
            publish_compiled_step(compiled)

        try:
            result = adapter.plan(
                runtime.llm,
                query=state["query"],
                history=state.get("history"),
                domain_data=state.get("presentation_domain_data", {}),
                template_id=template_id,
                capabilities=capabilities,
                grounded_facts=grounded_facts,
                on_valid_step=compile_and_stream,
            )
        except Exception as exc:  # Keep the already-rendered weather panel usable.
            logger.exception(
                "[PRESENTATION:PLANNER_EXCEPTION] domain=%s template=%s",
                domain_id,
                template_id,
            )
            result = {
                "plan": adapter.fallback_plan(state.get("presentation_domain_data", {}), capabilities, grounded_facts),
                "usage": {},
                "fallback": True,
                "error": {"type": "planner_exception", "detail": str(exc)},
            }
        planner_ms = (time.perf_counter() - planner_started) * 1000
        plan = result["plan"]
        try:
            if compiled_streamed_steps:
                compiled_plan = CompiledPresentationPlan(steps=compiled_streamed_steps)
            else:
                compiler_started = time.perf_counter()
                compiled_plan = adapter.compile(
                    plan,
                    template_metadata=metadata,
                    compact_data=compact_data,
                    grounded_facts=grounded_facts,
                )
                compiler_ms += (time.perf_counter() - compiler_started) * 1000
                for step in compiled_plan.steps:
                    publish_compiled_step(step)
        except Exception as exc:  # Do not turn a presentation failure into a weather failure.
            return {
                "error": f"presentation compiler unavailable: {exc}",
                "timings": {
                    **state.get("timings", {}),
                    "presentation_planner_ms": round(planner_ms, 2),
                    "presentation_compiler_ms": round(compiler_ms, 2),
                },
            }
        final_answer = "\n\n".join(step.narration for step in compiled_plan.steps)
        if callable(presentation_callback):
            presentation_callback("presentation_contract", {
                "schema_version": "lumi.presentation_live_ctc.v1",
                "prebuffer_ms": getattr(runtime, "presentation_ctc_prebuffer_ms", 8000),
                "scenes": [step.model_dump(mode="json") for step in compiled_plan.steps],
            })
        logger.info(
            "[PRESENTATION:PLAN] steps=%s streamed_steps=%s fallback=%s planner_ms=%.1f compiler_ms=%.1f",
            len(compiled_plan.steps),
            len(compiled_streamed_steps),
            result.get("fallback", False),
            planner_ms,
            compiler_ms,
        )
        usage = result.get("usage", {})
        return {
            "final_answer": final_answer,
            "presentation_plan": plan.model_dump(mode="json"),
            "compiled_presentation_plan": compiled_plan.model_dump(mode="json"),
            "grounded_facts": [fact.model_dump(mode="json") for fact in grounded_facts],
            "llm_usage": state.get("llm_usage", []) + ([usage] if usage else []),
            "timings": {
                **state.get("timings", {}),
                "presentation_planner_ms": round(planner_ms, 2),
                "presentation_compiler_ms": round(compiler_ms, 2),
            },
        }
    def select(state: GraphState) -> str: return state.get("selected_agent", "weather")
    def after_weather(state: GraphState) -> str: return "visual" if state.get("agent_result",{}).get("status")=="completed" else "end"
    def after_visual(state: GraphState) -> str:
        payload = state.get("visualization_payload", {})
        context = state.get("presentation_context", {})
        if isinstance(context, dict) and runtime.presentation_enabled and runtime.presentation_registry.get(context.get("domain_id")) and context.get("template_id"):
            return "presentation_planner"
        return "end"
    graph=StateGraph(GraphState)
    graph.add_node("router",router_node); graph.add_node("manager",manager_graph_node); graph.add_node("manager_error",manager_error_node); graph.add_node("weather",weather_node); graph.add_node("music",music_node); graph.add_node("visual",visual_node); graph.add_node("presentation_planner",presentation_planner_node)
    graph.add_edge(START,"router")
    graph.add_conditional_edges("router",lambda s: "manager" if s.get("route")=="manager" else select(s),{"manager":"manager","weather":"weather","music":"music","visual":"visual"})
    graph.add_conditional_edges("manager",select,{"weather":"weather","music":"music","visual":"visual","error":"manager_error"})
    graph.add_edge("manager_error",END)
    graph.add_conditional_edges("weather",after_weather,{"visual":"visual","end":END})
    graph.add_conditional_edges("music",after_weather,{"visual":"visual","end":END})
    graph.add_conditional_edges("visual",after_visual,{"presentation_planner":"presentation_planner","end":END})
    graph.add_edge("presentation_planner",END)
    return graph.compile()
