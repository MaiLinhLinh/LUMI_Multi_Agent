from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import types

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
TextChunkCallback = Callable[[str], None]
logger = logging.getLogger("lumi.toolcall")


def _tool_result_summary(result: dict[str, Any]) -> str:
    """Keep terminal diagnostics useful without dumping large tool payloads."""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    details: list[str] = []
    if data.get("status"):
        details.append(f"data.status={data['status']}")
    if isinstance(data.get("candidates"), list):
        details.append(f"candidates={len(data['candidates'])}")
    if isinstance(data.get("suggestions"), list):
        details.append(f"suggestions={len(data['suggestions'])}")
    if data.get("video_id"):
        details.append("video_id=present")
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    if error.get("code"):
        details.append(f"error={error['code']}")
    return ", ".join(details) or "no compact result fields"


def _requires_play_after_music_search(result: dict[str, Any]) -> bool:
    """True only for one verified exact music record, never for suggestions."""

    data = result.get("data")
    if not isinstance(data, dict) or data.get("status") != "found":
        return False
    exact_matches = data.get("exact_matches")
    return isinstance(exact_matches, list) and len(exact_matches) == 1


def _requires_text_choice_after_music_search(result: dict[str, Any]) -> bool:
    """Require a user-facing choice when several exact Music records remain."""

    data = result.get("data")
    if not isinstance(data, dict) or data.get("status") != "found":
        return False
    exact_matches = data.get("exact_matches")
    return isinstance(exact_matches, list) and len(exact_matches) > 1


def _music_choice_instruction(result: dict[str, Any]) -> str:
    """Build the explicit next-turn instruction for ambiguous exact tracks."""

    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    exact_matches = data.get("exact_matches")
    choices: list[str] = []
    if isinstance(exact_matches, list):
        for index, item in enumerate(exact_matches, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Phiên bản không rõ tên")
            version = str(item.get("version") or item.get("content_type") or "")
            label = f"{index}. {title}"
            if version and version.casefold() not in title.casefold():
                label += f" ({version})"
            choices.append(label)

    choice_text = "\n".join(choices) or "các phiên bản trong kết quả công cụ"
    return (
        "Có nhiều phiên bản khớp chính xác với yêu cầu. Không gọi bất kỳ công cụ nào. "
        "Hãy trả lời bằng tiếng Việt, yêu cầu người dùng chọn một phiên bản và chỉ nêu "
        "các phiên bản đã được công cụ trả về dưới đây:\n"
        f"{choice_text}"
    )


def _usage_metrics(response: Any) -> dict[str, int | None]:
    raw = getattr(response, "usage_metadata", None) or getattr(response, "usageMetadata", None)
    def value(*names: str) -> int | None:
        for name in names:
            item = raw.get(name) if isinstance(raw, dict) else getattr(raw, name, None)
            if isinstance(item, int) and not isinstance(item, bool):
                return item
        return None
    return {
        "input_tokens": value("prompt_token_count", "promptTokenCount", "prompt_tokens"),
        "output_tokens": value("candidates_token_count", "candidatesTokenCount", "completion_token_count"),
        "total_tokens": value("total_token_count", "totalTokenCount", "total_tokens"),
        "thought_tokens": value("thoughts_token_count", "thoughtsTokenCount"),
        "cached_tokens": value("cached_content_token_count", "cachedContentTokenCount"),
    }


def _candidate_content(response: Any) -> Any | None:
    """Return a candidate content block, if this response chunk has one."""
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if content is not None:
            return content
    return None


def _visible_text(response: Any) -> str:
    """Return only non-thinking text from a normal response or SSE chunk."""
    content = _candidate_content(response)
    if content is not None:
        parts: list[str] = []
        for part in getattr(content, "parts", None) or []:
            value = getattr(part, "text", None)
            if isinstance(value, str) and value and not getattr(part, "thought", False):
                parts.append(value)
        return "".join(parts)
    try:
        value = getattr(response, "text", "")
        return value if isinstance(value, str) else ""
    except Exception:
        return ""


class GeminiFunctionCallingRuntime:
    """Native Gemini tool loop with optional visible-text streaming."""

    def __init__(self, *, api_key: str, model: str, max_turns: int = 4) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_turns = max_turns

    def select_subagent(
        self,
        *,
        system_instruction: str,
        user_text: str,
        declaration: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the Manager's single native function-call decision."""
        started = time.perf_counter()
        tool = types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=declaration["name"],
                description=declaration["description"],
                parameters_json_schema=declaration["parameters"],
            )
        ])
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[tool],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY",
                    allowed_function_names=[declaration["name"]],
                ),
            ),
            temperature=0.0,
            max_output_tokens=128,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        )
        logger.info("[MANAGER:START] model=%s prompt_chars=%s", self.model, len(user_text))
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[types.Content(role="user", parts=[types.Part(text=user_text)])],
                config=config,
            )
        except Exception as exc:
            logger.exception("[MANAGER][ERROR] inference failed")
            return {
                "decision": None,
                "usage": {"stage": "manager", "inference_ms": round((time.perf_counter() - started) * 1000, 2)},
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
            }

        candidate = _candidate_content(response)
        calls = [part.function_call for part in getattr(candidate, "parts", []) if getattr(part, "function_call", None)]
        usage = {"stage": "manager", "inference_ms": round((time.perf_counter() - started) * 1000, 2), **_usage_metrics(response)}
        if not calls:
            logger.warning("[MANAGER] model returned no select_subagent call usage=%s", usage)
            return {"decision": None, "usage": usage, "error": {"type": "invalid_manager_response", "message": "No function call returned."}}
        call = calls[0]
        args = dict(call.args or {})
        logger.info("[MANAGER:DONE] tool=%s args=%s | %.1f ms | in=%s out=%s total=%s", call.name, args, usage["inference_ms"], usage["input_tokens"], usage["output_tokens"], usage["total_tokens"])
        return {"decision": args, "usage": usage}

    def run(
        self,
        *,
        system_instruction: str,
        user_text: str,
        declarations: list[dict[str, Any]],
        handlers: dict[str, ToolHandler],
        on_text_chunk: TextChunkCallback | None = None,
        force_function_names: list[str] | None = None,
    ) -> dict[str, Any]:
        request_started = time.perf_counter()
        logger.info("[LLM:START] model=%s tools=%s prompt_chars=%s", self.model, [item["name"] for item in declarations], len(user_text))
        tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=item["name"], description=item["description"], parameters_json_schema=item["parameters"])
            for item in declarations
        ])]
        # Gemma does not support disabling thinking with a zero budget in the
        # same way as every Gemini model.  Match the proven configuration from
        # the original application: request its lowest supported level.
        # The output cap keeps the user-facing answer concise; it does not
        # affect the structured data returned by a tool.
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.15,
            max_output_tokens=256,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        )
        def text_only_config_for(instruction: str) -> Any:
            """Keep a function-response turn structurally pure while directing text."""
            return types.GenerateContentConfig(
                system_instruction=f"{system_instruction}\n\n{instruction}",
                tools=tools,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="NONE"),
                ),
                temperature=0.15,
                max_output_tokens=256,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            )
        def forced_config_for(names: list[str] | None) -> Any | None:
            if not names:
                return None
            return types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=names,
                    )
                ),
                temperature=0.15,
                max_output_tokens=256,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            )

        initial_forced_config = forced_config_for(force_function_names)
        force_next_turn: list[str] | None = None
        force_text_only_next_turn = False
        text_choice_instruction_next_turn: str | None = None
        if force_function_names:
            logger.info("[LLM:FORCE] initial_tools=%s", force_function_names)
        contents: list[Any] = [types.Content(role="user", parts=[types.Part(text=user_text)])]
        trace: list[dict[str, Any]] = []
        usage: list[dict[str, Any]] = []
        stream_timings: dict[str, float | None] = {"time_to_first_visible_ms": None, "time_to_end_visible_ms": None}

        for turn in range(1, self.max_turns + 1):
            turn_forced_names = (
                force_function_names if turn == 1 else force_next_turn
            )
            turn_text_only = turn > 1 and force_text_only_next_turn
            turn_text_instruction = text_choice_instruction_next_turn
            active_config = (
                initial_forced_config
                if turn == 1 and initial_forced_config
                else forced_config_for(turn_forced_names)
                or (text_only_config_for(turn_text_instruction or "Return a concise Vietnamese answer.") if turn_text_only else config)
            )
            force_next_turn = None
            force_text_only_next_turn = False
            text_choice_instruction_next_turn = None
            started_inference = time.perf_counter()
            streaming_turn = on_text_chunk is not None and turn > 1
            visible_parts: list[str] = []
            first_visible_at: float | None = None
            last_visible_at: float | None = None
            streamed_candidate = None
            try:
                if streaming_turn:
                    response = None
                    for chunk in self.client.models.generate_content_stream(model=self.model, contents=contents, config=active_config):
                        response = chunk
                        candidate_content = _candidate_content(chunk)
                        if candidate_content is not None:
                            streamed_candidate = candidate_content
                        text = _visible_text(chunk)
                        if text:
                            now = time.perf_counter()
                            if first_visible_at is None:
                                first_visible_at = now
                                stream_timings["time_to_first_visible_ms"] = round((now - request_started) * 1000, 2)
                                logger.info("[LLM][STREAM] turn=%s first_visible_ms=%.1f", turn, stream_timings["time_to_first_visible_ms"])
                            last_visible_at = now
                            visible_parts.append(text)
                            on_text_chunk(text)
                    if response is None:
                        raise RuntimeError("Gemini stream returned no chunks.")
                else:
                    response = self.client.models.generate_content(model=self.model, contents=contents, config=active_config)
            except Exception as exc:
                logger.exception("[LLM][ERROR] turn=%s inference failed", turn)
                return {"text": "", "tool_trace": trace, "usage": usage, "stream_timings": stream_timings, "llm_error": {"type": exc.__class__.__name__, "message": str(exc)}}

            inference_ms = round((time.perf_counter() - started_inference) * 1000, 2)
            if last_visible_at is not None:
                stream_timings["time_to_end_visible_ms"] = round((last_visible_at - request_started) * 1000, 2)
                logger.info("[LLM][STREAM] turn=%s end_visible_ms=%.1f", turn, stream_timings["time_to_end_visible_ms"])
            turn_usage = {"turn": turn, "streaming": streaming_turn, "inference_ms": inference_ms, **_usage_metrics(response)}
            usage.append(turn_usage)
            logger.info("[LLM:TURN] turn=%s mode=%s | %.1f ms | in=%s out=%s total=%s thought=%s cached=%s", turn, "stream" if streaming_turn else "normal", inference_ms, turn_usage["input_tokens"], turn_usage["output_tokens"], turn_usage["total_tokens"], turn_usage["thought_tokens"], turn_usage["cached_tokens"])

            # The final SSE chunk may carry usage only.  Keep the last content
            # chunk so function calls/text are not lost when that happens.
            candidate = streamed_candidate if streaming_turn else _candidate_content(response)
            if candidate is None:
                return {"text": "Model did not return a valid response.", "tool_trace": trace, "usage": usage, "stream_timings": stream_timings}
            contents.append(candidate)
            calls = [part.function_call for part in candidate.parts if getattr(part, "function_call", None)]
            if not calls:
                final_text = "".join(visible_parts) if visible_parts else (_visible_text(response) or "")
                if streaming_turn and not final_text.strip():
                    # Gemma may account output tokens while exposing no visible
                    # SSE text. Retry once against the same tool-result context.
                    retry_started = time.perf_counter()
                    logger.warning("[LLM][STREAM] turn=%s produced no visible text; retrying once without stream", turn)
                    try:
                        retry = self.client.models.generate_content(model=self.model, contents=contents, config=active_config)
                        retry_text = _visible_text(retry)
                        retry_usage = {
                            "turn": turn,
                            "streaming": False,
                            "retry_after_empty_stream": True,
                            "inference_ms": round((time.perf_counter() - retry_started) * 1000, 2),
                            **_usage_metrics(retry),
                        }
                        usage.append(retry_usage)
                        logger.info("[LLM][STREAM-RETRY] turn=%s inference_ms=%.1f visible=%s", turn, retry_usage["inference_ms"], bool(retry_text.strip()))
                        if retry_text.strip():
                            final_text = retry_text
                            if on_text_chunk is not None:
                                on_text_chunk(retry_text)
                    except Exception:
                        logger.exception("[LLM][STREAM-RETRY] turn=%s failed", turn)
                logger.info("[LLM:DONE] turns=%s total_ms=%.1f", turn, (time.perf_counter() - request_started) * 1000)
                return {"text": final_text, "tool_trace": trace, "usage": usage, "stream_timings": stream_timings}

            parts: list[Any] = []
            called_names = {str(call.name) for call in calls}
            for call in calls:
                name = str(call.name)
                args = dict(call.args or {})
                started_tool = time.perf_counter()
                try:
                    result = handlers[name](args) if name in handlers else {"status": "error", "error": {"code": "unknown_tool"}}
                except Exception as exc:
                    logger.exception("[TOOL][ERROR] turn=%s tool=%s args=%s", turn, name, args)
                    result = {"status": "error", "error": {"code": "tool_exception", "message": str(exc)}}
                latency_ms = round((time.perf_counter() - started_tool) * 1000, 2)
                trace.append({"tool": name, "arguments": args, "status": result.get("status"), "result": result, "latency_ms": latency_ms})
                logger.info("[TOOL:DONE] turn=%s name=%s status=%s | %.1f ms | args=%s | %s", turn, name, result.get("status"), latency_ms, args, _tool_result_summary(result))
                if result.get("status") == "needs_clarification":
                    clarification = result.get("clarification") or {}
                    question = clarification.get("question") if isinstance(clarification, dict) else None
                    text = question if isinstance(question, str) and question.strip() else "Please provide more details."
                    return {"text": text, "tool_trace": trace, "usage": usage, "stream_timings": stream_timings}
                if result.get("status") in {"unavailable", "error"}:
                    error = result.get("error") or {}
                    message = error.get("message") if isinstance(error, dict) else None
                    text = message if isinstance(message, str) and message.strip() else "The data service is unavailable. Please try again later."
                    return {"text": text, "tool_trace": trace, "usage": usage, "stream_timings": stream_timings}
                if (
                    name == "search_music"
                    and "play_music" not in called_names
                    and _requires_play_after_music_search(result)
                ):
                    force_next_turn = ["play_music"]
                    logger.info(
                        "[LLM:FORCE] next_turn_tools=%s reason=unique_exact_music_match",
                        force_next_turn,
                    )
                elif (
                    name == "search_music"
                    and "play_music" not in called_names
                    and _requires_text_choice_after_music_search(result)
                ):
                    force_text_only_next_turn = True
                    text_choice_instruction_next_turn = _music_choice_instruction(result)
                    logger.info(
                        "[LLM:FORCE] next_turn_mode=text_only reason=ambiguous_exact_music_match"
                )
                parts.append(types.Part.from_function_response(name=name, response={"result": result.get("_llm_response", result)}))
            contents.append(types.Content(role="user", parts=parts))

        return {"text": "The tool-call turn limit was reached. Please try again.", "tool_trace": trace, "usage": usage, "stream_timings": stream_timings}
