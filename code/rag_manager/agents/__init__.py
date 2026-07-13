"""Agent implementations."""

from rag_manager.agents.aggregator import aggregate_agent_outputs
from rag_manager.agents.aggregator import build_no_output_response
from rag_manager.agents.aggregator import build_aggregator_payload
from rag_manager.agents.aggregator import get_single_agent_answer
from rag_manager.agents.aggregator import has_agent_outputs
from rag_manager.agents.aggregator import run_aggregator_agent
from rag_manager.agents.news import build_news_query
from rag_manager.agents.news import format_news_answer
from rag_manager.agents.news import run_news_agent
from rag_manager.agents.weather import run_weather_agent, run_weather_tool_agent
from rag_manager.agents.wiki import build_wiki_topic
from rag_manager.agents.wiki import format_wiki_answer
from rag_manager.agents.wiki import run_wiki_agent

__all__ = [
    "aggregate_agent_outputs",
    "build_aggregator_payload",
    "build_news_query",
    "build_no_output_response",
    "build_wiki_topic",
    "format_news_answer",
    "format_wiki_answer",
    "get_single_agent_answer",
    "has_agent_outputs",
    "run_aggregator_agent",
    "run_news_agent",
    "run_weather_agent",
    "run_weather_tool_agent",
    "run_wiki_agent",
]
