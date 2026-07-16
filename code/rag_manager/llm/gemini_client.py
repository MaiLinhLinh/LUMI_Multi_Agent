"""Gemini native API client wrapper with thinking disabled where supported."""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

from rag_manager.config import Settings

MAX_OUTPUT_TOKENS = 1024
MAX_TRANSIENT_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2
TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


class GeminiRequestError(RuntimeError):
    """Raised when Gemini still fails after bounded retry."""


@dataclass(frozen=True)
class _StreamResult:
    """Fully collected response and timing metadata from one streamed call."""

    text: str
    usage_response: Any
    timings: dict[str, float | None]


class GeminiClient:
    """Thin wrapper around the native Gemini Generate Content API."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = settings.gemini_model
        self.last_usage: dict[str, Any] = {}

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depends on runtime env
            raise GeminiRequestError(
                "Missing dependency: install google-genai to use Gemini native API."
            ) from exc

        self.client = genai.Client(api_key=settings.gemini_api_key)
        self._types = types
        self._call_sequence = 0

    def chat_text(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.0,
    ) -> str:
        """Call Gemini and return the assistant text response."""
        prompt = f"{system_prompt}\n\n{user_message}"
        self._call_sequence += 1
        call_id = self._call_sequence
        _debug_print(
            f"[Gemini][call={call_id}] START model={self.model} "
            f"prompt_chars={len(prompt)} temperature={temperature}"
        )
        try:
            stream_result = self._generate_content_stream_with_retry(
                prompt,
                _generation_configs(self.model, temperature, self._types),
                call_id=call_id,
            )
        except Exception as exc:
            _debug_print(
                f"[Gemini][call={call_id}] ERROR "
                f"type={type(exc).__name__} detail={exc}"
            )
            raise
        self.last_usage = extract_llm_usage_native(
            stream_result.usage_response,
            self.model,
        )
        self.last_usage.update(stream_result.timings)
        print_llm_cache_metrics(
            self.last_usage,
            source="gemini_native",
            call_id=call_id,
        )
        text = strip_thought_tags(stream_result.text)
        _debug_print(f"[Gemini][call={call_id}] RESULT {text}")
        return text

    def chat_json(
        self,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Call Gemini and parse the assistant response as a JSON object."""
        text = self.chat_text(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
        )
        return parse_json_object(text)

    def chat_structured_json(
        self,
        system_prompt: str,
        user_message: str,
        *,
        response_schema: type[Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Call Gemini with an API-constrained JSON response schema."""

        self._call_sequence += 1
        call_id = self._call_sequence
        _debug_print(
            f"[Gemini][call={call_id}] START_STRUCTURED model={self.model} "
            f"input_chars={len(user_message)} temperature={temperature}"
        )
        try:
            stream_result = self._generate_content_stream_with_retry(
                user_message,
                _structured_generation_configs(
                    self.model,
                    system_prompt,
                    response_schema,
                    temperature,
                    self._types,
                ),
                call_id=call_id,
            )
        except Exception as exc:
            _debug_print(
                f"[Gemini][call={call_id}] ERROR "
                f"type={type(exc).__name__} detail={exc}"
            )
            raise

        self.last_usage = extract_llm_usage_native(
            stream_result.usage_response,
            self.model,
        )
        self.last_usage.update(stream_result.timings)
        print_llm_cache_metrics(
            self.last_usage,
            source="gemini_native_structured",
            call_id=call_id,
        )
        text = stream_result.text.strip()
        try:
            parsed = response_schema.model_validate_json(text)
        except Exception as exc:  # Pydantic exception type is schema-owned
            raise GeminiRequestError(
                "Gemini returned output that does not match the response schema."
            ) from exc

        result = parsed.model_dump()
        if not isinstance(result, dict):
            raise GeminiRequestError("Structured response must be a JSON object.")
        _debug_print(f"[Gemini][call={call_id}] STRUCTURED_RESULT {result}")
        return result

    def _generate_content_stream_with_retry(
        self,
        prompt: str,
        configs: list[Any],
        *,
        call_id: int,
    ) -> _StreamResult:
        """Stream Gemini with retry logic, then return one collected response."""
        last_error: Exception | None = None
        logical_started_at = time.perf_counter()

        for config_index, config in enumerate(configs):
            for attempt in range(MAX_TRANSIENT_RETRIES + 1):
                try:
                    _debug_print(
                        f"[Gemini][call={call_id}] HTTP_STREAM_ATTEMPT "
                        f"config={config_index + 1}/{len(configs)} "
                        f"attempt={attempt + 1}/{MAX_TRANSIENT_RETRIES + 1}"
                    )
                    chunks = self.client.models.generate_content_stream(
                        model=self.model,
                        contents=prompt,
                        config=config,
                    )
                    text_parts: list[str] = []
                    usage_response: Any = None
                    last_chunk: Any = None
                    first_token_at: float | None = None
                    first_visible_at: float | None = None
                    last_visible_at: float | None = None

                    for chunk in chunks:
                        received_at = time.perf_counter()
                        last_chunk = chunk
                        token_present, visible_text = _stream_chunk_content(chunk)
                        if token_present and first_token_at is None:
                            first_token_at = received_at
                        if visible_text:
                            if first_visible_at is None:
                                first_visible_at = received_at
                            last_visible_at = received_at
                            text_parts.append(visible_text)
                        if _native_usage_metadata(chunk):
                            usage_response = chunk

                    completed_at = time.perf_counter()
                    return _StreamResult(
                        text="".join(text_parts),
                        usage_response=usage_response or last_chunk,
                        timings=_stream_timings(
                            started_at=logical_started_at,
                            first_token_at=first_token_at,
                            first_visible_at=first_visible_at,
                            last_visible_at=last_visible_at,
                            completed_at=completed_at,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - SDK exception classes vary by version
                    last_error = exc
                    _debug_print(
                        f"[Gemini][call={call_id}] HTTP_ERROR "
                        f"attempt={attempt + 1} type={type(exc).__name__} detail={exc}"
                    )

                    if _is_unsupported_thinking_config_error(exc) and config_index + 1 < len(configs):
                        break

                    if not _is_transient_error(exc):
                        raise GeminiRequestError(str(exc)) from exc

                    if attempt >= MAX_TRANSIENT_RETRIES:
                        raise GeminiRequestError(_retry_failure_message(exc)) from exc

                    time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))

        raise GeminiRequestError(_retry_failure_message(last_error)) from last_error


def _debug_print(message: str) -> None:
    """Print diagnostics while preserving Vietnamese characters."""

    text = str(message)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            raise
        buffer.write((text + "\n").encode("utf-8"))
        buffer.flush()


def _generation_configs(model: str, temperature: float, types: Any) -> list[Any]:
    base_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }

    if _uses_thinking_level(model):
        return [
            types.GenerateContentConfig(
                **base_kwargs,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            )
        ]

    return [
        types.GenerateContentConfig(
            **base_kwargs,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
        types.GenerateContentConfig(
            **base_kwargs,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        ),
        types.GenerateContentConfig(**base_kwargs),
    ]


def _structured_generation_configs(
    model: str,
    system_prompt: str,
    response_schema: type[Any],
    temperature: float,
    types: Any,
) -> list[Any]:
    """Build Structured Output configs while preserving model thinking support."""

    base_kwargs: dict[str, Any] = {
        "system_instruction": system_prompt,
        "temperature": temperature,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "response_mime_type": "application/json",
        "response_schema": response_schema,
    }

    if _uses_thinking_level(model):
        return [
            types.GenerateContentConfig(
                **base_kwargs,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            )
        ]

    return [
        types.GenerateContentConfig(
            **base_kwargs,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
        types.GenerateContentConfig(
            **base_kwargs,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        ),
        types.GenerateContentConfig(**base_kwargs),
    ]


def _uses_thinking_level(model: str) -> bool:
    normalized = model.lower()
    return (
        normalized.startswith("gemma")
        or normalized.startswith("gemini-3")
        or normalized.startswith("gemini-3.")
    )


def _retry_failure_message(error: Exception | None) -> str:
    status_code = _status_code(error)
    diagnostic = _error_diagnostic(error)
    if status_code == 429:
        return (
            "Loi Gemini: API dang bi gioi han toc do hoac qua tai sau khi thu lai. "
            f"Vui long cho mot luc roi thu lai. {diagnostic}"
        )
    if status_code in {408, 504}:
        return (
            "Loi Gemini: yeu cau bi qua thoi gian cho sau khi thu lai. "
            f"Hay kiem tra mang hoac tang REQUEST_TIMEOUT_SECONDS. {diagnostic}"
        )
    if status_code in {500, 502, 503}:
        return (
            "Loi Gemini: may chu Gemini tra loi tam thoi sau khi thu lai. "
            f"Vui long thu lai sau. {diagnostic}"
        )
    return f"Loi Gemini: request that bai sau khi thu lai. {diagnostic}"


def _error_diagnostic(error: Exception | None) -> str:
    """Return actionable error details without exposing credentials."""

    if error is None:
        return "[status_code=unknown; exception_type=unknown; detail=unknown]"
    status = _status_code(error)
    status_text = str(status) if status is not None else "unknown"
    exception_type = type(error).__name__
    detail = str(error).strip().replace("\r", " ").replace("\n", " ")
    detail = re.sub(r"(?i)(AIza)[A-Za-z0-9_-]+", r"\1***REDACTED***", detail)
    if len(detail) > 300:
        detail = detail[:297] + "..."
    return (
        f"[status_code={status_text}; exception_type={exception_type}; "
        f"detail={detail or 'empty'}]"
    )


def _is_transient_error(error: Exception) -> bool:
    status_code = _status_code(error)
    if status_code in TRANSIENT_STATUS_CODES:
        return True

    name = error.__class__.__name__.lower()
    return any(marker in name for marker in ("timeout", "connection", "servererror", "ratelimit"))


def _is_unsupported_thinking_config_error(error: Exception) -> bool:
    if _status_code(error) not in {400, 404, None}:
        return False

    message = str(error).lower()
    return "thinking" in message and any(
        marker in message
        for marker in (
            "unsupported",
            "unknown",
            "unrecognized",
            "invalid",
            "not supported",
            "field",
        )
    )


def _status_code(error: Exception | None) -> int | None:
    if error is None:
        return None

    for attr in ("status_code", "code"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value

    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def strip_thought_tags(text: str) -> str:
    """Remove model reasoning tags that must never be shown to users."""
    cleaned = re.sub(r"<thought\b[^>]*>.*?</thought>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"</?thought\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _stream_chunk_content(chunk: Any) -> tuple[bool, str]:
    """Return whether a chunk contains tokens and its non-thinking text."""

    saw_parts = False
    token_present = False
    visible_parts: list[str] = []
    for candidate in getattr(chunk, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            saw_parts = True
            part_text = getattr(part, "text", None)
            if not isinstance(part_text, str) or not part_text:
                continue
            token_present = True
            if not getattr(part, "thought", False):
                visible_parts.append(part_text)

    if saw_parts:
        return token_present, "".join(visible_parts)

    text = getattr(chunk, "text", None)
    if isinstance(text, str) and text:
        return True, text
    return False, ""


def _native_usage_metadata(response: Any) -> dict[str, Any]:
    """Read native usage metadata from either SDK naming convention."""

    usage_metadata = _object_to_dict(getattr(response, "usage_metadata", None))
    if not usage_metadata:
        usage_metadata = _object_to_dict(getattr(response, "usageMetadata", None))
    return usage_metadata


def _stream_timings(
    *,
    started_at: float,
    first_token_at: float | None,
    first_visible_at: float | None,
    last_visible_at: float | None,
    completed_at: float,
) -> dict[str, float | None]:
    """Build stream latency measurements in seconds from request start."""

    return {
        "time_to_first_token": _elapsed_timestamp(started_at, first_token_at),
        "time_to_first_visible": _elapsed_timestamp(started_at, first_visible_at),
        "time_to_last_visible": _elapsed_timestamp(started_at, last_visible_at),
        "visible_generation_duration": _between_timestamps(
            first_visible_at,
            last_visible_at,
        ),
        "total_request_time": round(max(0.0, completed_at - started_at), 6),
    }


def _elapsed_timestamp(started_at: float, timestamp: float | None) -> float | None:
    if timestamp is None:
        return None
    return round(max(0.0, timestamp - started_at), 6)


def _between_timestamps(
    started_at: float | None,
    completed_at: float | None,
) -> float | None:
    if started_at is None or completed_at is None:
        return None
    return round(max(0.0, completed_at - started_at), 6)


def extract_llm_usage_native(response: Any, model: str) -> dict[str, Any]:
    """Extract token/cache metadata from native Gemini API response."""
    usage_metadata = _native_usage_metadata(response)

    prompt_tokens = _int_value(
        usage_metadata.get("promptTokenCount"),
        usage_metadata.get("prompt_token_count"),
        usage_metadata.get("prompt_tokens"),
    )

    completion_tokens = _int_value(
        usage_metadata.get("candidatesTokenCount"),
        usage_metadata.get("candidates_token_count"),
        usage_metadata.get("completion_token_count"),
        usage_metadata.get("completion_tokens"),
    )

    total_tokens = _int_value(
        usage_metadata.get("totalTokenCount"),
        usage_metadata.get("total_token_count"),
        usage_metadata.get("total_tokens"),
    )

    thoughts_tokens = _int_value(
        usage_metadata.get("thoughtsTokenCount"),
        usage_metadata.get("thoughts_token_count"),
        usage_metadata.get("thought_tokens"),
    )

    cached_tokens = _cached_token_count_native(usage_metadata)
    cache_hit_ratio = None
    if prompt_tokens and cached_tokens is not None and prompt_tokens > 0:
        cache_hit_ratio = round(cached_tokens / prompt_tokens, 4)

    return {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "thoughts_tokens": thoughts_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "prefix_cache_hit": bool(cached_tokens and cached_tokens > 0),
        "cache_hit_ratio": cache_hit_ratio,
        "saved_tokens_estimated": cached_tokens,
        "kv_cache_hit": "not_exposed_by_gemini_api",
        "raw_usage_keys": sorted(usage_metadata.keys()),
    }


def print_llm_cache_metrics(
    usage: dict[str, Any],
    *,
    source: str,
    call_id: int | str,
) -> None:
    """Print API-provided cache usage for one successful logical LLM call."""

    cached_tokens = usage.get("cached_tokens")
    cache_hit_ratio = usage.get("cache_hit_ratio")
    saved_tokens = usage.get("saved_tokens_estimated")
    _debug_print(
        f"[LLM_CACHE][source={source}][call={call_id}] "
        f"cached_tokens={_cache_metric_value(cached_tokens)} "
        f"cache_hit_ratio={_cache_ratio_value(cache_hit_ratio)} "
        f"saved_tokens_estimated={_cache_metric_value(saved_tokens)}"
    )


def _cache_metric_value(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    return str(value)


def _cache_ratio_value(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    return f"{value:.4f}"


def _object_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dumped if isinstance(dumped, dict) else {}
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key))
    }


def _int_value(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int):
            return value
    return None


def _cached_token_count_native(usage_metadata: dict[str, Any]) -> int | None:
    cached_tokens = _int_value(
        usage_metadata.get("cachedContentTokenCount"),
        usage_metadata.get("cached_content_token_count"),
        usage_metadata.get("cached_token_count"),
        usage_metadata.get("totalCachedTokens"),
        usage_metadata.get("total_cached_tokens"),
    )
    if cached_tokens is not None:
        return cached_tokens

    cache_details = usage_metadata.get("cacheTokensDetails")
    if cache_details is None:
        cache_details = usage_metadata.get("cache_tokens_details")

    if isinstance(cache_details, list):
        total = 0
        found = False
        for detail in cache_details:
            detail_dict = _object_to_dict(detail)
            token_count = _int_value(
                detail_dict.get("tokenCount"),
                detail_dict.get("token_count"),
            )
            if token_count is not None:
                total += token_count
                found = True
        if found:
            return total

    return None


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from plain text or fenced JSON output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return {
            "error": "invalid_json",
            "message": str(exc),
            "raw": text,
        }

    if not isinstance(parsed, dict):
        return {
            "error": "json_not_object",
            "message": "Expected a JSON object.",
            "raw": text,
        }

    return parsed
