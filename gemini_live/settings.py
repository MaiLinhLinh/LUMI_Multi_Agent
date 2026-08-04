"""Configuration owned by the independent Gemini Live application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


APP_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    gemini_live_api_key: str
    gemini_live_model: str
    gemini_live_voice: str
    redis_url: str
    weather_redis_prefix: str
    weather_snapshot_max_age_seconds: int
    weather_snapshot_ttl_seconds: int
    weather_session_snapshot_ttl_seconds: int
    request_timeout_seconds: float


def load_settings() -> Settings:
    """Load this application's own `.env`, without reading code_toolcall config."""
    load_dotenv(APP_ROOT / ".env", override=False)

    def integer(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemma-4-26b-a4b-it").strip(),
        gemini_live_api_key=os.getenv("GEMINI_LIVE_API_KEY", "").strip(),
        gemini_live_model=os.getenv("GEMINI_LIVE_SPEECH_MODEL", os.getenv(
            "GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview"
        )).strip(),
        gemini_live_voice=os.getenv("GEMINI_LIVE_VOICE", "kore").strip() or "kore",
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0").strip(),
        weather_redis_prefix=os.getenv("WEATHER_REDIS_PREFIX", "weather").strip() or "weather",
        weather_snapshot_max_age_seconds=integer("WEATHER_SNAPSHOT_MAX_AGE_SECONDS", 14_400),
        weather_snapshot_ttl_seconds=integer("WEATHER_SNAPSHOT_TTL_SECONDS", 14_400),
        weather_session_snapshot_ttl_seconds=integer("WEATHER_SESSION_SNAPSHOT_TTL_SECONDS", 600),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60")),
    )
