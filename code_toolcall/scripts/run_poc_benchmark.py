"""Run the five POC scenarios with ten wording variants each.

This script calls the compiled LangGraph directly, while preserving session state
between scenario 1 and its paired follow-up scenario 2.  It does not write to
Redis or ChromaDB; only normal Gemini/Ollama retrieval requests are made.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rag_manager.config import load_settings
from rag_manager.graph import build_workflow


OUTPUT = Path("benchmark_results") / f"poc_benchmark_{datetime.now():%Y%m%d_%H%M%S}.json"
REQUEST_DELAY_SECONDS = float(os.getenv("POC_BENCHMARK_DELAY_SECONDS", "12"))

WEATHER_TOMORROW = [
    "Thời tiết Hà Nội ngày mai thế nào?",
    "Dự báo thời tiết Hà Nội ngày mai ra sao?",
    "Ngày mai ở Hà Nội có mưa không?",
    "Cho tôi xem dự báo Hà Nội vào ngày mai.",
    "Thời tiết ngày mai tại Hà Nội như thế nào?",
    "Mai ở Hà Nội trời thế nào?",
    "Hà Nội ngày mai có nóng không?",
    "Xem giúp tôi dự báo thời tiết Hà Nội cho ngày mai.",
    "Ngày mai Hà Nội nhiệt độ khoảng bao nhiêu?",
    "Dự báo cho Hà Nội vào ngày mai nhé.",
]
WEEK_FOLLOW_UP = [
    "Thế cả tuần thì sao?",
    "Cả tuần tới thì sao?",
    "Cho tôi xem dự báo 7 ngày tới.",
    "Vậy tình hình cả tuần tới ra sao?",
    "Còn dự báo cho cả tuần thì thế nào?",
    "Xem tiếp thời tiết cả tuần nhé.",
    "Thế 7 ngày tới có gì đáng chú ý?",
    "Cả tuần tới thời tiết thay đổi thế nào?",
    "Cho tôi dự báo trong tuần tới.",
    "Vậy dự báo dài ngày thì sao?",
]
HOURLY_RAIN = [
    "10 giờ tối nay ở Hà Nội có mưa không?",
    "Lúc 22 giờ tối nay Hà Nội có mưa không?",
    "Dự báo Hà Nội vào 22:00 tối nay thế nào?",
    "Khoảng 10 giờ đêm nay ở Hà Nội trời có mưa chứ?",
    "Tối nay lúc 22 giờ Hà Nội có mưa không?",
    "Cho tôi biết thời tiết Hà Nội lúc 10 giờ tối nay.",
    "22 giờ hôm nay tại Hà Nội có mưa không?",
    "Thời tiết ở Hà Nội vào 10 giờ đêm nay ra sao?",
    "Vào lúc 22:00 hôm nay, Hà Nội có mưa không?",
    "Kiểm tra giúp tôi mưa ở Hà Nội lúc 10 giờ tối nay.",
]
EXACT_MUSIC = [
    "Bật bài Đừng Làm Trái Tim Anh Đau của Sơn Tùng M-TP.",
    "Mở cho tôi bài Đừng Làm Trái Tim Anh Đau của Sơn Tùng M-TP.",
    "Phát bài Đừng Làm Trái Tim Anh Đau, ca sĩ Sơn Tùng M-TP.",
    "Cho mình nghe Đừng Làm Trái Tim Anh Đau của Sơn Tùng M-TP.",
    "Bật giúp tôi ca khúc Đừng Làm Trái Tim Anh Đau của Sơn Tùng M-TP.",
    "Mở bài Đừng Làm Trái Tim Anh Đau do Sơn Tùng M-TP hát.",
    "Tôi muốn nghe bài Đừng Làm Trái Tim Anh Đau của Sơn Tùng M-TP.",
    "Phát Đừng Làm Trái Tim Anh Đau của Sơn Tùng M-TP đi.",
    "Hãy bật bài Đừng Làm Trái Tim Anh Đau của Sơn Tùng M-TP.",
    "Mở nhạc Đừng Làm Trái Tim Anh Đau - Sơn Tùng M-TP.",
]
MUSIC_DISCOVERY = [
    "Mở một bài nhạc buồn.",
    "Gợi ý cho tôi một bài nhạc buồn.",
    "Tìm giúp tôi một ca khúc tâm trạng.",
    "Tôi muốn nghe nhạc buồn.",
    "Có bài nào buồn để nghe không?",
    "Mở cho tôi một bài hát có tâm trạng buồn.",
    "Tìm một bài nhạc nhẹ nhàng, buồn nhé.",
    "Gợi ý nhạc buồn cho tôi.",
    "Cho tôi nghe một bài phù hợp lúc buồn.",
    "Tìm nhạc buồn trong kho bài hát.",
]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _median(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return round(statistics.median(usable), 2) if usable else None


def _percentile_95(values: list[float | None]) -> float | None:
    usable = sorted(value for value in values if value is not None)
    if not usable:
        return None
    index = round(0.95 * (len(usable) - 1))
    return round(usable[index], 2)


def _tool(trace: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in trace if item.get("tool") == name), None)


def _usage_totals(items: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    inputs = [item.get("input_tokens") for item in items if isinstance(item.get("input_tokens"), int)]
    outputs = [item.get("output_tokens") for item in items if isinstance(item.get("output_tokens"), int)]
    return (sum(inputs) if inputs else None, sum(outputs) if outputs else None)


def _grade(case: str, result: dict[str, Any]) -> tuple[bool, bool, str]:
    agent = result.get("selected_agent")
    trace = result.get("tool_trace", [])
    get_weather = _tool(trace, "get_weather")
    search = _tool(trace, "search_music")
    play = _tool(trace, "play_music")
    payload = result.get("visualization_payload", {})
    template = payload.get("template_id") if isinstance(payload, dict) else None
    ui_type = payload.get("ui_type") if isinstance(payload, dict) else None
    answer_ok = bool(str(result.get("final_answer", "")).strip())

    if case == "weather_tomorrow":
        args = get_weather.get("arguments", {}) if get_weather else {}
        # A one-day question may legitimately use the specialised rain or
        # temperature view while still returning the requested daily forecast.
        tool_ok = bool(get_weather) and args.get("request_type") in {"forecast", "rain", "temperature"} and int(args.get("days", 1) or 1) == 1
        success = agent == "weather" and tool_ok and template == "weather_single_day" and answer_ok
        return success, tool_ok, f"agent={agent}, template={template}"
    if case == "weather_week_followup":
        args = get_weather.get("arguments", {}) if get_weather else {}
        tool_ok = bool(get_weather) and args.get("request_type") == "forecast" and int(args.get("days", 0) or 0) >= 7 and bool(args.get("location_text"))
        success = agent == "weather" and tool_ok and template == "weather_forecast" and answer_ok
        return success, tool_ok, f"agent={agent}, days={args.get('days')}, template={template}"
    if case == "weather_hourly":
        args = get_weather.get("arguments", {}) if get_weather else {}
        tool_ok = bool(get_weather) and args.get("request_type") == "hourly" and bool(args.get("time_text"))
        success = agent == "weather" and tool_ok and template == "weather_basic" and answer_ok
        return success, tool_ok, f"agent={agent}, time={args.get('time_text')}, template={template}"
    if case == "music_exact":
        search_data = (search or {}).get("result", {}).get("data", {})
        found = isinstance(search_data, dict) and search_data.get("status") == "found"
        not_found = isinstance(search_data, dict) and search_data.get("status") == "not_found"
        tool_ok = bool(search) and ((found and bool(play)) or (not_found and not play))
        render_ok = (found and ui_type == "youtube_player") or (not_found and ui_type != "youtube_player")
        success = agent == "music" and tool_ok and render_ok and answer_ok
        return success, tool_ok, f"agent={agent}, search={search_data.get('status') if isinstance(search_data, dict) else None}, played={bool(play)}"
    if case == "music_discovery":
        tool_ok = bool(search) and not play
        success = agent == "music" and tool_ok and ui_type != "youtube_player" and answer_ok
        return success, tool_ok, f"agent={agent}, searched={bool(search)}, played={bool(play)}"
    raise ValueError(case)


def _run(workflow: Any, query: str, session: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    first_text_at: float | None = None

    def callback(_: str, text: str) -> None:
        nonlocal first_text_at
        if text and first_text_at is None:
            first_text_at = time.perf_counter()

    history = session["history"]
    result = workflow.invoke({
        "query": query,
        "history": history,
        "weather_context": session["weather_context"],
        "music_session": session["music_session"],
        "session_id": session["session_id"],
        "tool_trace": [],
        "response_stream_callback": callback,
    })
    total_ms = (time.perf_counter() - started) * 1000
    answer = str(result.get("final_answer") or "")
    history.append({"role": "user", "content": query})
    history.append({"role": "assistant", "content": answer, "domain": result.get("selected_agent", "")})
    if isinstance(result.get("weather_context"), dict) and result["weather_context"].get("last_location_id"):
        session["weather_context"] = result["weather_context"]
    if isinstance(result.get("music_session"), dict):
        session["music_session"] = result["music_session"]
    record = {
        "query": query,
        "agent": result.get("selected_agent"),
        "answer": answer,
        "total_latency_ms": round(total_ms, 2),
        "backend_e2e_ttft_ms": round((first_text_at - started) * 1000, 2) if first_text_at else None,
        "timings": result.get("timings", {}),
        "llm_usage": result.get("llm_usage", []),
        "tool_trace": result.get("tool_trace", []),
        "visualization": result.get("visualization_payload", {}),
    }
    return result, record


def _raise_if_llm_failed(result: dict[str, Any]) -> None:
    agent_result = result.get("agent_result")
    if not isinstance(agent_result, dict):
        raise RuntimeError("Workflow did not return an agent result.")
    error = agent_result.get("llm_error")
    if isinstance(error, dict):
        raise RuntimeError(f"LLM request failed: {error.get('type')}: {error.get('message')}")
    if result.get("selected_agent") == "error":
        raise RuntimeError(f"Manager request failed: {result.get('manager_decision')}")


def _new_session(label: str) -> dict[str, Any]:
    return {"session_id": f"poc-{label}", "history": [], "weather_context": {}, "music_session": {}}


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "requests": len(rows),
        "successes": sum(bool(row["functional_success"]) for row in rows),
        "success_rate_percent": round(100 * sum(bool(row["functional_success"]) for row in rows) / len(rows), 1),
        "tool_call_correct": sum(bool(row["tool_call_correct"]) for row in rows),
        "tool_call_correct_rate_percent": round(100 * sum(bool(row["tool_call_correct"]) for row in rows) / len(rows), 1),
        "total_latency_p50_ms": _median([_number(row["total_latency_ms"]) for row in rows]),
        "total_latency_p95_ms": _percentile_95([_number(row["total_latency_ms"]) for row in rows]),
        "backend_e2e_ttft_p50_ms": _median([_number(row["backend_e2e_ttft_ms"]) for row in rows]),
        "input_tokens_p50": _median([_number(row["input_tokens"]) for row in rows]),
        "output_tokens_p50": _median([_number(row["output_tokens"]) for row in rows]),
    }


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("lumi.toolcall").setLevel(logging.WARNING)
    settings = load_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is missing from .env")
    workflow = build_workflow(settings)
    cases: dict[str, list[dict[str, Any]]] = {
        "weather_tomorrow": [],
        "weather_week_followup": [],
        "weather_hourly": [],
        "music_exact": [],
        "music_discovery": [],
    }

    # Paired requests make each follow-up a genuine history/context test.
    for index, (first_query, follow_up) in enumerate(zip(WEATHER_TOMORROW, WEEK_FOLLOW_UP), start=1):
        session = _new_session(f"weather-{index}")
        for case, query in (("weather_tomorrow", first_query), ("weather_week_followup", follow_up)):
            result, row = _run(workflow, query, session)
            _raise_if_llm_failed(result)
            success, tool_ok, note = _grade(case, result)
            input_tokens, output_tokens = _usage_totals(row["llm_usage"])
            row.update({"case": case, "functional_success": success, "tool_call_correct": tool_ok, "input_tokens": input_tokens, "output_tokens": output_tokens, "note": note})
            cases[case].append(row)
            print(f"[{case}] {index:02d}/10 success={success} tool={tool_ok} latency={row['total_latency_ms']:.0f}ms")
            time.sleep(REQUEST_DELAY_SECONDS)

    for case, queries in (("weather_hourly", HOURLY_RAIN), ("music_exact", EXACT_MUSIC), ("music_discovery", MUSIC_DISCOVERY)):
        for index, query in enumerate(queries, start=1):
            result, row = _run(workflow, query, _new_session(f"{case}-{index}"))
            _raise_if_llm_failed(result)
            success, tool_ok, note = _grade(case, result)
            input_tokens, output_tokens = _usage_totals(row["llm_usage"])
            row.update({"case": case, "functional_success": success, "tool_call_correct": tool_ok, "input_tokens": input_tokens, "output_tokens": output_tokens, "note": note})
            cases[case].append(row)
            print(f"[{case}] {index:02d}/10 success={success} tool={tool_ok} latency={row['total_latency_ms']:.0f}ms")
            time.sleep(REQUEST_DELAY_SECONDS)

    summary = {case: _summarize(rows) for case, rows in cases.items()}
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps({"generated_at": datetime.now().isoformat(), "summary": summary, "runs": cases}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
