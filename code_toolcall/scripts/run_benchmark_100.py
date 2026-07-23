"""Reproducible 100-sample benchmarks for the Manager, Weather, Music and E2E paths.

Each invocation runs exactly one table, writes every raw sample and its computed
metrics to benchmark_results/, and never writes to Redis or ChromaDB.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rag_manager.agents.manager import manager_node
from rag_manager.agents.music_agent import run_music as invoke_music_agent
from rag_manager.agents.weather_agent import run_weather as invoke_weather_agent
from rag_manager.config import load_settings
from rag_manager.graph import AppRuntime, build_workflow


DELAY_SECONDS = float(os.getenv("BENCHMARK_DELAY_SECONDS", "8"))
OUT_DIR = Path("benchmark_results")
LOCATIONS = ["Hà Nội", "Đà Nẵng", "Hồ Chí Minh", "Hải Phòng", "Cần Thơ"]
WEATHER_WORDINGS = [
    "Thời tiết {city} hôm nay thế nào?", "Cho tôi biết trời ở {city} bây giờ.",
    "Hiện tại {city} có mưa không?", "Nhiệt độ lúc này tại {city} bao nhiêu?",
    "Xem thời tiết hiện giờ ở {city}.",
]
FORECAST_WORDINGS = [
    "Dự báo thời tiết {city} ngày mai.", "Ngày mai ở {city} có mưa không?",
    "Mai trời {city} như thế nào?", "Xem nhiệt độ {city} vào ngày mai.",
    "Cho tôi dự báo {city} cho ngày mai.",
]
HOURLY_WORDINGS = [
    "{hour} giờ tối nay ở {city} có mưa không?", "Thời tiết {city} lúc {hour}:00 hôm nay thế nào?",
    "Kiểm tra giúp tôi trời {city} vào {hour} giờ tối nay.", "{city} lúc {hour} giờ đêm nay có mưa chứ?",
    "Dự báo theo giờ: {city}, {hour}:00 tối nay.",
]
WEEK_WORDINGS = [
    "Dự báo thời tiết {city} trong 7 ngày tới.", "Cả tuần tới ở {city} thời tiết ra sao?",
    "Xem giúp tôi dự báo 7 ngày cho {city}.", "Tuần này tại {city} có thay đổi thời tiết gì?",
    "Cho tôi xem thời tiết {city} trong một tuần.",
]
MUSIC_DISCOVERY = [
    "Mở một bài nhạc buồn.", "Gợi ý cho tôi nhạc tâm trạng.", "Tìm một ca khúc nhẹ nhàng, buồn.",
    "Tôi muốn nghe nhạc buồn.", "Có bài nào phù hợp lúc buồn không?", "Tìm nhạc chill buồn trong kho.",
    "Gợi ý nhạc để thư giãn buổi tối.", "Mở một bài hát tâm trạng.", "Tìm giúp tôi bài nhạc buồn nhé.",
    "Cho tôi một gợi ý nhạc nhẹ nhàng.",
]
MUSIC_EXACT = [
    "Mở bài 7-MINUTE STAGE | ĐỪNG LÀM TRÁI TIM ANH ĐAU.",
    "Phát bài 7-MINUTE STAGE | CHÚNG TA CỦA TƯƠNG LAI.",
    "Bật bài SON TUNG M-TP x TYGA | COME MY WAY.",
    "Mở COME MY WAY (softer version).",
    "Phát SƠN TÙNG M-TP x BOMATELA | CÓ CHẮC YÊU LÀ ĐÂY | SHOW RECAP.",
    "Mở bài ĐỪNG LÀM TRÁI TIM ANH ĐAU của Sơn Tùng M-TP.",
    "Bật CHÚNG TA CỦA TƯƠNG LAI của Sơn Tùng M-TP.",
    "Cho tôi nghe ĐỪNG LÀM TRÁI TIM ANH ĐAU.",
    "Phát CHÚNG TA CỦA TƯƠNG LAI.",
    "Mở bài COME MY WAY của Sơn Tùng.",
]


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    items = sorted(values)
    return round(items[round(q * (len(items) - 1))], 2)


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def usage_totals(usage: list[dict[str, Any]]) -> tuple[int, int, float]:
    inputs = sum(int(x["input_tokens"]) for x in usage if isinstance(x.get("input_tokens"), int))
    outputs = sum(int(x["output_tokens"]) for x in usage if isinstance(x.get("output_tokens"), int))
    inference = sum(float(x["inference_ms"]) for x in usage if num(x.get("inference_ms")) is not None)
    return inputs, outputs, inference


def effective_rates(usage: list[dict[str, Any]]) -> dict[str, float | None]:
    inputs, outputs, inference_ms = usage_totals(usage)
    seconds = inference_ms / 1000
    return {
        "input_tokens": inputs,
        "output_tokens": outputs,
        "inference_ms": round(inference_ms, 2),
        "prefill_rate_tok_s": round(inputs / seconds, 2) if seconds else None,
        "generation_rate_tok_s": round(outputs / seconds, 2) if seconds else None,
    }


def state(query: str) -> dict[str, Any]:
    return {"query": query, "history": [], "weather_context": {}, "music_session": {}, "session_id": "benchmark", "tool_trace": []}


def weather_cases() -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for wording_group, expected in ((WEATHER_WORDINGS, "current"), (FORECAST_WORDINGS, "forecast"), (HOURLY_WORDINGS, "hourly"), (WEEK_WORDINGS, "week")):
        for city in LOCATIONS:
            for idx, wording in enumerate(wording_group):
                cases.append({"query": wording.format(city=city, hour=[20, 21, 22, 23, 19][idx]), "expected": expected})
    assert len(cases) == 100
    return cases


def music_cases() -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for i in range(50):
        cases.append({"query": MUSIC_DISCOVERY[i % len(MUSIC_DISCOVERY)], "expected": "discovery"})
    for i in range(50):
        cases.append({"query": MUSIC_EXACT[i % len(MUSIC_EXACT)], "expected": "exact"})
    assert len(cases) == 100
    return cases


def trace_item(trace: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((x for x in trace if x.get("tool") == name), None)


def weather_grade(result: dict[str, Any], expected: str) -> tuple[bool, bool]:
    trace = result.get("tool_trace", [])
    item = trace_item(trace, "get_weather")
    if not item:
        return False, False
    args = item.get("arguments", {})
    data = item.get("result", {}).get("data", {})
    request_type = args.get("request_type")
    tool_ok = bool(data) and item.get("status") == "completed"
    if expected == "current": tool_ok = tool_ok and request_type == "current"
    elif expected == "forecast": tool_ok = tool_ok and request_type == "forecast" and int(args.get("days", 1) or 1) == 1
    elif expected == "hourly": tool_ok = tool_ok and request_type == "hourly" and bool(args.get("time_text"))
    else: tool_ok = tool_ok and request_type == "forecast" and int(args.get("days", 0) or 0) >= 7
    answer = str(result.get("answer") or "").strip()
    # Factual-answer contract: correct completed tool result plus a non-empty answer.
    answer_ok = tool_ok and bool(answer)
    return tool_ok, answer_ok


def music_grade(result: dict[str, Any], expected: str) -> tuple[bool, bool]:
    trace = result.get("tool_trace", [])
    search = trace_item(trace, "search_music")
    play = trace_item(trace, "play_music")
    if not search:
        return False, False
    status = search.get("result", {}).get("data", {}).get("status")
    if expected == "discovery":
        tool_ok = status in {"found", "not_found"} and play is None
    else:
        exact = search.get("result", {}).get("data", {}).get("exact_matches", [])
        tool_ok = status in {"found", "not_found"}
        if status == "found" and isinstance(exact, list) and len(exact) == 1:
            tool_ok = tool_ok and play is not None
        if status == "found" and isinstance(exact, list) and len(exact) > 1:
            tool_ok = tool_ok and play is None
    return tool_ok, tool_ok and bool(str(result.get("answer") or "").strip())


def write(table: str, rows: list[dict[str, Any]], summary: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"benchmark_100_{table}_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps({"generated_at": datetime.now().isoformat(), "table": table, "summary": summary, "runs": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def summarize_agent(rows: list[dict[str, Any]], latency_key: str, ttft_key: str | None = None) -> dict[str, Any]:
    tool = [bool(x["tool_call_correct"]) for x in rows]
    answer = [bool(x["answer_correct"]) for x in rows]
    rates = [x["rates"] for x in rows]
    result = {
        "requests": len(rows),
        "tool_call_correctness_percent": round(100 * sum(tool) / len(rows), 1),
        "answer_accuracy_percent": round(100 * sum(answer) / len(rows), 1),
        "prefill_rate_p50_tok_s": median([x["prefill_rate_tok_s"] for x in rates if x["prefill_rate_tok_s"] is not None]),
        "generation_rate_p50_tok_s": median([x["generation_rate_tok_s"] for x in rates if x["generation_rate_tok_s"] is not None]),
        "agent_latency_p50_ms": median([x[latency_key] for x in rows]),
        "input_tokens_p50": median([x["rates"]["input_tokens"] for x in rows]),
        "output_tokens_p50": median([x["rates"]["output_tokens"] for x in rows]),
    }
    if ttft_key:
        result["agent_ttft_p50_ms"] = median([x[ttft_key] for x in rows if x.get(ttft_key) is not None])
    return result


def run_manager(start_index: int = 0, count: int = 100) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = load_settings(); runtime = AppRuntime(settings).llm
    queries = []
    for case in weather_cases()[:40]: queries.append((case["query"], "weather"))
    for case in music_cases()[:40]: queries.append((case["query"], "music"))
    queries += [(f"Tương tác với biểu đồ kết quả thời tiết {i + 1}.", "visual") for i in range(20)]
    selected_queries = queries[start_index:start_index + count]
    if len(selected_queries) != count:
        raise ValueError(f"Requested manager batch [{start_index}, {start_index + count}) is outside 100 samples.")
    rows = []
    for local_index, (query, expected) in enumerate(selected_queries, 1):
        index = start_index + local_index
        started = time.perf_counter(); result = manager_node(state(query), runtime); latency = (time.perf_counter() - started) * 1000
        usage = result.get("llm_usage", []); rates = effective_rates(usage)
        rows.append({"index": index, "query": query, "expected_agent": expected, "selected_agent": result.get("selected_agent"), "routing_correct": result.get("selected_agent") == expected, "inference_latency_ms": round(latency, 2), "rates": rates, "manager_decision": result.get("manager_decision")})
        print(f"[manager] {index:03d}/100 route={result.get('selected_agent')} expected={expected} latency={latency:.0f}ms", flush=True)
        time.sleep(DELAY_SECONDS)
    summary = {"requests": count, "sample_start_index": start_index + 1, "sample_end_index": start_index + count, "routing_accuracy_percent": round(100 * sum(x["routing_correct"] for x in rows) / count, 1), "prefill_rate_p50_tok_s": median([x["rates"]["prefill_rate_tok_s"] for x in rows]), "generation_rate_p50_tok_s": median([x["rates"]["generation_rate_tok_s"] for x in rows]), "inference_latency_p50_ms": median([x["inference_latency_ms"] for x in rows]), "inference_latency_p95_ms": pct([x["inference_latency_ms"] for x in rows], .95), "input_tokens_p50": median([x["rates"]["input_tokens"] for x in rows]), "output_tokens_p50": median([x["rates"]["output_tokens"] for x in rows])}
    return rows, summary


def merge_manager(files: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for filename in files:
        payload = json.loads(Path(filename).read_text(encoding="utf-8"))
        if payload.get("table") != "manager":
            raise ValueError(f"Not a manager benchmark file: {filename}")
        rows.extend(payload.get("runs", []))
    rows.sort(key=lambda item: int(item.get("index", 0)))
    indices = [int(item.get("index", 0)) for item in rows]
    if indices != list(range(1, 101)):
        raise ValueError(f"Manager merge needs exactly unique indices 1..100, got {indices}")
    summary = {
        "requests": 100,
        "routing_accuracy_percent": round(sum(bool(x.get("routing_correct")) for x in rows), 1),
        "prefill_rate_p50_tok_s": median([x["rates"]["prefill_rate_tok_s"] for x in rows if x.get("rates", {}).get("prefill_rate_tok_s") is not None]),
        "generation_rate_p50_tok_s": median([x["rates"]["generation_rate_tok_s"] for x in rows if x.get("rates", {}).get("generation_rate_tok_s") is not None]),
        "inference_latency_p50_ms": median([x["inference_latency_ms"] for x in rows]),
        "inference_latency_p95_ms": pct([x["inference_latency_ms"] for x in rows], .95),
        "input_tokens_p50": median([x["rates"]["input_tokens"] for x in rows]),
        "output_tokens_p50": median([x["rates"]["output_tokens"] for x in rows]),
        "source_batches": [str(Path(filename)) for filename in files],
    }
    return rows, summary


def run_weather(start_index: int = 0, count: int = 100) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = load_settings(); app = AppRuntime(settings); rows = []; cases = weather_cases()[start_index:start_index + count]
    if len(cases) != count: raise ValueError("Weather batch is outside 100 samples.")
    for index, case in enumerate(cases, start_index + 1):
        first: float | None = None; started = time.perf_counter()
        def callback(_: str, text: str) -> None:
            nonlocal first
            if text and first is None: first = time.perf_counter()
        result = invoke_weather_agent(app.llm, app.weather, case["query"], on_text_chunk=callback)
        latency = (time.perf_counter() - started) * 1000; tool_ok, answer_ok = weather_grade(result, case["expected"])
        rows.append({"index": index, **case, "tool_call_correct": tool_ok, "answer_correct": answer_ok, "agent_latency_ms": round(latency, 2), "agent_ttft_ms": round((first - started) * 1000, 2) if first else None, "rates": effective_rates(result.get("llm_usage", [])), "tool_trace": result.get("tool_trace", []), "answer": result.get("answer")})
        print(f"[weather] {index:03d}/100 tool={tool_ok} answer={answer_ok} latency={latency:.0f}ms", flush=True)
        time.sleep(DELAY_SECONDS)
    return rows, summarize_agent(rows, "agent_latency_ms", "agent_ttft_ms")


def run_music(start_index: int = 0, count: int = 100) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = load_settings(); app = AppRuntime(settings); rows = []; cases = music_cases()[start_index:start_index + count]
    if len(cases) != count: raise ValueError("Music batch is outside 100 samples.")
    for index, case in enumerate(cases, start_index + 1):
        first: float | None = None; started = time.perf_counter()
        def callback(_: str, text: str) -> None:
            nonlocal first
            if text and first is None: first = time.perf_counter()
        result = invoke_music_agent(app.llm, app.music, case["query"], on_text_chunk=callback)
        latency = (time.perf_counter() - started) * 1000; tool_ok, answer_ok = music_grade(result, case["expected"])
        rows.append({"index": index, **case, "tool_call_correct": tool_ok, "answer_correct": answer_ok, "agent_latency_ms": round(latency, 2), "agent_ttft_ms": round((first - started) * 1000, 2) if first else None, "rates": effective_rates(result.get("llm_usage", [])), "tool_trace": result.get("tool_trace", []), "answer": result.get("answer")})
        print(f"[music] {index:03d}/100 tool={tool_ok} answer={answer_ok} latency={latency:.0f}ms", flush=True)
        time.sleep(DELAY_SECONDS)
    return rows, summarize_agent(rows, "agent_latency_ms", "agent_ttft_ms")


def run_e2e(start_index: int = 0, count: int = 100) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = load_settings(); workflow = build_workflow(settings); all_cases = weather_cases()[:50] + music_cases()[:50]; cases = all_cases[start_index:start_index + count]; rows = []
    if len(cases) != count: raise ValueError("E2E batch is outside 100 samples.")
    for index, case in enumerate(cases, start_index + 1):
        expected_agent = "weather" if "expected" in case and case["expected"] in {"current", "forecast", "hourly", "week"} else "music"
        first: float | None = None; started = time.perf_counter()
        def callback(_: str, text: str) -> None:
            nonlocal first
            if text and first is None: first = time.perf_counter()
        result = workflow.invoke({**state(case["query"]), "response_stream_callback": callback})
        latency = (time.perf_counter() - started) * 1000
        agent_result = result.get("agent_result", {})
        if expected_agent == "weather": tool_ok, answer_ok = weather_grade(agent_result, case["expected"])
        else: tool_ok, answer_ok = music_grade(agent_result, case["expected"])
        success = result.get("selected_agent") == expected_agent and tool_ok and answer_ok
        rows.append({"index": index, **case, "expected_agent": expected_agent, "selected_agent": result.get("selected_agent"), "success": success, "e2e_latency_ms": round(latency, 2), "e2e_ttft_ms": round((first - started) * 1000, 2) if first else None, "rates": effective_rates(result.get("llm_usage", [])), "tool_trace": result.get("tool_trace", []), "answer": result.get("final_answer")})
        print(f"[e2e] {index:03d}/100 success={success} agent={result.get('selected_agent')} latency={latency:.0f}ms", flush=True)
        time.sleep(DELAY_SECONDS)
    summary = {"requests": count, "e2e_success_rate_percent": round(100 * sum(x["success"] for x in rows) / count, 1), "prefill_rate_p50_tok_s": median([x["rates"]["prefill_rate_tok_s"] for x in rows if x["rates"]["prefill_rate_tok_s"] is not None]), "generation_rate_p50_tok_s": median([x["rates"]["generation_rate_tok_s"] for x in rows if x["rates"]["generation_rate_tok_s"] is not None]), "e2e_ttft_p50_ms": median([x["e2e_ttft_ms"] for x in rows if x["e2e_ttft_ms"] is not None]), "e2e_latency_p50_ms": median([x["e2e_latency_ms"] for x in rows]), "e2e_latency_p95_ms": pct([x["e2e_latency_ms"] for x in rows], .95)}
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("table", choices=["manager", "weather", "music", "e2e"]); parser.add_argument("--start", type=int, default=0); parser.add_argument("--count", type=int, default=100); parser.add_argument("--merge-manager", nargs=4, metavar="FILE"); args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING); logging.getLogger("lumi.toolcall").setLevel(logging.WARNING)
    if args.merge_manager:
        rows, summary = merge_manager(args.merge_manager)
        path = write("manager_merged", rows, summary); print(json.dumps(summary, ensure_ascii=False, indent=2)); print(f"Saved: {path}"); return
    if not load_settings().gemini_api_key: raise RuntimeError("GEMINI_API_KEY is missing from code_toolcall/.env")
    runner = {
        "manager": lambda: run_manager(args.start, args.count),
        "weather": lambda: run_weather(args.start, args.count),
        "music": lambda: run_music(args.start, args.count),
        "e2e": lambda: run_e2e(args.start, args.count),
    }[args.table]
    rows, summary = runner()
    path = write(args.table, rows, summary); print(json.dumps(summary, ensure_ascii=False, indent=2)); print(f"Saved: {path}")


if __name__ == "__main__": main()
