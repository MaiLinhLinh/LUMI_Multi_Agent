"""GNews service client."""

from __future__ import annotations

from typing import Any

from rag_manager.services.http_client import ServiceResponse, get_json


GNEWS_SEARCH_URL = "https://gnews.io/api/v4/search"
NEWS_SOURCE = "news"


def fetch_news(
    query: str,
    *,
    api_key: str,
    timeout_seconds: float = 8,
    max_results: int = 5,
) -> ServiceResponse:
    if not api_key.strip():
        return _news_error("Missing GNEWS_API_KEY.")
    if not query.strip():
        return _news_error("Missing news query.")

    response = get_json(
        GNEWS_SEARCH_URL,
        source=NEWS_SOURCE,
        params={
            "q": query,
            "apikey": api_key,
            "lang": "vi",
            "max": max_results,
        },
        timeout_seconds=timeout_seconds,
    )
    if not response.get("ok"):
        return response
    return {"ok": True, "data": compact_news_data(response["data"], max_results)}


def compact_news_data(data: dict[str, Any], max_results: int = 5) -> dict[str, Any]:
    articles = data.get("articles")
    if not isinstance(articles, list):
        articles = []

    compact_articles = []
    for article in articles[:max_results]:
        if not isinstance(article, dict):
            continue
        source = article.get("source")
        source_name = source.get("name", "") if isinstance(source, dict) else ""
        compact_articles.append(
            {
                "title": article.get("title", ""),
                "description": article.get("description", ""),
                "source": source_name,
                "published_at": article.get("publishedAt", ""),
                "url": article.get("url", ""),
            }
        )

    return {
        "total_articles": data.get("totalArticles", len(compact_articles)),
        "articles": compact_articles,
    }


def _news_error(message: str) -> ServiceResponse:
    return {
        "ok": False,
        "error": {
            "source": NEWS_SOURCE,
            "message": message,
            "status_code": None,
        },
    }
