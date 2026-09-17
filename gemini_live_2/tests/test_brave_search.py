from __future__ import annotations

import json
import unittest
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from gemini_live_2.search import (
    BraveSearchClient,
    BraveSearchConfigurationError,
    BraveSearchError,
)


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class BraveSearchTests(unittest.TestCase):
    def test_web_query_keeps_agent_query_and_applies_fixed_child_safety_policy(self) -> None:
        captured = {}

        def opener(request, *, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return _Response({"web": {"results": [{
                "title": "Vòng đời của bướm",
                "description": "Trứng, sâu, nhộng, bướm.",
                "url": "https://example.org/butterfly",
            }]}})

        client = BraveSearchClient(api_key="test-key", timeout_seconds=8, opener=opener)
        result = client.search_web("vòng đời của bướm")

        self.assertEqual(result.title, "Vòng đời của bướm")
        self.assertEqual(result.snippet, "Trứng, sâu, nhộng, bướm.")
        self.assertEqual(result.source_url, "https://example.org/butterfly")
        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["q"], ["vòng đời của bướm"])
        self.assertEqual(query["count"], ["1"])
        self.assertEqual(query["country"], ["ALL"])
        self.assertEqual(query["search_lang"], ["vi"])
        self.assertEqual(query["ui_lang"], ["en-US"])
        self.assertEqual(query["safesearch"], ["strict"])
        self.assertEqual(captured["timeout"], 8.0)

    def test_image_result_preserves_provider_fields_needed_by_future_store(self) -> None:
        client = BraveSearchClient(
            api_key="test-key",
            timeout_seconds=8,
            opener=lambda *_args, **_kwargs: _Response({"results": [{
                "title": "Cute pig illustration",
                "url": "https://example.org/pig",
                "properties": {"url": "https://cdn.example.org/pig.png"},
            }]}),
        )

        result = client.search_image("minh hoạ con heo")

        self.assertEqual(result.caption, "Cute pig illustration")
        self.assertEqual(result.source_url, "https://example.org/pig")
        self.assertEqual(result.remote_url, "https://cdn.example.org/pig.png")

    def test_provider_errors_are_structured_for_capability_handler(self) -> None:
        client = BraveSearchClient(
            api_key="test-key",
            timeout_seconds=8,
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
        )

        with self.assertRaisesRegex(BraveSearchError, "request failed"):
            client.search_web("con mèo")
        with self.assertRaises(BraveSearchConfigurationError):
            BraveSearchClient(api_key="", timeout_seconds=8)
