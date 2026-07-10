from rag_manager.agents.manager import classify_intent


class FakeGeminiClient:
    def __init__(self, response: dict) -> None:
        self.response = response

    def chat_json(self, system_prompt: str, user_message: str) -> dict:
        return self.response


def test_weather_only_routing() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["weather"],
            "execution_mode": "single",
            "primary_intent": "weather",
            "dependencies": [],
            "location": "Hà Nội",
            "news_query": "",
            "wiki_topic": "",
            "reason": "Người dùng hỏi thời tiết hiện tại.",
        }
    )

    plan = classify_intent(client, "Thời tiết Hà Nội hôm nay thế nào?")

    assert plan["topics"] == ["weather"]
    assert plan["execution_mode"] == "single"
    assert plan["primary_intent"] == "weather"
    assert plan["location"] == "Hà Nội"


def test_invalid_manager_json_falls_back_to_weather_for_weather_query() -> None:
    client = FakeGeminiClient(
        {
            "error": "invalid_json",
            "message": "Expecting property name enclosed in double quotes",
            "raw": "{topics:['weather']}",
        }
    )

    plan = classify_intent(client, "Thời tiết Hà Nội hôm nay thế nào?")

    assert plan["topics"] == ["weather"]
    assert plan["execution_mode"] == "single"
    assert plan["primary_intent"] == "weather"
    assert plan["location"] == "Hà Nội"
    assert "keyword fallback" in plan["reason"]


def test_news_only_routing() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["news"],
            "execution_mode": "single",
            "primary_intent": "news",
            "dependencies": [],
            "location": "",
            "news_query": "tin công nghệ mới nhất hôm nay",
            "wiki_topic": "",
            "reason": "Người dùng hỏi tin mới.",
        }
    )

    plan = classify_intent(client, "Tin công nghệ mới nhất hôm nay")

    assert plan["topics"] == ["news"]
    assert plan["execution_mode"] == "single"
    assert plan["primary_intent"] == "news"
    assert plan["news_query"] == "tin công nghệ mới nhất hôm nay"


def test_wiki_only_routing() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["wiki"],
            "execution_mode": "single",
            "primary_intent": "wiki",
            "dependencies": [],
            "location": "",
            "news_query": "",
            "wiki_topic": "Albert Einstein",
            "reason": "Người dùng hỏi kiến thức nền.",
        }
    )

    plan = classify_intent(client, "Albert Einstein là ai?")

    assert plan["topics"] == ["wiki"]
    assert plan["execution_mode"] == "single"
    assert plan["primary_intent"] == "wiki"
    assert plan["wiki_topic"] == "Albert Einstein"


def test_parallel_routing_for_independent_topics() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["weather", "news"],
            "execution_mode": "parallel",
            "primary_intent": "weather",
            "dependencies": [],
            "location": "Đà Nẵng",
            "news_query": "tin du lịch Đà Nẵng mới nhất",
            "wiki_topic": "",
            "reason": "Người dùng hỏi hai nhu cầu độc lập.",
        }
    )

    plan = classify_intent(
        client,
        "Thời tiết Đà Nẵng hôm nay và tin du lịch Đà Nẵng mới nhất?",
    )

    assert plan["topics"] == ["weather", "news"]
    assert plan["execution_mode"] == "parallel"
    assert plan["primary_intent"] == "weather"
    assert plan["dependencies"] == []
    assert plan["location"] == "Đà Nẵng"
    assert plan["news_query"] == "tin du lịch Đà Nẵng mới nhất"


def test_sequential_routing_for_dependent_topics() -> None:
    client = FakeGeminiClient(
        {
            "topics": ["wiki", "news"],
            "execution_mode": "sequential",
            "primary_intent": "wiki",
            "dependencies": [
                {
                    "from_topic": "wiki",
                    "to_topic": "news",
                    "reason": "Cần xác định chủ thể trước khi tìm tin mới.",
                }
            ],
            "location": "",
            "news_query": "tin mới về OpenAI",
            "wiki_topic": "OpenAI",
            "reason": "Người dùng hỏi thông tin nền rồi tin mới về cùng chủ thể.",
        }
    )

    plan = classify_intent(
        client,
        "OpenAI là gì và có tin mới gì về OpenAI?",
    )

    assert plan["topics"] == ["wiki", "news"]
    assert plan["execution_mode"] == "sequential"
    assert plan["primary_intent"] == "wiki"
    assert plan["dependencies"] == [
        {
            "from_topic": "wiki",
            "to_topic": "news",
            "reason": "Cần xác định chủ thể trước khi tìm tin mới.",
        }
    ]
    assert plan["wiki_topic"] == "OpenAI"
    assert plan["news_query"] == "tin mới về OpenAI"
