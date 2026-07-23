from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    gemini_live_api_key: str = ""
    gemini_live_model: str = "gemini-3.1-flash-live-preview"
    gemini_live_voice: str = ""
    redis_url: str = "redis://localhost:6379/0"
    weather_redis_prefix: str = "weather"
    weather_snapshot_max_age_seconds: int = 14400
    weather_snapshot_ttl_seconds: int = 14400
    weather_refresh_interval_seconds: int = 10800
    weather_locations_file: str = str(ROOT / "rag_manager" / "services" / "weather_locations_vn.json")
    openweather_api_key: str = ""
    request_timeout_seconds: float = 60.0
    music_chroma_path: str = str(ROOT / "data" / "chroma_music")
    music_chroma_collection: str = "music_tracks_v1"
    ollama_base_url: str = "http://localhost:11434"
    music_embedding_model: str = "bge-m3"
    music_embedding_dimensions: int = 1024
    music_embedding_timeout_seconds: float = 120.0
    music_embedding_batch_size: int = 16
    music_catalog_file: str = str(ROOT / "data" / "music_catalog.json")
    youtube_api_key: str = ""

def load_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)
    def number(name: str, default: int) -> int:
        try: return int(os.getenv(name, str(default)))
        except ValueError: return default
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemma-4-26b-a4b-it").strip(),
        gemini_live_api_key=os.getenv("GEMINI_LIVE_API_KEY", "").strip(),
        gemini_live_model=os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview").strip(),
        gemini_live_voice=os.getenv("GEMINI_LIVE_VOICE", "").strip(),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        weather_redis_prefix=os.getenv("WEATHER_REDIS_PREFIX", "weather"),
        weather_snapshot_max_age_seconds=number("WEATHER_SNAPSHOT_MAX_AGE_SECONDS", 14400),
        weather_snapshot_ttl_seconds=number("WEATHER_SNAPSHOT_TTL_SECONDS", 14400),
        weather_refresh_interval_seconds=number("WEATHER_REFRESH_INTERVAL_SECONDS", 10800),
        weather_locations_file=os.getenv("WEATHER_LOCATIONS_FILE", str(ROOT / "rag_manager" / "services" / "weather_locations_vn.json")),
        openweather_api_key=os.getenv("OPENWEATHER_API_KEY", "").strip(),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60")),
        music_chroma_path=os.getenv("MUSIC_CHROMA_PATH", str(ROOT / "data" / "chroma_music")),
        music_chroma_collection=os.getenv("MUSIC_CHROMA_COLLECTION", "music_tracks_v1"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        music_embedding_model=os.getenv("MUSIC_EMBEDDING_MODEL", "bge-m3"),
        music_embedding_dimensions=number("MUSIC_EMBEDDING_DIMENSIONS", 1024),
        music_embedding_timeout_seconds=float(os.getenv("MUSIC_EMBEDDING_TIMEOUT_SECONDS", "120")),
        music_embedding_batch_size=number("MUSIC_EMBEDDING_BATCH_SIZE", 16),
        music_catalog_file=os.getenv("MUSIC_CATALOG_FILE", str(ROOT / "data" / "music_catalog.json")),
        youtube_api_key=os.getenv("YOUTUBE_API_KEY", "").strip(),
    )
