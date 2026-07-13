"""Thin terminal entry point for the RAG Manager Agent app."""

from __future__ import annotations

from rag_manager.config import load_settings

RESPONSE_SEPARATOR = "-" * 60


def _load_workflow():
    try:
        from rag_manager.graph import build_workflow
    except ModuleNotFoundError as exc:
        if exc.name == "rag_manager.graph":
            return None
        raise
    return build_workflow()


def _print_help() -> None:
    print(
        "\n".join(
            [
                "Ví dụ câu hỏi:",
                "- Thời tiết Hà Nội hôm nay thế nào?",
                "- Tin công nghệ mới nhất hôm nay là gì?",
                "- Albert Einstein là ai?",
                "- Thời tiết Đà Nẵng và tin du lịch mới nhất?",
                "- template (xem các template phù hợp với dữ liệu gần nhất)",
                "- chọn mẫu 2 hoặc dùng template weather_forecast",
                "- tạo template giao diện tối, card theo từng ngày",
                "",
            ]
        )
    )


def _format_final_response(response: object) -> str:
    if not isinstance(response, str):
        return "Mình chưa có câu trả lời phù hợp."

    from rag_manager.llm.gemini_client import strip_thought_tags

    cleaned_response = strip_thought_tags(response)
    return cleaned_response or "Mình chưa có câu trả lời phù hợp."


def _print_bot_response(response: object) -> None:
    print(f"\n{RESPONSE_SEPARATOR}")
    print(f"Bot:\n{_format_final_response(response)}")
    print(f"{RESPONSE_SEPARATOR}\n")


def _response_from_result(result: dict) -> str:
    final_response = result.get("final_response")
    if isinstance(final_response, str) and final_response.strip():
        return _format_final_response(final_response)

    visualization_output = result.get("visualization_output", {})
    if isinstance(visualization_output, dict):
        message = visualization_output.get("message")
        if isinstance(message, str) and message.strip():
            return message

    return _format_final_response("")


def _print_visualization_output(result: dict) -> None:
    visualization_output = result.get("visualization_output", {})
    if not isinstance(visualization_output, dict):
        return

    html_path = result.get("visualization_html_path") or visualization_output.get("html_path")
    if isinstance(html_path, str) and html_path.strip():
        print(f"Visualization HTML: {html_path}")


def _print_debug_routing(result: dict, settings: object) -> None:
    if not getattr(settings, "debug_routing", False):
        return

    execution_mode = result.get("execution_mode", "unknown")
    selected_agents = result.get("selected_agents", [])
    cache_stats = result.get("cache_stats", {})
    timings = result.get("timings", {})

    print("Debug routing:")
    print(f"- Mode: {execution_mode}")
    print(f"- Topics: {_format_debug_value(selected_agents)}")
    print(f"- Cache: {_format_debug_value(cache_stats)}")
    print(f"- Timings: {_format_debug_value(timings)}")


def _format_debug_value(value: object) -> str:
    if value in ({}, [], (), None, ""):
        return "none"
    return str(value)


def _invoke_workflow_with_trace(workflow: object, state: dict) -> dict:
    print("\nTiến trình xử lý:")
    if hasattr(workflow, "stream"):
        return _stream_workflow_with_trace(workflow, state)

    print("- Bước 1: Chạy workflow LangGraph.")
    result = workflow.invoke(state)
    print("- Hoàn thành workflow.")
    return result


def _stream_workflow_with_trace(workflow: object, state: dict) -> dict:
    result = dict(state)
    current_step = 1
    active_node = "input_router"

    _print_workflow_step_start(current_step, active_node)

    try:
        for chunk in workflow.stream(state, stream_mode="updates"):
            if not isinstance(chunk, dict):
                continue

            for node_name, update in chunk.items():
                node_name = str(node_name)
                print(f"- Bước {current_step}: Hoàn thành - {_workflow_node_label(node_name)}")
                _print_workflow_step_details(node_name, update)
                if isinstance(update, dict):
                    _merge_result_update(result, update)

                for next_node in _next_nodes_after_update(node_name, result):
                    current_step += 1
                    active_node = next_node
                    _print_workflow_step_start(current_step, next_node)
    except Exception:
        print(f"  - Lỗi xảy ra khi đang chạy: {_workflow_node_label(active_node)}")
        raise

    print("- Hoàn thành workflow.")
    return result


def _print_workflow_step_start(step_number: int, node_name: str) -> None:
    print(f"- Bước {step_number}: Bắt đầu - {_workflow_node_label(node_name)}")


def _workflow_node_label(node_name: str) -> str:
    labels = {
        "input_router": "Input router phân loại câu hỏi mới hoặc lệnh visualization.",
        "manager_classify": "Manager phân tích câu hỏi và chọn agent.",
        "weather": "Weather agent xử lý dữ liệu thời tiết.",
        "news": "News agent xử lý dữ liệu tin tức.",
        "wiki": "Wiki agent xử lý dữ liệu Wikipedia.",
        "execute_parallel": "Chạy các agent song song.",
        "plan_sequence": "Chạy các agent tuần tự theo phụ thuộc.",
        "aggregate": "Aggregator tổng hợp câu trả lời cuối.",
        "visualize": "Visualization render HTML nếu có dữ liệu phù hợp.",
    }
    return labels.get(node_name, f"Node {node_name} hoàn thành.")


def _next_nodes_after_update(node_name: str, result: dict) -> list[str]:
    if node_name == "input_router":
        return ["visualize"] if result.get("input_route") == "visualize" else ["manager_classify"]
    if node_name == "manager_classify":
        return [_route_after_manager(result)]
    if node_name in {"weather", "news", "wiki", "execute_parallel", "plan_sequence"}:
        return ["aggregate"]
    if node_name == "aggregate":
        return ["visualize"]
    return []


def _route_after_manager(result: dict) -> str:
    execution_mode = result.get("execution_mode")
    if execution_mode == "parallel":
        return "execute_parallel"
    if execution_mode == "sequential":
        return "plan_sequence"

    selected_agents = result.get("selected_agents", [])
    if isinstance(selected_agents, list) and selected_agents:
        first_agent = selected_agents[0]
        if first_agent in {"weather", "news", "wiki"}:
            return str(first_agent)

    intent = result.get("intent", {})
    if isinstance(intent, dict):
        primary_intent = intent.get("primary_intent")
        if primary_intent in {"weather", "news", "wiki"}:
            return str(primary_intent)

    return "wiki"


def _print_workflow_step_details(node_name: str, update: object) -> None:
    if not isinstance(update, dict):
        print(f"  - Kết quả: {update}")
        return

    if node_name == "manager_classify":
        print(f"  - Mode: {update.get('execution_mode', 'unknown')}")
        print(f"  - Topics: {_format_debug_value(update.get('selected_agents', []))}")
        reason = _manager_reason(update)
        if reason:
            print(f"  - Lý do route: {reason}")
    if node_name == "input_router":
        print(f"  - Route: {update.get('input_route', 'domain')}")

    for key in ("weather_answer", "news_answer", "wiki_answer", "final_response"):
        value = update.get(key)
        if isinstance(value, str) and value.strip():
            print(f"  - {key}: {_short_text(value)}")

    for key in ("weather_data", "news_data", "wiki_data"):
        value = update.get(key)
        if isinstance(value, dict):
            print(f"  - {key}: {_summarize_data(value)}")

    if update.get("errors"):
        print(f"  - Errors: {_format_debug_value(update.get('errors'))}")
    if update.get("cache_stats"):
        print(f"  - Cache: {_format_debug_value(update.get('cache_stats'))}")
    if update.get("timings"):
        print(f"  - Timings: {_format_debug_value(update.get('timings'))}")
    if update.get("llm_usage"):
        _print_llm_usage(update["llm_usage"])
    visualization_output = update.get("visualization_output")
    if isinstance(visualization_output, dict):
        print(f"  - visualization: {_summarize_visualization_output(visualization_output)}")


def _summarize_visualization_output(output: dict) -> str:
    parts = [
        f"ok={output.get('ok', False)}",
        f"mode={output.get('mode', 'unknown')}",
    ]
    template_id = output.get("template_id")
    if isinstance(template_id, str) and template_id:
        parts.append(f"template={template_id}")
    html_path = output.get("html_path")
    if isinstance(html_path, str) and html_path:
        parts.append(f"html_path={html_path}")
    errors = output.get("errors")
    if errors:
        parts.append(f"errors={errors}")
    return ", ".join(parts)


def _manager_reason(update: dict) -> str:
    intent = update.get("intent", {})
    reason = intent.get("reason", "") if isinstance(intent, dict) else ""
    return reason.strip() if isinstance(reason, str) else ""


def _short_text(value: str, limit: int = 180) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 3]}..."


def _summarize_data(value: dict) -> str:
    if "error" in value:
        return f"error={value['error']}"
    summary_keys = [
        key
        for key in ("location", "query", "topic", "title", "total_articles")
        if key in value
    ]
    if summary_keys:
        return ", ".join(f"{key}={value[key]}" for key in summary_keys)
    return f"keys={list(value.keys())}"


def _print_llm_usage(llm_usage: object) -> None:
    if not isinstance(llm_usage, dict):
        return

    for agent_name, usage in llm_usage.items():
        if not isinstance(usage, dict):
            continue
        print(f"  - LLM usage [{agent_name}]:")
        print(f"    model: {_usage_value(usage, 'model')}")
        print(f"    prompt_tokens: {_usage_value(usage, 'prompt_tokens')}")
        print(f"    completion_tokens: {_usage_value(usage, 'completion_tokens')}")
        print(f"    thoughts_tokens: {_usage_value(usage, 'thoughts_tokens')}")
        print(f"    total_tokens: {_usage_value(usage, 'total_tokens')}")
        print(f"    cached_tokens: {_usage_value(usage, 'cached_tokens')}")
        print(f"    prefix_cache_hit: {_yes_no(usage.get('prefix_cache_hit'))}")
        print(f"    cache_hit_ratio: {_usage_value(usage, 'cache_hit_ratio')}")
        print(f"    kv_cache_hit: {usage.get('kv_cache_hit', 'not_exposed_by_gemini_api')}")
        print(f"    raw_usage_keys: {_usage_value(usage, 'raw_usage_keys')}")


def _usage_value(usage: dict, key: str) -> object:
    value = usage.get(key)
    return "unknown" if value is None else value


def _yes_no(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _merge_result_update(result: dict, update: dict) -> None:
    for key, value in update.items():
        if key in {"cache_stats", "context", "timings", "llm_usage"} and isinstance(value, dict):
            existing = result.get(key, {})
            result[key] = {**existing, **value} if isinstance(existing, dict) else value
        elif key == "errors" and isinstance(value, list):
            existing_errors = result.get("errors", [])
            result[key] = [*existing_errors, *value] if isinstance(existing_errors, list) else value
        else:
            result[key] = value


def _session_context_from_result(result: dict) -> dict:
    session_context = {}
    for key in (
        "last_domain_result",
        "available_templates",
        "active_template_id",
        "active_template_path",
        "visualization_html_path",
        "pending_template_state",
        "template_requirements",
        "template_clarification_round",
    ):
        value = result.get(key)
        if key in {"pending_template_state", "template_requirements", "template_clarification_round"}:
            if key in result:
                session_context[key] = value
        elif value not in (None, "", [], {}):
            session_context[key] = value
    return session_context


def _has_required_gemini_config(settings: object) -> bool:
    return bool(getattr(settings, "has_gemini_key", True))


def _print_missing_gemini_key_error() -> None:
    print("Lỗi cấu hình: thiếu GEMINI_API_KEY.")
    print("Hãy tạo file .env hoặc đặt biến môi trường GEMINI_API_KEY rồi chạy lại.")


def main() -> None:
    settings = load_settings()

    print("RAG Manager Agent - Gemini API")
    print("Nhập câu hỏi rồi Enter. Gõ 'help' để xem ví dụ, 'exit' để thoát, 'clear' để xóa lịch sử.\n")

    if not _has_required_gemini_config(settings):
        _print_missing_gemini_key_error()
        return

    workflow = _load_workflow()

    if workflow is None:
        print("Workflow LangGraph chưa được triển khai.")
        print("Hãy hoàn thành các task P2-P9 trước khi chạy app đầy đủ.")
        return

    history: list[dict[str, str]] = []
    session_context: dict = {}

    while True:
        try:
            query = input("Bạn: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nTạm biệt!")
            break

        if not query:
            continue
        if query.lower() in {"exit", "quit"}:
            print("Tạm biệt!")
            break
        if query.lower() == "clear":
            history.clear()
            print("Đã xóa lịch sử hội thoại.\n")
            continue
        if query.lower() in {"help", "?"}:
            _print_help()
            continue

        try:
            result = _invoke_workflow_with_trace(
                workflow,
                {
                    "query": query,
                    "history": list(history),
                    "settings": settings,
                    **session_context,
                }
            )
        except Exception as exc:
            print(f"Lỗi khi xử lý: {exc}\n")
            continue

        final_response = _response_from_result(result)
        _print_bot_response(final_response)
        _print_visualization_output(result)
        _print_debug_routing(result, settings)
        session_context.update(_session_context_from_result(result))

        history.append({"role": "user", "content": query})
        history.append({"role": "assistant", "content": final_response})


if __name__ == "__main__":
    main()
