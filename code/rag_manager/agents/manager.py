"""Manager Agent routing and weather-presence classification."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import TYPE_CHECKING, Any, cast

from rag_manager.llm.prompts import MANAGER_SYSTEM_PROMPT
from rag_manager.state import AgentTopic, ExecutionMode, ManagerPlan, WeatherRequirements

if TYPE_CHECKING:
    from rag_manager.llm.gemini_client import GeminiClient

VALID_TOPICS: set[AgentTopic] = {"weather", "news", "wiki"}
VALID_EXECUTION_MODES: set[ExecutionMode] = {"single", "parallel", "sequential"}
VALID_WEATHER_REQUIREMENT_STATUSES = {
    "not_applicable",
    "needs_clarification",
    "ready_for_weather",
}


def classify_intent(
    client: "GeminiClient",
    query: str,
    history: list[dict[str, str]] | None = None,
) -> ManagerPlan:
    """Classify intent using the latest query and relevant conversation history."""

    conversation = _conversation_history(query, history)
    user_message = json.dumps(
        {"query": query, "history": conversation},
        ensure_ascii=False,
    )
    raw_plan = client.chat_json(MANAGER_SYSTEM_PROMPT, user_message)
    if raw_plan.get("error"):
        return _heuristic_fallback_plan(
            query,
            conversation,
            str(raw_plan.get("message", "")),
        )

    plan = _filter_valid_topics(raw_plan)
    plan = _validate_execution_mode(plan)
    plan = _normalize_plan(plan, query, conversation)
    return cast(ManagerPlan, plan)


def _conversation_history(
    query: str,
    history: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    conversation: list[dict[str, str]] = []
    for message in history or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        if content.strip():
            conversation.append({"role": role, "content": content})
    if not (
        conversation
        and conversation[-1]["role"] == "user"
        and conversation[-1]["content"] == query
    ):
        conversation.append({"role": "user", "content": query})
    return conversation


def _filter_valid_topics(plan: dict[str, Any]) -> dict[str, Any]:
    topics = plan.get("topics", [])
    if not isinstance(topics, list):
        topics = []

    valid_topics = [
        topic
        for topic in topics
        if isinstance(topic, str) and topic in VALID_TOPICS
    ]
    plan["topics"] = list(dict.fromkeys(valid_topics))

    primary_intent = plan.get("primary_intent")
    if not isinstance(primary_intent, str) or primary_intent not in VALID_TOPICS:
        plan["primary_intent"] = valid_topics[0] if valid_topics else ""

    return plan


def _validate_execution_mode(plan: dict[str, Any]) -> dict[str, Any]:
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
        "news_query": "",
        "wiki_topic": query,
        "reason": reason or "Manager returned invalid JSON; fallback to wiki single.",
        "weather_requirements": _not_applicable_weather_requirements(),
    }


def _heuristic_fallback_plan(
    query: str,
    history: list[dict[str, str]],
    reason: str = "",
) -> ManagerPlan:
    topics = _heuristic_topics(query, history)
    if not topics:
        return _fallback_wiki_plan(query, reason)

    mode: ExecutionMode = "single" if len(topics) == 1 else "parallel"
    primary_intent = topics[0]
    return {
        "topics": topics,
        "execution_mode": mode,
        "primary_intent": primary_intent,
        "dependencies": [],
        "news_query": query if "news" in topics else "",
        "wiki_topic": query if "wiki" in topics else "",
        "reason": _fallback_reason(
            reason,
            "Manager returned invalid JSON; used keyword fallback.",
        ),
        "weather_requirements": _heuristic_weather_requirements(
            topics,
            query,
            history,
        ),
    }


def _heuristic_topics(
    query: str,
    history: list[dict[str, str]] | None = None,
) -> list[AgentTopic]:
    direct_topics = _topics_in_text(query)
    if direct_topics:
        return direct_topics

    if _has_recent_weather_context(history or []):
        return ["weather"]
    return ["wiki"] if query.strip() else []


def _topics_in_text(value: str) -> list[AgentTopic]:
    normalized = _normalize_text(value)
    topics: list[AgentTopic] = []

    if _contains_any(
        normalized,
        (
            "thoi tiet",
            "nhiet do",
            "du bao thoi tiet",
            "do am",
            "weather",
            "forecast",
        ),
    ):
        topics.append("weather")

    if _contains_any(
        normalized,
        (
            "tin tuc",
            "tin moi",
            "moi nhat",
            "cap nhat",
            "breaking",
            "news",
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

    return topics


def _has_recent_weather_context(history: list[dict[str, str]]) -> bool:
    for message in reversed(history[-6:]):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and "weather" in _topics_in_text(content):
            return True
    return False


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFKD",
        value.lower().replace("đ", "d"),
    )
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    )


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _extract_weather_location(query: str) -> str:
    normalized = " ".join(_normalize_text(query).split())
    match = re.search(
        r"(?:thoi tiet|nhiet do|du bao(?: thoi tiet)?)\s+(?:tai\s+|o\s+)?(.+)",
        normalized,
    )
    if not match:
        return ""
    candidate = re.split(
        r"\b(?:hien tai|bay gio|hom nay|toi nay|ngay mai|"
        r"\d+\s+ngay\s+toi|thu\s+(?:[2-7]|hai|ba|tu|nam|sau|bay)|"
        r"ngay\s+\d{1,2}[/-]\d{1,2}|the nao|ra sao)\b",
        match.group(1),
        maxsplit=1,
    )[0]
    candidate = candidate.strip(" ,.;:?")
    if not candidate or _has_time_expression(candidate):
        return ""
    return candidate


def _fallback_reason(reason: str, fallback_reason: str) -> str:
    return f"{fallback_reason} Parse error: {reason}" if reason else fallback_reason


def _normalize_plan(
    plan: dict[str, Any],
    query: str,
    history: list[dict[str, str]],
) -> dict[str, Any]:
    topics = plan.get("topics", [])
    if not isinstance(topics, list) or not topics:
        return _heuristic_fallback_plan(
            query,
            history,
            "Manager returned no valid topics.",
        )

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
        "news_query": _string_field(plan, "news_query"),
        "wiki_topic": _string_field(plan, "wiki_topic"),
        "reason": _string_field(plan, "reason"),
        "weather_requirements": _normalize_weather_requirements(
            plan,
            topics,
            query,
            history,
        ),
    }


def _normalize_weather_requirements(
    plan: dict[str, Any],
    topics: list[str],
    query: str,
    history: list[dict[str, str]],
) -> WeatherRequirements:
    if "weather" not in topics:
        return _not_applicable_weather_requirements()

    fallback = _heuristic_weather_requirements(topics, query, history)
    raw = plan.get("weather_requirements")
    if not isinstance(raw, dict):
        return fallback

    raw_location = raw.get("has_location_expression")
    raw_time = raw.get("has_time_expression")
    has_location = raw_location if isinstance(raw_location, bool) else fallback[
        "has_location_expression"
    ]
    has_time = raw_time if isinstance(raw_time, bool) else fallback[
        "has_time_expression"
    ]
    missing_fields = [
        field
        for field, present in (("location", has_location), ("time", has_time))
        if not present
    ]
    if not missing_fields:
        return {
            "status": "ready_for_weather",
            "has_location_expression": True,
            "has_time_expression": True,
            "missing_fields": [],
            "clarification_question": None,
        }

    question = raw.get("clarification_question")
    if not isinstance(question, str) or not question.strip():
        question = _weather_clarification_question(missing_fields)
    return {
        "status": "needs_clarification",
        "has_location_expression": has_location,
        "has_time_expression": has_time,
        "missing_fields": cast(list, missing_fields),
        "clarification_question": question.strip(),
    }


def _heuristic_weather_requirements(
    topics: list[str],
    query: str,
    history: list[dict[str, str]],
) -> WeatherRequirements:
    if "weather" not in topics:
        return _not_applicable_weather_requirements()

    relevant_user_text = _relevant_weather_user_text(query, history)
    has_location = any(_extract_weather_location(text) for text in relevant_user_text)
    has_time = any(_has_time_expression(text) for text in relevant_user_text)
    if not has_location and _latest_assistant_asks_for(history, "location"):
        normalized_query = _normalize_text(query).strip()
        has_location = bool(normalized_query) and not _has_time_expression(query)

    missing_fields = [
        field
        for field, present in (("location", has_location), ("time", has_time))
        if not present
    ]
    if not missing_fields:
        return {
            "status": "ready_for_weather",
            "has_location_expression": True,
            "has_time_expression": True,
            "missing_fields": [],
            "clarification_question": None,
        }
    return {
        "status": "needs_clarification",
        "has_location_expression": has_location,
        "has_time_expression": has_time,
        "missing_fields": cast(list, missing_fields),
        "clarification_question": _weather_clarification_question(missing_fields),
    }


def _relevant_weather_user_text(
    query: str,
    history: list[dict[str, str]],
) -> list[str]:
    user_texts = [
        message["content"]
        for message in history
        if isinstance(message, dict)
        and message.get("role") == "user"
        and isinstance(message.get("content"), str)
    ]
    if not user_texts or user_texts[-1] != query:
        user_texts.append(query)

    direct_latest = _topics_in_text(query)
    if direct_latest and "weather" not in direct_latest:
        return [query]

    start_index = 0
    for index in range(len(user_texts) - 1, -1, -1):
        if "weather" in _topics_in_text(user_texts[index]):
            start_index = index
            break
    return user_texts[start_index:]


def _latest_assistant_asks_for(
    history: list[dict[str, str]],
    field: str,
) -> bool:
    for message in reversed(history[:-1] if history else []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            return False
        normalized = _normalize_text(content)
        if field == "location":
            return _contains_any(normalized, ("o dau", "dia diem", "tinh hoac thanh pho"))
        return _contains_any(normalized, ("thoi diem nao", "ngay nao", "khoang thoi gian"))
    return False


def _has_time_expression(value: str) -> bool:
    normalized = " ".join(_normalize_text(value).split())
    return bool(
        re.search(
            r"\b(?:hien tai|bay gio|hom nay|toi nay|ngay mai|ngay kia|"
            r"\d+\s+ngay\s+toi|thu\s+(?:[2-7]|hai|ba|tu|nam|sau|bay)|"
            r"chu nhat|tuan nay|tuan toi)\b",
            normalized,
        )
        or re.search(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?\b", normalized)
        or re.search(r"\b\d{4}-\d{2}-\d{2}\b", normalized)
    )


def _weather_clarification_question(missing_fields: list[str]) -> str:
    if missing_fields == ["location"]:
        return "Bạn muốn xem thời tiết ở tỉnh hoặc thành phố nào?"
    if missing_fields == ["time"]:
        return "Bạn muốn xem thời tiết vào thời điểm nào?"
    return "Bạn muốn xem thời tiết ở đâu và vào thời điểm nào?"


def _not_applicable_weather_requirements() -> WeatherRequirements:
    return {
        "status": "not_applicable",
        "has_location_expression": False,
        "has_time_expression": False,
        "missing_fields": [],
        "clarification_question": None,
    }


def _string_field(plan: dict[str, Any], key: str) -> str:
    value = plan.get(key, "")
    return value if isinstance(value, str) else ""
