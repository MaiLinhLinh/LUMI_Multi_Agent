"""Smoke tests proving Weather is a self-contained registered Live domain."""

from __future__ import annotations

import unittest

from gemini_live.domains.registry import LiveDomainRegistry
from gemini_live.domains.weather import WeatherLiveDomain
from gemini_live.settings import Settings


def _settings() -> Settings:
    return Settings(
        gemini_api_key="test-key",
        gemini_model="test-model",
        gemini_live_api_key="test-live-key",
        gemini_live_model="test-live-model",
        gemini_live_voice="kore",
        redis_url="redis://localhost:6379/0",
        weather_redis_prefix="weather",
        weather_snapshot_max_age_seconds=14_400,
        weather_snapshot_ttl_seconds=14_400,
        weather_session_snapshot_ttl_seconds=600,
        request_timeout_seconds=1.0,
        live_turn_timeout_seconds=45.0,
        live_idle_timeout_seconds=900.0,
        live_reconnect_grace_seconds=30.0,
    )


class WeatherDomainRegistrationTests(unittest.TestCase):
    def test_weather_registers_its_own_tool_and_guidance(self) -> None:
        registry = LiveDomainRegistry()
        weather = WeatherLiveDomain(_settings())
        registry.register(weather)

        self.assertIs(registry.domain_for_tool("get_weather"), weather)
        self.assertEqual(registry.tool_declarations()[0]["name"], "get_weather")
        self.assertTrue(registry.prompt_guidance())


if __name__ == "__main__":
    unittest.main()
