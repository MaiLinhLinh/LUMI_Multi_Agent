"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is installed in runtime env
    load_dotenv = None


DEFAULT_GEMINI_BASE_URL = ""
DEFAULT_GEMINI_MODEL = "gemma-4-26b-a4b-it"


def _load_dotenv_if_available() -> None:
    if load_dotenv is not None:
        load_dotenv()


def _get_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _get_optional_int_env(name: str) -> int | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def _get_bool_env(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_base_url: str
    gemini_model: str
    openweather_api_key: str
    gnews_api_key: str
    weather_cache_ttl_seconds: int
    news_cache_ttl_seconds: int
    wiki_cache_ttl_seconds: int | None
    request_timeout_seconds: int
    debug_routing: bool
    redis_url: str = "redis://localhost:6379/0"
    weather_redis_prefix: str = "weather"
    weather_snapshot_ttl_seconds: int = 14400
    weather_snapshot_max_age_seconds: int = 14400
    weather_refresh_interval_seconds: int = 10800
    weather_locations_file: str = ""
    music_chroma_path: str = "data/chroma_music"
    music_chroma_collection: str = "music_tracks_v1"
    music_catalog_file: str = ""
    ollama_base_url: str = "http://localhost:11434"
    music_embedding_model: str = "bge-m3"
    music_embedding_dimensions: int = 1024
    music_embedding_batch_size: int = 16
    music_embedding_timeout_seconds: int = 120
    youtube_api_key: str = ""

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key)


def load_settings() -> Settings:
    _load_dotenv_if_available()
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_base_url=os.getenv("GEMINI_BASE_URL", DEFAULT_GEMINI_BASE_URL).strip()
        or DEFAULT_GEMINI_BASE_URL,
        gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        or DEFAULT_GEMINI_MODEL,
        openweather_api_key=os.getenv("OPENWEATHER_API_KEY", "").strip(),
        gnews_api_key=os.getenv("GNEWS_API_KEY", "").strip(),
        weather_cache_ttl_seconds=_get_int_env("WEATHER_CACHE_TTL_SECONDS", 3600),
        news_cache_ttl_seconds=_get_int_env("NEWS_CACHE_TTL_SECONDS", 900),
        wiki_cache_ttl_seconds=_get_optional_int_env("WIKI_CACHE_TTL_SECONDS"),
        request_timeout_seconds=_get_int_env("REQUEST_TIMEOUT_SECONDS", 8),
        debug_routing=_get_bool_env("DEBUG_ROUTING", False),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
        or "redis://localhost:6379/0",
        weather_redis_prefix=os.getenv("WEATHER_REDIS_PREFIX", "weather").strip()
        or "weather",
        weather_snapshot_ttl_seconds=_get_int_env(
            "WEATHER_SNAPSHOT_TTL_SECONDS", 14400
        ),
        weather_snapshot_max_age_seconds=_get_int_env(
            "WEATHER_SNAPSHOT_MAX_AGE_SECONDS", 14400
        ),
        weather_refresh_interval_seconds=_get_int_env(
            "WEATHER_REFRESH_INTERVAL_SECONDS", 10800
        ),
        weather_locations_file=os.getenv("WEATHER_LOCATIONS_FILE", "").strip(),
        music_chroma_path=os.getenv(
            "MUSIC_CHROMA_PATH", "data/chroma_music"
        ).strip()
        or "data/chroma_music",
        music_chroma_collection=os.getenv(
            "MUSIC_CHROMA_COLLECTION", "music_tracks_v1"
        ).strip()
        or "music_tracks_v1",
        music_catalog_file=os.getenv("MUSIC_CATALOG_FILE", "").strip(),
        ollama_base_url=os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        ).strip()
        or "http://localhost:11434",
        music_embedding_model=os.getenv(
            "MUSIC_EMBEDDING_MODEL", "bge-m3"
        ).strip()
        or "bge-m3",
        music_embedding_dimensions=_get_int_env(
            "MUSIC_EMBEDDING_DIMENSIONS", 1024
        ),
        music_embedding_batch_size=_get_int_env(
            "MUSIC_EMBEDDING_BATCH_SIZE", 16
        ),
        music_embedding_timeout_seconds=_get_int_env(
            "MUSIC_EMBEDDING_TIMEOUT_SECONDS", 120
        ),
        youtube_api_key=os.getenv("YOUTUBE_API_KEY", "").strip(),
    )
