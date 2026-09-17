"""Turn successful Plan Agent tool results into small trusted progress facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from gemini_live_2.plan_agent.tools import (
    DESCRIBE_WIDGETS,
    SEARCH_IMAGE,
    SEARCH_WEB,
    TOOL_RESULT_PROGRESS_METADATA,
)


@dataclass(frozen=True, slots=True)
class ToolResultAvailable:
    """One successful native-tool result, before Plan Agent consumes it again."""

    plan_run_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    response: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PlanProgress:
    """A bounded, trusted fact for the Gemini bridge."""

    plan_run_id: str
    message: str


class ProgressReporter:
    """Formats only explicitly supported tool-result facts; it never infers state."""

    def build(self, event: ToolResultAvailable) -> PlanProgress | None:
        if event.tool_name == SEARCH_WEB:
            title = self._first_search_field(event.response, "title")
            return self._message(event.plan_run_id, f"Đã tìm được nguồn “{title}”.") if title else None
        if event.tool_name == SEARCH_IMAGE:
            caption = self._first_search_field(event.response, "caption")
            return self._message(event.plan_run_id, f"Đã tìm được hình ảnh “{caption}”.") if caption else None
        metadata = TOOL_RESULT_PROGRESS_METADATA.get(event.tool_name)
        message = metadata.get("on_results_message") if metadata else None
        return self._message(event.plan_run_id, message) if isinstance(message, str) else None

    @staticmethod
    def _message(plan_run_id: str, message: str) -> PlanProgress:
        return PlanProgress(plan_run_id=plan_run_id, message=message)

    @staticmethod
    def _first_search_field(response: Mapping[str, Any], field: str) -> str | None:
        verified_data = response.get("verified_data")
        if not isinstance(verified_data, Mapping):
            return None
        data = verified_data.get("data")
        if not isinstance(data, Mapping):
            return None
        results = data.get("search_results")
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        if not isinstance(first, Mapping):
            return None
        value = first.get(field)
        return value.strip() if isinstance(value, str) and value.strip() else None
