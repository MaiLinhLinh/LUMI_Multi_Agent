"""Aggregator Agent implementation."""

from __future__ import annotations

import json
from time import perf_counter
from typing import TYPE_CHECKING, Any

from rag_manager.config import Settings, load_settings
from rag_manager.llm.prompts import AGGREGATOR_SYSTEM_PROMPT
from rag_manager.state import AgentState

if TYPE_CHECKING:
    from rag_manager.llm.gemini_client import GeminiClient as GeminiClientType


def run_aggregator_agent(
    state: AgentState,
    *,
    settings: Settings | None = None,
    client: "GeminiClientType | None" = None,
) -> AgentState:
    """Run the aggregator agent and return state updates."""
    started_at = perf_counter()
    single_answer = get_single_agent_answer(state)
    if single_answer:
        return {
            "final_response": single_answer,
            "timings": {"aggregate": _elapsed_since(started_at)},
        }

    if not has_agent_outputs(state):
        return {
            "final_response": build_no_output_response(state),
            "timings": {"aggregate": _elapsed_since(started_at)},
        }

    settings = settings or load_settings()
    client = client or _create_gemini_client(settings)
    final_response = aggregate_agent_outputs(client, state)
    return {
        "final_response": final_response,
        "timings": {"aggregate": _elapsed_since(started_at)},
    }


def aggregate_agent_outputs(
    client: "GeminiClientType",
    state: AgentState,
) -> str:
    user_message = "\n".join(
        [
            f"User query: {state.get('query', '')}",
            "Aggregator JSON:",
            json.dumps(build_aggregator_payload(state), ensure_ascii=False, sort_keys=True),
        ]
    )
    return client.chat_text(
        AGGREGATOR_SYSTEM_PROMPT,
        user_message,
        temperature=0.2,
    )


def build_aggregator_payload(state: AgentState) -> dict[str, Any]:
    agent_outputs = _collect_agent_outputs(state)
    return {
        "query": state.get("query", ""),
        "execution_mode": state.get("execution_mode", ""),
        "selected_agents": state.get("selected_agents", []),
        "intent": state.get("intent", {}),
        "agent_outputs": agent_outputs,
        "successful_agents": list(agent_outputs),
        "failed_agents": _collect_failed_agents(state),
        "cache_stats": state.get("cache_stats", {}),
        "timings": state.get("timings", {}),
        "errors": state.get("errors", []),
    }


def get_single_agent_answer(state: AgentState) -> str:
    if state.get("execution_mode") != "single":
        return ""

    selected_agents = state.get("selected_agents", [])
    topic = selected_agents[0] if len(selected_agents) == 1 else ""
    if not topic:
        intent = state.get("intent", {})
        topic = intent.get("primary_intent", "") if isinstance(intent, dict) else ""

    if topic not in {"weather", "news", "wiki"}:
        return ""

    answer = state.get(f"{topic}_answer", "")
    return answer.strip() if isinstance(answer, str) else ""


def has_agent_outputs(state: AgentState) -> bool:
    return bool(_collect_agent_outputs(state))


def build_no_output_response(state: AgentState) -> str:
    errors = state.get("errors", [])
    if not errors:
        return "Mình chưa có dữ liệu từ agent nào để tổng hợp câu trả lời."

    error_lines = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        source = error.get("source", "unknown")
        message = error.get("message", "Không rõ lỗi.")
        error_lines.append(f"- {source}: {message}")

    if not error_lines:
        return "Các agent không trả được dữ liệu đủ để tổng hợp câu trả lời."

    return "\n".join(
        [
            "Mình chưa có dữ liệu thành công từ agent nào để tổng hợp câu trả lời.",
            "Lỗi ghi nhận:",
            *error_lines,
        ]
    )


def _collect_agent_outputs(state: AgentState) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    for topic in ("weather", "news", "wiki"):
        answer = state.get(f"{topic}_answer", "")
        data = state.get(f"{topic}_data", {})
        if answer or data:
            outputs[topic] = {
                "answer": answer,
                "data": data,
            }
    return outputs


def _collect_failed_agents(state: AgentState) -> list[dict[str, str]]:
    failed_agents: list[dict[str, str]] = []
    for error in state.get("errors", []):
        if not isinstance(error, dict):
            continue
        failed_agents.append(
            {
                "source": str(error.get("source", "unknown")),
                "message": str(error.get("message", "")),
            }
        )
    return failed_agents


def _create_gemini_client(settings: Settings) -> "GeminiClientType":
    from rag_manager.llm.gemini_client import GeminiClient

    return GeminiClient(settings)


def _elapsed_since(started_at: float) -> float:
    return perf_counter() - started_at
