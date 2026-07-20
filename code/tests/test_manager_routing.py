import json

import pytest

from rag_manager.agents.manager import classify_intent
from rag_manager.agents.manager_structured_schema import ManagerPlanResponse
from rag_manager.llm.gemini_client import GeminiRequestError
from rag_manager.llm.prompts import MANAGER_SYSTEM_PROMPT


class FakeGeminiClient:
    def __init__(self, response: dict | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.system_prompt = ""
        self.user_message = ""
        self.response_schema = None

    def chat_structured_json(
        self,
        system_prompt: str,
        user_message: str,
        *,
        response_schema: type,
        temperature: float = 0.0,
    ) -> dict:
        self.system_prompt = system_prompt
        self.user_message = user_message
        self.response_schema = response_schema
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return response_schema.model_validate(self.response).model_dump()


def test_manager_routes_weather_with_six_field_structured_output() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["weather"],
            "execution_mode": "single",
            "primary_intent": "weather",
            "dependencies": [],
            "news_query": "",
            "wiki_topic": "",
        }
    )

    plan = classify_intent(client, "Thời tiết Hà Nội hôm nay thế nào?")

    assert plan == {
        "topics": ["weather"],
        "execution_mode": "single",
        "primary_intent": "weather",
        "dependencies": [],
        "news_query": "",
        "wiki_topic": "",
    }
    assert set(plan) == {
        "topics",
        "execution_mode",
        "primary_intent",
        "dependencies",
        "news_query",
        "wiki_topic",
    }
    assert client.response_schema is ManagerPlanResponse


def test_manager_sends_current_query_with_empty_relevant_history() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["wiki"],
            "execution_mode": "single",
            "primary_intent": "wiki",
            "dependencies": [],
            "news_query": "",
            "wiki_topic": "Albert Einstein",
        }
    )

    classify_intent(client, "Albert Einstein là ai?")

    assert json.loads(client.user_message) == {
        "query": "Albert Einstein là ai?",
        "relevant_history": [],
    }


def test_manager_routes_music_without_inventing_music_metadata() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["music"],
            "execution_mode": "single",
            "primary_intent": "music",
            "dependencies": [],
            "news_query": "",
            "wiki_topic": "",
        }
    )

    plan = classify_intent(client, "Bật nhạc Sơn Tùng")

    assert plan["topics"] == ["music"]
    assert plan["primary_intent"] == "music"
    assert plan["news_query"] == ""
    assert plan["wiki_topic"] == ""
    assert "YouTube URL" in MANAGER_SYSTEM_PROMPT
    assert "artist biographies" in MANAGER_SYSTEM_PROMPT


def test_manager_sends_relevant_history_for_short_weather_follow_up() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["weather"],
            "execution_mode": "single",
            "primary_intent": "weather",
            "dependencies": [],
            "news_query": "",
            "wiki_topic": "",
        }
    )

    classify_intent(
        client,
        "hôm nay",
        history=[
            {"role": "user", "content": "Thời tiết Hà Nội thế nào?"},
            {
                "role": "assistant",
                "content": "Bạn muốn biết thời tiết Hà Nội vào thời điểm nào?",
            },
            {"role": "user", "content": "hôm nay"},
        ],
    )

    assert json.loads(client.user_message) == {
        "query": "hôm nay",
        "relevant_history": [
            {"role": "user", "content": "Thời tiết Hà Nội thế nào?"},
            {
                "role": "assistant",
                "content": "Bạn muốn biết thời tiết Hà Nội vào thời điểm nào?",
            },
        ],
    }


def test_manager_keeps_schema_constrained_dependency_shape() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["wiki", "news"],
            "execution_mode": "sequential",
            "primary_intent": "wiki",
            "dependencies": [
                {
                    "from_topic": "wiki",
                    "to_topic": "news",
                }
            ],
            "news_query": "tin mới về OpenAI",
            "wiki_topic": "OpenAI",
        }
    )

    plan = classify_intent(client, "OpenAI là gì và có tin mới gì về OpenAI?")

    assert plan["dependencies"] == [
        {"from_topic": "wiki", "to_topic": "news"}
    ]


def test_manager_does_not_fallback_when_structured_request_fails() -> None:
    client = FakeGeminiClient(error=GeminiRequestError("structured request failed"))

    with pytest.raises(GeminiRequestError, match="structured request failed"):
        classify_intent(client, "Tin công nghệ mới nhất")


def test_manager_response_schema_has_exactly_six_required_fields() -> None:
    schema = ManagerPlanResponse.model_json_schema()
    expected_fields = {
        "topics",
        "execution_mode",
        "primary_intent",
        "dependencies",
        "news_query",
        "wiki_topic",
    }

    assert set(schema["properties"]) == expected_fields
    assert set(schema["required"]) == expected_fields
