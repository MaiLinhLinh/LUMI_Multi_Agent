from __future__ import annotations

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
from rag_manager.state import GraphState
from rag_manager.tools.music_tools import MusicTools
from rag_manager.tools.visual_tools import VisualTools
from rag_manager.tools.weather_tools import WeatherTools

class AppRuntime:
    def __init__(self, settings: Settings) -> None:
        self.llm = GeminiFunctionCallingRuntime(api_key=settings.gemini_api_key, model=settings.gemini_model)
        self.weather = WeatherTools(settings)
        self.visual = VisualTools()
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
        started=time.perf_counter(); callback=state.get("response_stream_callback"); result=run_weather(runtime.llm, runtime.weather, state["query"], state.get("history"), state.get("weather_context"), (lambda text: callback("weather", text)) if callable(callback) else None)
        return {"agent_result":result,"final_answer":result["answer"],"weather_context":result.get("weather_context", state.get("weather_context", {})),"tool_trace":result["tool_trace"],"llm_usage":state.get("llm_usage", [])+result.get("llm_usage", []),"timings":{**state.get("timings", {}),"weather_ms":round((time.perf_counter()-started)*1000,2),**result.get("stream_timings", {})}}
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
        return {"visualization_payload":result["payload"],"tool_trace":state.get("tool_trace",[])+result["tool_trace"],"timings":{**state.get("timings",{}),"visual_ms":round((time.perf_counter()-started)*1000,2)}}
    def select(state: GraphState) -> str: return state.get("selected_agent", "weather")
    def after_weather(state: GraphState) -> str: return "visual" if state.get("agent_result",{}).get("status")=="completed" else "end"
    graph=StateGraph(GraphState)
    graph.add_node("router",router_node); graph.add_node("manager",manager_graph_node); graph.add_node("manager_error",manager_error_node); graph.add_node("weather",weather_node); graph.add_node("music",music_node); graph.add_node("visual",visual_node)
    graph.add_edge(START,"router")
    graph.add_conditional_edges("router",lambda s: "manager" if s.get("route")=="manager" else select(s),{"manager":"manager","weather":"weather","music":"music","visual":"visual"})
    graph.add_conditional_edges("manager",select,{"weather":"weather","music":"music","visual":"visual","error":"manager_error"})
    graph.add_edge("manager_error",END)
    graph.add_conditional_edges("weather",after_weather,{"visual":"visual","end":END})
    graph.add_conditional_edges("music",after_weather,{"visual":"visual","end":END})
    graph.add_edge("visual",END)
    return graph.compile()
