import main


class _Settings:
    def __init__(
        self,
        *,
        debug_routing: bool = False,
        has_gemini_key: bool = True,
    ) -> None:
        self.debug_routing = debug_routing
        self.has_gemini_key = has_gemini_key


def test_load_workflow_returns_compiled_langgraph_workflow() -> None:
    workflow = main._load_workflow()

    assert workflow is not None
    assert hasattr(workflow, "invoke")


def test_main_input_loop_prompts_user_and_invokes_workflow(monkeypatch, capsys) -> None:
    prompts: list[str] = []

    class FakeWorkflow:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def invoke(self, state: dict) -> dict:
            self.calls.append(state)
            return {"final_response": "Cau tra loi thu nghiem"}

    workflow = FakeWorkflow()
    user_inputs = iter(["Test question"])

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        try:
            return next(user_inputs)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(main, "_load_workflow", lambda: workflow)
    monkeypatch.setattr(main, "load_settings", lambda: object())
    monkeypatch.setattr("builtins.input", fake_input)

    main.main()

    output = capsys.readouterr().out
    assert prompts == ["Bạn: ", "Bạn: "]
    assert len(workflow.calls) == 1
    assert workflow.calls[0]["query"] == "Test question"
    assert workflow.calls[0]["history"] == []
    assert "Bot:\nCau tra loi thu nghiem" in output


def test_main_passes_in_memory_history_to_follow_up_turn(monkeypatch, capsys) -> None:
    class FakeWorkflow:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def invoke(self, state: dict) -> dict:
            self.calls.append({"query": state["query"], "history": list(state["history"])})
            return {"final_response": f"Answer for {state['query']}"}

    workflow = FakeWorkflow()
    user_inputs = iter(["First question", "Second question"])

    def fake_input(prompt: str) -> str:
        try:
            return next(user_inputs)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(main, "_load_workflow", lambda: workflow)
    monkeypatch.setattr(main, "load_settings", lambda: object())
    monkeypatch.setattr("builtins.input", fake_input)

    main.main()

    capsys.readouterr()
    assert workflow.calls == [
        {"query": "First question", "history": []},
        {
            "query": "Second question",
            "history": [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "Answer for First question"},
            ],
        },
    ]


def test_main_streams_workflow_steps_when_available(monkeypatch, capsys) -> None:
    class FakeWorkflow:
        def __init__(self) -> None:
            self.stream_calls: list[dict] = []
            self.invoke_calls = 0

        def stream(self, state: dict, stream_mode: str):
            self.stream_calls.append({"state": state, "stream_mode": stream_mode})
            yield {
                "manager_classify": {
                    "intent": {"reason": "route reason"},
                    "execution_mode": "parallel",
                    "selected_agents": ["weather", "news"],
                    "timings": {"manager": 0.01},
                    "llm_usage": {
                        "manager": {
                            "model": "manager-model",
                            "prompt_tokens": 100,
                            "completion_tokens": 20,
                            "thoughts_tokens": 0,
                            "total_tokens": 120,
                            "cached_tokens": 80,
                            "prefix_cache_hit": True,
                            "cache_hit_ratio": 0.8,
                            "kv_cache_hit": "not_exposed_by_gemini_api",
                            "raw_usage_keys": ["cachedContentTokenCount"],
                        }
                    },
                }
            }
            yield {
                "weather": {
                    "weather_data": {"location": "Ha Noi"},
                    "weather_answer": "Weather answer",
                    "cache_stats": {"weather": {"hits": 0, "misses": 1}},
                    "timings": {"weather": 0.02},
                }
            }
            yield {
                "aggregate": {
                    "final_response": "Final streamed answer",
                    "timings": {"aggregate": 0.03},
                }
            }

        def invoke(self, state: dict) -> dict:
            self.invoke_calls += 1
            return {"final_response": "Should not be used"}

    workflow = FakeWorkflow()
    user_inputs = iter(["Test streaming"])

    def fake_input(prompt: str) -> str:
        try:
            return next(user_inputs)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(main, "_load_workflow", lambda: workflow)
    monkeypatch.setattr(main, "load_settings", lambda: object())
    monkeypatch.setattr("builtins.input", fake_input)

    main.main()

    output = capsys.readouterr().out
    assert workflow.invoke_calls == 0
    assert workflow.stream_calls[0]["stream_mode"] == "updates"
    assert workflow.stream_calls[0]["state"]["query"] == "Test streaming"
    assert "Tiến trình xử lý:" in output
    assert "Manager phân tích câu hỏi" in output
    assert "Weather agent xử lý dữ liệu thời tiết" in output
    assert "Aggregator tổng hợp câu trả lời cuối" in output
    assert "Mode: parallel" in output
    assert "Topics: ['weather', 'news']" in output
    assert "LLM usage [manager]" in output
    assert "thoughts_tokens: 0" in output
    assert "cached_tokens: 80" in output
    assert "prefix_cache_hit: yes" in output
    assert "kv_cache_hit: not_exposed_by_gemini_api" in output
    assert "raw_usage_keys: ['cachedContentTokenCount']" in output
    assert "weather_answer: Weather answer" in output
    assert main.RESPONSE_SEPARATOR in output
    assert "Bot:\nFinal streamed answer" in output


def test_main_prints_active_step_when_stream_fails_before_first_update(monkeypatch, capsys) -> None:
    class FakeWorkflow:
        def stream(self, state: dict, stream_mode: str):
            raise RuntimeError("Gemini timeout")
            yield {}

    workflow = FakeWorkflow()
    user_inputs = iter(["Timeout question"])

    def fake_input(prompt: str) -> str:
        try:
            return next(user_inputs)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(main, "_load_workflow", lambda: workflow)
    monkeypatch.setattr(main, "load_settings", lambda: object())
    monkeypatch.setattr("builtins.input", fake_input)

    main.main()

    output = capsys.readouterr().out
    assert "Manager" in output
    assert "Gemini timeout" in output


def test_print_bot_response_formats_vietnamese_output(capsys) -> None:
    main._print_bot_response("  Dòng một.\nDòng hai.  ")

    output = capsys.readouterr().out
    assert output == (
        f"\n{main.RESPONSE_SEPARATOR}\n"
        "Bot:\nDòng một.\nDòng hai.\n"
        f"{main.RESPONSE_SEPARATOR}\n\n"
    )


def test_format_final_response_has_vietnamese_fallback() -> None:
    assert main._format_final_response("") == "Mình chưa có câu trả lời phù hợp."
    assert main._format_final_response(None) == "Mình chưa có câu trả lời phù hợp."


def test_format_final_response_strips_thought_tags() -> None:
    response = "<thought>hidden reasoning</thought>Thời tiết tại Hà Nội hiện tại có mây."

    assert main._format_final_response(response) == "Thời tiết tại Hà Nội hiện tại có mây."


def test_print_debug_routing_is_optional(capsys) -> None:
    result = {
        "execution_mode": "parallel",
        "selected_agents": ["weather", "news"],
        "cache_stats": {"weather": {"hits": 1}},
        "timings": {"total": 0.12},
    }

    main._print_debug_routing(result, _Settings(debug_routing=False))

    assert capsys.readouterr().out == ""


def test_print_debug_routing_outputs_mode_topics_cache_and_timings(capsys) -> None:
    result = {
        "execution_mode": "parallel",
        "selected_agents": ["weather", "news"],
        "cache_stats": {"weather": {"hits": 1}},
        "timings": {"manager": 0.01, "total": 0.12},
    }

    main._print_debug_routing(result, _Settings(debug_routing=True))

    output = capsys.readouterr().out
    assert "Debug routing:" in output
    assert "- Mode: parallel" in output
    assert "- Topics: ['weather', 'news']" in output
    assert "- Cache: {'weather': {'hits': 1}}" in output
    assert "- Timings: {'manager': 0.01, 'total': 0.12}" in output


def test_main_missing_gemini_api_key_prints_config_error(monkeypatch, capsys) -> None:
    workflow_loads = 0

    def fake_load_workflow() -> None:
        nonlocal workflow_loads
        workflow_loads += 1
        return None

    monkeypatch.setattr(main, "load_settings", lambda: _Settings(has_gemini_key=False))
    monkeypatch.setattr(main, "_load_workflow", fake_load_workflow)

    main.main()

    output = capsys.readouterr().out
    assert workflow_loads == 0
    assert "GEMINI_API_KEY" in output
    assert "Lỗi cấu hình" in output


def test_main_workflow_error_is_printed_and_loop_continues(monkeypatch, capsys) -> None:
    class FakeWorkflow:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def invoke(self, state: dict) -> dict:
            self.calls.append({"query": state["query"], "history": list(state["history"])})
            if state["query"] == "Broken question":
                raise RuntimeError("boom")
            return {"final_response": "Recovered answer"}

    workflow = FakeWorkflow()
    user_inputs = iter(["Broken question", "Working question"])

    def fake_input(prompt: str) -> str:
        try:
            return next(user_inputs)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(main, "_load_workflow", lambda: workflow)
    monkeypatch.setattr(main, "load_settings", lambda: object())
    monkeypatch.setattr("builtins.input", fake_input)

    main.main()

    output = capsys.readouterr().out
    assert workflow.calls == [
        {"query": "Broken question", "history": []},
        {"query": "Working question", "history": []},
    ]
    assert "Lỗi khi xử lý: boom" in output
    assert main.RESPONSE_SEPARATOR in output
    assert "Bot:\nRecovered answer" in output


def test_main_exit_command_stops_without_invoking_workflow(monkeypatch, capsys) -> None:
    prompts: list[str] = []

    class FakeWorkflow:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, state: dict) -> dict:
            self.calls += 1
            return {"final_response": "Should not be used"}

    workflow = FakeWorkflow()

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return "exit"

    monkeypatch.setattr(main, "_load_workflow", lambda: workflow)
    monkeypatch.setattr(main, "load_settings", lambda: object())
    monkeypatch.setattr("builtins.input", fake_input)

    main.main()

    output = capsys.readouterr().out
    assert prompts == ["Bạn: "]
    assert workflow.calls == 0
    assert "Tạm biệt!" in output


def test_main_clear_command_resets_conversation_history(monkeypatch, capsys) -> None:
    prompts: list[str] = []

    class FakeWorkflow:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def invoke(self, state: dict) -> dict:
            self.calls.append({"query": state["query"], "history": list(state["history"])})
            return {"final_response": f"Answer for {state['query']}"}

    workflow = FakeWorkflow()
    user_inputs = iter(["First question", "clear", "Second question"])

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        try:
            return next(user_inputs)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(main, "_load_workflow", lambda: workflow)
    monkeypatch.setattr(main, "load_settings", lambda: object())
    monkeypatch.setattr("builtins.input", fake_input)

    main.main()

    output = capsys.readouterr().out
    assert prompts == ["Bạn: ", "Bạn: ", "Bạn: ", "Bạn: "]
    assert workflow.calls == [
        {"query": "First question", "history": []},
        {"query": "Second question", "history": []},
    ]
    assert "Đã xóa lịch sử hội thoại." in output


def test_main_help_command_prints_examples_without_invoking_workflow(monkeypatch, capsys) -> None:
    prompts: list[str] = []

    class FakeWorkflow:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, state: dict) -> dict:
            self.calls += 1
            return {"final_response": "Should not be used"}

    workflow = FakeWorkflow()
    user_inputs = iter(["help"])

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        try:
            return next(user_inputs)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(main, "_load_workflow", lambda: workflow)
    monkeypatch.setattr(main, "load_settings", lambda: object())
    monkeypatch.setattr("builtins.input", fake_input)

    main.main()

    output = capsys.readouterr().out
    assert prompts == ["Bạn: ", "Bạn: "]
    assert workflow.calls == 0
    assert "Ví dụ câu hỏi:" in output
    assert "Thời tiết Hà Nội" in output
    assert "Tin công nghệ" in output
    assert "Albert Einstein" in output
