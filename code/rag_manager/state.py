"""Shared state schema for the LangGraph workflow."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


ExecutionMode = Literal["single", "parallel", "sequential"]
InputRoute = Literal["domain", "social", "visualize"]
AgentTopic = Literal["weather", "news", "wiki", "music"]
WeatherStatus = Literal[
    "needs_clarification",
    "unavailable",
    "error",
    "completed",
]
MusicStatus = Literal[
    "not_configured",
    "needs_clarification",
    "unavailable",
    "error",
    "completed",
]


class PlanDependency(TypedDict):
    """Dependency between two agent topics in a sequential plan."""

    from_topic: AgentTopic
    to_topic: AgentTopic


class ManagerPlan(TypedDict):
    """Six-field routing plan produced by the Manager Agent."""

    topics: list[AgentTopic]
    execution_mode: ExecutionMode
    primary_intent: AgentTopic
    dependencies: list[PlanDependency]
    news_query: str
    wiki_topic: str


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
    music: float
    aggregate: float
    total: float


class LlmUsageStats(TypedDict, total=False):
    """LLM token/cache metadata grouped by agent."""

    manager: dict[str, Any]
    weather: dict[str, Any]
    news: dict[str, Any]
    wiki: dict[str, Any]
    music: dict[str, Any]
    aggregate: dict[str, Any]


class AgentError(TypedDict):
    """Recoverable error captured during graph execution."""

    source: str
    message: str


class AgentState(TypedDict, total=False):
    """State passed between LangGraph nodes."""

    query: str
    history: list[dict[str, Any]]
    settings: Any
    manager_client: Any
    semantic_router_client: Any
    weather_store: Any
    weather_client: Any
    weather_session: dict[str, Any]
    news_cache: Any
    news_client: Any
    wiki_cache: Any
    wiki_client: Any
    music_client: Any
    music_search_service: Any
    music_session: dict[str, Any]
    music_player: dict[str, Any]
    response_stream_callback: Any
    aggregator_client: Any
    visualization_orchestrator: Any
    input_route: InputRoute
    intent: ManagerPlan
    execution_mode: ExecutionMode
    selected_agents: list[AgentTopic]
    context: dict[str, Any]
    weather_data: dict[str, Any]
    weather_answer: str
    weather_status: WeatherStatus
    weather_error: dict[str, Any]
    news_data: dict[str, Any]
    news_answer: str
    wiki_data: dict[str, Any]
    wiki_answer: str
    music_data: dict[str, Any]
    music_answer: str
    music_status: MusicStatus
    music_error: dict[str, Any]
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
