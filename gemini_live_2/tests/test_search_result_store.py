import unittest

from gemini_live_2.search import SearchResultStore, SearchResultStoreError


class SearchResultStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.store = SearchResultStore(ttl_seconds=30 * 60, clock=lambda: self.now)

    def test_keeps_typed_results_only_for_the_owning_session(self) -> None:
        image_id = self.store.put_image(
            session_id="session-a",
            remote_url="https://images.example/pig.png",
            source_url="https://example/pig",
            caption="Một chú heo",
        )
        self.assertTrue(image_id.startswith("img_"))
        self.assertEqual(self.store.get_image(session_id="session-a", result_id=image_id).caption, "Một chú heo")
        with self.assertRaisesRegex(SearchResultStoreError, "unavailable"):
            self.store.get_image(session_id="session-b", result_id=image_id)
        with self.assertRaisesRegex(SearchResultStoreError, "unavailable"):
            self.store.get_web(session_id="session-a", result_id=image_id)

    def test_expiry_and_explicit_session_clear_require_a_new_search(self) -> None:
        result_id = self.store.put_web(
            session_id="session-a",
            title="Vòng đời bướm",
            snippet="Trứng, sâu, nhộng, bướm.",
            source_url="https://example/butterfly",
        )
        self.now += 1800
        with self.assertRaisesRegex(SearchResultStoreError, "search again"):
            self.store.get_web(session_id="session-a", result_id=result_id)

        result_id = self.store.put_web(
            session_id="session-a",
            title="Con mèo",
            snippet="Một con mèo.",
            source_url="https://example/cat",
        )
        self.store.clear_session("session-a")
        with self.assertRaisesRegex(SearchResultStoreError, "search again"):
            self.store.get_web(session_id="session-a", result_id=result_id)


if __name__ == "__main__":
    unittest.main()
