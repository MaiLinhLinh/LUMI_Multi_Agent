"""Gemini native API client wrapper with thinking disabled where supported."""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

from rag_manager.config import Settings

MAX_OUTPUT_TOKENS = 1024
MAX_TRANSIENT_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2
TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


class GeminiRequestError(RuntimeError):
    """Raised when Gemini still fails after bounded retry."""


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
            response = self._generate_content_with_retry(
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
        self.last_usage = extract_llm_usage_native(response, self.model)
        text = strip_thought_tags(_response_text(response))
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

    def _generate_content_with_retry(
        self,
        prompt: str,
        configs: list[Any],
        *,
        call_id: int,
    ) -> Any:
        """Call Gemini with retry logic and thinking-config fallback."""
        last_error: Exception | None = None

        for config_index, config in enumerate(configs):
            for attempt in range(MAX_TRANSIENT_RETRIES + 1):
                try:
                    _debug_print(
                        f"[Gemini][call={call_id}] HTTP_ATTEMPT "
                        f"config={config_index + 1}/{len(configs)} "
                        f"attempt={attempt + 1}/{MAX_TRANSIENT_RETRIES + 1}"
                    )
                    return self.client.models.generate_content(
                        model=self.model,
                        contents=prompt,
                        config=config,
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


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text

    parts = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            if getattr(part, "thought", False):
                continue
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str):
                parts.append(part_text)

    return "".join(parts)


def extract_llm_usage_native(response: Any, model: str) -> dict[str, Any]:
    """Extract token/cache metadata from native Gemini API response."""
    usage_metadata = _object_to_dict(getattr(response, "usage_metadata", None))
    if not usage_metadata:
        usage_metadata = _object_to_dict(getattr(response, "usageMetadata", None))

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
        "kv_cache_hit": "not_exposed_by_gemini_api",
        "raw_usage_keys": sorted(usage_metadata.keys()),
    }


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
