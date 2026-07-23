"""LLM Manager Agent for ambiguous domain routing."""
from __future__ import annotations

from typing import Any

from rag_manager.llm.function_calling_runtime import GeminiFunctionCallingRuntime
from rag_manager.state import GraphState
from rag_manager.tools.registry import declarations


MANAGER_DECLARATION = declarations("manager")[0]
SYSTEM = """You are the Manager Agent for a Vietnamese assistant. You must call
select_subagent exactly once. Choose weather only for weather conditions,
forecasts, rain, temperature, wind, or humidity. Choose music only to find or
play music from the local catalog. Choose visual only when the user explicitly
asks to interact with an existing visualization. Use the current user request
over older history. Do not infer a domain merely from an ambiguous word."""
_VALID_AGENTS = {"weather", "music", "visual"}


def manager_node(state: GraphState, runtime: GeminiFunctionCallingRuntime) -> dict[str, Any]:
    """Use Gemini for an ambiguous route; preserve history as routing context."""
    history = state.get("history", [])
    history_text = "\n".join(
        f"{item.get('role', '')}: {item.get('content', '')}"
        for item in history[-6:]
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
    )
    prompt = f"Current user request: {state.get('query', '')}"
    if history_text:
        prompt = f"Relevant conversation history:\n{history_text}\n\n{prompt}"
    output = runtime.select_subagent(
        system_instruction=SYSTEM,
        user_text=prompt,
        declaration=MANAGER_DECLARATION,
    )
    decision = output.get("decision") if isinstance(output.get("decision"), dict) else {}
    agent = str(decision.get("agent", "")).strip().lower()
    if agent not in _VALID_AGENTS:
        # This node is reached only after the high-confidence local router
        # declined to decide. Never silently guess a domain here.
        agent = "error"
        reason = "Manager LLM did not return a valid route."
    else:
        reason = str(decision.get("reason", "")).strip() or "Manager LLM selected the matching domain."
    return {
        "selected_agent": agent,
        "manager_decision": {
            "selected_agent": agent,
            "reason": reason,
            "source": "gemini_function_call",
            "error": output.get("error"),
        },
        "llm_usage": [output.get("usage", {})],
    }
