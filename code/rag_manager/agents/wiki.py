"""Wiki Agent implementation."""

from __future__ import annotations

import json
from time import perf_counter
from typing import TYPE_CHECKING

from rag_manager.cache import MemoryCache, wiki_cache_key
from rag_manager.config import Settings, load_settings
from rag_manager.llm.prompts import WIKI_SYSTEM_PROMPT
from rag_manager.services.wiki_api import fetch_wiki_summary
from rag_manager.state import AgentState

if TYPE_CHECKING:
    from rag_manager.llm.gemini_client import GeminiClient as GeminiClientType


def run_wiki_agent(
    state: AgentState,
    *,
    cache: MemoryCache | None = None,
    settings: Settings | None = None,
    client: "GeminiClientType | None" = None,
) -> AgentState:
    """Run the wiki agent and return state updates."""
    started_at = perf_counter()
    cache = cache or MemoryCache()
    settings = settings or load_settings()
    topic = build_wiki_topic(state)
    cache_key = wiki_cache_key(topic)

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        answer = format_wiki_answer(
            _get_client(client, settings),
            state.get("query", ""),
            cached_data,
        )
        return {
            "wiki_data": cached_data,
            "wiki_answer": answer,
            "cache_stats": {"wiki": cache.stats()},
            "timings": {"wiki": _elapsed_since(started_at)},
        }

    response = fetch_wiki_summary(
        topic,
        timeout_seconds=settings.request_timeout_seconds,
    )
    if not response.get("ok"):
        wiki_data = {"topic": topic, "error": response["error"]}
        if _is_wiki_topic_not_found_error(response):
            answer = _wiki_topic_not_found_answer(topic)
        else:
            answer = format_wiki_answer(
                _get_client(client, settings),
                state.get("query", ""),
                wiki_data,
            )
        return {
            "wiki_data": wiki_data,
            "wiki_answer": answer,
            "cache_stats": {"wiki": cache.stats()},
            "timings": {"wiki": _elapsed_since(started_at)},
        }

    wiki_data = response["data"]
    cache.set(
        cache_key,
        wiki_data,
        ttl_seconds=settings.wiki_cache_ttl_seconds,
    )
    answer = format_wiki_answer(
        _get_client(client, settings),
        state.get("query", ""),
        wiki_data,
    )
    return {
        "wiki_data": wiki_data,
        "wiki_answer": answer,
        "cache_stats": {"wiki": cache.stats()},
        "timings": {"wiki": _elapsed_since(started_at)},
    }


def build_wiki_topic(state: AgentState) -> str:
    intent = state.get("intent", {})
    wiki_topic = intent.get("wiki_topic", "") if isinstance(intent, dict) else ""
    if isinstance(wiki_topic, str) and wiki_topic.strip():
        return wiki_topic.strip()

    context = state.get("context", {})
    context_topic = context.get("wiki_topic", "") if isinstance(context, dict) else ""
    if isinstance(context_topic, str) and context_topic.strip():
        return context_topic.strip()

    query = state.get("query", "")
    return query.strip() if isinstance(query, str) else ""


def format_wiki_answer(
    client: "GeminiClientType",
    query: str,
    wiki_data: dict,
) -> str:
    user_message = "\n".join(
        [
            f"User query: {query}",
            "Wiki JSON:",
            json.dumps(wiki_data, ensure_ascii=False, sort_keys=True),
        ]
    )
    return client.chat_text(
        WIKI_SYSTEM_PROMPT,
        user_message,
        temperature=0.2,
    )


def _create_gemini_client(settings: Settings) -> "GeminiClientType":
    from rag_manager.llm.gemini_client import GeminiClient

    return GeminiClient(settings)


def _get_client(
    client: "GeminiClientType | None",
    settings: Settings,
) -> "GeminiClientType":
    return client or _create_gemini_client(settings)


def _is_wiki_topic_not_found_error(response: dict) -> bool:
    error = response.get("error", {})
    if not isinstance(error, dict):
        return False
    message = error.get("message", "")
    return isinstance(message, str) and "not found" in message.lower()


def _wiki_topic_not_found_answer(topic: str) -> str:
    return (
        f"Mình không tìm thấy thông tin phù hợp trên Wikipedia cho chủ đề '{topic}'. "
        "Bạn có thể thử viết lại tên riêng, thêm ngữ cảnh hoặc dùng một từ khóa cụ thể hơn."
    )


def _elapsed_since(started_at: float) -> float:
    return perf_counter() - started_at
