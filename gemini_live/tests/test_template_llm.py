"""Tests for the Template LLM boundary; no real model call is made."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from gemini_live.template_engine.template_llm import (
    TemplateDecisionRequest,
    TemplateDecisionService,
    TemplateLayoutRequest,
    TemplateLayoutService,
    TemplateLayoutServiceError,
    load_asset_catalog,
)
from gemini_live.settings import Settings


_EDUCATION_ASSET_CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "domains" / "education" / "templates" / "assets" / "catalog.json"
)


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, object]] = []

    async def generate_content(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self.response_text)


class _FakeClient:
    def __init__(self, response_text: str) -> None:
        self.models = _FakeModels(response_text)
        self.aio = type("Aio", (), {"models": self.models})()


class TemplateLlmTests(unittest.TestCase):
    def test_asset_catalog_exposes_caption_but_keeps_internal_path(self) -> None:
        assets = load_asset_catalog(_EDUCATION_ASSET_CATALOG_PATH)

        self.assertEqual({asset.id for asset in assets}, {"dog", "cat"})
        self.assertTrue(all(asset.path.endswith(".jpg") for asset in assets))
        self.assertTrue(all(asset.public_url("/assets/education").startswith("/assets/education/") for asset in assets))
        self.assertTrue(all("path" not in asset.for_llm() for asset in assets))

    def test_service_calls_configured_model_and_validates_its_json(self) -> None:
        fake_client = _FakeClient(json.dumps(_dog_cat_layout(), ensure_ascii=False))
        service = TemplateLayoutService(
            _settings(),
            asset_catalog_path=_EDUCATION_ASSET_CATALOG_PATH,
            client_factory=lambda **_kwargs: fake_client,
        )

        spec = asyncio.run(service.create_layout(TemplateLayoutRequest(
            domain_id="education",
            template_brief="Người dùng đang hỏi: hãy dạy bé về chó và mèo.",
            recent_history=({"role": "user", "text": "Hãy dạy bé về chó và mèo"},),
        )))

        self.assertEqual([block.id for block in spec.blocks], ["b1", "b2", "b3", "b4"])
        call = fake_client.models.calls[0]
        self.assertEqual(call["model"], "test-template-model")
        sent = json.loads(str(call["contents"]))
        self.assertEqual(sent["canvas"], {"columns": 12, "rows": 10})
        self.assertEqual(sent["allowed_blocks"], ["text", "image"])
        self.assertEqual(sent["assets"], [
            {"id": "dog", "caption": "Minh hoạ một chú chó thân thiện dành cho trẻ em."},
            {"id": "cat", "caption": "Minh hoạ một chú mèo thân thiện dành cho trẻ em."},
        ])

    def test_service_rejects_invalid_model_layout(self) -> None:
        invalid = _dog_cat_layout()
        invalid["blocks"][2]["asset_id"] = "unknown"
        service = TemplateLayoutService(
            _settings(),
            asset_catalog_path=_EDUCATION_ASSET_CATALOG_PATH,
            client_factory=lambda **_kwargs: _FakeClient(json.dumps(invalid, ensure_ascii=False)),
        )

        with self.assertRaisesRegex(TemplateLayoutServiceError, "không hợp lệ"):
            asyncio.run(service.create_layout(TemplateLayoutRequest(
                domain_id="education", template_brief="Dạy bé về chó và mèo"
            )))

    def test_decision_service_accepts_catalogued_existing_template(self) -> None:
        root = _EDUCATION_ASSET_CATALOG_PATH.parent.parent
        fake_client = _FakeClient(json.dumps({
            "decision": "use_existing", "template_id": "object_group_math"
        }))
        service = TemplateDecisionService(_settings(), client_factory=lambda **_kwargs: fake_client)

        decision = asyncio.run(service.decide(TemplateDecisionRequest(
            domain_id="education",
            presentation_brief="Tạo bài cộng bằng các nhóm vật thể.",
            template_catalog_path=root / "catalog.json",
            asset_catalog_path=_EDUCATION_ASSET_CATALOG_PATH,
            render_data={"operation": "+", "left_count": 3, "right_count": 2},
        )))

        self.assertEqual(decision.decision, "use_existing")
        self.assertEqual(decision.template_id, "object_group_math")
        sent = json.loads(str(fake_client.models.calls[0]["contents"]))
        self.assertEqual({item["id"] for item in sent["templates"]}, {
            "object_group_math", "repeated_groups_arithmetic"
        })
        self.assertEqual(sent["render_data"], {"operation": "+", "left_count": 3, "right_count": 2})
        config = fake_client.models.calls[0]["config"]
        self.assertEqual(config.response_json_schema["type"], "object")
        self.assertEqual(
            config.response_json_schema["properties"]["decision"]["enum"],
            ["use_existing", "create_layout"],
        )

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


def _dog_cat_layout() -> dict[str, object]:
    return {
        "blocks": [
            {
                "id": "b1", "type": "text", "content": "Cùng tìm hiểu chó và mèo",
                "grid": {"col": 1, "row": 1, "col_span": 12, "row_span": 1},
            },
            {
                "id": "b2", "type": "text", "content": "Con hãy quan sát hai bạn nhé!",
                "grid": {"col": 1, "row": 2, "col_span": 12, "row_span": 1},
            },
            {
                "id": "b3", "type": "image", "asset_id": "dog", "label": "Chó",
                "grid": {"col": 1, "row": 3, "col_span": 5, "row_span": 5},
            },
            {
                "id": "b4", "type": "image", "asset_id": "cat", "label": "Mèo",
                "grid": {"col": 7, "row": 3, "col_span": 5, "row_span": 5},
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
