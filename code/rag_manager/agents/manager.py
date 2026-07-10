"""Manager Agent routing module."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, cast

from rag_manager.llm.prompts import MANAGER_SYSTEM_PROMPT
from rag_manager.state import AgentTopic, ExecutionMode, ManagerPlan

if TYPE_CHECKING:
    from rag_manager.llm.gemini_client import GeminiClient

VALID_TOPICS: set[AgentTopic] = {"weather", "news", "wiki"}
VALID_EXECUTION_MODES: set[ExecutionMode] = {"single", "parallel", "sequential"}


def classify_intent(client: "GeminiClient", query: str) -> ManagerPlan:
    """Classify user intent and produce a validated manager plan."""
    user_message = f"User query:\n{query}"
    raw_plan = client.chat_json(MANAGER_SYSTEM_PROMPT, user_message)
    if raw_plan.get("error"):
        return _heuristic_fallback_plan(query, str(raw_plan.get("message", "")))

    plan = _filter_valid_topics(raw_plan)
    plan = _validate_execution_mode(plan)
    plan = _normalize_plan(plan, query)
    return cast(ManagerPlan, plan)


def _filter_valid_topics(plan: dict) -> dict:
    topics = plan.get("topics", [])
    if not isinstance(topics, list):
        topics = []

    valid_topics = [
        topic for topic in topics
        if isinstance(topic, str) and topic in VALID_TOPICS
    ]
    plan["topics"] = valid_topics

    primary_intent = plan.get("primary_intent")
    if not isinstance(primary_intent, str) or primary_intent not in VALID_TOPICS:
        plan["primary_intent"] = valid_topics[0] if valid_topics else ""

    return plan


def _validate_execution_mode(plan: dict) -> dict:
    mode = plan.get("execution_mode")
    if isinstance(mode, str) and mode in VALID_EXECUTION_MODES:
        return plan

    topics = plan.get("topics", [])
    topic_count = len(topics) if isinstance(topics, list) else 0
    plan["execution_mode"] = "single" if topic_count <= 1 else "parallel"
    return plan


def _fallback_wiki_plan(query: str, reason: str = "") -> ManagerPlan:
    return {
        "topics": ["wiki"],
        "execution_mode": "single",
        "primary_intent": "wiki",
        "dependencies": [],
        "location": "",
        "news_query": "",
        "wiki_topic": query,
        "reason": reason or "Manager returned invalid JSON; fallback to wiki single.",
    }


def _heuristic_fallback_plan(query: str, reason: str = "") -> ManagerPlan:
    topics = _heuristic_topics(query)
    if not topics:
        return _fallback_wiki_plan(query, reason)

    mode: ExecutionMode = "single" if len(topics) == 1 else "parallel"
    primary_intent = topics[0]
    return {
        "topics": topics,
        "execution_mode": mode,
        "primary_intent": primary_intent,
        "dependencies": [],
        "location": _extract_weather_location(query) if "weather" in topics else "",
        "news_query": query if "news" in topics else "",
        "wiki_topic": query if "wiki" in topics else "",
        "reason": _fallback_reason(reason, "Manager returned invalid JSON; used keyword fallback."),
    }


def _heuristic_topics(query: str) -> list[AgentTopic]:
    normalized = _normalize_text(query)
    topics: list[AgentTopic] = []

    if _contains_any(
        normalized,
        (
            "thoi tiet",
            "nhiet do",
            "du bao",
            "mua",
            "bao",
            "gio",
            "do am",
            "nong",
            "lanh",
        ),
    ):
        topics.append("weather")

    if _contains_any(
        normalized,
        (
            "tin",
            "tin tuc",
            "moi nhat",
            "cap nhat",
            "breaking",
            "su kien",
            "thi truong",
        ),
    ) and "thoi tiet" not in normalized:
        topics.append("news")

    if _contains_any(
        normalized,
        (
            "la ai",
            "la gi",
            "dinh nghia",
            "tieu su",
            "lich su",
            "khai niem",
            "wiki",
            "wikipedia",
        ),
    ):
        topics.append("wiki")

    if not topics and normalized:
        topics.append("wiki")

    return topics


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower()).replace("đ", "d")
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _extract_weather_location(query: str) -> str:
    normalized = _normalize_text(query)
    match = re.search(
        r"(?:thoi tiet|nhiet do|du bao)\s+(.+?)(?:\s+hom nay|\s+ngay mai|\s+the nao|\?|$)",
        normalized,
    )
    if not match:
        return ""
    return query[match.start(1):match.end(1)].strip(" ,.;:")


def _fallback_reason(reason: str, fallback_reason: str) -> str:
    return f"{fallback_reason} Parse error: {reason}" if reason else fallback_reason


def _normalize_plan(plan: dict, query: str) -> dict:
    topics = plan.get("topics", [])
    if not isinstance(topics, list) or not topics:
        return _heuristic_fallback_plan(query, "Manager returned no valid topics.")

    dependencies = plan.get("dependencies", [])
    if not isinstance(dependencies, list):
        dependencies = []

    normalized_dependencies = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        from_topic = dependency.get("from_topic", dependency.get("from"))
        to_topic = dependency.get("to_topic", dependency.get("to"))
        if from_topic not in VALID_TOPICS or to_topic not in VALID_TOPICS:
            continue
        normalized_dependencies.append(
            {
                "from_topic": from_topic,
                "to_topic": to_topic,
                "reason": str(dependency.get("reason", "")),
            }
        )

    primary_intent = plan.get("primary_intent")
    if primary_intent not in topics:
        primary_intent = topics[0]

    return {
        "topics": topics,
        "execution_mode": plan["execution_mode"],
        "primary_intent": primary_intent,
        "dependencies": normalized_dependencies,
        "location": _string_field(plan, "location"),
        "news_query": _string_field(plan, "news_query"),
        "wiki_topic": _string_field(plan, "wiki_topic"),
        "reason": _string_field(plan, "reason"),
    }


def _string_field(plan: dict, key: str) -> str:
    value = plan.get(key, "")
    return value if isinstance(value, str) else ""
