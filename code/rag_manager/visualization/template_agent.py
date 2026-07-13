"""Template Agent workflow with deterministic guardrails."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import unicodedata

from rag_manager.visualization.assembler import (
    assemble_existing_template,
    assemble_template_from_base,
    assemble_template_from_base_source,
)
from rag_manager.visualization.artifacts import ArtifactError, ArtifactStore
from rag_manager.visualization.components import (
    filter_visible_components,
    search_components_by_metadata,
)
from rag_manager.visualization.execution_plan import (
    ExecutionPlanError,
    build_runtime_assembly_input,
    validate_execution_plan,
)
from rag_manager.visualization.inspector import inspect_actual_data
from rag_manager.visualization.llm_output import (
    LlmOutputError,
    parse_llm_json_response,
    validate_component_selection_output,
    validate_fill_plan_output,
    validate_requirements_output,
    validate_strategy_output,
    validate_todo_list_output,
)
from rag_manager.visualization.paths import resolve_asset_path, resolve_output_dir
from rag_manager.visualization.prompt_loader import (
    PromptAssetError,
    load_template_agent_prompt,
    render_prompt,
)
from rag_manager.visualization.registry import lookup_template, recommend_templates
from rag_manager.visualization.renderer import render_template, save_visualization_output
from rag_manager.visualization.validator import (
    VisualizationValidationError,
    canonicalize_color,
    validate_color_value,
    validate_security,
    validate_template_syntax,
)
from rag_manager.visualization.tools import execute_tool, validate_tool_request, validate_tool_result


class TemplateAgentError(ValueError):
    """Raised when Template Agent output violates deterministic constraints."""


@dataclass
class TemplateAgentResult:
    """Result returned by the Template Agent workflow."""

    ok: bool
    mode: str
    template_id: str | None = None
    html: str = ""
    html_path: str | None = None
    template_path: str | None = None
    available_templates: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TemplateAgentWorkflow:
    """Create or customize templates using fixed deterministic steps."""

    def __init__(self, llm: Any | None = None, *, max_repair_attempts: int = 1) -> None:
        self.llm = llm
        self.max_repair_attempts = max(0, max_repair_attempts)

    def run(self, request: Any) -> dict[str, Any]:
        """Run deterministic workflow; LLM wrappers are constrained by candidates."""

        try:
            result = self._run(request)
        except (TemplateAgentError, LlmOutputError, PromptAssetError, VisualizationValidationError) as exc:
            return {
                "ok": False,
                "mode": _request_mode(request),
                "message": str(exc),
                "errors": ["template_agent_validation_failed"],
            }
        return _result_dict(result)

    def _run(self, request: Any) -> TemplateAgentResult:
        if getattr(request, "action", "") == "cancel_template":
            return TemplateAgentResult(
                ok=False,
                mode=_request_mode(request),
                message="Đã hủy yêu cầu tạo template.",
                errors=["template_request_cancelled"],
            )
        # New requests arrive with a validated Semantic Router result. Do not
        # interpret the user's prose again; legacy direct callers may still
        # use the requirement extractor for backward compatibility.
        semantic_result = getattr(request, "semantic_result", None)
        provided_requirements = getattr(request, "requirements", None)
        if isinstance(semantic_result, dict):
            semantic_template = semantic_result.get("template", {})
            is_design_request = (
                isinstance(semantic_template, dict)
                and semantic_template.get("action") == "design_template"
            )
            requirements_output = {
                "status": "ready" if is_design_request else semantic_result.get("status", "ready"),
                # LLM1 is intentionally thin. LLM2 receives the original
                # query and owns all detailed requirement extraction.
                "requirements": {},
                "missing_information": semantic_result.get("missing_information", []),
                "clarifying_question": semantic_result.get("clarifying_question"),
            }
        else:
            requirements_output = self.llm_extract_requirements(request)
        if requirements_output.get("status") == "cancelled":
            return TemplateAgentResult(
                ok=False,
                mode=_request_mode(request),
                message="Đã hủy yêu cầu tạo template.",
                errors=["template_request_cancelled"],
            )
        if requirements_output.get("status") == "needs_clarification":
            requirements = requirements_output.get("requirements", {})
            pending_state = {
                "status": "collecting_requirements",
                "action": _request_mode(request),
                "requirements": requirements if isinstance(requirements, dict) else {},
                "missing_information": requirements_output.get("missing_information", []),
                "clarification_round": _request_clarification_round(request) + 1,
            }
            return TemplateAgentResult(
                ok=False,
                mode=_request_mode(request),
                message=_string_value(requirements_output.get("clarifying_question"))
                or "Bạn có thể mô tả thêm giao diện mong muốn không?",
                errors=["missing_template_requirements"],
                metadata={
                    "requirements": requirements_output,
                    "pending_template_state": pending_state,
                },
            )

        requirements = requirements_output.get("requirements", {})
        if not isinstance(requirements, dict):
            raise TemplateAgentError("Extracted template requirements must be an object.")
        requirements = _normalize_planner_requirements(requirements)
        source_template_id = getattr(request, "source_template_id", None)
        if isinstance(source_template_id, str) and source_template_id.strip():
            requirements = {
                **requirements,
                "source_template_id": source_template_id.strip(),
                "preserve_layout": True,
            }

        domain_result = _request_domain_result(request)
        envelope = _extract_envelope(domain_result)
        if not envelope:
            raise TemplateAgentError("Can co du lieu domain truoc khi tao template.")

        inspection = inspect_actual_data(envelope)
        domain = inspection["domain"]
        schema_version = inspection["schema_version"]
        available_fields = inspection["available_fields"]
        complete_templates = recommend_templates(
            domain=domain,
            schema_version=schema_version,
            available_fields=available_fields,
            filters={"requirements": requirements},
        )
        base_templates = search_base_templates_by_metadata(
            domain=domain,
            requirements=requirements,
        )
        components = search_components_by_metadata(
            domain=domain,
        )
        visible = filter_visible_components(components, available_fields)

        # The typed planner is the canonical path for design_template and for
        # any request explicitly anchored to the active template.  The legacy
        # base workflow below remains available for old direct callers.
        if _should_use_typed_planner(request, source_template_id):
            return self._run_typed_plan(
                request=request,
                requirements=requirements,
                inspection=inspection,
                complete_templates=complete_templates,
                base_templates=base_templates,
                visible_components=visible["visible_components"],
                available_fields=available_fields,
                domain_result=domain_result,
            )

        strategy = self.llm_decide_template_strategy(
            request=request,
            requirements=requirements,
            complete_templates=complete_templates,
            base_templates=base_templates,
            visible_components=visible["visible_components"],
        )

        # A create/customize request must produce a new artifact when it
        # contains user requirements. An existing compatible template is a
        # candidate/source, not a completed result for this workflow.
        if (
            strategy.get("strategy") == "existing_template"
            and _request_mode(request) in {"create", "customize", "design"}
            and _request_user_request(request).strip()
        ):
            base_id = _first_valid_base_id(base_templates)
            if not base_id:
                raise TemplateAgentError("No compatible base template is available for customization.")
            strategy = {
                "strategy": "assemble_base",
                "base_template": base_id,
                "reason": "Existing match used as a candidate; user requested a generated/customized artifact.",
            }

        if strategy.get("strategy") == "existing_template":
            template_id = _required_candidate_id(
                strategy,
                "template_id",
                {template["id"] for template in complete_templates if isinstance(template.get("id"), str)},
            )
            return TemplateAgentResult(
                ok=True,
                mode=_request_mode(request),
                template_id=template_id,
                available_templates=complete_templates,
                message="Existing template match selected.",
                metadata={"strategy": strategy, "inspection": inspection},
            )

        generated_base: dict[str, Any] | None = None
        if strategy.get("strategy") == "create_new_base_template":
            generated_base = self.llm_generate_new_base(
                request=request,
                requirements=requirements,
                base_templates=base_templates,
                visible_components=visible["visible_components"],
            )
            base_id = str(generated_base["metadata"]["id"])
            base_templates_for_validation = [generated_base["metadata"]]
        elif strategy.get("strategy") == "assemble_base":
            base_id = _required_candidate_id(
                strategy,
                "base_template",
                {base["id"] for base in base_templates if isinstance(base.get("id"), str)},
            )
            base_templates_for_validation = base_templates
        else:
            raise TemplateAgentError(
                "Template strategy must be existing_template, assemble_base, or create_new_base_template."
            )
        selected_components = self.llm_select_components(
            request=request,
            requirements=requirements,
            base_template=base_id,
            visible_components=visible["visible_components"],
        )
        _validate_selected_components(selected_components, visible["visible_components"])

        todo_list = self.llm_generate_todo_list(
            request=request,
            requirements=requirements,
            selected_components=selected_components,
        )
        fill_plan = self.llm_generate_fill_plan(
            request=request,
            requirements=requirements,
            base_template=base_id,
            selected_components=selected_components,
        )
        if generated_base is None:
            template_html, fill_plan = self._assemble_with_repair(
                request=request,
                fill_plan=fill_plan,
                base_templates=base_templates_for_validation,
                visible_components=visible["visible_components"],
                available_fields=available_fields,
            )
        else:
            generated_base["path"] = str(
                save_base_template(
                    base_html=generated_base["base_html"],
                    contract=generated_base["contract"],
                    metadata=generated_base["metadata"],
                    output_dir=_request_output_dir(request),
                )
            )
            template_html = assemble_template_from_base_source(
                fill_plan,
                base_html=generated_base["base_html"],
                contract=generated_base["contract"],
                available_fields=available_fields,
            )
        template_html = _apply_style_modifications(
            template_html,
            requirements=requirements,
            request_text=_request_user_request(request),
        )
        render_data = _render_data_from_envelope(envelope)
        preview_html = render_template(
            template_html,
            answer=_extract_answer(domain_result),
            data=render_data,
        )
        html_path = save_visualization_output(preview_html, output_dir=_request_output_dir(request))
        template_path = save_template(
            template_html=template_html,
            fill_plan=fill_plan,
            metadata={
                "source": {
                    "base_template": base_id,
                    "components": selected_components,
                    "todo_list": todo_list,
                    "requirements": requirements,
                    "strategy": strategy,
                    "validation": "deterministic",
                },
                "domain": domain,
                "schema_version": schema_version,
                "generated_base_id": (
                    generated_base["metadata"]["id"] if generated_base else None
                ),
            },
            output_dir=_request_output_dir(request),
        )

        return TemplateAgentResult(
            ok=True,
            mode=_request_mode(request),
            template_id=Path(template_path).parent.name,
            html=preview_html,
            html_path=str(html_path),
            template_path=str(template_path),
            available_templates=complete_templates,
            message="Generated template artifact.",
            metadata={"fill_plan": fill_plan, "todo_list": todo_list, "inspection": inspection},
        )

    def _run_typed_plan(
        self,
        *,
        request: Any,
        requirements: dict[str, Any],
        inspection: dict[str, Any],
        complete_templates: list[dict[str, Any]],
        base_templates: list[dict[str, Any]],
        visible_components: list[dict[str, Any]],
        available_fields: list[str],
        domain_result: dict[str, Any] | None,
    ) -> TemplateAgentResult:
        """Execute LLM2 → optional LLM3 → LLM4 → Assembler deterministically."""

        plan = self.llm_general_plan(
            request=request,
            requirements=requirements,
            complete_templates=complete_templates,
            base_templates=base_templates,
            visible_components=visible_components,
            inspection=inspection,
        )
        if isinstance(plan, dict) and plan.get("status") != "needs_clarification":
            forced_clarification = _deterministic_planner_clarification(
                plan,
                request=request,
                active_metadata=_active_template_metadata(
                    getattr(request, "source_template_id", None) or getattr(request, "template_id", None)
                ),
            )
            if forced_clarification is not None:
                plan = forced_clarification
        if isinstance(plan, dict) and plan.get("status") == "needs_clarification":
            clarification = plan.get("clarification", {})
            if not isinstance(clarification, dict):
                raise TemplateAgentError("LLM2 clarification must be an object.")
            question = _string_value(clarification.get("question"))
            if not question:
                raise TemplateAgentError("LLM2 clarification requires a question.")
            planner_requirements = plan.get("requirements", {})
            if not isinstance(planner_requirements, dict):
                planner_requirements = {}
            pending_state = {
                "status": "collecting_planner_clarification",
                "action": _request_mode(request),
                "original_query": _request_user_request(request),
                "template_id": getattr(request, "source_template_id", None) or getattr(request, "template_id", None),
                "requirements": requirements,
                "merged_requirements": planner_requirements,
                "clarification": clarification,
                "clarification_round": _request_clarification_round(request) + 1,
            }
            return TemplateAgentResult(
                ok=False,
                mode=_request_mode(request),
                template_id=getattr(request, "source_template_id", None),
                message=question,
                errors=["llm2_needs_clarification"],
                metadata={"pending_template_state": pending_state, "planner_response": plan},
            )
        plan = _repair_visual_asset_plan(plan, request=request)
        plan = validate_execution_plan(plan)
        extracted_requirements = plan.get("requirements")
        if isinstance(extracted_requirements, dict) and extracted_requirements:
            requirements = extracted_requirements
        artifact_store = ArtifactStore(_request_output_dir(request))
        generated_component_refs: list[dict[str, Any]] = []
        generated_base_ref: dict[str, Any] | None = None

        for generation_request in plan["generation_plan"]["components"]:
            artifact = self.llm_generate_component(
                request=request,
                requirements=requirements,
                generation_request=generation_request,
            )
            staged = artifact_store.stage_artifact(
                {"component_html": artifact["component_html"], "metadata": artifact["metadata"]},
                {"request_id": _request_id(request), "kind": "component", "source": "llm3"},
            )
            validated = artifact_store.validate_artifact(staged.artifact_id)
            generated_component_refs.append(
                {
                    "ref_type": "artifact",
                    "artifact_id": validated.artifact_id,
                    "kind": "component",
                    "status": "validated",
                    "slot": generation_request.get("slot", "metrics"),
                }
            )

        generation_base = plan["generation_plan"].get("base")
        if isinstance(generation_base, dict):
            generated = self.llm_generate_new_base(
                request=request,
                requirements=requirements,
                base_templates=base_templates,
                visible_components=visible_components,
            )
            staged = artifact_store.stage_artifact(
                {"base_html": generated["base_html"], "contract": generated["contract"], "metadata": generated["metadata"]},
                {"request_id": _request_id(request), "kind": "base_template", "source": "llm3"},
            )
            validated = artifact_store.validate_artifact(staged.artifact_id)
            generated_base_ref = {
                "ref_type": "artifact",
                "artifact_id": validated.artifact_id,
                "kind": "base_template",
                "status": "validated",
            }

        assembly_input = build_runtime_assembly_input(
            plan,
            generated_base_ref=generated_base_ref,
            generated_component_refs=generated_component_refs,
        )
        active_metadata = _active_template_metadata(_assembly_template_id(assembly_input))
        assembly_input["style_targets"] = active_metadata.get("style_targets", [])
        assembly_input["extension_points"] = active_metadata.get("extension_points", [])
        fill_plan = self.llm4_fill_plan(
            request=request,
            execution_plan=plan,
            assembly_input=assembly_input,
        )
        fill_plan = _complete_base_fill_plan(fill_plan, plan, assembly_input)
        template_id = _assembly_template_id(assembly_input)
        artifact_map = {
            ref["artifact_id"]: artifact_store.get_artifact(ref["artifact_id"]).__dict__
            for ref in generated_component_refs
        }
        if assembly_input["target"]["mode"] == "existing_template":
            template_html = assemble_existing_template(
                template_id,
                fill_plan,
                available_fields=available_fields,
                artifact_components=artifact_map,
            )
        else:
            base_ref = assembly_input["target"]["base_ref"]
            if base_ref["ref_type"] == "artifact":
                base_artifact = artifact_store.get_artifact(base_ref["artifact_id"])
                template_html = assemble_template_from_base_source(
                    fill_plan,
                    base_html=base_artifact.content["base_html"],
                    contract=base_artifact.content["contract"],
                    available_fields=available_fields,
                    artifact_components=artifact_map,
                )
            else:
                template_html = assemble_template_from_base(
                    fill_plan,
                    available_fields=available_fields,
                    artifact_components=artifact_map,
                )
        for ref in generated_component_refs:
            artifact_store.mark_used_by_llm4(ref["artifact_id"])
        if generated_base_ref:
            artifact_store.mark_used_by_llm4(generated_base_ref["artifact_id"])
        preview_html = render_template(
            template_html,
            answer=_extract_answer(domain_result),
            data=_render_data_from_envelope(_extract_envelope(domain_result)),
        )
        html_path = save_visualization_output(preview_html, output_dir=_request_output_dir(request))
        template_path = save_template(
            template_html=template_html,
            fill_plan=fill_plan,
            metadata={
                "id": template_id if assembly_input["target"]["mode"] == "existing_template" else None,
                "kind": "complete_template",
                "fillable": True,
                "source": {
                    "execution_plan": plan,
                    "assembly_input": assembly_input,
                    "validation": "deterministic",
                },
                "domain": inspection["domain"],
                "schema_versions": [inspection["schema_version"]],
                "required_fields": inspection.get("available_fields", []),
            },
            output_dir=_request_output_dir(request),
        )
        return TemplateAgentResult(
            ok=True,
            mode=_request_mode(request),
            template_id=template_id,
            html=preview_html,
            html_path=str(html_path),
            template_path=str(template_path),
            available_templates=complete_templates,
            message="Template assembled from typed execution plan.",
            metadata={"execution_plan": plan, "assembly_input": assembly_input, "fill_plan": fill_plan},
        )

    def llm_general_plan(self, *, request: Any, requirements: dict[str, Any], complete_templates: list[dict[str, Any]], base_templates: list[dict[str, Any]], visible_components: list[dict[str, Any]], inspection: dict[str, Any]) -> dict[str, Any]:
        """LLM2 planner with a bounded tool-call loop."""

        if self.llm is None:
            return _heuristic_execution_plan(request, requirements, complete_templates, base_templates, visible_components)
        variables = {
            "visualization_context_json": _visualization_context(request),
            "requirements_json": requirements,
            "source_template_id": getattr(request, "source_template_id", None) or getattr(request, "template_id", None),
            "template_candidates_json": complete_templates + base_templates,
            "component_candidates_json": visible_components,
            "domain_metadata_json": inspection,
            "active_template_metadata_json": _active_template_metadata(
                getattr(request, "source_template_id", None) or getattr(request, "template_id", None)
            ),
            "planner_feedback_json": {},
            "tool_results_json": [],
        }
        repair_attempted = False
        for _ in range(3):
            output = self._call_llm_json("general_planner", variables)
            tool_requests = output.get("tool_requests", []) if isinstance(output, dict) else []
            if not tool_requests:
                if (
                    isinstance(output, dict)
                    and output.get("status") != "needs_clarification"
                    and not repair_attempted
                ):
                    ambiguity = _deterministic_planner_clarification(
                        output,
                        request=request,
                        active_metadata=_active_template_metadata(
                            getattr(request, "source_template_id", None)
                            or getattr(request, "template_id", None)
                        ),
                    )
                    if ambiguity is not None:
                        variables["planner_feedback_json"] = {
                            "must_correct": True,
                            "reason": ambiguity["clarification"].get("reason"),
                            "required_action": "Return needs_clarification instead of choosing a target.",
                            "question_guidance": ambiguity["clarification"].get("question"),
                            "candidates": ambiguity["clarification"].get("candidates", []),
                        }
                        repair_attempted = True
                        continue
                return output
            results = []
            for item in tool_requests:
                try:
                    tool_request = validate_tool_request(item)
                    result = execute_tool(tool_request["tool_name"], {**tool_request["arguments"], "tool_call_id": tool_request["tool_call_id"]})
                    results.append(validate_tool_result(result, expected_call_id=tool_request["tool_call_id"]))
                except ValueError as exc:
                    raise TemplateAgentError(str(exc)) from exc
            variables["tool_results_json"] = results
        raise TemplateAgentError("LLM2 tool-call loop exceeded deterministic limit.")

    def llm_generate_component(self, *, request: Any, requirements: dict[str, Any], generation_request: dict[str, Any]) -> dict[str, Any]:
        if self.llm is not None:
            output = self._call_llm_json("generate_component", {"requirements_json": requirements, "generation_request_json": generation_request})
            if not isinstance(output.get("component_html"), str) or not isinstance(output.get("metadata"), dict):
                raise TemplateAgentError("LLM3 component output is invalid.")
            return output
        # Offline fallback is still passed through the same artifact validator.
        return {
            "component_html": '<div class="generated-rain-icon" aria-label="Rain">☔</div>',
            "metadata": {
                "kind": "component",
                "supported_slots": [generation_request.get("slot", "metrics")],
                "required_fields": generation_request.get("required_fields", []),
            },
        }

    def llm4_fill_plan(self, *, request: Any, execution_plan: dict[str, Any], assembly_input: dict[str, Any]) -> dict[str, Any]:
        if self.llm is not None:
            output = self._call_llm_json("generate_fill_plan_v2", {"execution_plan_json": execution_plan, "assembly_input_json": assembly_input})
            return _validate_typed_fill_plan(output, assembly_input)
        return _deterministic_fill_plan(execution_plan, assembly_input)

    def llm_generate_new_base(
        self,
        *,
        request: Any,
        requirements: dict[str, Any],
        base_templates: list[dict[str, Any]],
        visible_components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate and validate a new fillable base when no base fits."""

        if self.llm is not None:
            output = self._call_llm_json(
                "generate_new_base",
                {
                    "requirements_json": requirements,
                    "base_templates_json": base_templates,
                    "visible_components_json": visible_components,
                },
            )
            base_html = output.get("base_html")
            contract = output.get("contract")
            metadata = output.get("metadata", {})
            if not isinstance(base_html, str) or not isinstance(contract, dict):
                raise TemplateAgentError(
                    "Generated base output must contain base_html and contract."
                )
            if not isinstance(metadata, dict):
                raise TemplateAgentError("Generated base metadata must be an object.")
        else:
            if not base_templates:
                raise TemplateAgentError("No base template is available for fallback generation.")
            source = base_templates[0]
            source_id = source.get("id")
            if not isinstance(source_id, str):
                raise TemplateAgentError("Generated base fallback has no valid source id.")
            source_dir = resolve_asset_path("base_templates", source_id)
            base_html = (source_dir / str(source.get("template_file", "base.html"))).read_text(
                encoding="utf-8"
            )
            contract = json.loads((source_dir / "contract.json").read_text(encoding="utf-8"))
            metadata = dict(source)

        # A generated base may still contain slot placeholders; those are
        # validated after Assembler materializes the complete template.
        if "{{ slot." not in base_html:
            validate_template_syntax(base_html)
        validate_security(base_html)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        metadata = {
            **metadata,
            "id": f"generated_base_{timestamp}",
            "kind": "base_template",
            "template_file": "base.html",
            "domains": [metadata.get("domain", "weather")],
        }
        return {
            "base_html": base_html,
            "contract": contract,
            "metadata": metadata,
        }

    def llm_extract_requirements(
        self,
        request: Any,
        inspection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract user requirements; override or fake in tests for LLM behavior."""

        if self.llm is not None:
            output = self._call_llm_json(
                "extract_requirements",
                {
                    "user_request": _request_user_request(request),
                    "inspection_json": {
                        "previous_template_state": _previous_template_state(request),
                        "source_template_id": getattr(request, "source_template_id", None),
                        "modification_request": getattr(request, "modification_request", ""),
                        "inspection": inspection or {},
                    },
                },
            )
            return validate_requirements_output(output)
        return _heuristic_requirements(request)

    def llm_decide_template_strategy(
        self,
        *,
        request: Any,
        requirements: dict[str, Any],
        complete_templates: list[dict[str, Any]],
        base_templates: list[dict[str, Any]],
        visible_components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Choose between existing template and base assembly."""

        if self.llm is not None:
            output = self._call_llm_json(
                "decide_template_strategy",
                {
                    "requirements_json": requirements,
                    "selected_template_id": getattr(request, "template_id", None),
                    "modification_request": getattr(request, "modification_request", ""),
                    "complete_templates_json": complete_templates,
                    "base_templates_json": base_templates,
                    "visible_components_json": visible_components,
                },
            )
            return validate_strategy_output(
                output,
                template_ids={
                    template["id"]
                    for template in complete_templates
                    if isinstance(template.get("id"), str)
                },
                base_ids={base["id"] for base in base_templates if isinstance(base.get("id"), str)},
            )
        if complete_templates and not _request_user_request(request).strip():
            return {
                "strategy": "existing_template",
                "template_id": complete_templates[0]["id"],
                "reason": "Compatible complete template exists.",
            }
        return {
            "strategy": "assemble_base",
            "base_template": base_templates[0]["id"] if base_templates else "",
            "reason": "Create/customize request needs a generated template artifact.",
        }

    def llm_select_components(
        self,
        *,
        request: Any,
        requirements: dict[str, Any],
        base_template: str,
        visible_components: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """Select candidate components by slot from visible components."""

        if self.llm is not None:
            output = self._call_llm_json(
                "select_components",
                {
                    "requirements_json": requirements,
                    "base_template": base_template,
                    "visible_components_json": visible_components,
                },
            )
            return validate_component_selection_output(
                output,
                visible_component_ids={
                    component["id"]
                    for component in visible_components
                    if isinstance(component.get("id"), str)
                },
            )
        selected: dict[str, list[str]] = {}
        for component in visible_components:
            component_id = component.get("id")
            slots = component.get("supported_slots", [])
            if not isinstance(component_id, str) or not isinstance(slots, list):
                continue
            for slot in slots:
                if isinstance(slot, str) and slot not in selected:
                    selected[slot] = [component_id]
        return selected

    def llm_generate_todo_list(
        self,
        *,
        request: Any,
        requirements: dict[str, Any],
        selected_components: dict[str, list[str]],
    ) -> list[str]:
        """Generate implementation notes for generated template metadata."""

        if self.llm is not None:
            output = self._call_llm_json(
                "generate_todo_list",
                {
                    "requirements_json": requirements,
                    "selected_components_json": selected_components,
                },
            )
            return validate_todo_list_output(output)
        return [
            "Use candidate base template only.",
            "Use visible components only.",
            "Validate fill plan before rendering.",
        ]

    def llm_generate_fill_plan(
        self,
        *,
        request: Any,
        requirements: dict[str, Any],
        base_template: str,
        selected_components: dict[str, list[str]],
    ) -> dict[str, Any]:
        """Generate a fill plan constrained to selected component candidates."""

        if self.llm is not None:
            output = self._call_llm_json(
                "generate_fill_plan",
                {
                    "requirements_json": requirements,
                    "base_template": base_template,
                    "selected_components_json": selected_components,
                },
            )
            return validate_fill_plan_output(
                output,
                base_ids={base_template},
                visible_component_ids={
                    component_id
                    for component_ids in selected_components.values()
                    for component_id in component_ids
                },
            )
        return {
            "base_template": base_template,
            "parameters": {"page_title": "Weather Dashboard"},
            "slots": selected_components,
        }

    def llm_repair_template(
        self,
        *,
        request: Any,
        template_html: str,
        validation_errors: list[str],
        fill_plan: dict[str, Any] | None = None,
        base_templates: list[dict[str, Any]] | None = None,
        visible_components: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Repair hook placeholder for future LLM use."""

        if self.llm is None:
            return template_html
        output = self._call_llm_json(
            "repair_template",
            {
                "validation_errors_json": validation_errors,
                "fill_plan_json": fill_plan or {},
                "base_templates_json": base_templates or [],
                "visible_components_json": visible_components or [],
            },
        )
        return validate_fill_plan_output(
            output,
            base_ids={
                base["id"]
                for base in (base_templates or [])
                if isinstance(base.get("id"), str)
            },
            visible_component_ids={
                component["id"]
                for component in (visible_components or [])
                if isinstance(component.get("id"), str)
            },
        )

    def _assemble_with_repair(
        self,
        *,
        request: Any,
        fill_plan: dict[str, Any],
        base_templates: list[dict[str, Any]],
        visible_components: list[dict[str, Any]],
        available_fields: list[str],
    ) -> tuple[str, dict[str, Any]]:
        current_fill_plan = fill_plan
        validation_errors: list[str] = []
        for attempt in range(self.max_repair_attempts + 1):
            try:
                _validate_fill_plan_candidates(current_fill_plan, base_templates, visible_components)
                return (
                    assemble_template_from_base(
                        current_fill_plan,
                        available_fields=available_fields,
                    ),
                    current_fill_plan,
                )
            except (TemplateAgentError, VisualizationValidationError) as exc:
                validation_errors.append(str(exc))
                if self.llm is None or attempt >= self.max_repair_attempts:
                    raise TemplateAgentError(str(exc)) from exc
                repaired = self.llm_repair_template(
                    request=request,
                    template_html="",
                    validation_errors=validation_errors,
                    fill_plan=current_fill_plan,
                    base_templates=base_templates,
                    visible_components=visible_components,
                )
                if not isinstance(repaired, dict):
                    raise TemplateAgentError("LLM repair output must be a fill plan.")
                current_fill_plan = repaired
        raise TemplateAgentError("Template repair failed.")

    def _call_llm_json(self, prompt_name: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            prompt = render_prompt(load_template_agent_prompt(prompt_name), variables)
            _debug_print(
                f"[TemplateAgent][{prompt_name}] START "
                f"prompt_chars={len(prompt)}"
            )
            if hasattr(self.llm, "chat_json"):
                output = self.llm.chat_json(
                    system_prompt="Return only valid JSON.",
                    user_message=prompt,
                    temperature=0.0,
                )
                if not isinstance(output, dict):
                    raise LlmOutputError("chat_json must return a JSON object.")
                _debug_print(f"[TemplateAgent][{prompt_name}] RESULT {output}")
                return output
            if hasattr(self.llm, "chat_text"):
                text = self.llm.chat_text(
                    system_prompt="Return only valid JSON.",
                    user_message=prompt,
                    temperature=0.0,
                )
                output = parse_llm_json_response(text)
                _debug_print(f"[TemplateAgent][{prompt_name}] RESULT {output}")
                return output
        except (PromptAssetError, LlmOutputError) as exc:
            raise TemplateAgentError(str(exc)) from exc
        raise TemplateAgentError("Template Agent LLM client must support chat_json or chat_text.")


def _debug_print(message: str) -> None:
    """Print diagnostics while preserving Vietnamese characters."""

    text = str(message)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            raise
        buffer.write((text + "\n").encode("utf-8"))
        buffer.flush()


def search_base_templates_by_metadata(
    domain: str | None = None,
    requirements: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Search base template metadata by supported domain."""

    base_root = resolve_asset_path("base_templates")
    if not base_root.exists():
        return []

    results = []
    for metadata_path in sorted(base_root.rglob("metadata.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            continue
        domains = metadata.get("domains", [])
        if domain is not None and domain not in domains:
            continue
        result = dict(metadata)
        result["score"] = _base_template_score(result, requirements or {})
        results.append(result)
    return sorted(results, key=lambda item: (-int(item.get("score", 0)), str(item.get("id", ""))))


def save_template(
    *,
    template_html: str,
    fill_plan: dict[str, Any],
    metadata: dict[str, Any],
    output_dir: str | Path | None = None,
) -> Path:
    """Save generated template artifact with metadata and fill plan."""

    root = resolve_output_dir(output_dir) / "generated_templates"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    artifact_dir = root / f"generated_template_{timestamp}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    metadata = dict(metadata)
    metadata.setdefault("id", artifact_dir.name)
    metadata.setdefault("kind", "complete_template")
    metadata.setdefault("template_file", "template.html")
    metadata.setdefault("schema_versions", [metadata.get("schema_version", "")])
    metadata.setdefault("required_fields", ["location.name"])
    metadata.setdefault("optional_fields", [])
    template_path = artifact_dir / "template.html"
    template_path.write_text(template_html, encoding="utf-8")
    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_dir / "fill_plan.json").write_text(
        json.dumps(fill_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return template_path


def save_base_template(
    *,
    base_html: str,
    contract: dict[str, Any],
    metadata: dict[str, Any],
    output_dir: str | Path | None = None,
) -> Path:
    """Persist a generated base as a reusable, inspectable artifact."""

    root = resolve_output_dir(output_dir) / "generated_bases"
    artifact_dir = root / str(metadata["id"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "base.html").write_text(base_html, encoding="utf-8")
    (artifact_dir / "contract.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifact_dir


def _validate_selected_components(
    selected_components: dict[str, list[str]],
    visible_components: list[dict[str, Any]],
) -> None:
    if not isinstance(selected_components, dict):
        raise TemplateAgentError("Selected components must be a dictionary.")
    visible_ids = {component["id"] for component in visible_components if isinstance(component.get("id"), str)}
    for slot, component_ids in selected_components.items():
        if not isinstance(slot, str) or not isinstance(component_ids, list):
            raise TemplateAgentError("Selected component slots must map to component id lists.")
        for component_id in component_ids:
            if component_id not in visible_ids:
                raise TemplateAgentError(f"LLM selected unknown or hidden component_id: {component_id}")


def _validate_fill_plan_candidates(
    fill_plan: dict[str, Any],
    base_templates: list[dict[str, Any]],
    visible_components: list[dict[str, Any]],
) -> None:
    base_ids = {base["id"] for base in base_templates if isinstance(base.get("id"), str)}
    visible_ids = {component["id"] for component in visible_components if isinstance(component.get("id"), str)}
    base_id = fill_plan.get("base_template") if isinstance(fill_plan, dict) else None
    if base_id not in base_ids:
        raise TemplateAgentError(f"LLM selected unknown base_id: {base_id}")
    slots = fill_plan.get("slots") if isinstance(fill_plan, dict) else None
    if not isinstance(slots, dict):
        raise TemplateAgentError("LLM fill plan must contain slots.")
    for component_ids in slots.values():
        if not isinstance(component_ids, list):
            raise TemplateAgentError("LLM fill plan slot values must be lists.")
        for component_id in component_ids:
            if component_id not in visible_ids:
                raise TemplateAgentError(f"LLM selected unknown or hidden component_id: {component_id}")


def _required_candidate_id(
    data: dict[str, Any],
    key: str,
    candidates: set[str],
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or value not in candidates:
        raise TemplateAgentError(f"LLM selected invalid {key}: {value}")
    return value


def _first_valid_base_id(base_templates: list[dict[str, Any]]) -> str | None:
    for base in base_templates:
        value = base.get("id") if isinstance(base, dict) else None
        if isinstance(value, str) and value:
            return value
    return None


def _apply_style_modifications(
    template_html: str,
    *,
    requirements: dict[str, Any],
    request_text: str,
) -> str:
    """Apply a small allow-listed style layer without letting LLM write CSS."""

    searchable = json.dumps(requirements, ensure_ascii=False).casefold()
    searchable = f"{searchable} {request_text.casefold()}"
    searchable = "".join(
        char
        for char in unicodedata.normalize("NFD", searchable)
        if unicodedata.category(char) != "Mn"
    )
    css = ""
    if "mau hong" in searchable or "pink" in searchable or "trang hong" in searchable:
        css = """
<style data-generated-style="pink-white">
  :root { --generated-page-background: linear-gradient(135deg, #fff8fb 0%, #ffe6f1 100%); }
  body { background: var(--generated-page-background) !important; }
</style>
"""
    elif "nen trang" in searchable or "white background" in searchable:
        css = """
<style data-generated-style="white">
  body { background: #ffffff !important; }
</style>
"""
    if not css:
        return template_html
    head_marker = "</head>"
    if head_marker in template_html:
        return template_html.replace(head_marker, f"{css}\n{head_marker}", 1)
    return f"{css}\n{template_html}"


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


def _request_domain_result(request: Any) -> dict[str, Any] | None:
    value = getattr(request, "domain_result", None)
    return value if isinstance(value, dict) else None


def _request_mode(request: Any) -> str:
    value = getattr(request, "mode", "create")
    return value if isinstance(value, str) else "create"


def _request_output_dir(request: Any) -> str | Path | None:
    return getattr(request, "output_dir", None)


def _request_user_request(request: Any) -> str:
    value = getattr(request, "user_request", "")
    return value if isinstance(value, str) else ""




def _visualization_context(request: Any) -> dict[str, Any]:
    value = getattr(request, "visualization_context", None)
    if isinstance(value, dict) and value:
        return value
    # Compatibility fallback for direct callers created before the context
    # envelope was introduced.
    return {
        "conversation_history": [
            {"role": "user", "content": _request_user_request(request)}
        ] if _request_user_request(request) else [],
        "merged_requirements": {},
        "domain_context": {},
    }


def _previous_template_state(request: Any) -> dict[str, Any]:
    value = getattr(request, "previous_template_state", None)
    return value if isinstance(value, dict) else {}


def _request_clarification_round(request: Any) -> int:
    state = _previous_template_state(request)
    value = state.get("clarification_round", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _active_template_metadata(template_id: Any) -> dict[str, Any]:
    """Expose only the current template contract needed by LLM2."""

    if not isinstance(template_id, str) or not template_id.strip():
        return {}
    try:
        metadata = dict(lookup_template(template_id.strip()).metadata)
    except Exception:
        return {}
    return {
        "id": metadata.get("id"),
        "kind": metadata.get("kind"),
        "name": metadata.get("name"),
        "domain": metadata.get("domain"),
        "style_targets": metadata.get("style_targets", []),
        "extension_points": metadata.get("extension_points", []),
        "fillable": metadata.get("fillable", False),
    }


def _normalize_planner_requirements(requirements: dict[str, Any]) -> dict[str, Any]:
    """Remove target guesses made by LLM1 before LLM2 sees requirements."""

    normalized = dict(requirements)
    for key in (
        "page_background", "weather_hero", "forecast_header",
        "metrics_section", "weather_card", "forecast_content",
    ):
        value = normalized.pop(key, None)
        if isinstance(value, dict):
            for property_name, property_value in value.items():
                normalized.setdefault(property_name, property_value)
    return normalized


def _heuristic_requirements(request: Any) -> dict[str, Any]:
    """Small offline fallback for tests and environments without an LLM."""

    user_request = _request_user_request(request).strip()
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFD", user_request.casefold())
        if unicodedata.category(char) != "Mn"
    )
    normalized = " ".join(normalized.split())
    previous = _previous_template_state(request)
    previous_requirements = previous.get("requirements", {})
    if not isinstance(previous_requirements, dict):
        previous_requirements = {}

    bare_create_phrases = {
        "create template",
        "tao template",
        "tao template",
        "toi muon tao template moi",
        "toi muon tao template moi",
    }
    if _request_mode(request) == "create" and normalized in bare_create_phrases:
        return {
            "status": "needs_clarification",
            "requirements": previous_requirements,
            "missing_information": ["purpose", "primary_content"],
            "clarifying_question": (
                "Bạn muốn trang này giúp người xem làm gì và thông tin nào cần được "
                "chú ý nhất? Bạn có thể mô tả tự nhiên, không cần dùng thuật ngữ kỹ thuật."
            ),
        }
    return {
        "status": "ready",
        "requirements": {
            **previous_requirements,
            "purpose": {"description": user_request},
        },
        "missing_information": [],
        "clarifying_question": None,
    }





def _base_template_score(metadata: dict[str, Any], requirements: dict[str, Any]) -> int:
    score = 100
    presentation = requirements.get("presentation", {})
    if isinstance(presentation, dict):
        preferred = set(_string_list(presentation.get("preferred_patterns")))
        supported = set(_string_list(metadata.get("slots")))
        score += len(preferred & supported) * 10
    return score


def _string_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _result_dict(result: TemplateAgentResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "mode": result.mode,
        "template_id": result.template_id,
        "html": result.html,
        "html_path": result.html_path,
        "template_path": result.template_path,
        "available_templates": result.available_templates,
        "message": result.message,
        "errors": result.errors,
        "metadata": result.metadata,
    }


def _should_use_typed_planner(request: Any, source_template_id: str | None) -> bool:
    action = getattr(request, "action", "")
    mode = _request_mode(request)
    return bool(source_template_id) or action == "design_template" or mode == "design"


def _request_id(request: Any) -> str:
    value = getattr(request, "request_id", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "visualization"


def _assembly_template_id(assembly_input: dict[str, Any]) -> str:
    target = assembly_input.get("target", {})
    ref = target.get("template_ref") if isinstance(target, dict) else None
    if isinstance(ref, dict) and isinstance(ref.get("id"), str):
        return ref["id"]
    return "generated_template"


def _heuristic_execution_plan(
    request: Any,
    requirements: dict[str, Any],
    complete_templates: list[dict[str, Any]],
    base_templates: list[dict[str, Any]],
    visible_components: list[dict[str, Any]],
) -> dict[str, Any]:
    source_id = getattr(request, "source_template_id", None) or getattr(request, "template_id", None)
    text = f"{_request_user_request(request)} {getattr(request, 'modification_request', '')}".casefold()
    normalized = "".join(
        char for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )
    style: list[dict[str, Any]] = []
    requirement_text = json.dumps(requirements, ensure_ascii=False).casefold()
    normalized_requirements = "".join(
        char for char in unicodedata.normalize("NFD", requirement_text)
        if unicodedata.category(char) != "Mn"
    )
    normalized = f"{normalized} {normalized_requirements}"
    active_metadata = _active_template_metadata(source_id)
    style_targets = active_metadata.get("style_targets", [])
    extension_points = active_metadata.get("extension_points", [])
    wants_component = any(word in normalized for word in ("icon", "component", "mua", "rain"))

    if wants_component:
        requested_slot = _infer_requested_slot(normalized)
        if not requested_slot and len(extension_points) != 1:
            return {
                "status": "needs_clarification",
                "clarification": {
                    "reason": "missing_position",
                    "question": "Bạn muốn thêm component vào vị trí nào: " + ", ".join(
                        str(item.get("label") or item.get("slot"))
                        for item in extension_points
                        if isinstance(item, dict)
                    ) + "?",
                    "missing_information": ["slot"],
                    "candidates": [item.get("slot") for item in extension_points if isinstance(item, dict)],
                },
            }

    if "hong" in normalized or "pink" in normalized:
        target_id = _infer_style_target(normalized, style_targets)
        if not target_id and len(style_targets) != 1:
            return {
                "status": "needs_clarification",
                "clarification": {
                    "reason": "ambiguous_target",
                    "question": "Bạn muốn đổi nền toàn trang hay nền của vùng giao diện nào?",
                    "missing_information": ["style target"],
                    "candidates": [item.get("id") for item in style_targets if isinstance(item, dict)],
                },
            }
        requested_color = requirements.get("background_color")
        if not isinstance(requested_color, str) or not validate_color_value(requested_color):
            requested_color = "pink"
        style.append({"target_id": target_id or "page_background", "property": "background_color", "value": requested_color})
    elif "nen trang" in normalized or "white background" in normalized:
        style.append({"target_id": "page_background", "property": "background_color", "value": "white"})

    generation_components: list[dict[str, Any]] = []
    reuse_components: list[dict[str, Any]] = []
    if wants_component:
        matching = [
            item for item in visible_components
            if any(word in json.dumps(item, ensure_ascii=False).casefold() for word in ("rain", "mua", "icon"))
            and "metrics" in item.get("supported_slots", [])
        ]
        if matching:
            candidate = matching[0]
            reuse_components.append({"ref_type": "registry", "id": candidate["id"], "kind": "component", "slot": _infer_requested_slot(normalized) or "metrics"})
        else:
            generation_components.append({
                "generation_key": "rain_icon",
                "kind": "component",
                "slot": _infer_requested_slot(normalized) or "metrics",
                "description": "Rain icon component",
                "required_fields": [],
            })

    if source_id:
        template = next((item for item in complete_templates if item.get("id") == source_id), None)
        if not isinstance(template, dict):
            raise TemplateAgentError(f"Unknown source template: {source_id}")
        return {
            "plan_version": "1.0",
            "target": {
                "mode": "existing_template",
                "template_ref": {"ref_type": "registry", "id": source_id, "kind": "complete_template"},
                "base_ref": None,
                "preserve_existing_structure": True,
            },
            "lookup_plan": {"templates": [], "base_templates": [], "components": []},
            "resource_plan": {"reuse_components": reuse_components},
            "generation_plan": {"base": None, "components": generation_components},
            "modification_plan": {"style": style, "content": [], "layout": []},
            "todo_list": ["Preserve existing template structure", "Apply requested operations", "Validate and render"],
}

    if not base_templates:
        raise TemplateAgentError("No compatible base template is available.")
    base_id = base_templates[0].get("id")
    if not isinstance(base_id, str):
        raise TemplateAgentError("Compatible base template has no valid ID.")
    # Select only compatible, visible registry components.  This keeps the
    # base path complete (including required hero slots) without allowing the
    # planner to invent component IDs.
    for slot in ("hero", "metrics", "chart", "footer"):
        if any(item.get("slot") == slot for item in reuse_components):
            continue
        candidate = next(
            (
                item for item in visible_components
                if slot in item.get("supported_slots", [])
            ),
            None,
        )
        if isinstance(candidate, dict) and isinstance(candidate.get("id"), str):
            reuse_components.append({"ref_type": "registry", "id": candidate["id"], "kind": "component", "slot": slot})
    return {
        "plan_version": "1.0",
        "target": {
            "mode": "base_template",
            "template_ref": None,
            "base_ref": {"ref_type": "registry", "id": base_id, "kind": "base_template"},
            "preserve_existing_structure": False,
        },
        "lookup_plan": {"templates": [], "base_templates": [], "components": []},
        "resource_plan": {"reuse_components": reuse_components},
        "generation_plan": {"base": None, "components": generation_components},
        "modification_plan": {"style": style, "content": [], "layout": []},
        "todo_list": ["Use selected base template", "Create fill plan", "Assemble and validate"],
    }


def _infer_style_target(text: str, targets: list[Any]) -> str | None:
    if any(token in text for token in ("toan trang", "body", "man hinh", "toan bo giao dien")):
        return "page_background"
    if any(token in text for token in ("phia tren", "top", "header", "hero", "xanh vang", "xanh-vang", "blue yellow")):
        for item in targets:
            if isinstance(item, dict) and item.get("id") in {"weather_hero", "forecast_header"}:
                return item["id"]
    return None


def _infer_requested_slot(text: str) -> str | None:
    mapping = (
        (("chi so", "metrics", "thong so"), "metrics"),
        (("dau the", "hero", "header"), "hero"),
        (("du bao", "forecast", "chart", "bieu do"), "chart"),
        (("cuoi", "footer", "phia duoi"), "footer"),
    )
    for words, slot in mapping:
        if any(word in text for word in words):
            return slot
    return None


def _deterministic_planner_clarification(
    plan: dict[str, Any],
    *,
    request: Any,
    active_metadata: dict[str, Any],
) -> dict[str, Any] | None:
    """Reject an LLM2 guess when the user did not identify a unique target."""

    text = _normalize_for_matching(_request_user_request(request))
    style_targets = [
        item for item in active_metadata.get("style_targets", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    style = plan.get("modification_plan", {}).get("style", [])
    has_style_change = isinstance(style, list) and bool(style)
    if has_style_change and len(style_targets) > 1 and not _explicit_style_target(text):
        labels = [str(item.get("label") or item["id"]) for item in style_targets]
        return {
            "status": "needs_clarification",
            "clarification": {
                "reason": "ambiguous_target",
                "question": "Bạn muốn đổi nền của vùng nào: " + ", ".join(labels) + "?",
                "missing_information": ["style target"],
                "candidates": [item["id"] for item in style_targets],
            },
        }

    extension_points = [
        item for item in active_metadata.get("extension_points", [])
        if isinstance(item, dict) and isinstance(item.get("slot"), str)
    ]
    resource_plan = plan.get("resource_plan", {})
    generation_plan = plan.get("generation_plan", {})
    has_component_change = bool(
        isinstance(resource_plan, dict) and resource_plan.get("reuse_components")
    ) or bool(
        isinstance(generation_plan, dict) and generation_plan.get("components")
    )
    if has_component_change and len(extension_points) > 1 and not _infer_requested_slot(text):
        labels = [str(item.get("label") or item["slot"]) for item in extension_points]
        return {
            "status": "needs_clarification",
            "clarification": {
                "reason": "missing_position",
                "question": "Bạn muốn thêm component vào vị trí nào: " + ", ".join(labels) + "?",
                "missing_information": ["slot"],
                "candidates": [item["slot"] for item in extension_points],
            },
        }
    return None


def _explicit_style_target(text: str) -> bool:
    return any(
        token in text
        for token in (
            "toan trang", "toan bo giao dien", "whole page", "entire page", "full page", "body", "man hinh",
            "phia tren", "header", "hero", "tieu de", "xanh vang",
            "blue yellow", "chi so", "metrics", "the thoi tiet",
            "noi dung du bao", "forecast",
        )
    )


def _normalize_for_matching(value: str) -> str:
    normalized = "".join(
        char for char in unicodedata.normalize("NFD", value.casefold())
        if unicodedata.category(char) != "Mn"
    )
    return " ".join(normalized.split())


def _deterministic_fill_plan(plan: dict[str, Any], assembly_input: dict[str, Any]) -> dict[str, Any]:
    target = assembly_input["target"]
    if target["mode"] == "existing_template":
        operations: list[dict[str, Any]] = []
        for item in plan["modification_plan"]["style"]:
            operations.append({
                "op": "set_style",
                "target_id": item["target_id"],
                "property": item["property"],
                "value": item["value"],
            })
        components = assembly_input.get("components", [])
        for component in components:
            slot = component.get("slot", "metrics")
            operations.append({"op": "insert_component", "slot": slot, "component_ref": component, "position": "append"})
        return {
            "plan_type": "fill_plan",
            "target": target,
            "operations": operations,
            "parameters": {},
        }
    base_ref = target["base_ref"]
    slots: dict[str, list[str]] = {"hero": [], "metrics": [], "chart": [], "footer": []}
    for item in assembly_input.get("components", []):
        if item.get("ref_type") != "registry" or not isinstance(item.get("id"), str):
            continue
        slot = item.get("slot", "metrics")
        if slot in slots:
            slots[slot].append(item["id"])
    return {
        "plan_type": "fill_plan",
        "target": target,
        "base_template": base_ref.get("id") if base_ref.get("ref_type") == "registry" else "generated",
        "parameters": {"page_title": "Weather Dashboard"},
        "slots": slots,
    }


def _complete_base_fill_plan(
    fill_plan: dict[str, Any],
    execution_plan: dict[str, Any],
    assembly_input: dict[str, Any],
) -> dict[str, Any]:
    """Ensure every selected/generated component reaches a base slot."""

    if assembly_input.get("target", {}).get("mode") != "base_template":
        return fill_plan
    if not isinstance(fill_plan, dict):
        raise TemplateAgentError("Base fill plan must be an object.")
    normalized = dict(fill_plan)
    slots = normalized.get("slots", {})
    if not isinstance(slots, dict):
        slots = {}
    slots = {name: list(values) if isinstance(values, list) else [] for name, values in slots.items()}
    for component in assembly_input.get("components", []):
        if not isinstance(component, dict):
            continue
        slot = component.get("slot", "metrics")
        if not isinstance(slot, str):
            continue
        ref: Any = component
        if component.get("ref_type") == "registry":
            ref = component.get("id")
        existing_keys = {
            item.get("artifact_id") if isinstance(item, dict) else item
            for item in slots.get(slot, [])
        }
        key = component.get("artifact_id") or component.get("id")
        if key not in existing_keys:
            slots.setdefault(slot, []).append(ref)
    normalized["slots"] = slots
    normalized.setdefault("parameters", {"page_title": "World Cup Dashboard"})
    style = normalized.get("style", [])
    if not isinstance(style, list):
        style = []
    for item in execution_plan.get("modification_plan", {}).get("style", []):
        if isinstance(item, dict) and item not in style:
            style.append(item)
    normalized["style"] = style
    return normalized


def _repair_visual_asset_plan(plan: dict[str, Any], *, request: Any) -> dict[str, Any]:
    """Convert visual asset intent into executable component generation requests."""

    if not isinstance(plan, dict):
        return plan
    repaired = dict(plan)
    modification = dict(repaired.get("modification_plan", {}))
    style = modification.get("style", [])
    if isinstance(style, list):
        modification["style"] = [
            item for item in style
            if isinstance(item, dict) and item.get("property") != "background_image"
        ]
    repaired["modification_plan"] = modification
    generation = dict(repaired.get("generation_plan", {}))
    generated = list(generation.get("components", [])) if isinstance(generation.get("components", []), list) else []
    resource = repaired.get("resource_plan", {})
    reused = resource.get("reuse_components", []) if isinstance(resource, dict) else []
    text = json.dumps(_visualization_context(request), ensure_ascii=False).casefold()
    wants_trophy = any(token in text for token in ("world cup", "worldcup", "world cúp", "cúp", "cup", "trophy"))
    wants_player = any(token in text for token in ("người đá bóng", "football player", "soccer player", "cầu thủ"))
    existing_keys = {
        str(item.get("generation_key") or item.get("id") or item.get("artifact_id"))
        for item in [*generated, *(reused if isinstance(reused, list) else [])]
        if isinstance(item, dict)
    }
    if wants_trophy and not any("trophy" in key or "cup" in key for key in existing_keys):
        generated.append({
            "generation_key": "football_trophy",
            "kind": "component",
            "slot": "hero",
            "description": "World Cup football trophy illustration",
            "required_fields": [],
        })
    if wants_player and not any("player" in key for key in existing_keys):
        generated.append({
            "generation_key": "football_player",
            "kind": "component",
            "slot": "hero",
            "description": "Football player illustration",
            "required_fields": [],
        })
    generation["components"] = generated
    repaired["generation_plan"] = generation
    return repaired


def _validate_typed_fill_plan(fill_plan: dict[str, Any], assembly_input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fill_plan, dict) or fill_plan.get("plan_type") != "fill_plan":
        raise TemplateAgentError("LLM4 output must be a fill_plan.")
    fill_plan = _normalize_typed_fill_plan(fill_plan)
    target = fill_plan.get("target")
    if not isinstance(target, dict) or target.get("mode") != assembly_input["target"].get("mode"):
        raise TemplateAgentError("LLM4 fill_plan target does not match execution target.")
    allowed_refs = {
        (item.get("ref_type"), item.get("id") or item.get("artifact_id"))
        for item in assembly_input.get("components", [])
        if isinstance(item, dict)
    }
    operations = fill_plan.get("operations", [])
    if not isinstance(operations, list):
        raise TemplateAgentError("LLM4 operations must be a list.")
    for operation in operations:
        if not isinstance(operation, dict):
            raise TemplateAgentError("LLM4 operation must be an object.")
        if operation.get("op") == "set_style":
            target_id = operation.get("target_id")
            allowed_targets = {
                item.get("id") for item in assembly_input.get("style_targets", [])
                if isinstance(item, dict)
            }
            if target_id not in allowed_targets:
                raise TemplateAgentError("LLM4 style target is not declared by the template.")
            if operation.get("property") not in {"background_color", "surface_color", "text_color", "accent_color", "border_color"}:
                raise TemplateAgentError("LLM4 style property is not allow-listed.")
            if not validate_color_value(operation.get("value")):
                raise TemplateAgentError("LLM4 style value is not a valid color.")
            operation["value"] = canonicalize_color(operation["value"])
        if operation.get("op") == "insert_component":
            ref = operation.get("component_ref", {})
            key = (ref.get("ref_type"), ref.get("id") or ref.get("artifact_id")) if isinstance(ref, dict) else None
            if key not in allowed_refs:
                raise TemplateAgentError("LLM4 referenced an unknown component.")
    return fill_plan


def _normalize_typed_fill_plan(fill_plan: dict[str, Any]) -> dict[str, Any]:
    """Convert one known legacy LLM4 shape into the canonical operation shape."""

    normalized = dict(fill_plan)
    operations = normalized.get("operations", [])
    if not isinstance(operations, list):
        raise TemplateAgentError("LLM4 operations must be a list.")
    converted: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            converted.append(operation)
            continue
        if operation.get("op"):
            converted.append(dict(operation))
            continue
        # Compatibility boundary for the exact malformed shape observed in
        # production logs.  The canonical prompt still requires `op`.
        if operation.get("operation") == "modify_style":
            parameters = operation.get("parameters", {})
            if not isinstance(parameters, dict):
                raise TemplateAgentError("Legacy modify_style parameters must be an object.")
            for property_name, value in parameters.items():
                converted.append({
                    "op": "set_style",
                    "property": property_name,
                    "value": value,
                })
            continue
        converted.append(dict(operation))
    normalized["operations"] = converted
    return normalized
