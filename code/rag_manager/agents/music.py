"""Music Agent workflow: extraction, validation, retrieval, and clarification."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from time import perf_counter
from typing import Any, Mapping

from rag_manager.agents.music_structured_schema import (
    MusicCandidateResolutionResponse,
    MusicExtractionResponse,
)
from rag_manager.config import Settings, load_settings
from rag_manager.llm.gemini_client import strip_thought_tags
from rag_manager.llm.prompts import (
    MUSIC_PIPELINE_CANDIDATE_RESOLUTION_SYSTEM_PROMPT,
    MUSIC_PIPELINE_EXTRACTION_SYSTEM_PROMPT,
    MUSIC_PIPELINE_RESPONSE_SYSTEM_PROMPT,
)
from rag_manager.services.music_search_service import music_title_aliases
from rag_manager.services.music_embedding_service import (
    OllamaMusicEmbeddingService,
)
from rag_manager.services.music_player_payload import (
    MusicPlayerPayloadError,
    build_music_player_payload,
)
from rag_manager.services.music_repository import MusicChromaRepository
from rag_manager.services.music_result_validator import MusicResultValidator
from rag_manager.services.music_search_service import MusicSearchService
from rag_manager.services.music_session import MusicSessionManager
from rag_manager.state import AgentState


_MUSIC_STATUSES = {"needs_clarification", "unavailable", "error", "completed"}
_MUSIC_ERROR_ANSWER = (
    "Hệ thống chưa thể tìm nhạc lúc này. Bạn vui lòng thử lại sau."
)
_UNSAFE_LLM2_OUTPUT = re.compile(
    r"https?://|<\s*iframe\b|\bvideo_id\b|\$(?:contains|regex)\b",
    re.IGNORECASE,
)


def run_music_agent(
    state: AgentState,
    *,
    settings: Settings | None = None,
    client: object | None = None,
    search_service: MusicSearchService | None = None,
    validator: MusicResultValidator | None = None,
    session_manager: MusicSessionManager | None = None,
) -> AgentState:
    """Run Music LLM1, trusted validation, hybrid search, and optional LLM2."""

    started_at = perf_counter()
    settings = settings or load_settings()
    pipeline_client = client or state.get("music_client")
    query = state.get("query", "")
    query = query if isinstance(query, str) else ""
    history = state.get("history", [])
    history = history if isinstance(history, list) else []
    validator = validator or MusicResultValidator()
    session_manager = session_manager or MusicSessionManager()
    music_session = session_manager.normalize(state.get("music_session"))
    session_resolution = session_manager.resolve_query(query, music_session)
    resolved_from_query = isinstance(session_resolution, dict)

    usage: dict[str, Any] = {} if resolved_from_query else {"call_1": {}}
    extraction: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    search_result: dict[str, Any] = {}
    decision: dict[str, Any] = {}
    music_error: dict[str, Any] = {}
    status = "error"
    answer = ""
    candidate_resolution_question = ""

    if resolved_from_query:
        decision = dict(session_resolution)
        status = str(decision.get("status", "error"))
        validation = {
            "status": "resolved_from_session",
            "code": str(decision.get("code", "session_resolution")),
            "source": decision.get("source"),
        }
        _log_music_event(
            stage="session_resolution",
            code=str(decision.get("code", "session_resolution")),
            status=status,
            source=decision.get("source"),
            selected_index=decision.get("selected_index"),
        )

    if not resolved_from_query and pipeline_client is None:
        try:
            from rag_manager.llm.gemini_client import GeminiClient

            pipeline_client = GeminiClient(settings)
        except Exception as exc:  # noqa: BLE001 - external LLM boundary
            music_error = _music_error(
                "llm1_extraction",
                "llm1_api_error",
                exc,
                retryable=True,
            )

    if not resolved_from_query and pipeline_client is not None and not music_error:
        try:
            if not hasattr(pipeline_client, "chat_structured_json"):
                raise TypeError(
                    "Music pipeline client must provide chat_structured_json()."
                )
            extraction = pipeline_client.chat_structured_json(
                MUSIC_PIPELINE_EXTRACTION_SYSTEM_PROMPT,
                _extraction_message(query, history),
                response_schema=MusicExtractionResponse,
            )
            usage["call_1"] = _client_usage(pipeline_client)
            _log_music_event(
                stage="llm1_extraction",
                code="llm1_result",
                status="received",
                result=extraction,
            )
            extracted_session_action = session_manager.resolve_extraction(
                extraction,
                music_session,
            )
            if isinstance(extracted_session_action, dict):
                decision = extracted_session_action
                status = str(decision.get("status", "error"))
                validation = {
                    "status": "resolved_from_session",
                    "code": str(decision.get("code", "session_resolution")),
                    "source": decision.get("source"),
                }
                _log_music_event(
                    stage="session_resolution",
                    code=str(decision.get("code", "session_resolution")),
                    status=status,
                    source=decision.get("source"),
                    selected_index=decision.get("selected_index"),
                )
            else:
                validation = validator.validate_extraction(extraction)
                status = str(validation.get("status", "error"))
                _log_music_event(
                    stage="validation",
                    code=str(validation.get("code", "invalid_validation_result")),
                    status=status,
                    field=validation.get("field"),
                )

            if status == "ready_for_search":
                canonical = validation.get("canonical_extraction", {})
                if not isinstance(canonical, dict):
                    raise ValueError("Music validator returned invalid canonical data.")
                service = search_service or state.get("music_search_service")
                if service is None:
                    service = _default_search_service(
                        settings.music_chroma_path,
                        settings.music_chroma_collection,
                        settings.ollama_base_url,
                        settings.music_embedding_model,
                        settings.music_embedding_dimensions,
                        settings.music_embedding_timeout_seconds,
                    )
                search_result = service.search(canonical)
                _log_music_event(
                    stage="catalog_search",
                    code="search_completed",
                    status="received",
                    strategy=search_result.get("strategy"),
                    candidate_count=len(search_result.get("candidates", [])),
                    diagnostics=search_result.get("diagnostics", {}),
                )
                decision = validator.evaluate_search_result(canonical, search_result)
                status = str(decision.get("status", "error"))
                _log_music_event(
                    stage="result_validation",
                    code=str(decision.get("code", "invalid_result_decision")),
                    status=status,
                    reason=decision.get("reason"),
                )
        except Exception as exc:  # noqa: BLE001 - normalize external boundaries
            status = "error"
            music_error = _music_error(
                "music_pipeline",
                "music_pipeline_failed",
                exc,
                retryable=True,
            )
            _log_music_event(
                stage=music_error["stage"],
                code=music_error["code"],
                status=status,
                message=music_error["message"],
            )

    if (
        status == "needs_clarification"
        and decision.get("code") == "multiple_music_matches"
        and not resolved_from_query
        and pipeline_client is not None
        and hasattr(pipeline_client, "chat_structured_json")
    ):
        candidates = search_result.get("candidates", [])
        if isinstance(candidates, list) and candidates:
            try:
                resolution = pipeline_client.chat_structured_json(
                    MUSIC_PIPELINE_CANDIDATE_RESOLUTION_SYSTEM_PROMPT,
                    _candidate_resolution_message(query, extraction, candidates),
                    response_schema=MusicCandidateResolutionResponse,
                )
                usage["call_2"] = _client_usage(pipeline_client)
                resolved = _apply_candidate_resolution(resolution, candidates)
                if resolved["status"] == "completed":
                    decision = resolved
                    status = "completed"
                else:
                    candidate_resolution_question = _text(resolved.get("question"))
                _log_music_event(
                    stage="llm2_candidate_resolution",
                    code=str(resolved.get("code")),
                    status=str(resolved.get("status")),
                    selected_index=resolved.get("selected_index"),
                )
            except Exception as exc:  # noqa: BLE001 - deterministic fallback below
                _log_music_event(
                    stage="llm2_candidate_resolution",
                    code="candidate_resolution_fallback",
                    status=status,
                    message=str(exc) or exc.__class__.__name__,
                )

    if status == "completed":
        selected = decision.get("selected_candidate")
        if decision.get("code") == "music_search_results":
            answer = _search_results_answer(search_result.get("candidates"))
        elif decision.get("code") == "playback_stopped":
            answer = "Đã dừng phát nhạc."
        elif not isinstance(selected, dict):
            status = "error"
            music_error = {
                "stage": "result_validation",
                "code": "missing_selected_candidate",
                "message": "Completed music result has no selected database candidate.",
                "retryable": False,
            }
            answer = _MUSIC_ERROR_ANSWER
        else:
            answer = _completed_answer(selected)
    elif status == "needs_clarification":
        issue = decision or validation
        response_request = validation.get("canonical_extraction")
        if not isinstance(response_request, Mapping):
            response_request = extraction
        answer = _clarification_fallback(
            issue,
            search_result,
            response_request,
        )
        if candidate_resolution_question:
            answer = candidate_resolution_question
            stream_callback = state.get("response_stream_callback")
            if callable(stream_callback):
                stream_callback("music", answer)
        if (
            not candidate_resolution_question
            and "call_2" not in usage
            and not resolved_from_query
            and pipeline_client is not None
            and hasattr(pipeline_client, "chat_text")
        ):
            try:
                stream_callback = state.get("response_stream_callback")
                stream_kwargs = (
                    {
                        "on_text_chunk": lambda chunk: stream_callback(
                            "music",
                            chunk,
                        )
                    }
                    if callable(stream_callback)
                    else {}
                )
                llm2_answer = pipeline_client.chat_text(
                    MUSIC_PIPELINE_RESPONSE_SYSTEM_PROMPT,
                    _clarification_message(
                        issue,
                        search_result,
                        response_request,
                    ),
                    **stream_kwargs,
                )
                usage["call_2"] = _client_usage(pipeline_client)
                llm2_answer = strip_thought_tags(llm2_answer)
                if (
                    not llm2_answer
                    or _UNSAFE_LLM2_OUTPUT.search(llm2_answer)
                    or len(llm2_answer) > 500
                ):
                    raise ValueError("Music LLM2 returned unsafe or empty output.")
                answer = llm2_answer
            except Exception as exc:  # noqa: BLE001 - safe deterministic fallback
                _log_music_event(
                    stage="llm2_clarification",
                    code="llm2_fallback_used",
                    status=status,
                    message=str(exc) or exc.__class__.__name__,
                )
    else:
        status = "error"
        answer = _MUSIC_ERROR_ANSWER

    if status not in _MUSIC_STATUSES:
        status = "error"
        answer = _MUSIC_ERROR_ANSWER
        music_error = {
            "stage": "music_pipeline",
            "code": "invalid_music_status",
            "message": "Music pipeline returned an unsupported public status.",
            "retryable": False,
        }

    music_player: dict[str, Any] = {}
    if status == "completed" and decision.get("code") != "music_search_results":
        player_candidate = decision.get("selected_candidate")
        if decision.get("code") == "playback_stopped":
            player_candidate = music_session.get("current_candidate")
        try:
            if not isinstance(player_candidate, Mapping):
                raise MusicPlayerPayloadError(
                    "missing_music_player_candidate",
                    "Completed Music action has no trusted player candidate.",
                )
            music_player = build_music_player_payload(
                player_candidate,
                player_action=_player_action(decision),
            )
            _log_music_event(
                stage="player_payload",
                code="youtube_player_payload_ready",
                status=status,
                player_action=music_player["player_action"],
                video_id=music_player["music"]["video_id"],
            )
        except MusicPlayerPayloadError as exc:
            status = "error"
            answer = _MUSIC_ERROR_ANSWER
            music_error = {
                "stage": "player_payload",
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            }
            _log_music_event(
                stage="player_payload",
                code=exc.code,
                status=status,
                message=str(exc),
            )

    canonical_extraction = validation.get("canonical_extraction")
    session_extraction = (
        canonical_extraction
        if isinstance(canonical_extraction, dict)
        else (
            extraction
            if validation.get("status") == "resolved_from_session"
            else {}
        )
    )
    music_session = session_manager.apply_pipeline_result(
        music_session,
        extraction=session_extraction,
        search_result=search_result,
        decision=decision,
        status=status,
    )

    music_data = {
        "schema_version": "music.workflow.v1",
        "status": status,
        "extraction": extraction,
        "validation": validation,
        "search": {
            "strategy": search_result.get("strategy"),
            "query": search_result.get("query"),
            "diagnostics": search_result.get("diagnostics", {}),
        },
        "candidates": search_result.get("candidates", []),
        "decision": decision,
    }
    if music_player:
        music_data["player"] = music_player
    selected_candidate = decision.get("selected_candidate")
    if status == "completed" and isinstance(selected_candidate, dict):
        music_data["selected_candidate"] = selected_candidate
    update: AgentState = {
        "music_status": status,
        "music_answer": answer,
        "final_response": answer,
        "music_data": music_data,
        "music_session": music_session,
        "timings": {"music": perf_counter() - started_at},
        "llm_usage": {"music": usage},
    }
    if music_error:
        update["music_error"] = music_error
    if music_player:
        update["music_player"] = music_player
    return update


@lru_cache(maxsize=4)
def _default_search_service(
    chroma_path: str,
    collection_name: str,
    ollama_base_url: str,
    embedding_model: str,
    embedding_dimensions: int,
    embedding_timeout_seconds: int,
) -> MusicSearchService:
    repository = MusicChromaRepository(
        path=chroma_path,
        collection_name=collection_name,
        embedding_dimensions=embedding_dimensions,
    )
    embedding_service = OllamaMusicEmbeddingService(
        base_url=ollama_base_url,
        model=embedding_model,
        dimensions=embedding_dimensions,
        timeout_seconds=embedding_timeout_seconds,
    )
    return MusicSearchService(
        repository=repository,
        embedding_service=embedding_service,
    )


def _extraction_message(query: str, history: list[Any]) -> str:
    return json.dumps(
        {
            "query": query,
            "relevant_history": _music_conversation_messages(query, history),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _clarification_message(
    issue: Mapping[str, Any],
    search_result: Mapping[str, Any],
    request: Mapping[str, Any],
) -> str:
    candidates = search_result.get("candidates", [])
    summaries = [
        _candidate_summary(candidate, index)
        for index, candidate in enumerate(candidates[:5], start=1)
        if isinstance(candidate, Mapping)
    ] if isinstance(candidates, list) else []
    return json.dumps(
        {
            "reason": issue.get("code"),
            "field": issue.get("field"),
            "requested_music": {
                "title": request.get("title"),
                "artist": request.get("artist"),
                "search_query": request.get("search_query"),
            },
            "candidate_summaries": summaries,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _candidate_resolution_message(
    query: str,
    extraction: Mapping[str, Any],
    candidates: list[Any],
) -> str:
    summaries = [
        _candidate_resolution_summary(candidate, index)
        for index, candidate in enumerate(candidates[:5], start=1)
        if isinstance(candidate, Mapping)
    ]
    return json.dumps(
        {
            "query": query,
            "extraction": {
                "title": extraction.get("title"),
                "artist": extraction.get("artist"),
                "version": extraction.get("version"),
                "search_query": extraction.get("search_query"),
            },
            "candidate_summaries": summaries,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _candidate_resolution_summary(
    candidate: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    raw_aliases = candidate.get("title_aliases")
    aliases = (
        [str(value) for value in raw_aliases if isinstance(value, str) and value]
        if isinstance(raw_aliases, list)
        else list(music_title_aliases(_text(candidate.get("title"))))
    )
    artists = candidate.get("artists")
    return {
        "index": index,
        "title": _text(candidate.get("title")),
        "title_aliases": aliases,
        "artists": [
            str(value) for value in artists if isinstance(value, str) and value.strip()
        ] if isinstance(artists, list) else [],
        "version": _text(candidate.get("version")) or None,
    }


def _apply_candidate_resolution(
    resolution: Mapping[str, Any],
    candidates: list[Any],
) -> dict[str, Any]:
    decision = resolution.get("decision")
    confidence = resolution.get("confidence")
    index = resolution.get("selection_index")
    if (
        decision == "selected"
        and confidence == "high"
        and isinstance(index, int)
        and not isinstance(index, bool)
        and 1 <= index <= min(5, len(candidates))
        and isinstance(candidates[index - 1], Mapping)
    ):
        return {
            "status": "completed",
            "code": "music_candidate_selected",
            "reason": "llm2_candidate_resolution",
            "selected_candidate": dict(candidates[index - 1]),
            "selected_index": index,
        }
    question = _text(resolution.get("question"))
    if question and (
        _UNSAFE_LLM2_OUTPUT.search(question) or len(question) > 500
    ):
        question = ""
    return {
        "status": "needs_clarification",
        "code": "candidate_resolution_needs_clarification",
        "question": question,
    }


def _clarification_fallback(
    issue: Mapping[str, Any],
    search_result: Mapping[str, Any],
    request: Mapping[str, Any],
) -> str:
    code = issue.get("code")
    candidates = search_result.get("candidates", [])
    if code == "multiple_music_matches" and isinstance(candidates, list):
        summaries = [
            _candidate_summary(candidate, index)
            for index, candidate in enumerate(candidates[:5], start=1)
            if isinstance(candidate, Mapping)
        ]
        if summaries:
            return "Bạn muốn nghe bài nào: " + "; ".join(summaries) + "?"
    if code == "music_not_found":
        title = _text(request.get("title"))
        if title:
            return f"Hiện tại tôi chưa tìm thấy bài “{title}” trong kho nhạc."
        search_query = _text(request.get("search_query")) or _text(
            search_result.get("query")
        )
        if search_query:
            return f"Hiện tại tôi chưa tìm thấy “{search_query}” trong kho nhạc."
        return "Hiện tại tôi chưa tìm thấy kết quả phù hợp trong kho nhạc."
    if code == "selection_context_required":
        return "Tôi chưa có danh sách bài hát trước đó. Bạn muốn tìm bài nào?"
    if code == "selection_index_out_of_range":
        details = issue.get("details", {})
        count = details.get("candidate_count") if isinstance(details, dict) else None
        if isinstance(count, int):
            return f"Danh sách hiện có {count} bài. Bạn muốn chọn bài số mấy?"
        return "Số thứ tự đó không có trong danh sách. Bạn muốn chọn bài nào?"
    if code == "no_next_music_candidate":
        return "Đây đã là bài cuối trong danh sách. Bạn muốn tìm bài khác không?"
    if code == "player_context_required":
        return "Tôi chưa có bài hát đang phát để thực hiện thao tác này. Bạn muốn nghe bài nào?"
    return "Bạn muốn nghe bài hát hoặc nghệ sĩ nào?"


def _candidate_summary(candidate: Mapping[str, Any], index: int) -> str:
    title = _text(candidate.get("title")) or "Không rõ tên"
    artists = candidate.get("artists")
    artist = ", ".join(str(value) for value in artists if str(value).strip()) if isinstance(artists, list) else ""
    version = _text(candidate.get("version"))
    details = " — ".join(value for value in (artist, version) if value)
    return f"{index}. {title}" + (f" — {details}" if details else "")


def _search_results_answer(raw_candidates: Any) -> str:
    candidates = raw_candidates if isinstance(raw_candidates, list) else []
    summaries = [
        _candidate_summary(candidate, index)
        for index, candidate in enumerate(candidates[:5], start=1)
        if isinstance(candidate, Mapping)
    ]
    if not summaries:
        return "Hiện tại tôi chưa tìm thấy kết quả phù hợp trong kho nhạc."
    return "Tôi tìm thấy các bài sau:\n" + "\n".join(summaries)


def _completed_answer(candidate: Mapping[str, Any]) -> str:
    title = _text(candidate.get("title")) or "bài hát bạn yêu cầu"
    artists = candidate.get("artists")
    artist = ", ".join(str(value) for value in artists if str(value).strip()) if isinstance(artists, list) else ""
    if artist:
        return f"Đây là bài “{title}” của {artist}."
    return f"Đây là bài “{title}”."


def _player_action(decision: Mapping[str, Any]) -> str:
    code = decision.get("code")
    if code == "playback_stopped":
        return "stop"
    if code == "current_candidate_replayed":
        return "replay"
    return "play"


def _music_conversation_messages(
    query: str,
    history: list[Any],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        if item.get("domain") != "music":
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    if (
        messages
        and messages[-1]["role"] == "user"
        and messages[-1]["content"] == query.strip()
    ):
        messages.pop()
    return messages[-4:]


def _music_error(
    stage: str,
    code: str,
    exc: Exception,
    *,
    retryable: bool,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "code": code,
        "message": str(exc) or exc.__class__.__name__,
        "retryable": retryable,
        "details": {"exception_type": exc.__class__.__name__},
    }


def _client_usage(client: object) -> dict[str, Any]:
    usage = getattr(client, "last_usage", {})
    return dict(usage) if isinstance(usage, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _log_music_event(
    *,
    stage: str,
    code: str,
    status: str,
    **details: Any,
) -> None:
    payload = {"stage": stage, "code": code, "status": status, **details}
    print(
        "[MUSIC_PIPELINE] "
        + json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True),
        flush=True,
    )
