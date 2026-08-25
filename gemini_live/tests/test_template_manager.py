"""Tests for shared TemplateManager selection; no real model call is made."""

from __future__ import annotations

import asyncio
import unittest

from gemini_live.presentation import PresentationRequest
from gemini_live.settings import Settings
from gemini_live.template_engine.template_llm import TemplateDecision
from gemini_live.template_engine.template_manager import TemplateManager, TemplateManagerError


class _DecisionService:
    def __init__(self, decision: TemplateDecision) -> None:
        self.decision = decision
        self.requests: list[object] = []

    async def decide(self, request: object) -> TemplateDecision:
        self.requests.append(request)
        return self.decision


class TemplateManagerTests(unittest.TestCase):
    def test_manager_passes_domain_catalog_paths_to_shared_decision_service(self) -> None:
        service = _DecisionService(TemplateDecision("use_existing", template_id="object_group_math"))
        manager = TemplateManager(_settings(), decision_service=service)  # type: ignore[arg-type]

        result = asyncio.run(manager.resolve(PresentationRequest(
            domain_id="education",
            presentation_brief="Dạy bé đếm hai nhóm bóng.",
            render_data={"left_count": 2, "right_count": 3},
        )))

        self.assertEqual(result.decision, "use_existing")
        self.assertEqual(result.template_id, "object_group_math")
        request = service.requests[0]
        self.assertEqual(request.template_catalog_path.name, "catalog.json")
        self.assertEqual(request.asset_catalog_path.name, "catalog.json")
        self.assertEqual(request.render_data, {"left_count": 2, "right_count": 3})

    def test_manager_rejects_empty_brief_before_calling_model(self) -> None:
        service = _DecisionService(TemplateDecision("use_existing", template_id="object_group_math"))
        manager = TemplateManager(_settings(), decision_service=service)  # type: ignore[arg-type]

        with self.assertRaisesRegex(TemplateManagerError, "presentation_brief"):
            asyncio.run(manager.resolve(PresentationRequest(domain_id="education")))
        self.assertEqual(service.requests, [])


def _settings() -> Settings:
    return Settings(
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
        template_llm_api_key="test-template-key",
        template_llm_model="test-template-model",
    )


if __name__ == "__main__":
    unittest.main()
