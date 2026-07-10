"""LangGraph workflow assembly for the RAG Manager Agent app."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from langgraph.graph import END, StateGraph

from rag_manager.agents.aggregator import run_aggregator_agent
from rag_manager.agents.manager import classify_intent
from rag_manager.agents.news import run_news_agent
from rag_manager.agents.weather import run_weather_agent
from rag_manager.agents.wiki import run_wiki_agent
from rag_manager.config import Settings, load_settings
from rag_manager.state import GraphState


def build_workflow():
    """Build and compile the application workflow."""
    graph = StateGraph(GraphState)
    graph.add_node("manager_classify", manager_classify_node)
    graph.add_node("weather", weather_node)
    graph.add_node("news", news_node)
    graph.add_node("wiki", wiki_node)
    graph.add_node("execute_parallel", execute_parallel_node)
    graph.add_node("plan_sequence", plan_sequence_node)
    graph.add_node("aggregate", aggregate_node)
    graph.set_entry_point("manager_classify")
    graph.add_conditional_edges(
        "manager_classify",
        route_execution_mode,
        {
            "weather": "weather",
            "news": "news",
            "wiki": "wiki",
            "parallel": "execute_parallel",
            "sequential": "plan_sequence",
        },
    )
    graph.add_edge("weather", "aggregate")
    graph.add_edge("news", "aggregate")
    graph.add_edge("wiki", "aggregate")
    graph.add_edge("execute_parallel", "aggregate")
    graph.add_edge("plan_sequence", "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()


def manager_classify_node(state: GraphState) -> GraphState:
    started_at = perf_counter()
    client = state.get("manager_client") or _create_gemini_client(_get_settings(state))
    plan = classify_intent(client, state.get("query", ""))
    return {
        "intent": plan,
        "execution_mode": plan["execution_mode"],
        "selected_agents": plan["topics"],
        "timings": {"manager": _elapsed_since(started_at)},
        **_llm_usage_update("manager", client),
    }


def route_execution_mode(state: GraphState) -> str:
    execution_mode = state.get("execution_mode")
    if execution_mode == "parallel":
        return "parallel"
    if execution_mode == "sequential":
        return "sequential"
    return _single_agent_route(state)


def _single_agent_route(state: GraphState) -> str:
    intent = state.get("intent", {})
    primary_intent = intent.get("primary_intent", "") if isinstance(intent, dict) else ""
    if primary_intent in {"weather", "news", "wiki"}:
        return primary_intent

    selected_agents = state.get("selected_agents", [])
    if selected_agents:
        first_agent = selected_agents[0]
        return first_agent if first_agent in {"weather", "news", "wiki"} else "wiki"

    return "wiki"


def weather_node(state: GraphState) -> GraphState:
    return _merge_state_updates(
        [
            _state_metadata(state),
            run_weather_agent(
                state,
                cache=state.get("weather_cache"),
                settings=_get_settings(state),
                client=state.get("weather_client"),
            ),
        ]
    )


def news_node(state: GraphState) -> GraphState:
    return _merge_state_updates(
        [
            _state_metadata(state),
            run_news_agent(
                state,
                cache=state.get("news_cache"),
                settings=_get_settings(state),
                client=state.get("news_client"),
            ),
        ]
    )


def wiki_node(state: GraphState) -> GraphState:
    return _merge_state_updates(
        [
            _state_metadata(state),
            run_wiki_agent(
                state,
                cache=state.get("wiki_cache"),
                settings=_get_settings(state),
                client=state.get("wiki_client"),
            ),
        ]
    )


def execute_parallel_node(state: GraphState) -> GraphState:
    return asyncio.run(_execute_parallel_async(state))


async def _execute_parallel_async(state: GraphState) -> GraphState:
    selected_agents = _selected_topics(state)
    tasks = []

    if "weather" in selected_agents:
        tasks.append(("weather", asyncio.to_thread(weather_node, state)))
    if "news" in selected_agents:
        tasks.append(("news", asyncio.to_thread(news_node, state)))
    if "wiki" in selected_agents:
        tasks.append(("wiki", asyncio.to_thread(wiki_node, state)))

    if not tasks:
        return _state_metadata(state)

    topics = [topic for topic, _task in tasks]
    results = await asyncio.gather(
        *(task for _topic, task in tasks),
        return_exceptions=True,
    )
    updates = [
        _parallel_result_update(topic, result)
        for topic, result in zip(topics, results)
    ]
    return _merge_state_updates([_state_metadata(state), *updates])


def plan_sequence_node(state: GraphState) -> GraphState:
    running_state = dict(state)
    updates: list[GraphState] = []

    for topic in _sequence_topics(state):
        update = _with_topic_context(topic, _run_topic_node(topic, running_state))
        merged_update = _merge_state_updates(
            [{"context": running_state.get("context", {})}, update]
        )
        updates.append(merged_update)
        running_state.update(merged_update)

    return _merge_state_updates(updates)


def aggregate_node(state: GraphState) -> GraphState:
    return _merge_state_updates(
        [
            _state_metadata(state),
            run_aggregator_agent(
                state,
                settings=_get_settings(state),
                client=state.get("aggregator_client"),
            ),
        ]
    )


def _get_settings(state: GraphState) -> Settings:
    settings = state.get("settings")
    return settings if isinstance(settings, Settings) else load_settings()


def _create_gemini_client(settings: Settings) -> Any:
    from rag_manager.llm.gemini_client import GeminiClient

    return GeminiClient(settings)


def _elapsed_since(started_at: float) -> float:
    return perf_counter() - started_at


def _merge_state_updates(updates: list[GraphState]) -> GraphState:
    merged: GraphState = {}
    for update in updates:
        for key, value in update.items():
            if key in {"cache_stats", "context", "timings", "llm_usage"} and isinstance(value, dict):
                existing = merged.get(key, {})
                if isinstance(existing, dict):
                    merged[key] = {**existing, **value}
                else:
                    merged[key] = value
            elif key == "errors" and isinstance(value, list):
                existing_errors = merged.get("errors", [])
                if isinstance(existing_errors, list):
                    merged[key] = [*existing_errors, *value]
                else:
                    merged[key] = value
            else:
                merged[key] = value
    return merged


def _state_metadata(state: GraphState) -> GraphState:
    metadata: GraphState = {}
    for key in ("cache_stats", "context", "timings", "llm_usage"):
        value = state.get(key)
        if isinstance(value, dict):
            metadata[key] = value
    errors = state.get("errors")
    if isinstance(errors, list):
        metadata["errors"] = errors
    return metadata


def _parallel_result_update(topic: str, result: object) -> GraphState:
    if isinstance(result, Exception):
        return {
            "errors": [
                {
                    "source": topic,
                    "message": str(result) or result.__class__.__name__,
                }
            ]
        }
    return result if isinstance(result, dict) else {}


def _llm_usage_update(agent_name: str, client: Any) -> GraphState:
    usage = getattr(client, "last_usage", {})
    if not isinstance(usage, dict) or not usage:
        return {}
    return {"llm_usage": {agent_name: usage}}


def _sequence_topics(state: GraphState) -> list[str]:
    selected_agents = _selected_topics(state)
    intent = state.get("intent", {})
    dependencies = intent.get("dependencies", []) if isinstance(intent, dict) else []

    ordered_topics: list[str] = []
    if isinstance(dependencies, list):
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            from_topic = dependency.get("from_topic")
            to_topic = dependency.get("to_topic")
            for topic in (from_topic, to_topic):
                if topic in selected_agents and topic not in ordered_topics:
                    ordered_topics.append(topic)

    if not ordered_topics:
        fallback_order = ["weather", "wiki", "news"]
        ordered_topics = [topic for topic in fallback_order if topic in selected_agents]

    for topic in selected_agents:
        if topic not in ordered_topics:
            ordered_topics.append(topic)

    return ordered_topics


def _selected_topics(state: GraphState) -> list[str]:
    topics: list[str] = []
    for topic in state.get("selected_agents", []):
        if topic in {"weather", "news", "wiki"} and topic not in topics:
            topics.append(topic)
    return topics


def _run_topic_node(topic: str, state: GraphState) -> GraphState:
    if topic == "weather":
        return weather_node(state)
    if topic == "news":
        return news_node(state)
    if topic == "wiki":
        return wiki_node(state)
    return {}


def _with_topic_context(topic: str, update: GraphState) -> GraphState:
    context = _topic_context(topic, update)
    if not context:
        return update

    existing_context = update.get("context", {})
    if not isinstance(existing_context, dict):
        existing_context = {}

    return {
        **update,
        "context": {
            **existing_context,
            **context,
            "last_topic": topic,
        },
    }


def _topic_context(topic: str, update: GraphState) -> dict[str, Any]:
    context: dict[str, Any] = {}
    data_key = f"{topic}_data"
    answer_key = f"{topic}_answer"

    if data_key in update:
        context[data_key] = update[data_key]
    if answer_key in update:
        context[answer_key] = update[answer_key]

    return context
