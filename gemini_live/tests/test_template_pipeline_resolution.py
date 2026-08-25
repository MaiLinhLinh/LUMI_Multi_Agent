"""TemplateManager integration at the presentation-pipeline boundary."""

from __future__ import annotations

import asyncio
import unittest

from gemini_live.presentation import PresentationPipeline, PresentationRequest
from gemini_live.template_engine.template_manager import TemplateResolution


class _TemplateManager:
    def __init__(self) -> None:
        self.requests: list[PresentationRequest] = []

    async def resolve(self, request: PresentationRequest, *, recent_history: tuple[dict[str, str], ...] = ()) -> TemplateResolution:
        del recent_history
        self.requests.append(request)
        return TemplateResolution(decision="use_existing", template_id="selected_template")


class TemplatePipelineResolutionTests(unittest.TestCase):
    def test_fixed_template_bypasses_template_manager(self) -> None:
        manager = _TemplateManager()
        pipeline = PresentationPipeline(template_manager=manager)  # type: ignore[arg-type]
        request = PresentationRequest(domain_id="education", template_id="object_group_math")

        resolved = asyncio.run(pipeline.resolve_template(request=request))

        self.assertIs(resolved, request)
        self.assertEqual(manager.requests, [])

    def test_unfixed_request_uses_template_manager_decision(self) -> None:
        manager = _TemplateManager()
        pipeline = PresentationPipeline(template_manager=manager)  # type: ignore[arg-type]
        request = PresentationRequest(domain_id="education", presentation_brief="Dạy bé về chó.")

        resolved = asyncio.run(pipeline.resolve_template(request=request))

        self.assertIsInstance(resolved, PresentationRequest)
        assert isinstance(resolved, PresentationRequest)
        self.assertEqual(resolved.template_id, "selected_template")
        self.assertEqual(manager.requests, [request])


if __name__ == "__main__":
    unittest.main()
