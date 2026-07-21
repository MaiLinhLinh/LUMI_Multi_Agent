"""High-confidence routing and unified semantic analysis for user input."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from typing import Any

from rag_manager.visualization.llm_output import (
    LlmOutputError,
    validate_semantic_router_output,
)
from rag_manager.visualization.paths import resolve_asset_path


def is_high_confidence_domain_query(query: object) -> bool:
    """Return True only for unambiguous domain questions.

    This intentionally errs on the side of sending uncertain input to the
    semantic LLM instead of incorrectly bypassing visualization handling.
    """

    if not isinstance(query, str):
        return False
    normalized = _normalize_text(query)
    if not normalized or _contains_visualization_signal(normalized):
        return False

    # A bare domain token is deliberately insufficient. The bypass requires
    # a recognizable domain question/request pattern with some subject or
    # context after the domain phrase.
    domain_patterns = (
        r"^(?:thoi tiet|du bao thoi tiet|nhiet do tai)\s+\S+",
        r"^(?:tin tuc|tin moi nhat|news|wikipedia)\s+\S+",
        r"^(?:cho toi biet|cho minh biet)\s+(?:ve )?thoi tiet\b.+",
        r"\b(?:hom nay|ngay mai)\s+thoi tiet\b",
        r"\b(?:ai la|la ai|dinh nghia)\b.+",
        r"\bwhat(?:'s| is) the weather\b.+",
        r"\bweather\s+(?:in|today|tomorrow|forecast)\b.+",
        r"\b(?:latest news|news about|who is)\b.+",
        r"\b(?:weather|news|forecast|wikipedia)\b.+",
        r"^(?:bat|mo|phat|nghe|xem)\s+(?:nhac|bai|mv)\b.+",
        r"^(?:cho toi|cho minh)\s+(?:nghe|xem|bat|mo)\s+(?:nhac|bai|mv)\b.+",
        r"^(?:play|listen to|open|watch)\s+(?:music|song|track|mv)\b.+",
    )
    return any(re.search(pattern, normalized) for pattern in domain_patterns)


def is_explicit_visualization_query(query: object) -> bool:
    """Return whether the current query explicitly changes/selects presentation.

    Domain follow-ups must not become visualization commands merely because an
    active domain template is present in the router context.
    """

    if not isinstance(query, str):
        return False
    normalized = _normalize_text(query)
    return bool(normalized) and _contains_visualization_signal(normalized)


def analyze_input(
    client: Any | None,
    *,
    query: str,
    history: list[dict[str, str]] | None = None,
    previous_template_state: dict[str, Any] | None = None,
    active_template_id: str | None = None,
    available_templates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return one validated high-level route without domain/template details."""

    has_template_context = bool(
        previous_template_state or active_template_id or available_templates
    )
    if client is None or not hasattr(client, "chat_json"):
        return _fallback_semantic_result(
            query,
            has_template_context=has_template_context,
        )

    prompt_path = resolve_asset_path("prompts", "semantic_router.txt")
    context = {
        "recent_history": _recent_router_history(query, history or []),
        "template_request_pending": bool(previous_template_state),
        "has_active_template": bool(active_template_id),
    }
    prompt = prompt_path.read_text(encoding="utf-8")
    _debug_print(f"[SemanticRouter] START prompt_chars={len(prompt)}")
    try:
        raw = client.chat_json(
            prompt,
            json.dumps({"query": query, "context": context}, ensure_ascii=False),
        )
    except Exception as exc:
        _debug_print(
            f"[SemanticRouter] ERROR type={type(exc).__name__} detail={exc}"
        )
        raise
    _debug_print(f"[SemanticRouter] RAW_RESULT {raw}")
    if isinstance(raw, dict) and raw.get("error"):
        return _fallback_semantic_result(
            query,
            has_template_context=has_template_context,
        )
    try:
        return validate_semantic_router_output(raw)
    except (LlmOutputError, TypeError, AttributeError):
        return _fallback_semantic_result(
            query,
            has_template_context=has_template_context,
        )


def _fallback_semantic_result(
    query: str,
    *,
    has_template_context: bool = False,
) -> dict[str, Any]:
    normalized = _normalize_text(query)
    if _contains_visualization_signal(normalized) or (
        has_template_context
        and bool(re.fullmatch(r"(?:mau[ ]+)?[1-9][0-9]*", normalized))
    ):
        return {"route": "template", "domain_request": None}
    if _is_social_query(normalized):
        return {"route": "social", "domain_request": None}
    return {"route": "domain", "domain_request": query}


def _recent_router_history(
    query: str,
    history: list[dict[str, Any]],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    if (
        messages
        and messages[-1]["role"] == "user"
        and messages[-1]["content"] == query.strip()
    ):
        messages.pop()
    return messages[-4:]


def _is_social_query(normalized: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:xin chao|chao ban|hello|hi|hey|cam on|cam on ban|"
            r"thank you|thanks|tam biet|hen gap lai)[!?.]*",
            normalized,
        )
    )


def _contains_visualization_signal(normalized: str) -> bool:
    padded = f" {normalized} "
    visualization_markers = (
        "template",
        "mau hien tai",
        "giao dien",
        "bo cuc",
        "layout",
        "dashboard",
        "theme",
        "style",
        "component",
        "render",
        "mau sac",
        "doi nen",
        "hien thi",
        "display",
    )
    # These verbs indicate an operation on presentation, even when the
    # request also mentions a domain such as weather or news.
    presentation_verbs = (
        "tao",
        "thiet ke",
        "doi",
        "chinh sua",
        "tuy chinh",
        "dung",
        "chon",
        "create",
        "design",
        "change",
        "customize",
        "select",
    )
    return any(marker in normalized for marker in visualization_markers) or any(
        f" {verb} " in padded for verb in presentation_verbs
    )


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return " ".join(without_marks.split())


def _debug_print(message: str) -> None:
    """Print diagnostics while preserving Vietnamese characters."""

    text = str(message)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            raise
        buffer.write((text + "\n").encode("utf-8"))
        buffer.flush()
