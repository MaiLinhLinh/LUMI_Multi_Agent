"""Configuration owned only by ``gemini_live_2``.

Values are read lazily by the running process from this project's own .env.
No secret is logged or copied by source code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    gemini_live_api_key: str
    gemini_live_model: str
    gemini_live_voice: str
    live_turn_timeout_seconds: float
    live_idle_timeout_seconds: float
    live_reconnect_grace_seconds: float
    presentation_animation_delay_ms: int
    plan_agent_api_key: str
    plan_agent_model: str
    planner_provider: str = "gemini"
    cerebras_api_key: str = ""
    cerebras_planner_model: str = "gpt-oss-120b"


def load_settings() -> Settings:
    load_dotenv(APP_ROOT / ".env", override=False)

    def integer(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    return Settings(
        gemini_live_api_key=os.getenv("GEMINI_LIVE_API_KEY", "").strip(),
        gemini_live_model=(os.getenv("GEMINI_LIVE_SPEECH_MODEL") or os.getenv("GEMINI_LIVE_MODEL") or "gemini-3.1-flash-live-preview").strip(),
        gemini_live_voice=os.getenv("GEMINI_LIVE_VOICE", "kore").strip() or "kore",
        live_turn_timeout_seconds=float(os.getenv("LIVE_TURN_TIMEOUT_SECONDS", "45")),
        live_idle_timeout_seconds=float(os.getenv("LIVE_IDLE_TIMEOUT_SECONDS", "900")),
        live_reconnect_grace_seconds=float(os.getenv("LIVE_RECONNECT_GRACE_SECONDS", "30")),
        presentation_animation_delay_ms=max(0, integer("PRESENTATION_ANIMATION_DELAY_MS", 300)),
        plan_agent_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        plan_agent_model=os.getenv("GEMINI_MODEL", "gemma-4-26b-a4b-it").strip() or "gemma-4-26b-a4b-it",
        planner_provider=os.getenv("PLANNER_PROVIDER", "gemini").strip().lower() or "gemini",
        cerebras_api_key=os.getenv("CEREBRAS_API_KEY", "").strip(),
        cerebras_planner_model=(os.getenv("CEREBRAS_PLANNER_MODEL", "gpt-oss-120b").strip() or "gpt-oss-120b"),
    )
