"""News Agent implementation."""

from __future__ import annotations

import json
from time import perf_counter
from typing import TYPE_CHECKING

from rag_manager.cache import MemoryCache, news_cache_key
from rag_manager.config import Settings, load_settings
from rag_manager.llm.prompts import NEWS_SYSTEM_PROMPT
from rag_manager.services.news_api import fetch_news
from rag_manager.state import AgentState

if TYPE_CHECKING:
    from rag_manager.llm.gemini_client import GeminiClient as GeminiClientType


def run_news_agent(
    state: AgentState,
    *,
    cache: MemoryCache | None = None,
    settings: Settings | None = None,
    client: "GeminiClientType | None" = None,
) -> AgentState:
    """Run the news agent and return state updates."""
    started_at = perf_counter()
    cache = cache or MemoryCache()
    settings = settings or load_settings()
    query = build_news_query(state)
    cache_key = news_cache_key(query)

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        answer = format_news_answer(
            _get_client(client, settings),
            state.get("query", ""),
            cached_data,
        )
        return {
            "news_data": cached_data,
            "news_answer": answer,
            "cache_stats": {"news": cache.stats()},
            "timings": {"news": _elapsed_since(started_at)},
        }

    response = fetch_news(
        query,
        api_key=settings.gnews_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
    if not response.get("ok"):
        news_data = {"query": query, "error": response["error"]}
        if _is_missing_gnews_key_error(response):
            answer = _missing_gnews_key_answer()
        else:
            answer = format_news_answer(
                _get_client(client, settings),
                state.get("query", ""),
                news_data,
            )
        return {
            "news_data": news_data,
            "news_answer": answer,
            "cache_stats": {"news": cache.stats()},
            "timings": {"news": _elapsed_since(started_at)},
        }

    news_data = response["data"]
    cache.set(
        cache_key,
        news_data,
        ttl_seconds=settings.news_cache_ttl_seconds,
    )
    answer = format_news_answer(
        _get_client(client, settings),
        state.get("query", ""),
        news_data,
    )
    return {
        "news_data": news_data,
        "news_answer": answer,
        "cache_stats": {"news": cache.stats()},
        "timings": {"news": _elapsed_since(started_at)},
    }


def build_news_query(state: AgentState) -> str:
    intent = state.get("intent", {})
    news_query = intent.get("news_query", "") if isinstance(intent, dict) else ""
    if isinstance(news_query, str) and news_query.strip():
        return news_query.strip()

    context = state.get("context", {})
    context_query = context.get("news_query", "") if isinstance(context, dict) else ""
    if isinstance(context_query, str) and context_query.strip():
        return context_query.strip()

    query = state.get("query", "")
    return query.strip() if isinstance(query, str) else ""


def format_news_answer(
    client: "GeminiClientType",
    query: str,
    news_data: dict,
) -> str:
    user_message = "\n".join(
        [
            f"User query: {query}",
            "News JSON:",
            json.dumps(news_data, ensure_ascii=False, sort_keys=True),
        ]
    )
    return client.chat_text(
        NEWS_SYSTEM_PROMPT,
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


def _is_missing_gnews_key_error(response: dict) -> bool:
    error = response.get("error", {})
    if not isinstance(error, dict):
        return False
    message = error.get("message", "")
    return isinstance(message, str) and "GNEWS_API_KEY" in message


def _missing_gnews_key_answer() -> str:
    return (
        "Mình chưa thể tra cứu tin tức vì thiếu GNEWS_API_KEY. "
        "Hãy cấu hình GNEWS_API_KEY trong file .env hoặc biến môi trường rồi chạy lại."
    )


def _elapsed_since(started_at: float) -> float:
    return perf_counter() - started_at
