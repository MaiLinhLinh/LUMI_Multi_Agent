from __future__ import annotations

from typing import Any

from rag_manager.llm.function_calling_runtime import GeminiFunctionCallingRuntime
from rag_manager.tools.weather_tools import WEATHER_DECLARATION, WeatherTools


SYSTEM = """You are a Weather sub-agent.
Your task is to understand weather requests and call get_weather. Do not write complete weather responses yourself.
Identify the location and time range from the latest message, recent conversation history, and confirmed weather context. History/context is used only to fill in missing fields, not as evidence of weather data.
For follow-up questions, inherit missing location, date, time range, and request_type only from the most recent context. Replace a field only when the user explicitly states they want to change that field.
When the combined request contains both a location and a time range, you must call get_weather.
Use current only when the user explicitly states they want weather at the present time. Use hourly when the user asks about a specific time of day, such as "14:00", "2 PM", "3 PM", "three in the afternoon", or "this morning at eight"; normalize into time_text as HH:MM. Use forecast for named days, today/tomorrow, multi-day ranges, and extreme value questions like "which hour/day has the most rain".
Ask only one short clarifying question in Vietnamese when the location or time range is still missing after combining the latest message, history, and context. Do not guess a new location or date. If no tool call is made, you may only return the clarifying question."""


def run_weather(
    runtime: GeminiFunctionCallingRuntime,
    tools: WeatherTools,
    query: str,
    history: list[dict[str, Any]] | None = None,
    weather_context: dict[str, Any] | None = None,
    on_text_chunk: Any = None,
    presentation_enabled: bool = True,
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
        """- Confirmed context: Da Nang, next 3 days.
            User: "Which day has the most rain?"
            → call get_weather for Da Nang, next 3 days.
            - Confirmed context: Hanoi, today.
            User: "When is rain most likely?"
            → call get_weather for Hanoi, today."""
        )
    prompt_parts.append(f"Current user request: {query}")
    output = runtime.run(
        system_instruction=SYSTEM,
        user_text="\n\n".join(prompt_parts),
        declarations=[WEATHER_DECLARATION],
        handlers={"get_weather": lambda args: tools.get_weather(args, weather_context=context)},
        on_text_chunk=on_text_chunk,
        stop_after_completed_tools={"get_weather"} if presentation_enabled else None,
    )
    latest = next(
        (item.get("result") for item in reversed(output["tool_trace"]) if item["tool"] == "get_weather"),
        None,
    )
    answer = output.get("text", "")
    if latest is None and output.get("completed_without_tool") and answer.strip():
        latest = {"status": "needs_clarification", "data": {}}
    elif latest is None:
        latest = {"status": "error", "data": {}}
    data = latest.get("data", {}) if isinstance(latest, dict) else {}
    llm_response = latest.get("_llm_response", {}) if isinstance(latest, dict) else {}
    weather_facts = llm_response.get("weather_facts", {}) if isinstance(llm_response, dict) else {}
    next_context = dict(context)
    if isinstance(data, dict) and data.get("location_id"):
        next_context = {
            "last_location_id": data["location_id"],
            "last_location_name": data.get("location") or context.get("last_location_name", ""),
            "last_request_type": data.get("request_type", "forecast"),
            "last_start_date": data.get("requested_date", ""),
            "last_days": data.get("requested_days", 1),
        }
        snapshot = latest.get("_session_snapshot") if isinstance(latest, dict) else None
        if isinstance(snapshot, dict):
            next_context["session_snapshot"] = snapshot
    if not answer and latest.get("status") == "completed":
        answer = "Dữ liệu thời tiết đã được cập nhật. Bạn có thể xem đầy đủ thông tin ở phần trực quan bên cạnh."
    return {
        "answer": answer,
        "status": latest.get("status", "completed"),
        "data": data,
        "weather_facts": weather_facts,
        "presentation_deferred": presentation_enabled and latest.get("status") == "completed",
        "weather_context": next_context,
        "llm_usage": output.get("usage", []),
        "stream_timings": output.get("stream_timings", {}),
        "tool_trace": output["tool_trace"],
    }
