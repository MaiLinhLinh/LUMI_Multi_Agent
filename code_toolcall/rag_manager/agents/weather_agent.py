from __future__ import annotations

from typing import Any

from rag_manager.llm.function_calling_runtime import GeminiFunctionCallingRuntime
from rag_manager.tools.weather_tools import WEATHER_DECLARATION, WeatherTools


SYSTEM = """You are the Weather sub-agent. Call get_weather only after the request has a resolved location and time scope, and use only tool facts in a completed answer. Its required request_type is current only for an explicitly stated present-time request, forecast for a named day or multi-day outlook, and hourly for an exact clock time. An hourly call must include time_text in HH:MM 24-hour format. A confirmed weather context may be supplied. Resolve location and time scope independently: a new location replaces only the location; a new date, time, or range replaces only that temporal field. For a conversational follow-up such as 'thế ... thì sao', 'còn ...', or a comparison, preserve the confirmed date/range/request_type when the user changes location but gives no new temporal expression. For a fresh standalone request, never assume current: if location is missing, ask for the location; if time scope is missing, ask whether the user means now, today, tomorrow, or another period; if both are missing, ask one concise question for both. Do not call a tool before this clarification. Answer in Vietnamese. Keep the final answer concise and focused: one short summary, then at most 3 short bullets only when useful."""


def run_weather(
    runtime: GeminiFunctionCallingRuntime,
    tools: WeatherTools,
    query: str,
    history: list[dict[str, Any]] | None = None,
    weather_context: dict[str, Any] | None = None,
    on_text_chunk: Any = None,
) -> dict[str, Any]:
    recent_history = "\n".join(
        f"{item.get('role', '')}: {item.get('content', '')}"
        for item in (history or [])[-6:]
        if isinstance(item, dict)
    )
    context = weather_context if isinstance(weather_context, dict) else {}
    prompt_parts: list[str] = []
    if recent_history:
        prompt_parts.append(f"Relevant conversation history:\n{recent_history}")
    if context.get("last_location_id"):
        prompt_parts.append(
            "Confirmed weather context (resolve each field using the system rules): "
            f"location={context.get('last_location_name')}, location_id={context.get('last_location_id')}, "
            f"last_request_type={context.get('last_request_type')}, "
            f"last_start_date={context.get('last_start_date')}, last_days={context.get('last_days')}"
        )
        prompt_parts.append(
            "How to apply this context:\n"
            "- If the user says 'vậy ở một địa điểm khác?' or 'còn <địa điểm>?', replace only location; "
            f"preserve request_type={context.get('last_request_type')}, date={context.get('last_start_date')}, days={context.get('last_days')}.\n"
            "- If the user says 'thế ngày mai?', preserve "
            f"location={context.get('last_location_name')} and replace only the date with tomorrow.\n"
            "- If the user gives a new date, exact time, or range, replace only that temporal field."
        )
    prompt_parts.append(f"Current user request: {query}")
    output = runtime.run(
        system_instruction=SYSTEM,
        user_text="\n\n".join(prompt_parts),
        declarations=[WEATHER_DECLARATION],
        handlers={"get_weather": lambda args: tools.get_weather(args, weather_context=context)},
        on_text_chunk=on_text_chunk,
    )
    latest = next(
        (item.get("result") for item in reversed(output["tool_trace"]) if item["tool"] == "get_weather"),
        {"status": "error"},
    )
    data = latest.get("data", {}) if isinstance(latest, dict) else {}
    next_context = dict(context)
    if isinstance(data, dict) and data.get("location_id"):
        next_context = {
            "last_location_id": data["location_id"],
            "last_location_name": data.get("location") or context.get("last_location_name", ""),
            "last_request_type": data.get("request_type", "forecast"),
            "last_start_date": data.get("requested_date", ""),
            "last_days": data.get("requested_days", 1),
        }
    answer = output.get("text", "")
    if not answer and latest.get("status") == "completed":
        answer = "Dữ liệu thời tiết đã được cập nhật. Bạn có thể xem đầy đủ thông tin ở phần trực quan bên cạnh."
    return {
        "answer": answer,
        "status": latest.get("status", "completed"),
        "data": data,
        "weather_context": next_context,
        "llm_usage": output.get("usage", []),
        "stream_timings": output.get("stream_timings", {}),
        "tool_trace": output["tool_trace"],
    }
