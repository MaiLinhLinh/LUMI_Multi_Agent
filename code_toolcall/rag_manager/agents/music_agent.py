from __future__ import annotations

import json
from typing import Any

from rag_manager.llm.function_calling_runtime import GeminiFunctionCallingRuntime
from rag_manager.services.music_session import MusicSessionManager
from rag_manager.tools.music_tools import MUSIC_DECLARATIONS, MusicTools


SEARCH_AND_PLAY_SYSTEM = """You are the Music sub-agent. Decide which tool is needed next.
You may receive a trusted shortlist from the current music session. If the requested track is in that shortlist,
call play_music with its record_id and do not search again. If no shortlist item matches, call search_music.
After search_music returns data.status=found with one exact suitable candidate and the user asked to play,
call play_music with that candidate's record_id. If data.status=not_found, do not play an alternative:
briefly state that the requested track is unavailable and offer only data.candidates as choices.
If search_music returns two or more candidates and the user has not named one exact track or selected an item
from a previous shortlist, do not call play_music. Ask the user in Vietnamese which track they want, and list
only the candidates returned by the tool.
When the user explicitly names a track, call search_music with request_kind=exact_track and both query and title.
If the user also names an artist, include artist. Never invent a URL, video_id, record_id, title, or artist.
After play_music returns, answer in concise Vietnamese using only tool facts."""

DIRECT_PLAY_SYSTEM = """You are the Music sub-agent. The backend has already matched exactly one trusted
music-session candidate to the user's request. You must call play_music with that candidate's record_id now.
After the tool returns, answer concisely in Vietnamese that the verified track is playing. Never invent a URL,
video_id, record_id, title, or artist."""


def _play_declarations() -> list[dict[str, Any]]:
    return [item for item in MUSIC_DECLARATIONS if item["name"] == "play_music"]


def _shortlist(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "record_id": candidate.get("record_id"),
            "title": candidate.get("title"),
            "artists": candidate.get("artists", []),
            "title_aliases": candidate.get("title_aliases", []),
        }
        for candidate in candidates[:5]
    ]


def run_music(
    runtime: GeminiFunctionCallingRuntime,
    tools: MusicTools,
    query: str,
    history: list[dict[str, Any]] | None = None,
    music_session: dict[str, Any] | None = None,
    on_text_chunk: Any = None,
) -> dict[str, Any]:
    sessions = MusicSessionManager()
    saved_session = sessions.normalize(music_session)
    session_candidates = saved_session.get("last_candidates", [])
    session_allowed = {
        str(candidate.get("record_id")): candidate
        for candidate in session_candidates
        if candidate.get("record_id")
    }
    direct_decision = sessions.resolve_query(query, saved_session)
    direct_candidate = (
        direct_decision.get("selected_candidate")
        if isinstance(direct_decision, dict)
        and direct_decision.get("status") == "completed"
        and isinstance(direct_decision.get("selected_candidate"), dict)
        else None
    )

    context = "\n".join(
        f"{item.get('role', '')}: {item.get('content', '')}"
        for item in (history or [])[-6:]
    )
    if direct_candidate:
        prompt = (
            f"Current user request: {query}\n\n"
            "Trusted exact candidate:\n"
            + json.dumps(_shortlist([direct_candidate]), ensure_ascii=False)
        )
        declarations = _play_declarations()
        system_instruction = DIRECT_PLAY_SYSTEM
        force_function_names = ["play_music"]
    else:
        prompt_parts = []
        if context:
            prompt_parts.append(f"Relevant conversation history:\n{context}")
        if session_candidates:
            prompt_parts.append(
                "Trusted music-session shortlist. These record_id values may be passed only to play_music:\n"
                + json.dumps(_shortlist(session_candidates), ensure_ascii=False)
            )
        prompt_parts.append(f"Current user request: {query}")
        prompt = "\n\n".join(prompt_parts)
        declarations = MUSIC_DECLARATIONS
        system_instruction = SEARCH_AND_PLAY_SYSTEM
        force_function_names = None

    def play_handler(args: dict[str, Any]) -> dict[str, Any]:
        allowed = {**session_allowed, **tools.allowed}
        return tools.play_music(args, allowed_candidates=allowed)

    output = runtime.run(
        system_instruction=system_instruction,
        user_text=prompt,
        declarations=declarations,
        handlers={"search_music": tools.search_music, "play_music": play_handler},
        on_text_chunk=on_text_chunk,
        force_function_names=force_function_names,
    )
    latest_play = next(
        (item for item in reversed(output["tool_trace"]) if item["tool"] == "play_music"),
        None,
    )
    latest_search = next(
        (item for item in reversed(output["tool_trace"]) if item["tool"] == "search_music"),
        None,
    )
    search_result = latest_search.get("result", {}) if latest_search else {}
    search_data = search_result.get("data", {}) if isinstance(search_result, dict) else {}
    search_arguments = latest_search.get("arguments", {}) if latest_search else {}
    decision: dict[str, Any] = direct_decision or {}
    if latest_play:
        record_id = str(latest_play.get("arguments", {}).get("record_id", ""))
        selected = ({**session_allowed, **tools.allowed}).get(record_id)
        if selected:
            decision = {"selected_candidate": selected, "selected_index": None}
    result_status = (latest_play or latest_search or {}).get("result", {}).get("status", "completed")
    updated_session = sessions.apply_pipeline_result(
        saved_session,
        extraction=search_arguments if isinstance(search_arguments, dict) else {},
        search_result=search_data if isinstance(search_data, dict) else {},
        decision=decision,
        status=result_status,
    )
    player_result = latest_play.get("result", {}) if latest_play else {}
    return {
        "answer": output["text"],
        "status": result_status,
        "data": search_data,
        "music_player": player_result.get("data", {}) if isinstance(player_result, dict) else {},
        "music_session": updated_session,
        "llm_usage": output.get("usage", []),
        "stream_timings": output.get("stream_timings", {}),
        "tool_trace": output["tool_trace"],
    }
