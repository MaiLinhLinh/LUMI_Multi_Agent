"""External data service clients."""

from rag_manager.services.http_client import ServiceError, ServiceResponse, get_json
from rag_manager.services.news_api import fetch_news
from rag_manager.services.weather_api import fetch_weather
from rag_manager.services.wiki_api import fetch_wiki_summary

__all__ = [
    "ServiceError",
    "ServiceResponse",
    "fetch_news",
    "fetch_weather",
    "fetch_wiki_summary",
    "get_json",
]
