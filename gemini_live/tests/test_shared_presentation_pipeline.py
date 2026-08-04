"""Tests for presentation behaviour that must remain domain-neutral."""

from __future__ import annotations

import unittest

from gemini_live.presentation.pipeline import concrete_animation_capabilities


class SharedPresentationPipelineTests(unittest.TestCase):
    def test_resolves_static_and_entity_template_targets(self) -> None:
        html = (
            '<section data-present-id="weather.overview"></section>'
            '<section data-present-id="weather.day.2.rain_risk"></section>'
        )
        capabilities = {
            "overview": {"target_id": "weather.overview", "allowed_effects": ["reveal"]},
            "rain_risk": {
                "target_pattern": "weather.day.{day_index}.rain_risk",
                "allowed_effects": ["draw_circle"],
            },
        }
        resolved = concrete_animation_capabilities(html, capabilities)
        self.assertEqual(resolved["weather.overview"], ["reveal"])
        self.assertEqual(resolved["weather.day.2.rain_risk"], ["draw_circle"])


if __name__ == "__main__":
    unittest.main()
