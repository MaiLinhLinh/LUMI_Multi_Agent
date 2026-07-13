"""Thin visualization orchestrator for deterministic render routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from rag_manager.visualization.registry import (
    TemplateRegistryError,
    lookup_template,
    recommend_templates,
)
from rag_manager.visualization.renderer import render_template, save_visualization_output
from rag_manager.visualization.template_agent import TemplateAgentWorkflow


VisualizationMode = Literal["auto", "choose", "create", "customize", "design"]
CREATE_NEW_TEMPLATE_ID = "__create_new_template__"


@dataclass(frozen=True)
class VisualizationRequest:
    """Input for a visualization orchestration run."""

    domain_result: dict[str, Any] | None = None
    mode: VisualizationMode = "auto"
    template_id: str | None = None
    output_dir: str | Path | None = None
    user_request: str = ""
    previous_template_state: dict[str, Any] | None = None
    action: str = ""
    modification_request: str = ""
    source_template_id: str | None = None
    semantic_result: dict[str, Any] | None = None
    requirements: dict[str, Any] = field(default_factory=dict)
    visualization_context: dict[str, Any] = field(default_factory=dict)
    request_id: str = "visualization"


@dataclass
class VisualizationResult:
    """Result returned by the visualization orchestrator."""

    ok: bool
    mode: str
    template_id: str | None = None
    html: str = ""
    html_path: str | None = None
    available_templates: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class VisualizationOrchestrator:
    """Route visualization requests without owning template details."""

    def __init__(self, template_agent_workflow: TemplateAgentWorkflow | None = None) -> None:
        self.template_agent_workflow = template_agent_workflow or TemplateAgentWorkflow()

    def run(self, request: VisualizationRequest | dict[str, Any]) -> VisualizationResult:
        """Run the visualization route selected by request mode/template."""

        normalized_request = _normalize_request(request)
        if normalized_request.semantic_result is not None:
            return self._execute_semantic_result(normalized_request)
        if normalized_request.action == "template_request":
            return VisualizationResult(
                ok=False,
                mode="auto",
                message="Semantic router result is required for a template request.",
                errors=["missing_semantic_router_result"],
            )
        if normalized_request.mode in {"create", "customize", "design"}:
            return self._run_template_agent(normalized_request)
        return self._run_existing_template(normalized_request)

    def _execute_semantic_result(self, request: VisualizationRequest) -> VisualizationResult:
        """Execute a validated Semantic Router result without another LLM call."""

        semantic = request.semantic_result or {}
        status = semantic.get("status")
        if status == "cancelled":
            return VisualizationResult(
                ok=False, mode="auto", message="Đã hủy yêu cầu template.",
                errors=["template_request_cancelled"],
            )
        template = semantic.get("template", {})
        if not isinstance(template, dict):
            return VisualizationResult(
                ok=False, mode="auto", message="Invalid semantic template result.",
                errors=["invalid_semantic_router_result"],
            )
        action = template.get("action")
        # LLM1 is not responsible for design clarification. If it already
        # identified design_template, let LLM2 inspect the raw query and
        # template metadata instead.
        if status == "needs_clarification" and action != "design_template":
            return VisualizationResult(
                ok=False,
                mode="auto",
                message=_string_value(semantic.get("clarifying_question"))
                or "Bạn có thể mô tả rõ hơn yêu cầu template không?",
                errors=["missing_template_requirements"],
                metadata={"semantic_result": semantic},
            )
        available_templates = self._compatible_templates(request, include_create_action=True)
        if action == "show_options":
            return VisualizationResult(
                ok=False,
                mode="choose",
                available_templates=available_templates,
                message=_template_choice_message(available_templates),
                errors=["template_selection_required"],
                metadata={"semantic_result": semantic},
            )
        if action == "select_existing":
            action_payload = {
                "selection": {
                    "index": template.get("selection_index"),
                    "template_id": template.get("template_id"),
                }
            }
            selected_id = _resolve_action_template_id(action_payload, available_templates)
            if not selected_id:
                return VisualizationResult(
                    ok=False, mode="choose", available_templates=available_templates,
                    message="Không xác định được template bạn muốn chọn.",
                    errors=["invalid_template_selection"],
                )
            return self._run_existing_template(
                VisualizationRequest(
                    domain_result=request.domain_result,
                    mode="choose",
                    template_id=selected_id,
                    output_dir=request.output_dir,
                )
            )
        if action == "design_template":
            source_template_id = template.get("template_id")
            if template.get("source") == "current":
                source_template_id = source_template_id or request.source_template_id
            design_request = VisualizationRequest(
                domain_result=request.domain_result,
                mode="design",
                template_id=source_template_id,
                source_template_id=source_template_id,
                output_dir=request.output_dir,
                user_request=request.user_request,
                previous_template_state=request.previous_template_state,
                action="design_template",
                requirements=template.get("requirements", {})
                if isinstance(template.get("requirements", {}), dict)
                else {},
                visualization_context=request.visualization_context,
                semantic_result=semantic,
            )
            return self._run_template_agent(design_request)
        if action == "cancel":
            return VisualizationResult(
                ok=False, mode="auto", message="Đã hủy yêu cầu template.",
                errors=["template_request_cancelled"],
            )
        return VisualizationResult(
            ok=False, mode="auto", message="Không xác định được template action.",
            errors=["invalid_semantic_router_result"],
        )

    def _run_existing_template(self, request: VisualizationRequest) -> VisualizationResult:
        envelope = _extract_envelope(request.domain_result)
        if not envelope:
            return VisualizationResult(
                ok=False,
                mode=request.mode,
                message="Can co du lieu domain truoc khi tao visualization.",
                errors=["missing_domain_result"],
            )

        
        available_templates = self._compatible_templates(request)
        if request.mode == "choose" and not request.template_id and not any(
            item.get("id") == CREATE_NEW_TEMPLATE_ID for item in available_templates
            if isinstance(item, dict)
        ):
            available_templates = [
                *available_templates,
                {
                    "id": CREATE_NEW_TEMPLATE_ID,
                    "kind": "action",
                    "type": "create_new_template",
                    "name": "Tạo template mới",
                    "description": "Tạo giao diện mới theo mô tả của bạn.",
                },
            ]

        # A bare "change template" request is a selection step, not an
        # instruction to silently render the first template again.
        if request.mode == "choose" and not request.template_id:
            return VisualizationResult(
                ok=False,
                mode=request.mode,
                available_templates=available_templates,
                message=_template_choice_message(available_templates),
                errors=["template_selection_required"],
            )

        try:
            template_id = request.template_id or _first_template_id(available_templates)
            if not template_id:
                return VisualizationResult(
                    ok=False,
                    mode=request.mode,
                    available_templates=available_templates,
                    message="Khong tim thay template visualization phu hop voi du lieu hien tai.",
                    errors=["no_compatible_template"],
                )
            template_asset = lookup_template(template_id)
            if request.template_id and not any(
                template.get("id") == request.template_id for template in available_templates
            ):
                return VisualizationResult(
                    ok=False,
                    mode=request.mode,
                    template_id=request.template_id,
                    available_templates=available_templates,
                    message=(
                        f"Template '{request.template_id}' không tương thích với dữ liệu hiện tại."
                    ),
                    errors=["incompatible_template"],
                )
        except TemplateRegistryError as exc:
            return VisualizationResult(
                ok=False,
                mode=request.mode,
                available_templates=available_templates,
                message=str(exc),
                errors=["template_lookup_failed"],
            )

        template_html = template_asset.template_path.read_text(encoding="utf-8")
        render_data = _render_data_from_envelope(envelope)
        html = render_template(
            template_html=template_html,
            answer=_extract_answer(request.domain_result),
            data=render_data,
        )
        html_path = save_visualization_output(html, output_dir=request.output_dir)
        return VisualizationResult(
            ok=True,
            mode=request.mode,
            template_id=template_asset.template_id,
            html=html,
            html_path=str(html_path),
            available_templates=available_templates,
            message="Visualization rendered.",
        )

    def _compatible_templates(
        self,
        request: VisualizationRequest,
        *,
        include_create_action: bool = False,
    ) -> list[dict[str, Any]]:
        envelope = _extract_envelope(request.domain_result)
        if not envelope:
            return []
        templates = recommend_templates(
            domain=_string_value(envelope.get("domain")),
            schema_version=_string_value(envelope.get("schema_version")),
            available_fields=_string_list(envelope.get("available_fields")),
        )
        if include_create_action:
            templates = [
                *templates,
                {
                    "id": CREATE_NEW_TEMPLATE_ID,
                    "kind": "action",
                    "type": "create_new_template",
                    "name": "Tạo template mới",
                    "description": "Tạo giao diện mới theo mô tả.",
                },
            ]
        return templates

    def _run_template_agent(self, request: VisualizationRequest) -> VisualizationResult:
        result = self.template_agent_workflow.run(request)
        if isinstance(result, VisualizationResult):
            return result
        if isinstance(result, dict):
            return VisualizationResult(
                ok=bool(result.get("ok")),
                mode=request.mode,
                template_id=_optional_string(result.get("template_id")),
                html=_string_value(result.get("html")),
                html_path=_optional_string(result.get("html_path")),
                available_templates=result.get("available_templates", [])
                if isinstance(result.get("available_templates"), list)
                else [],
                message=_string_value(result.get("message")),
                errors=result.get("errors", []) if isinstance(result.get("errors"), list) else [],
                metadata=result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {},
            )
        return VisualizationResult(
            ok=False,
            mode=request.mode,
            message="Template Agent workflow returned an unsupported result.",
            errors=["unsupported_template_agent_result"],
        )


def _normalize_request(request: VisualizationRequest | dict[str, Any]) -> VisualizationRequest:
    if isinstance(request, VisualizationRequest):
        return request
    if isinstance(request, dict):
        mode = request.get("mode", "auto")
        if mode not in {"auto", "choose", "create", "customize", "design"}:
            mode = "auto"
        return VisualizationRequest(
            domain_result=request.get("domain_result")
            if isinstance(request.get("domain_result"), dict)
            else None,
            mode=mode,
            template_id=_optional_string(request.get("template_id")),
            output_dir=request.get("output_dir"),
            user_request=_string_value(request.get("user_request")),
            previous_template_state=(
                request.get("previous_template_state")
                if isinstance(request.get("previous_template_state"), dict)
                else None
            ),
            action=_string_value(request.get("action")),
            modification_request=_string_value(request.get("modification_request")),
            source_template_id=_optional_string(request.get("source_template_id")),
            semantic_result=(
                request.get("semantic_result")
                if isinstance(request.get("semantic_result"), dict)
                else None
            ),
            requirements=request.get("requirements")
            if isinstance(request.get("requirements"), dict)
            else {},
            visualization_context=request.get("visualization_context")
            if isinstance(request.get("visualization_context"), dict)
            else {},
            request_id=_string_value(request.get("request_id")) or "visualization",
        )
    return VisualizationRequest()


def _extract_envelope(domain_result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(domain_result, dict):
        return {}
    for key in ("weather_data", "data_envelope", "domain_data"):
        value = domain_result.get(key)
        if isinstance(value, dict) and value.get("domain"):
            return value
    if domain_result.get("domain") and isinstance(domain_result.get("data"), dict):
        return domain_result
    return {}


def _extract_answer(domain_result: dict[str, Any] | None) -> str:
    if not isinstance(domain_result, dict):
        return ""
    for key in ("answer", "weather_answer", "final_response"):
        value = domain_result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _render_data_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope.get("data")
    render_data = dict(data) if isinstance(data, dict) else {}
    source = envelope.get("source")
    if isinstance(source, dict):
        render_data["source"] = source
    return render_data


def _first_template_id(templates: list[dict[str, Any]]) -> str | None:
    if not templates:
        return None
    template_id = templates[0].get("id")
    return template_id if isinstance(template_id, str) else None


def _resolve_action_template_id(
    action: dict[str, Any],
    available_templates: list[dict[str, Any]],
) -> str | None:
    selection = action.get("selection", {})
    if not isinstance(selection, dict):
        return None
    template_id = selection.get("template_id")
    if isinstance(template_id, str) and template_id != CREATE_NEW_TEMPLATE_ID:
        return template_id
    index = selection.get("index")
    if not isinstance(index, int) or index < 1 or index > len(available_templates):
        return None
    selected = available_templates[index - 1]
    if not isinstance(selected, dict):
        return None
    selected_id = selected.get("id")
    if selected_id == CREATE_NEW_TEMPLATE_ID:
        return None
    return selected_id if isinstance(selected_id, str) else None


def _template_choice_message(templates: list[dict[str, Any]]) -> str:
    if not templates:
        return "Không có template phù hợp với dữ liệu hiện tại."
    lines = ["Bạn có thể chọn template bằng cách nhập 'chọn mẫu <số>':"]
    for index, template in enumerate(templates, start=1):
        template_id = template.get("id", "unknown")
        if template_id == CREATE_NEW_TEMPLATE_ID:
            lines.append(f"{index}. Tạo template mới — mô tả giao diện bạn mong muốn")
            continue
        name = template.get("name", template_id)
        description = template.get("description", "")
        suffix = f" — {description}" if description else ""
        lines.append(f"{index}. {name} ({template_id}){suffix}")
    return "\n".join(lines)


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
