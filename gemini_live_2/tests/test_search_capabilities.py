import unittest

from gemini_live_2.gateway import CapabilityExecutionContext, GatewayExecutionError
from gemini_live_2.search.brave import BraveImageResult, BraveSearchQuota, BraveWebResult
from gemini_live_2.search.capabilities import build_search_capabilities
from gemini_live_2.search.result_store import SearchResultStore


class _SearchClient:
    def __init__(self) -> None:
        self.queries: list[tuple[str, str]] = []

    def search_web(self, query: str) -> BraveWebResult:
        self.queries.append(("web", query))
        return BraveWebResult(title="Vòng đời bướm", snippet="Trứng, sâu, nhộng, bướm.", source_url="https://example/butterfly")

    def search_image(self, query: str) -> BraveImageResult:
        self.queries.append(("image", query))
        return BraveImageResult(
            remote_url="https://images.example/butterfly.png",
            source_url="https://example/butterfly-image",
            caption="Vòng đời bướm",
        )


class SearchCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _SearchClient()
        self.store = SearchResultStore()
        self.capabilities = {
            item.descriptor.id: item
            for item in build_search_capabilities(
                domain_id="education",
                client_factory=lambda: self.client,
                quota=BraveSearchQuota(max_requests_per_session=2),
                result_store=self.store,
            )
        }
        self.context = CapabilityExecutionContext(session_id="session-a")

    def test_web_returns_provider_fields_and_image_hides_remote_url_from_agent_data(self) -> None:
        web = self.capabilities["search_web"].handler({"query": "vòng đời bướm"}, self.context)
        self.assertEqual(web.data["search_results"][0]["title"], "Vòng đời bướm")
        self.assertEqual(web.data["search_results"][0]["source_url"], "https://example/butterfly")

        image = self.capabilities["search_image"].handler({"query": "ảnh vòng đời bướm"}, self.context)
        result = image.data["search_results"][0]
        self.assertEqual(result["kind"], "image")
        self.assertEqual(result["caption"], "Vòng đời bướm")
        self.assertNotIn("remote_url", result)
        self.assertEqual(
            self.store.get_image(session_id="session-a", result_id=result["result_id"]).remote_url,
            "https://images.example/butterfly.png",
        )

    def test_invalid_arguments_and_quota_are_safe_capability_errors(self) -> None:
        handler = self.capabilities["search_web"].handler
        with self.assertRaisesRegex(GatewayExecutionError, "exactly query"):
            handler({"query": "bướm", "extra": True}, self.context)
        handler({"query": "bướm"}, self.context)
        handler({"query": "mèo"}, self.context)
        with self.assertRaisesRegex(GatewayExecutionError, "quota"):
            handler({"query": "chó"}, self.context)


if __name__ == "__main__":
    unittest.main()
