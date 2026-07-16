"""Routing-only Manager Agent."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from rag_manager.agents.manager_structured_schema import ManagerPlanResponse
from rag_manager.llm.prompts import MANAGER_SYSTEM_PROMPT
from rag_manager.state import ManagerPlan

if TYPE_CHECKING:
    from rag_manager.llm.gemini_client import GeminiClient


def classify_intent(
    client: "GeminiClient",
    query: str,
    history: list[dict[str, str]] | None = None,
) -> ManagerPlan:
    """Route the current query and relevant history using Structured Output."""

    user_message = json.dumps(
        {
            "query": query,
            "relevant_history": _manager_relevant_history(query, history or []),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    plan = client.chat_structured_json(
        MANAGER_SYSTEM_PROMPT,
        user_message,
        response_schema=ManagerPlanResponse,
    )
    return cast(ManagerPlan, plan)


def _manager_relevant_history(
    query: str,
    history: list[Any],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for message in history:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        stripped_content = content.strip()
        if stripped_content:
            messages.append({"role": role, "content": stripped_content})
    if (
        messages
        and messages[-1]["role"] == "user"
        and messages[-1]["content"] == query.strip()
    ):
        messages.pop()
    return messages[-8:]
