"""Shared state schema for the LangGraph workflow."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


ExecutionMode = Literal["single", "parallel", "sequential"]
InputRoute = Literal["domain", "visualize"]
AgentTopic = Literal["weather", "news", "wiki"]


class PlanDependency(TypedDict):
    """Dependency between two agent topics in a sequential plan."""

    from_topic: AgentTopic
    to_topic: AgentTopic
    reason: str


class ManagerPlan(TypedDict):
    """Validated routing plan produced by the Manager Agent."""

    topics: list[AgentTopic]
    execution_mode: ExecutionMode
    primary_intent: AgentTopic
    dependencies: list[PlanDependency]
    location: str
    news_query: str
    wiki_topic: str
    reason: str


class CacheStats(TypedDict, total=False):
    """Cache hit/miss counters grouped by agent or data source."""

    weather: dict[str, int]
    news: dict[str, int]
    wiki: dict[str, int]


class TimingStats(TypedDict, total=False):
    """Latency measurements in seconds."""

    manager: float
    weather: float
    news: float
    wiki: float
    aggregate: float
    total: float


class LlmUsageStats(TypedDict, total=False):
    """LLM token/cache metadata grouped by agent."""

    manager: dict[str, Any]
    weather: dict[str, Any]
    news: dict[str, Any]
    wiki: dict[str, Any]
    aggregate: dict[str, Any]


class AgentError(TypedDict):
    """Recoverable error captured during graph execution."""

    source: str
    message: str


class AgentState(TypedDict, total=False):
    """State passed between LangGraph nodes."""

    query: str
    history: list[dict[str, str]]
    settings: Any
    manager_client: Any
    semantic_router_client: Any
    weather_cache: Any
    weather_client: Any
    news_cache: Any
    news_client: Any
    wiki_cache: Any
    wiki_client: Any
    aggregator_client: Any
    visualization_orchestrator: Any
    input_route: InputRoute
    intent: ManagerPlan
    execution_mode: ExecutionMode
    selected_agents: list[AgentTopic]
    context: dict[str, Any]
    weather_data: dict[str, Any]
    weather_answer: str
    news_data: dict[str, Any]
    news_answer: str
    wiki_data: dict[str, Any]
    wiki_answer: str
    final_response: str
    visualization_request: dict[str, Any]
    visualization_context: dict[str, Any]
    semantic_result: dict[str, Any]
    visualization_output: dict[str, Any]
    visualization_html_path: str
    last_domain_result: dict[str, Any]
    available_templates: list[dict[str, Any]]
    active_template_id: str
    active_template_path: str
    pending_visualization_action: str
    pending_template_state: dict[str, Any]
    template_requirements: dict[str, Any]
    template_clarification_round: int
    cache_stats: CacheStats
    timings: TimingStats
    llm_usage: LlmUsageStats
    errors: list[AgentError]


GraphState = AgentState
