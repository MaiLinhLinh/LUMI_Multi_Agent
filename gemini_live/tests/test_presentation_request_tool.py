"""Tests for the domain-neutral presentation-request tool."""

from __future__ import annotations

import asyncio
import unittest

from gemini_live.domains import DomainRequest, LiveDomainRegistry
from gemini_live.live import LiveToolDispatcher
from gemini_live.live.orchestrator import LiveSessionOrchestrator
from gemini_live.presentation import PresentationPipeline, PresentationRequest
from gemini_live.presentation.request_domain import (
    CREATE_PRESENTATION_REQUEST_DECLARATION,
    PresentationRequestLiveDomain,
)
from gemini_live.template_engine.template_manager import TemplateResolution


class _TemplateManager:
    async def resolve(self, request: PresentationRequest, *, recent_history: tuple[dict[str, str], ...] = ()) -> TemplateResolution:
        del request, recent_history
        from gemini_live.template_engine.layout_contract import validate_template_layout_output

        return TemplateResolution(
            decision="create_layout",
            layout=validate_template_layout_output({
                "blocks": [{
                    "id": "title", "type": "text", "content": "Chú chó",
                    "grid": {"col": 1, "row": 1, "col_span": 12, "row_span": 1},
                }],
            }, allowed_asset_ids=()),
        )


class PresentationRequestToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.domain = PresentationRequestLiveDomain(
            supported_domain_ids=("education", "weather"),
            presentation_instruction_for=lambda domain_id: f"instruction:{domain_id}",
        )

    def test_creates_an_unresolved_presentation_request(self) -> None:
        result = asyncio.run(self.domain.execute_tool(
            "create_presentation_request",
            {"domain_id": "education", "presentation_brief": "Cho trẻ xem chó và mèo."},
            request=DomainRequest(query="Cho tôi xem con chó và con mèo"),
            context={},
        ))

        self.assertEqual(result.status, "completed")
        self.assertIsInstance(result.presentation, PresentationRequest)
        assert isinstance(result.presentation, PresentationRequest)
        self.assertEqual(result.presentation.domain_id, "education")
        self.assertIsNone(result.presentation.template_id)
        self.assertEqual(result.presentation.presentation_brief, "Cho trẻ xem chó và mèo.")
        self.assertEqual(result.presentation.render_data, {})
        self.assertEqual(result.presentation.presentation_instruction, "instruction:education")

    def test_declaration_limits_domain_id_to_registered_domains(self) -> None:
        declaration = self.domain.tool_declarations[0]
        domain_schema = declaration["parameters"]["properties"]["domain_id"]

        self.assertEqual(domain_schema["enum"], ["education", "weather"])
        self.assertNotIn("enum", CREATE_PRESENTATION_REQUEST_DECLARATION["parameters"]["properties"]["domain_id"])

    def test_orchestrator_resolves_an_unfixed_request_before_rendering(self) -> None:
        registry = LiveDomainRegistry()
        registry.register(self.domain)
        orchestrator = LiveSessionOrchestrator(
            LiveToolDispatcher(registry),
            presentation_pipeline=PresentationPipeline(template_manager=_TemplateManager()),  # type: ignore[arg-type]
        )

        outcome = asyncio.run(orchestrator.execute_tool_call_result(
            session_id="presentation-request",
            query="Cho tôi xem con chó",
            tool_name="create_presentation_request",
            arguments={"domain_id": "education", "presentation_brief": "Hiển thị một chú chó."},
        ))

        self.assertEqual(outcome.response["status"], "completed")
        self.assertEqual(outcome.response["domain_id"], "education")
        self.assertEqual(outcome.response["presentation_instruction"], "instruction:education")
        self.assertIsNotNone(outcome.presentation)

    def test_rejects_an_unknown_target_domain(self) -> None:
        result = asyncio.run(self.domain.execute_tool(
            "create_presentation_request",
            {"domain_id": "unknown", "presentation_brief": "Hiển thị nội dung."},
            request=DomainRequest(query="Hiển thị nội dung"),
            context={},
        ))

        self.assertEqual(result.status, "invalid_arguments")
        self.assertIsNone(result.presentation)


if __name__ == "__main__":
    unittest.main()
