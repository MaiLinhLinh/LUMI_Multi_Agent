import pytest
from google.genai import types

from rag_manager.agents.manager_structured_schema import ManagerPlanResponse
from rag_manager.llm import gemini_client
from rag_manager.llm.gemini_client import GeminiClient, GeminiRequestError


class _FakeModels:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.kwargs: list[dict[str, object]] = []

    def generate_content_stream(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, tuple):
            return iter(outcome)
        return iter((outcome,))


class _FakeNativeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models


class _FakeResponse:
    text = "ok"


class _FakeError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _client_with_outcomes(outcomes: list[object]) -> tuple[GeminiClient, _FakeModels]:
    models = _FakeModels(outcomes)
    client = object.__new__(GeminiClient)
    client.model = "gemma-4-26b-a4b-it"
    client.client = _FakeNativeClient(models)
    client.last_usage = {}
    client._types = types
    client._call_sequence = 0
    return client, models


def test_gemma_client_uses_native_generate_content_with_minimal_thinking() -> None:
    client, models = _client_with_outcomes([_FakeResponse()])

    result = client.chat_text("system", "user")

    assert result == "ok"
    assert models.calls == 1
    assert models.kwargs[0]["model"] == "gemma-4-26b-a4b-it"
    assert models.kwargs[0]["contents"] == "system\n\nuser"
    assert _config_dict(models.kwargs[0]["config"]) == {
        "temperature": 0.0,
        "maxOutputTokens": gemini_client.MAX_OUTPUT_TOKENS,
        "thinkingConfig": {"thinkingLevel": types.ThinkingLevel.MINIMAL},
    }
    assert client.last_usage["time_to_first_token"] is not None
    assert client.last_usage["time_to_first_visible"] is not None
    assert client.last_usage["time_to_last_visible"] is not None
    assert client.last_usage["visible_generation_duration"] == 0.0
    assert client.last_usage["total_request_time"] is not None


def test_streaming_records_first_token_and_visible_latency_separately(monkeypatch) -> None:
    class Part:
        def __init__(self, text: str, *, thought: bool) -> None:
            self.text = text
            self.thought = thought

    class Content:
        def __init__(self, parts: list[Part]) -> None:
            self.parts = parts

    class Candidate:
        def __init__(self, parts: list[Part]) -> None:
            self.content = Content(parts)

    class Chunk:
        def __init__(
            self,
            text: str,
            *,
            thought: bool = False,
            usage_metadata: dict | None = None,
        ) -> None:
            self.candidates = [Candidate([Part(text, thought=thought)])]
            self.usage_metadata = usage_metadata

    client, _models = _client_with_outcomes(
        [
            (
                Chunk("hidden", thought=True),
                Chunk("Visible "),
                Chunk(
                    "answer",
                    usage_metadata={
                        "promptTokenCount": 10,
                        "candidatesTokenCount": 2,
                        "totalTokenCount": 12,
                    },
                ),
            )
        ]
    )
    timestamps = iter((10.0, 10.2, 10.5, 10.8, 11.0))
    monkeypatch.setattr(gemini_client.time, "perf_counter", lambda: next(timestamps))

    result = client.chat_text("system", "user")

    assert result == "Visible answer"
    assert client.last_usage["time_to_first_token"] == 0.2
    assert client.last_usage["time_to_first_visible"] == 0.5
    assert client.last_usage["time_to_last_visible"] == 0.8
    assert client.last_usage["visible_generation_duration"] == 0.3
    assert client.last_usage["total_request_time"] == 1.0


def test_chat_text_forwards_visible_chunks_without_waiting_for_full_result() -> None:
    class Chunk:
        def __init__(self, text: str) -> None:
            self.text = text

    client, _models = _client_with_outcomes(
        [(Chunk("Xin "), Chunk("chào"))]
    )
    received: list[str] = []

    result = client.chat_text(
        "system",
        "user",
        on_text_chunk=received.append,
    )

    assert received == ["Xin ", "chào"]
    assert result == "Xin chào"


def test_structured_json_uses_system_instruction_and_response_schema() -> None:
    class StructuredResponse:
        text = (
            '{"topics":["weather"],"execution_mode":"single",'
            '"primary_intent":"weather","dependencies":[],'
            '"news_query":"","wiki_topic":""}'
        )

    client, models = _client_with_outcomes([StructuredResponse()])

    result = client.chat_structured_json(
        "manager system prompt",
        '{"query":"Thời tiết Hà Nội"}',
        response_schema=ManagerPlanResponse,
    )

    assert result["topics"] == ["weather"]
    assert models.kwargs[0]["contents"] == '{"query":"Thời tiết Hà Nội"}'
    config = _config_dict(models.kwargs[0]["config"])
    assert config["systemInstruction"] == "manager system prompt"
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] is ManagerPlanResponse
    assert config["thinkingConfig"] == {
        "thinkingLevel": types.ThinkingLevel.MINIMAL
    }


def test_structured_json_rejects_output_outside_schema() -> None:
    class InvalidStructuredResponse:
        text = '{"topics":["unsupported"]}'

    client, _models = _client_with_outcomes([InvalidStructuredResponse()])

    with pytest.raises(GeminiRequestError, match="does not match"):
        client.chat_structured_json(
            "manager system prompt",
            '{"query":"test"}',
            response_schema=ManagerPlanResponse,
        )


def test_gemini_client_retries_transient_error_then_returns_text(monkeypatch) -> None:
    monkeypatch.setattr(gemini_client.time, "sleep", lambda seconds: None)
    client, models = _client_with_outcomes(
        [
            _FakeError("network down", status_code=503),
            _FakeResponse(),
        ]
    )

    result = client.chat_text("system", "user")

    assert result == "ok"
    assert models.calls == 2


def test_gemini_client_strips_thought_tags_from_text_response() -> None:
    class ThoughtResponse:
        text = "<thought>hidden reasoning</thought>Visible answer"

    client, _models = _client_with_outcomes([ThoughtResponse()])

    result = client.chat_text("system", "user")

    assert result == "Visible answer"


def test_gemini_client_skips_thought_parts_when_text_helper_is_unavailable() -> None:
    class ThoughtPart:
        text = "hidden reasoning"
        thought = True

    class AnswerPart:
        text = "Visible answer"
        thought = False

    class Content:
        parts = [ThoughtPart(), AnswerPart()]

    class Candidate:
        content = Content()

    class PartsResponse:
        candidates = [Candidate()]

    client, _models = _client_with_outcomes([PartsResponse()])

    result = client.chat_text("system", "user")

    assert result == "Visible answer"


def test_gemini_client_records_prefix_cache_usage_from_native_response(capsys) -> None:
    class UsageResponse:
        text = "ok"
        usage_metadata = {
            "promptTokenCount": 100,
            "candidatesTokenCount": 20,
            "thoughtsTokenCount": 5,
            "totalTokenCount": 120,
            "cachedContentTokenCount": 75,
        }

    client, _models = _client_with_outcomes([UsageResponse()])

    result = client.chat_text("system", "user")

    assert result == "ok"
    assert client.last_usage["model"] == "gemma-4-26b-a4b-it"
    assert client.last_usage["prompt_tokens"] == 100
    assert client.last_usage["completion_tokens"] == 20
    assert client.last_usage["thoughts_tokens"] == 5
    assert client.last_usage["total_tokens"] == 120
    assert client.last_usage["cached_tokens"] == 75
    assert client.last_usage["prefix_cache_hit"] is True
    assert client.last_usage["cache_hit_ratio"] == 0.75
    assert client.last_usage["saved_tokens_estimated"] == 75
    assert client.last_usage["kv_cache_hit"] == "not_exposed_by_gemini_api"
    terminal_output = capsys.readouterr().out
    assert "[LLM_CACHE][source=gemini_native][call=1]" in terminal_output
    assert "cached_tokens=75" in terminal_output
    assert "cache_hit_ratio=0.7500" in terminal_output
    assert "saved_tokens_estimated=75" in terminal_output


def test_gemini_client_reads_total_cached_tokens_alias() -> None:
    class UsageResponse:
        text = "ok"
        usage_metadata = {
            "prompt_token_count": 100,
            "candidates_token_count": 20,
            "total_token_count": 120,
            "total_cached_tokens": 60,
        }

    client, _models = _client_with_outcomes([UsageResponse()])

    result = client.chat_text("system", "user")

    assert result == "ok"
    assert client.last_usage["cached_tokens"] == 60
    assert client.last_usage["prefix_cache_hit"] is True
    assert client.last_usage["cache_hit_ratio"] == 0.6


def test_gemma_client_does_not_fall_back_to_missing_thinking_config() -> None:
    client, models = _client_with_outcomes(
        [
            _FakeError("thinking_level is not supported", status_code=400),
        ]
    )

    with pytest.raises(GeminiRequestError):
        client.chat_text("system", "user")

    assert models.calls == 1
    assert _config_dict(models.kwargs[0]["config"])["thinkingConfig"] == {
        "thinkingLevel": types.ThinkingLevel.MINIMAL
    }


def test_gemini_client_timeout_error_is_clear_after_retry_limit(monkeypatch) -> None:
    monkeypatch.setattr(gemini_client.time, "sleep", lambda seconds: None)
    client, models = _client_with_outcomes(
        [
            _FakeError("timeout", status_code=504)
            for _ in range(gemini_client.MAX_TRANSIENT_RETRIES + 1)
        ]
    )

    with pytest.raises(GeminiRequestError) as exc_info:
        client.chat_text("system", "user")

    assert models.calls == gemini_client.MAX_TRANSIENT_RETRIES + 1
    assert "qua thoi gian cho" in str(exc_info.value)
    assert "REQUEST_TIMEOUT_SECONDS" in str(exc_info.value)
    assert "status_code=504" in str(exc_info.value)
    assert "exception_type=_FakeError" in str(exc_info.value)
    assert "detail=timeout" in str(exc_info.value)


def test_gemini_client_rate_limit_error_is_clear_after_retry_limit(monkeypatch) -> None:
    monkeypatch.setattr(gemini_client.time, "sleep", lambda seconds: None)
    client, models = _client_with_outcomes(
        [
            _FakeError("rate limited", status_code=429)
            for _ in range(gemini_client.MAX_TRANSIENT_RETRIES + 1)
        ]
    )

    with pytest.raises(GeminiRequestError) as exc_info:
        client.chat_text("system", "user")

    assert models.calls == gemini_client.MAX_TRANSIENT_RETRIES + 1
    assert "gioi han toc do" in str(exc_info.value)
    assert "thu lai" in str(exc_info.value)
    assert "status_code=429" in str(exc_info.value)
    assert "detail=rate limited" in str(exc_info.value)


def test_gemini_client_includes_network_error_details_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(gemini_client.time, "sleep", lambda seconds: None)
    client, _models = _client_with_outcomes(
        [
            _FakeError("Server disconnected; key=AIzaSecretShouldNotAppear", status_code=503)
            for _ in range(gemini_client.MAX_TRANSIENT_RETRIES + 1)
        ]
    )

    with pytest.raises(GeminiRequestError) as exc_info:
        client.chat_text("system", "user")

    message = str(exc_info.value)
    assert "status_code=503" in message
    assert "exception_type=_FakeError" in message
    assert "Server disconnected" in message
    assert "AIzaSecretShouldNotAppear" not in message


def _config_dict(config: object) -> dict:
    return config.model_dump(exclude_none=True, by_alias=True)
