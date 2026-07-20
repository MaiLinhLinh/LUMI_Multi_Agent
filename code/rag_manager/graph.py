"""LangGraph workflow assembly for the RAG Manager Agent app."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from langgraph.graph import END, StateGraph

from rag_manager.agents.aggregator import run_aggregator_agent
from rag_manager.agents.manager import classify_intent
from rag_manager.agents.music import run_music_agent
from rag_manager.agents.news import run_news_agent
from rag_manager.agents.weather import run_weather_llm_pipeline
from rag_manager.agents.wiki import run_wiki_agent
from rag_manager.config import Settings, load_settings
from rag_manager.state import GraphState
from rag_manager.semantic_router import (
    analyze_input,
    is_explicit_visualization_query,
    is_high_confidence_domain_query,
)
from rag_manager.visualization.orchestrator import (
    CREATE_NEW_TEMPLATE_ID,
    VisualizationOrchestrator,
    VisualizationRequest,
    VisualizationResult,
)
from rag_manager.visualization.template_agent import TemplateAgentWorkflow


def build_workflow():
    """Build and compile the application workflow."""
    graph = StateGraph(GraphState)
    graph.add_node("input_router", input_router_node)
    graph.add_node("manager_classify", manager_classify_node)
    graph.add_node("weather", weather_node)
    graph.add_node("news", news_node)
    graph.add_node("wiki", wiki_node)
    graph.add_node("music", music_node)
    graph.add_node("execute_parallel", execute_parallel_node)
    graph.add_node("plan_sequence", plan_sequence_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("visualize", visualize_node)
    graph.set_entry_point("input_router")
    graph.add_conditional_edges(
        "input_router",
        route_input,
        {
            "manager_classify": "manager_classify",
            "visualize": "visualize",
        },
    )
    graph.add_conditional_edges(
        "manager_classify",
        route_execution_mode,
        {
            "weather": "weather",
            "news": "news",
            "wiki": "wiki",
            "music": "music",
            "parallel": "execute_parallel",
            "sequential": "plan_sequence",
        },
    )
    graph.add_conditional_edges(
        "weather",
        route_after_weather_execution,
        {"aggregate": "aggregate", "visualize": "visualize", "end": END},
    )
    graph.add_edge("news", "aggregate")
    graph.add_edge("wiki", "aggregate")
    graph.add_edge("music", "aggregate")
    graph.add_conditional_edges(
        "execute_parallel",
        route_after_weather_execution,
        {"aggregate": "aggregate", "end": END},
    )
    graph.add_conditional_edges(
        "plan_sequence",
        route_after_weather_execution,
        {"aggregate": "aggregate", "end": END},
    )
    graph.add_edge("aggregate", "visualize")
    graph.add_edge("visualize", END)
    return graph.compile()


def input_router_node(state: GraphState) -> GraphState:
    query = state.get("query", "")
    pending_template_state = state.get("pending_template_state")
    has_pending_template_request = (
        isinstance(pending_template_state, dict)
        and pending_template_state.get("status") == "collecting_requirements"
    )
    is_weather_followup = _has_active_weather_context(state) and not (
        is_explicit_visualization_query(query)
    )
    if (
        is_high_confidence_domain_query(query) or is_weather_followup
    ) and not has_pending_template_request:
        return {
            "input_route": "domain",
            "semantic_result": {},
            "visualization_request": {"mode": "auto", "action": "auto_render"},
            "pending_visualization_action": "",
            "pending_template_state": {},
            "template_requirements": {},
            "template_clarification_round": 0,
            "visualization_context": {},
        }

    client = state.get("semantic_router_client") or state.get("manager_client")
    if client is None or not hasattr(client, "chat_json"):
        settings = _get_settings(state)
        client = _create_gemini_client(settings) if settings.has_gemini_key else None
    semantic_result = analyze_input(
        client,
        query=query,
        history=state.get("history", []),
        previous_template_state=pending_template_state
        if isinstance(pending_template_state, dict)
        else None,
        active_template_id=_string_value(state.get("active_template_id")),
        available_templates=state.get("available_templates", [])
        if isinstance(state.get("available_templates", []), list)
        else [],
    )
    semantic_result = _merge_semantic_requirements(
        semantic_result,
        previous_state=pending_template_state
        if isinstance(pending_template_state, dict)
        else None,
    )
    if semantic_result.get("route") == "domain":
        return {
            "input_route": "domain",
            "semantic_result": semantic_result,
            "visualization_request": {"mode": "auto", "action": "auto_render"},
            "pending_visualization_action": "",
            "pending_template_state": {},
            "template_requirements": {},
            "template_clarification_round": 0,
            "visualization_context": {},
            **_llm_usage_update("semantic_router", client),
        }

    pending_state = _pending_template_state_from_semantic(
        semantic_result,
        previous_state=pending_template_state
        if isinstance(pending_template_state, dict)
        else None,
    )
    visualization_context = _build_visualization_context(
        previous_context=state.get("visualization_context")
        if isinstance(state.get("visualization_context"), dict)
        else {},
        query=query,
        domain_result=state.get("last_domain_result")
        if isinstance(state.get("last_domain_result"), dict)
        else None,
        pending_state=pending_state,
    )

    visualization_request = {
        "mode": "auto",
        "action": "semantic_request",
        "user_request": query,
        "semantic_result": semantic_result,
        "previous_template_state": pending_state,
        "visualization_context": visualization_context,
    }
    update: GraphState = {
        "input_route": "visualize",
        "semantic_result": semantic_result,
        "visualization_request": visualization_request,
        "pending_visualization_action": semantic_result.get("template", {}).get(
            "action", "clarification"
        ),
        "visualization_context": visualization_context,
        **_llm_usage_update("semantic_router", client),
    }
    if pending_state is None:
        update.update(
            {
                "pending_template_state": {},
                "template_requirements": {},
                "template_clarification_round": 0,
            }
        )
    else:
        update.update(
            {
                "pending_template_state": pending_state,
                "template_requirements": pending_state["requirements"],
                "template_clarification_round": pending_state[
                    "clarification_round"
                ],
            }
        )
    return update


def _has_active_weather_context(state: GraphState) -> bool:
    """Return True when the latest conversational domain is active Weather."""

    weather_session = state.get("weather_session")
    if isinstance(weather_session, dict) and weather_session.get("active") is True:
        return True

    history = state.get("history")
    if not isinstance(history, list):
        return False
    for message in reversed(history):
        if not isinstance(message, dict):
            continue
        domain = message.get("domain")
        if isinstance(domain, str) and domain.strip():
            return domain.strip() == "weather"
    return False


def route_input(state: GraphState) -> str:
    return "visualize" if state.get("input_route") == "visualize" else "manager_classify"


def manager_classify_node(state: GraphState) -> GraphState:
    started_at = perf_counter()
    client = state.get("manager_client") or _create_gemini_client(_get_settings(state))
    semantic_result = state.get("semantic_result")
    domain_request = (
        semantic_result.get("domain_request")
        if isinstance(semantic_result, dict)
        else None
    )
    manager_query = (
        domain_request.strip()
        if isinstance(domain_request, str) and domain_request.strip()
        else state.get("query", "")
    )
    raw_history = state.get("history", [])
    history = raw_history if isinstance(raw_history, list) else []
    plan = classify_intent(client, manager_query, history=history)
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


def route_after_weather_execution(state: GraphState) -> str:
    if state.get("weather_status") in {
        "needs_clarification",
        "unavailable",
        "error",
    }:
        return "end"
    if (
        state.get("weather_status") == "completed"
        and state.get("execution_mode") == "single"
        and _selected_topics(state) == ["weather"]
    ):
        return "visualize"
    return "aggregate"


def _single_agent_route(state: GraphState) -> str:
    intent = state.get("intent", {})
    primary_intent = intent.get("primary_intent", "") if isinstance(intent, dict) else ""
    if primary_intent in {"weather", "news", "wiki", "music"}:
        return primary_intent

    selected_agents = state.get("selected_agents", [])
    if selected_agents:
        first_agent = selected_agents[0]
        return first_agent if first_agent in {"weather", "news", "wiki", "music"} else "wiki"

    return "wiki"


def weather_node(state: GraphState) -> GraphState:
    return _merge_state_updates(
        [
            _state_metadata(state),
            run_weather_llm_pipeline(
                state,
                store=state.get("weather_store"),
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


def music_node(state: GraphState) -> GraphState:
    return _merge_state_updates(
        [
            _state_metadata(state),
            run_music_agent(
                state,
                settings=_get_settings(state),
                client=state.get("music_client"),
                search_service=state.get("music_search_service"),
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
    if "music" in selected_agents:
        tasks.append(("music", asyncio.to_thread(music_node, state)))

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
        if running_state.get("weather_status") in {
            "needs_clarification",
            "unavailable",
            "error",
        }:
            break

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


def visualize_node(state: GraphState) -> GraphState:
    visualization_request = state.get("visualization_request", {})
    if not isinstance(visualization_request, dict):
        visualization_request = {"mode": "auto", "action": "auto_render"}

    domain_result = _domain_result_for_visualization(state)
    semantic_result = visualization_request.get("semantic_result")
    semantic_needs_response = (
        isinstance(semantic_result, dict)
        and semantic_result.get("status") in {"needs_clarification", "cancelled"}
    )
    if not domain_result and visualization_request.get("action") not in {
        "create_template",
        "customize_template",
        "design_template",
        "continue_template",
        "cancel_template",
    } and not semantic_needs_response:
        message = "Can hoi domain truoc khi chon hoac tao visualization."
        return {
            "visualization_output": {
                "ok": False,
                "mode": visualization_request.get("mode", "auto"),
                "message": message,
                "errors": ["missing_domain_result"],
            },
            "pending_visualization_action": "",
            "visualization_html_path": "",
        }

    is_template_request = visualization_request.get("action") == "semantic_request"
    template_id = None if is_template_request else _template_id_from_visualization_request(
        state, visualization_request
    )
    is_create_new_selection = template_id == CREATE_NEW_TEMPLATE_ID
    if is_create_new_selection:
        template_id = None
        visualization_request = {
            **visualization_request,
            "action": "template_request",
        }
        is_template_request = True
    mode = "auto" if is_template_request else _visualization_mode(
        visualization_request, template_id=template_id
    )
    orchestrator = state.get("visualization_orchestrator")
    if orchestrator is None:
        orchestrator = _create_visualization_orchestrator(state)
    result = orchestrator.run(
        VisualizationRequest(
            domain_result=domain_result,
            mode=mode,
            template_id=template_id,
            source_template_id=_string_value(visualization_request.get("source_template_id"))
            or _string_value(state.get("active_template_id")),
            user_request=_string_value(visualization_request.get("user_request")),
            previous_template_state=(
                visualization_request.get("previous_template_state")
                if isinstance(visualization_request.get("previous_template_state"), dict)
                else state.get("pending_template_state")
            ),
            visualization_context=(
                visualization_request.get("visualization_context")
                if isinstance(visualization_request.get("visualization_context"), dict)
                else state.get("visualization_context", {})
            ),
            action=_string_value(visualization_request.get("action")),
            modification_request=_string_value(visualization_request.get("modification_request")),
            # Semantic Router output is only meaningful for visualization
            # requests that still need template action dispatch. A domain
            # request has already completed the domain workflow and must use
            # the deterministic auto-render path based on its domain result.
            semantic_result=(
                visualization_request.get("semantic_result")
                if visualization_request.get("action") == "semantic_request"
                and isinstance(visualization_request.get("semantic_result"), dict)
                else None
            ),
        )
    )
    output = _visualization_result_dict(result)
    visualization_context = _context_after_visualization_result(
        visualization_request.get("visualization_context")
        if isinstance(visualization_request.get("visualization_context"), dict)
        else state.get("visualization_context", {}),
        output,
    )
    update: GraphState = {
        "visualization_output": output,
        "last_domain_result": domain_result,
        "available_templates": output.get("available_templates", []),
        "pending_visualization_action": "",
        "visualization_html_path": "",
        "visualization_context": visualization_context,
    }
    if output.get("ok") and isinstance(output.get("template_id"), str):
        update["active_template_id"] = output["template_id"]
    if output.get("ok") and isinstance(output.get("template_path"), str):
        update["active_template_path"] = output["template_path"]
    metadata = output.get("metadata")
    if isinstance(metadata, dict):
        pending_template_state = metadata.get("pending_template_state")
        if isinstance(pending_template_state, dict):
            update["pending_template_state"] = pending_template_state
            update["template_requirements"] = pending_template_state.get("requirements", {})
            update["template_clarification_round"] = pending_template_state.get(
                "clarification_round", 0
            )
        elif "missing_template_requirements" not in output.get("errors", []):
            update["pending_template_state"] = {}
            update["template_requirements"] = {}
            update["template_clarification_round"] = 0
    html_path = output.get("html_path")
    if isinstance(html_path, str) and html_path:
        update["visualization_html_path"] = html_path
    return update


def _pending_template_state_from_semantic(
    semantic_result: dict[str, Any],
    *,
    previous_state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build the session's structured template-gathering state.

    The Semantic Router owns interpretation and merging. This helper only
    persists its validated result in a stable graph-state shape so the next
    turn can provide it back as context.
    """

    status = semantic_result.get("status")
    if status == "cancelled":
        return None
    if status == "ready":
        # A ready response from LLM1 only means routing is complete. If LLM2
        # was waiting for a design clarification, keep that state so the next
        # LLM2 call receives the original request plus the user's answer.
        if (
            isinstance(previous_state, dict)
            and previous_state.get("status") == "collecting_planner_clarification"
        ):
            return {
                **previous_state,
                "status": "resolving_planner_clarification",
            }
        return None
    if status != "needs_clarification":
        return previous_state

    template = semantic_result.get("template", {})
    requirements = (
        template.get("requirements", {}) if isinstance(template, dict) else {}
    )
    if not isinstance(requirements, dict):
        requirements = {}
    previous_requirements = (
        previous_state.get("requirements", {})
        if isinstance(previous_state, dict)
        else {}
    )
    if isinstance(previous_requirements, dict):
        requirements = {**previous_requirements, **requirements}
    previous_round = (
        previous_state.get("clarification_round", 0)
        if isinstance(previous_state, dict)
        else 0
    )
    try:
        clarification_round = int(previous_round) + 1
    except (TypeError, ValueError):
        clarification_round = 1
    return {
        "status": "collecting_requirements",
        "requirements": requirements,
        "missing_information": list(semantic_result.get("missing_information", [])),
        "clarifying_question": semantic_result.get("clarifying_question"),
        "source": template.get("source", "none") if isinstance(template, dict) else "none",
        "action": template.get("action") if isinstance(template, dict) else None,
        "template_id": template.get("template_id") if isinstance(template, dict) else None,
        "extracted_keywords": list(template.get("extracted_keywords", []))
        if isinstance(template, dict)
        else [],
        "clarification_round": clarification_round,
    }


def _merge_semantic_requirements(
    semantic_result: dict[str, Any],
    *,
    previous_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Preserve confirmed requirements when a router response is partial."""

    if not isinstance(previous_state, dict):
        return semantic_result
    previous_requirements = previous_state.get("requirements", {})
    template = semantic_result.get("template", {})
    current_requirements = template.get("requirements", {}) if isinstance(template, dict) else {}
    if not isinstance(previous_requirements, dict) or not isinstance(current_requirements, dict):
        return semantic_result
    merged_template = {**template, "requirements": {**previous_requirements, **current_requirements}}
    return {**semantic_result, "template": merged_template}


def _create_visualization_orchestrator(state: GraphState) -> VisualizationOrchestrator:
    """Build the default orchestrator and wire Gemini only for Template Agent paths."""

    client = state.get("template_agent_client")
    if client is None:
        client = state.get("manager_client")
    if client is None or not (
        hasattr(client, "chat_json") or hasattr(client, "chat_text")
    ):
        settings = _get_settings(state)
        client = _create_gemini_client(settings) if settings.has_gemini_key else None
    return VisualizationOrchestrator(
        template_agent_workflow=TemplateAgentWorkflow(llm=client)
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
    for key in (
        "cache_stats",
        "context",
        "timings",
        "llm_usage",
        "last_domain_result",
        "available_templates",
        "weather_session",
    ):
        value = state.get(key)
        if isinstance(value, dict):
            metadata[key] = value
        elif key == "available_templates" and isinstance(value, list):
            metadata[key] = value
    errors = state.get("errors")
    if isinstance(errors, list):
        metadata["errors"] = errors
    return metadata


def _parallel_result_update(topic: str, result: object) -> GraphState:
    if isinstance(result, Exception):
        update: GraphState = {
            "errors": [
                {
                    "source": topic,
                    "message": str(result) or result.__class__.__name__,
                }
            ]
        }
        if topic == "weather":
            answer = "Hệ thống chưa thể xử lý yêu cầu thời tiết lúc này. Bạn vui lòng thử lại sau."
            update.update(
                {
                    "weather_status": "error",
                    "weather_answer": answer,
                    "weather_error": {
                        "stage": "weather_agent",
                        "code": "agent_execution_failed",
                        "message": str(result) or result.__class__.__name__,
                        "retryable": True,
                    },
                    "final_response": answer,
                }
            )
        return update
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
        fallback_order = ["weather", "music", "wiki", "news"]
        ordered_topics = [topic for topic in fallback_order if topic in selected_agents]

    for topic in selected_agents:
        if topic not in ordered_topics:
            ordered_topics.append(topic)

    return ordered_topics


def _selected_topics(state: GraphState) -> list[str]:
    topics: list[str] = []
    for topic in state.get("selected_agents", []):
        if topic in {"weather", "news", "wiki", "music"} and topic not in topics:
            topics.append(topic)
    return topics


def _run_topic_node(topic: str, state: GraphState) -> GraphState:
    if topic == "weather":
        return weather_node(state)
    if topic == "news":
        return news_node(state)
    if topic == "wiki":
        return wiki_node(state)
    if topic == "music":
        return music_node(state)
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


def _domain_result_for_visualization(state: GraphState) -> dict[str, Any]:
    if state.get("input_route") == "visualize":
        last_domain_result = state.get("last_domain_result")
        return last_domain_result if isinstance(last_domain_result, dict) else {}

    weather_data = state.get("weather_data")
    if isinstance(weather_data, dict) and weather_data.get("domain") == "weather":
        return {
            "weather_data": weather_data,
            "weather_answer": _string_value(state.get("weather_answer")),
            "final_response": _string_value(state.get("final_response")),
        }

    last_domain_result = state.get("last_domain_result")
    return last_domain_result if isinstance(last_domain_result, dict) else {}


def _build_visualization_context(
    *,
    previous_context: dict[str, Any],
    query: str,
    domain_result: dict[str, Any] | None,
    pending_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the compact, visualization-only context envelope for LLM2."""

    context = dict(previous_context) if isinstance(previous_context, dict) else {}
    history = context.get("conversation_history", [])
    history = list(history) if isinstance(history, list) else []
    if query.strip() and not (
        history
        and isinstance(history[-1], dict)
        and history[-1].get("role") == "user"
        and history[-1].get("content") == query
    ):
        history.append({"role": "user", "content": query})
    context["conversation_history"] = history[-8:]
    context.setdefault("merged_requirements", {})
    if isinstance(pending_state, dict):
        pending_requirements = pending_state.get("merged_requirements")
        if isinstance(pending_requirements, dict):
            context["merged_requirements"] = pending_requirements
    context["domain_context"] = _domain_context_snapshot(
        domain_result,
        fallback=context.get("domain_context", {}),
    )
    return context


def _context_after_visualization_result(
    context: object,
    output: dict[str, Any],
) -> dict[str, Any]:
    current = dict(context) if isinstance(context, dict) else {}
    history = list(current.get("conversation_history", []))
    metadata = output.get("metadata", {})
    if isinstance(metadata, dict):
        execution_plan = metadata.get("execution_plan")
        if isinstance(execution_plan, dict) and isinstance(execution_plan.get("requirements"), dict):
            current["merged_requirements"] = execution_plan["requirements"]
        pending_state = metadata.get("pending_template_state")
        if isinstance(pending_state, dict) and isinstance(pending_state.get("merged_requirements"), dict):
            current["merged_requirements"] = pending_state["merged_requirements"]
    if "llm2_needs_clarification" in output.get("errors", []):
        question = output.get("message")
        if isinstance(question, str) and question.strip() and not (
            history
            and isinstance(history[-1], dict)
            and history[-1].get("role") == "assistant"
            and history[-1].get("content") == question
        ):
            history.append({"role": "assistant", "content": question})
    current["conversation_history"] = history[-8:]
    return current


def _domain_context_snapshot(
    domain_result: dict[str, Any] | None,
    *,
    fallback: object,
) -> dict[str, Any]:
    if not isinstance(domain_result, dict):
        return dict(fallback) if isinstance(fallback, dict) else {}
    envelope = domain_result
    for key in ("weather_data", "data_envelope", "domain_data"):
        candidate = domain_result.get(key)
        if isinstance(candidate, dict) and candidate.get("domain"):
            envelope = candidate
            break
    snapshot = {
        "domain": envelope.get("domain"),
        "schema_version": envelope.get("schema_version"),
        "available_fields": envelope.get("available_fields", []),
    }
    return {key: value for key, value in snapshot.items() if value not in (None, [], {})}


def _template_id_from_visualization_request(
    state: GraphState,
    visualization_request: dict[str, Any],
) -> str | None:
    template_id = visualization_request.get("template_id")
    if isinstance(template_id, str) and template_id.strip():
        return template_id.strip()

    template_index = visualization_request.get("template_index")
    available_templates = state.get("available_templates", [])
    if isinstance(template_index, int) and isinstance(available_templates, list):
        selected = _template_from_index(available_templates, template_index)
        if selected:
            return selected
    return None


def _template_from_index(available_templates: list[object], template_index: int) -> str | None:
    if template_index < 1 or template_index > len(available_templates):
        return None
    template = available_templates[template_index - 1]
    if isinstance(template, dict) and isinstance(template.get("id"), str):
        return template["id"]
    return None


def _visualization_mode(visualization_request: dict[str, Any], *, template_id: str | None) -> str:
    mode = visualization_request.get("mode")
    if mode in {"choose", "create", "customize"}:
        return mode
    if template_id:
        return "choose"
    return "auto"


def _visualization_result_dict(result: object) -> dict[str, Any]:
    if isinstance(result, VisualizationResult):
        return {
            "ok": result.ok,
            "mode": result.mode,
            "template_id": result.template_id,
            "html": result.html,
            "html_path": result.html_path,
            "available_templates": result.available_templates,
            "message": result.message,
            "errors": result.errors,
            "metadata": result.metadata,
        }
    if isinstance(result, dict):
        return result
    return {
        "ok": False,
        "message": "Visualization orchestrator returned an unsupported result.",
        "errors": ["unsupported_visualization_result"],
    }


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""
