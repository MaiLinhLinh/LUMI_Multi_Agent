"""Structured Output schema for the routing-only Manager Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AgentTopic = Literal["weather", "news", "wiki"]
ExecutionMode = Literal["single", "parallel", "sequential"]


class PlanDependencyResponse(BaseModel):
    """One dependency edge in a sequential execution plan."""

    from_topic: AgentTopic = Field(description="Topic whose result is needed first.")
    to_topic: AgentTopic = Field(description="Topic that consumes the earlier result.")


class ManagerPlanResponse(BaseModel):
    """Six-field routing contract constrained by the Gemini API."""

    topics: list[AgentTopic] = Field(
        min_length=1,
        description="Unique selected topics in execution order.",
    )
    execution_mode: ExecutionMode
    primary_intent: AgentTopic
    dependencies: list[PlanDependencyResponse]
    news_query: str = Field(description="News search query, or an empty string.")
    wiki_topic: str = Field(description="Wikipedia topic, or an empty string.")
