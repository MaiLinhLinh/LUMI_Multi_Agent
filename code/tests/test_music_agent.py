import json

from rag_manager.agents.music import run_music_agent
from rag_manager.agents.music_structured_schema import (
    MusicCandidateResolutionResponse,
)
from rag_manager.config import Settings
from rag_manager.services.music_result_validator import MusicResultValidator


def _settings() -> Settings:
    return Settings(
        gemini_api_key="gemini-key",
        gemini_base_url="",
        gemini_model="gemma-4-26b-a4b-it",
        openweather_api_key="",
        gnews_api_key="",
        weather_cache_ttl_seconds=3600,
        news_cache_ttl_seconds=900,
        wiki_cache_ttl_seconds=None,
        request_timeout_seconds=8,
        debug_routing=False,
    )


def _candidate(title: str, video_id: str) -> dict:
    return {
        "record_id": f"youtube_{video_id}",
        "track_id": f"track_{video_id}",
        "title": title,
        "artists": ["Sơn Tùng M-TP"],
        "video_id": video_id,
        "content_type": "official_mv",
        "version": "official MV",
        "thumbnail_url": "",
        "duration_seconds": 240,
        "release_date": "2024-01-01",
        "release_date_origin": "youtube_published_at_proxy",
        "view_count": 100,
        "ranking": {
            "dense_rank": 1,
            "bm25_rank": 1,
            "rrf_score": 0.03,
            "exact_boost": 0.07,
            "final_score": 0.10,
        },
    }


class StubMusicClient:
    def __init__(
        self,
        extraction: dict,
        answer: str = "Bạn muốn nghe bài nào?",
        candidate_resolution: dict | None = None,
    ):
        self.extraction = extraction
        self.answer = answer
        self.candidate_resolution = candidate_resolution
        self.calls: list[tuple[str, str]] = []
        self.last_usage: dict = {}

    def chat_structured_json(
        self,
        system_prompt: str,
        user_message: str,
        *,
        response_schema: type,
        temperature: float = 0.0,
    ) -> dict:
        if response_schema is MusicCandidateResolutionResponse:
            self.calls.append(("llm2", user_message))
            payload = self.candidate_resolution or {
                "decision": "needs_clarification",
                "selection_index": None,
                "confidence": "low",
                "question": self.answer,
            }
            self.last_usage = {"model": "music-model", "total_tokens": 8}
            return response_schema.model_validate(payload).model_dump()
        self.calls.append(("llm1", user_message))
        self.last_usage = {"model": "music-model", "total_tokens": 20}
        return response_schema.model_validate(self.extraction).model_dump()

    def chat_text(
        self,
        system_prompt: str,
        user_message: str,
        *,
        on_text_chunk=None,
    ) -> str:
        self.calls.append(("llm2", user_message))
        self.last_usage = {"model": "music-model", "total_tokens": 8}
        if on_text_chunk is not None:
            midpoint = max(1, len(self.answer) // 2)
            on_text_chunk(self.answer[:midpoint])
            on_text_chunk(self.answer[midpoint:])
        return self.answer


class StubSearchService:
    def __init__(self, candidates: list[dict], strategy: str = "hybrid_rrf"):
        self.candidates = candidates
        self.strategy = strategy
        self.calls: list[dict] = []

    def search(self, extraction: dict) -> dict:
        self.calls.append(extraction)
        return {
            "strategy": self.strategy,
            "query": extraction.get("search_query", ""),
            "candidates": self.candidates,
            "diagnostics": {"elapsed_seconds": 0.01},
        }


def test_music_agent_completes_unique_exact_title_without_llm2() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": "Lạc Trôi Sơn Tùng",
            "title": "Lạc Trôi",
            "artist": "Sơn Tùng",
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        }
    )
    search = StubSearchService([_candidate("Lạc Trôi", "Llw9Q6akRo4")])

    result = run_music_agent(
        {"query": "Bật bài Lạc Trôi", "history": []},
        settings=_settings(),
        client=client,
        search_service=search,
    )

    assert result["music_status"] == "completed"
    assert result["music_answer"] == "Đây là bài “Lạc Trôi” của Sơn Tùng M-TP."
    assert result["music_data"]["decision"]["selected_candidate"]["video_id"] == (
        "Llw9Q6akRo4"
    )
    assert result["music_player"]["ui_type"] == "youtube_player"
    assert result["music_player"]["player_action"] == "play"
    assert result["music_player"]["music"]["video_id"] == "Llw9Q6akRo4"
    assert [call[0] for call in client.calls] == ["llm1"]
    assert "call_2" not in result["llm_usage"]["music"]


def test_music_llm1_receives_only_four_prior_music_messages() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": "Láº¡c TrÃ´i SÆ¡n TÃ¹ng",
            "title": "Láº¡c TrÃ´i",
            "artist": "SÆ¡n TÃ¹ng",
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        }
    )
    query = "Báº­t bÃ i Láº¡c TrÃ´i"
    history = [
        {"role": "user", "content": "Nháº¡c SÆ¡n TÃ¹ng", "domain": "music"},
        {"role": "assistant", "content": "Báº¡n muá»‘n nghe bÃ i nÃ o?", "domain": "music"},
        {"role": "user", "content": "Thá»i tiáº¿t HÃ  Ná»™i", "domain": "weather"},
        {"role": "assistant", "content": "HÃ  Ná»™i nhiá»u mÃ¢y", "domain": "weather"},
        {"role": "user", "content": "BÃ i Ä‘áº§u tiÃªn", "domain": "music"},
        {"role": "assistant", "content": "Äang phÃ¡t bÃ i Ä‘áº§u tiÃªn", "domain": "music"},
        {"role": "user", "content": "Xin chÃ o", "domain": "other"},
        {"role": "user", "content": "BÃ i tiáº¿p theo", "domain": "music"},
        {"role": "assistant", "content": "Äang phÃ¡t bÃ i tiáº¿p theo", "domain": "music"},
        {"role": "user", "content": query, "domain": "music"},
    ]

    run_music_agent(
        {"query": query, "history": history},
        settings=_settings(),
        client=client,
        search_service=StubSearchService([_candidate("Láº¡c TrÃ´i", "Llw9Q6akRo4")]),
    )

    llm1_payload = json.loads(client.calls[0][1])
    assert llm1_payload["query"] == query
    assert llm1_payload["relevant_history"] == [
        {"role": "user", "content": "BÃ i Ä‘áº§u tiÃªn"},
        {"role": "assistant", "content": "Äang phÃ¡t bÃ i Ä‘áº§u tiÃªn"},
        {"role": "user", "content": "BÃ i tiáº¿p theo"},
        {"role": "assistant", "content": "Äang phÃ¡t bÃ i tiáº¿p theo"},
    ]


def test_music_agent_asks_when_artist_request_has_multiple_results() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": "nhạc Sơn Tùng",
            "title": None,
            "artist": "Sơn Tùng",
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        },
        answer="Bạn muốn nghe Lạc Trôi hay Nơi Này Có Anh?",
    )
    search = StubSearchService(
        [
            _candidate("Lạc Trôi", "Llw9Q6akRo4"),
            _candidate("Nơi Này Có Anh", "FN7ALfpGxiI"),
        ]
    )

    result = run_music_agent(
        {"query": "Bật nhạc Sơn Tùng", "history": []},
        settings=_settings(),
        client=client,
        search_service=search,
    )

    assert result["music_status"] == "needs_clarification"
    assert result["music_answer"].endswith("?")
    assert [call[0] for call in client.calls] == ["llm1", "llm2"]
    llm2_payload = json.loads(client.calls[1][1])
    assert len(llm2_payload["candidate_summaries"]) == 2
    assert "video_id" not in client.calls[1][1]
    assert "Llw9Q6akRo4" not in client.calls[1][1]


def test_music_agent_returns_search_results_without_asking_or_playing() -> None:
    client = StubMusicClient(
        {
            "action": "search",
            "search_query": "các bài hát của Sơn Tùng",
            "title": None,
            "artist": "Sơn Tùng",
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        }
    )
    search = StubSearchService(
        [
            _candidate("Lạc Trôi", "Llw9Q6akRo4"),
            _candidate("Nơi Này Có Anh", "FN7ALfpGxiI"),
        ]
    )

    result = run_music_agent(
        {"query": "Liệt kê các bài hát của Sơn Tùng", "history": []},
        settings=_settings(),
        client=client,
        search_service=search,
    )

    assert result["music_status"] == "completed"
    assert result["music_data"]["decision"] == {
        "status": "completed",
        "code": "music_search_results",
        "reason": "catalog_search_completed",
        "candidate_count": 2,
    }
    assert "1. Lạc Trôi" in result["music_answer"]
    assert "2. Nơi Này Có Anh" in result["music_answer"]
    assert "music_player" not in result
    assert [call[0] for call in client.calls] == ["llm1"]
    assert result["music_session"]["last_candidate_ids"] == [
        "youtube_Llw9Q6akRo4",
        "youtube_FN7ALfpGxiI",
    ]


def test_music_agent_selects_unique_search_alias_without_llm2() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": "nhạc chạy ngay đi",
            "title": None,
            "artist": None,
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        }
    )
    search = StubSearchService(
        [
            _candidate("CHẠY NGAY ĐI | RUN NOW", "32sYGCOYJUM"),
            _candidate("Lạc Trôi", "Llw9Q6akRo4"),
            _candidate("Nơi Này Có Anh", "FN7ALfpGxiI"),
        ]
    )

    result = run_music_agent(
        {"query": "Bật nhạc chạy ngay đi", "history": []},
        settings=_settings(),
        client=client,
        search_service=search,
    )

    assert result["music_status"] == "completed"
    assert result["music_data"]["decision"]["reason"] == (
        "unique_exact_search_alias"
    )
    assert result["music_player"]["music"]["video_id"] == "32sYGCOYJUM"
    assert [call[0] for call in client.calls] == ["llm1"]


def test_music_llm2_can_select_only_a_returned_candidate() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": "chạy ngay đi Sơn Tùng",
            "title": None,
            "artist": None,
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        },
        candidate_resolution={
            "decision": "selected",
            "selection_index": 2,
            "confidence": "high",
            "question": None,
        },
    )
    search = StubSearchService(
        [
            _candidate("Lạc Trôi", "Llw9Q6akRo4"),
            _candidate("CHẠY NGAY ĐI | RUN NOW", "32sYGCOYJUM"),
        ]
    )

    result = run_music_agent(
        {"query": "Bật nhạc chạy ngay đi Sơn Tùng", "history": []},
        settings=_settings(),
        client=client,
        search_service=search,
    )

    assert result["music_status"] == "completed"
    assert result["music_data"]["decision"]["reason"] == (
        "llm2_candidate_resolution"
    )
    assert result["music_player"]["music"]["video_id"] == "32sYGCOYJUM"
    assert [call[0] for call in client.calls] == ["llm1", "llm2"]


def test_music_agent_not_found_names_the_requested_title() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": "Chúng Ta Của Ngày Hôm Qua",
            "title": "Chúng Ta Của Ngày Hôm Qua",
            "artist": None,
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        },
        # Force the safety fallback so the user-facing result is deterministic
        # even if LLM2 ignores the music_not_found instruction.
        answer="https://example.com/unsafe",
    )

    result = run_music_agent(
        {"query": "Bài Chúng Ta Của Ngày Hôm Qua", "history": []},
        settings=_settings(),
        client=client,
        search_service=StubSearchService([]),
    )

    assert result["music_status"] == "needs_clarification"
    assert result["music_answer"] == (
        "Hiện tại tôi chưa tìm thấy bài “Chúng Ta Của Ngày Hôm Qua” "
        "trong kho nhạc."
    )
    assert [call[0] for call in client.calls] == ["llm1", "llm2"]
    llm2_payload = json.loads(client.calls[1][1])
    assert llm2_payload["reason"] == "music_not_found"
    assert llm2_payload["requested_music"]["title"] == (
        "Chúng Ta Của Ngày Hôm Qua"
    )


def test_music_llm2_forwards_text_chunks_to_web_callback() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": "nhạc Sơn Tùng",
            "title": None,
            "artist": "Sơn Tùng",
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        },
        answer="Bạn muốn nghe Lạc Trôi hay Nơi Này Có Anh?",
    )
    chunks: list[tuple[str, str]] = []

    result = run_music_agent(
        {
            "query": "Bật nhạc Sơn Tùng",
            "history": [],
            "response_stream_callback": lambda domain, text: chunks.append(
                (domain, text)
            ),
        },
        settings=_settings(),
        client=client,
        search_service=StubSearchService(
            [
                _candidate("Lạc Trôi", "Llw9Q6akRo4"),
                _candidate("Nơi Này Có Anh", "FN7ALfpGxiI"),
            ]
        ),
    )

    assert result["music_status"] == "needs_clarification"
    assert {domain for domain, _text in chunks} == {"music"}
    assert "".join(text for _domain, text in chunks) == result["music_answer"]


def test_music_agent_selects_latest_without_llm2() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": "bài mới nhất Sơn Tùng",
            "title": None,
            "artist": "Sơn Tùng",
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": "release_date",
            "sort_order": "desc",
            "selection_index": None,
        }
    )
    search = StubSearchService(
        [_candidate("Đừng Làm Trái Tim Anh Đau", "abPmZCZZrFA")],
        strategy="structured_sort",
    )

    result = run_music_agent(
        {"query": "Bật bài mới nhất của Sơn Tùng", "history": []},
        settings=_settings(),
        client=client,
        search_service=search,
    )

    assert result["music_status"] == "completed"
    assert result["music_answer"].startswith("Đây là bài")
    assert [call[0] for call in client.calls] == ["llm1"]


def test_music_agent_does_not_search_numbered_selection_without_session() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": None,
            "title": None,
            "artist": None,
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": 2,
        }
    )
    search = StubSearchService([])

    result = run_music_agent(
        {"query": "Bài thứ hai", "history": []},
        settings=_settings(),
        client=client,
        search_service=search,
    )

    assert result["music_status"] == "needs_clarification"
    assert search.calls == []
    assert result["music_data"]["validation"]["code"] == (
        "selection_context_required"
    )


def test_music_agent_saves_candidates_then_selects_second_without_llm_or_search() -> None:
    first_client = StubMusicClient(
        {
            "action": "play",
            "search_query": "nhạc Sơn Tùng",
            "title": None,
            "artist": "Sơn Tùng",
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        },
        answer="Bạn muốn chọn bài nào?",
    )
    candidates = [
        _candidate("Lạc Trôi", "Llw9Q6akRo4"),
        _candidate("Nơi Này Có Anh", "FN7ALfpGxiI"),
    ]
    first = run_music_agent(
        {"query": "Bật nhạc Sơn Tùng", "history": []},
        settings=_settings(),
        client=first_client,
        search_service=StubSearchService(candidates),
    )
    second_client = StubMusicClient({})
    second_search = StubSearchService([])

    second = run_music_agent(
        {
            "query": "Bài thứ hai",
            "history": [],
            "music_session": first["music_session"],
        },
        settings=_settings(),
        client=second_client,
        search_service=second_search,
    )

    assert first["music_status"] == "needs_clarification"
    assert first["music_session"]["last_candidate_ids"] == [
        "youtube_Llw9Q6akRo4",
        "youtube_FN7ALfpGxiI",
    ]
    assert "ranking" not in first["music_session"]["last_candidates"][0]
    assert second["music_status"] == "completed"
    assert second["music_data"]["selected_candidate"]["title"] == (
        "Nơi Này Có Anh"
    )
    assert second["music_session"]["current_candidate_index"] == 2
    assert second["music_session"]["current_source_id"] == (
        "youtube_FN7ALfpGxiI"
    )
    assert second_client.calls == []
    assert second_search.calls == []
    assert second["llm_usage"]["music"] == {}


def test_music_agent_selects_saved_candidate_by_exact_title_without_search() -> None:
    candidates = [
        _candidate("Lạc Trôi", "Llw9Q6akRo4"),
        _candidate("Nơi Này Có Anh", "FN7ALfpGxiI"),
    ]
    session = {
        "last_candidates": candidates,
        "last_candidate_ids": [item["record_id"] for item in candidates],
    }
    client = StubMusicClient({})
    search = StubSearchService([])

    result = run_music_agent(
        {
            "query": "Chọn Nơi Này Có Anh",
            "history": [],
            "music_session": session,
        },
        settings=_settings(),
        client=client,
        search_service=search,
    )

    assert result["music_status"] == "completed"
    assert result["music_data"]["selected_candidate"]["video_id"] == (
        "FN7ALfpGxiI"
    )
    assert client.calls == []
    assert search.calls == []


def test_music_agent_resolves_next_replay_and_stop_from_session() -> None:
    candidates = [
        _candidate("Lạc Trôi", "Llw9Q6akRo4"),
        _candidate("Nơi Này Có Anh", "FN7ALfpGxiI"),
    ]
    initial = {
        "last_candidates": candidates,
        "current_candidate": candidates[0],
        "current_source_id": candidates[0]["record_id"],
        "current_track_id": candidates[0]["track_id"],
        "current_candidate_index": 1,
        "playback_status": "playing",
    }

    next_result = run_music_agent(
        {"query": "Bài tiếp theo", "history": [], "music_session": initial},
        settings=_settings(),
        client=StubMusicClient({}),
        search_service=StubSearchService([]),
    )
    replay_result = run_music_agent(
        {
            "query": "Phát lại",
            "history": [],
            "music_session": next_result["music_session"],
        },
        settings=_settings(),
        client=StubMusicClient({}),
        search_service=StubSearchService([]),
    )
    stop_result = run_music_agent(
        {
            "query": "Dừng nhạc",
            "history": [],
            "music_session": replay_result["music_session"],
        },
        settings=_settings(),
        client=StubMusicClient({}),
        search_service=StubSearchService([]),
    )

    assert next_result["music_data"]["selected_candidate"]["title"] == (
        "Nơi Này Có Anh"
    )
    assert replay_result["music_data"]["selected_candidate"]["title"] == (
        "Nơi Này Có Anh"
    )
    assert stop_result["music_status"] == "completed"
    assert stop_result["music_answer"] == "Đã dừng phát nhạc."
    assert stop_result["music_player"]["player_action"] == "stop"
    assert stop_result["music_session"]["playback_status"] == "stopped"
    assert stop_result["music_session"]["current_candidate"]["title"] == (
        "Nơi Này Có Anh"
    )


def test_music_agent_rejects_invalid_database_video_id_before_player_payload() -> None:
    client = StubMusicClient(
        {
            "action": "play",
            "search_query": "Lạc Trôi Sơn Tùng",
            "title": "Lạc Trôi",
            "artist": "Sơn Tùng",
            "genre": None,
            "mood": None,
            "language": None,
            "version": None,
            "sort_by": None,
            "sort_order": None,
            "selection_index": None,
        }
    )
    invalid = _candidate("Lạc Trôi", "bad-id")

    result = run_music_agent(
        {"query": "Bật bài Lạc Trôi", "history": []},
        settings=_settings(),
        client=client,
        search_service=StubSearchService([invalid]),
    )

    assert result["music_status"] == "error"
    assert result["music_error"]["stage"] == "player_payload"
    assert result["music_error"]["code"] == "invalid_youtube_video_id"
    assert "music_player" not in result


def test_validator_rejects_llm_supplied_url_or_database_filter() -> None:
    validator = MusicResultValidator()
    base = {
        "action": "play",
        "search_query": "https://youtube.com/watch?v=fake",
        "title": None,
        "artist": None,
        "genre": None,
        "mood": None,
        "language": None,
        "version": None,
        "sort_by": None,
        "sort_order": None,
        "selection_index": None,
    }

    url_result = validator.validate_extraction(base)
    filter_result = validator.validate_extraction(
        {**base, "search_query": '{"where_document": {"$contains": "x"}}'}
    )

    assert url_result["code"] == "unsafe_music_retrieval_value"
    assert filter_result["code"] == "unsafe_music_retrieval_value"
