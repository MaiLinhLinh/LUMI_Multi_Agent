from typing import Any, TypedDict

class GraphState(TypedDict, total=False):
    query: str
    history: list[dict[str, str]]
    weather_context: dict[str, Any]
    music_session: dict[str, Any]
    session_id: str
    route: str
    selected_agent: str
    manager_decision: dict[str, Any]
    agent_result: dict[str, Any]
    final_answer: str
    visualization_payload: dict[str, Any]
    music_player: dict[str, Any]
    tool_trace: list[dict[str, Any]]
    timings: dict[str, float]
    llm_usage: list[dict[str, Any]]
    response_stream_callback: Any
    error: str
