import unittest

from gemini_live_2.search.brave import BraveImageResult, BraveWebResult
from gemini_live_2.search.capabilities import PlanAgentSearchService, SearchExecutionError
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


class PlanAgentSearchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _SearchClient()
        self.store = SearchResultStore()
        self.service = PlanAgentSearchService(
            client_factory=lambda: self.client,
            result_store=self.store,
        )

    def test_web_returns_provider_fields_and_image_hides_remote_url_from_agent_data(self) -> None:
        web = self.service.search_web(
            query="vòng đời bướm", domain_id="education", session_id="session-a"
        )
        self.assertEqual(web.data["search_results"][0]["title"], "Vòng đời bướm")
        self.assertEqual(web.data["search_results"][0]["source_url"], "https://example/butterfly")

        image = self.service.search_image(
            query="ảnh vòng đời bướm", domain_id="education", session_id="session-a"
        )
        result = image.data["search_results"][0]
        self.assertEqual(result["kind"], "image")
        self.assertEqual(result["caption"], "Vòng đời bướm")
        self.assertNotIn("remote_url", result)
        self.assertEqual(
            self.store.get_image(session_id="session-a", result_id=result["result_id"]).remote_url,
            "https://images.example/butterfly.png",
        )

    def test_invalid_arguments_are_safe_capability_errors(self) -> None:
        with self.assertRaisesRegex(SearchExecutionError, "non-empty"):
            self.service.search_web(query="", domain_id="education", session_id="session-a")
        self.service.search_web(query="bướm", domain_id="education", session_id="session-a")
        self.service.search_web(query="mèo", domain_id="education", session_id="session-a")
        self.assertIsNotNone(
            self.service.search_web(query="chó", domain_id="education", session_id="session-a")
        )


if __name__ == "__main__":
    unittest.main()
