"""Shared preparation of trusted presentation data for every business domain.

Domains provide a normalized view model, selected template ID, and an adapter.
This module renders the panel, discovers its capabilities, and produces facts
verified by the domain.  It deliberately does not decide narration or scenes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .base import DomainPresentationAdapter
from .capabilities import load_template_metadata, presentation_capabilities
from gemini_live.trace import trace
from .planner_schemas import GroundedFact
from .renderer import JinjaPresentationRenderer
from .schemas import RenderedPanel


_PRESENT_ID = re.compile(r'data-present-id\s*=\s*["\']([^"\']+)["\']')


@dataclass(frozen=True)
class PreparedPresentation:
    """Trusted output of the shared presentation pipeline."""

    panel: RenderedPanel
    template_metadata: dict[str, Any]
    declared_capabilities: dict[str, dict[str, Any]]
    grounded_facts: list[GroundedFact]
    concrete_animation_capabilities: dict[str, list[str]]
    visual_stage_map: str = ""


@dataclass(frozen=True)
class LiveFactPack:
    """Public facts for Gemini Live plus server-only visual resolution data.

    Gemini receives only ``facts_for_live`` and ``supported_effects``.  The
    target map remains server-owned so the model never needs to construct or
    guess a DOM identifier.
    """

    facts_for_live: list[dict[str, Any]]
    anchor_target_map: dict[str, dict[str, Any]]
    panel_anchor_map: dict[str, dict[str, Any]]
    supported_effects: list[dict[str, str]]
    effect_id_map: dict[str, str]


@dataclass(frozen=True)
class PresentationRequest:
    """Domain output consumed by the shared pipeline after a successful tool call."""

    domain_id: str
    template_id: str
    view_model: dict[str, Any]
    adapter: DomainPresentationAdapter
    domain_data: dict[str, Any]
    compact_data: dict[str, Any]
    render_panel: bool = True


class PresentationPipeline:
    """Run presentation stages shared by all business domains.

    Domains provide a view model and DomainPresentationAdapter. This class owns
    renderer, metadata, and verified fact preparation only.
    """

    def __init__(
        self,
        *,
        renderer: JinjaPresentationRenderer | None = None,
    ) -> None:
        self._renderer = renderer or JinjaPresentationRenderer()

    def prepare(
        self,
        *,
        request: PresentationRequest,
    ) -> PreparedPresentation:
        if request.adapter.domain_id != request.domain_id:
            raise ValueError("Presentation adapter does not belong to the requested domain.")

        panel = self._renderer.render(
            domain_id=request.domain_id,
            template_id=request.template_id,
            data=request.view_model,
        )
        trace("VIEW_MODEL_READY template=%s", request.template_id)
        metadata = load_template_metadata(request.domain_id, request.template_id)
        declared = presentation_capabilities(metadata)
        trace("ADAPTER_START")
        facts = request.adapter.build_candidate_facts(
            request.domain_data,
            compact_data=request.compact_data,
            presentation_capabilities=declared,
        )
        trace("ADAPTER_DONE facts=%s fact_ids=%s", len(facts), [fact.id for fact in facts])
        visual_stage_map = self._renderer.render_visual_stage_map(
            domain_id=request.domain_id,
            template_id=request.template_id,
            data=request.view_model,
            stage_context=request.adapter.live_visual_stage_context(
                domain_data=request.domain_data,
                compact_data=request.compact_data,
                view_model=request.view_model,
            ),
        )
        return PreparedPresentation(
            panel=panel,
            template_metadata=metadata,
            declared_capabilities=declared,
            grounded_facts=facts,
            concrete_animation_capabilities=concrete_animation_capabilities(panel.html, declared),
            visual_stage_map=visual_stage_map,
        )

    @staticmethod
    def build_live_fact_pack(
        request: PresentationRequest,
        prepared: PreparedPresentation,
    ) -> LiveFactPack:
        """Expose compact fact/anchor aliases and retain trusted DOM mapping.

        Fact aliases describe verified data. Visual calls use independent,
        compact anchor aliases; the backend remains the sole authority that
        resolves them to actual ``data-present-id`` values.
        """

        panel_anchor_map = _build_panel_anchor_map(prepared)
        panel_effect_ids = {
            effect_id
            for evidence in panel_anchor_map.values()
            for effect_id in evidence["allowed_effect_ids"]
        }
        effect_id_map = {
            effect_id: effect
            for effect, effect_id in _EFFECT_IDS.items()
            if effect_id in panel_effect_ids
        }

        facts_for_live: list[dict[str, Any]] = []
        anchor_target_map: dict[str, dict[str, Any]] = {}
        for index, fact in enumerate(prepared.grounded_facts, start=1):
            alias = f"f{index}"
            item: dict[str, Any] = {
                "id": alias,
                "metric": fact.metric,
                "operation": fact.operation,
                "value": fact.value,
                "unit": fact.unit,
                "entity": fact.entity,
                "visualizable": False,
            }
            capability = prepared.declared_capabilities.get(fact.focus)
            target_id = _resolve_capability_target(capability, fact.entity)
            anchor_id = _resolve_capability_anchor(capability, fact.entity)
            evidence = panel_anchor_map.get(anchor_id or "")
            if fact.visualizable and target_id and evidence and evidence.get("target_id") == target_id:
                item["visualizable"] = True
                item["anchor_id"] = anchor_id
                effect_aliases = evidence["allowed_effect_ids"]
                existing = anchor_target_map.get(anchor_id)
                if existing is not None and existing.get("target_id") != target_id:
                    raise ValueError(f"anchor_id resolves to multiple targets: {anchor_id}")
                if existing is None:
                    anchor_target_map[anchor_id] = {
                        "target_id": target_id,
                        "allowed_effect_ids": effect_aliases,
                    }
                else:
                    existing["allowed_effect_ids"] = sorted(
                        set(existing.get("allowed_effect_ids", [])) | set(effect_aliases)
                    )
            facts_for_live.append(item)
        return LiveFactPack(
            facts_for_live=facts_for_live,
            anchor_target_map=anchor_target_map,
            panel_anchor_map=panel_anchor_map,
            supported_effects=[
                {"id": effect_id, "description": _EFFECT_DESCRIPTIONS[effect_id]}
                for effect_id in sorted(panel_effect_ids)
            ],
            effect_id_map=effect_id_map,
        )


def concrete_animation_capabilities(
    html: str,
    declared_capabilities: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Intersect rendered DOM anchors with template-declared legal effects."""
    target_ids = sorted({match.group(1) for match in _PRESENT_ID.finditer(html)})
    allowed: dict[str, list[str]] = {}
    for target_id in target_ids:
        effects: set[str] = set()
        for capability in declared_capabilities.values():
            if not isinstance(capability, dict):
                continue
            if target_id == capability.get("target_id") or _matches_pattern(
                target_id, capability.get("target_pattern")
            ):
                effects.update(
                    effect
                    for effect in capability.get("allowed_effects", [])
                    if isinstance(effect, str)
                )
        if effects:
            allowed[target_id] = sorted(effects)
    return allowed


def _build_panel_anchor_map(prepared: PreparedPresentation) -> dict[str, dict[str, Any]]:
    """Resolve every metadata-declared anchor that exists in the rendered panel."""

    anchor_target_map: dict[str, dict[str, Any]] = {}
    for capability in prepared.declared_capabilities.values():
        if not isinstance(capability, dict):
            continue
        for target_id in prepared.concrete_animation_capabilities:
            entity = _entity_for_capability_target(capability, target_id)
            if entity is None:
                continue
            anchor_id = _resolve_capability_anchor(capability, entity)
            if anchor_id is None:
                raise ValueError("visual capability must declare anchor_id or anchor_id_pattern")
            effect_aliases = [
                _effect_id(effect)
                for effect in prepared.concrete_animation_capabilities[target_id]
            ]
            existing = anchor_target_map.get(anchor_id)
            if existing is not None and existing.get("target_id") != target_id:
                raise ValueError(f"anchor_id resolves to multiple targets: {anchor_id}")
            if existing is None:
                anchor_target_map[anchor_id] = {
                    "target_id": target_id,
                    "allowed_effect_ids": effect_aliases,
                }
            else:
                existing["allowed_effect_ids"] = sorted(
                    set(existing["allowed_effect_ids"]) | set(effect_aliases)
                )
    return anchor_target_map


def _entity_for_capability_target(
    capability: dict[str, Any],
    target_id: str,
) -> dict[str, Any] | None:
    """Recover pattern values from a rendered target so its public anchor can resolve."""

    fixed_entity = capability.get("fixed_entity", {})
    if not isinstance(fixed_entity, dict):
        return None
    declared_target = capability.get("target_id")
    if isinstance(declared_target, str) and declared_target:
        return dict(fixed_entity) if declared_target == target_id else None

    pattern = capability.get("target_pattern")
    if not isinstance(pattern, str) or not pattern:
        return None
    entity = dict(fixed_entity)
    match = _match_target_pattern(pattern, target_id)
    if match is None:
        return None
    entity.update(match)
    return entity if _entity_matches_fixed(capability, entity) else None


def _match_target_pattern(pattern: str, target_id: str) -> dict[str, int] | None:
    parts = re.split(r"(\{[a-z_]+\})", pattern)
    regex_parts: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if re.fullmatch(r"\{[a-z_]+\}", part):
            name = part[1:-1]
            if name in seen:
                return None
            seen.add(name)
            regex_parts.append(fr"(?P<{name}>\d+)")
        else:
            regex_parts.append(re.escape(part))
    match = re.fullmatch("".join(regex_parts), target_id)
    if match is None:
        return None
    return {name: int(value) for name, value in match.groupdict().items()}


def _resolve_capability_target(
    capability: dict[str, Any] | None,
    entity: dict[str, Any],
) -> str | None:
    """Resolve one declared metadata capability to its concrete DOM target."""

    if not isinstance(capability, dict) or not _entity_matches_fixed(capability, entity):
        return None
    target_id = capability.get("target_id")
    if isinstance(target_id, str) and target_id:
        return target_id
    return _render_pattern(capability.get("target_pattern"), entity)


def _resolve_capability_anchor(
    capability: dict[str, Any] | None,
    entity: dict[str, Any],
) -> str | None:
    """Resolve the public anchor exclusively from template metadata."""

    if not isinstance(capability, dict) or not _entity_matches_fixed(capability, entity):
        return None
    anchor_id = capability.get("anchor_id")
    if isinstance(anchor_id, str) and anchor_id:
        return anchor_id
    return _render_pattern(capability.get("anchor_id_pattern"), entity)


def _entity_matches_fixed(capability: dict[str, Any], entity: dict[str, Any]) -> bool:
    fixed_entity = capability.get("fixed_entity", {})
    if not isinstance(fixed_entity, dict):
        return False
    return all(entity.get(key) == value for key, value in fixed_entity.items())


def _render_pattern(pattern: Any, entity: dict[str, Any]) -> str | None:
    if not isinstance(pattern, str) or not pattern:
        return None

    values = _template_values(entity)
    placeholders = re.findall(r"\{([a-z_]+)\}", pattern)
    if any(name not in values for name in placeholders):
        return None
    try:
        return pattern.format(**values)
    except (KeyError, ValueError):
        return None


def _template_values(entity: dict[str, Any]) -> dict[str, Any]:
    """Return the small, shared placeholder vocabulary for template metadata."""

    values = dict(entity)
    for source, one_based in (("day_index", "day_number"), ("interval_index", "interval_number")):
        value = entity.get(source)
        if isinstance(value, int) and not isinstance(value, bool):
            values[one_based] = value + 1

    if "index" not in values:
        for key in ("group_index", "day_index", "interval_index"):
            value = entity.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                values["index"] = value
                break
    return values


def _matches_pattern(target_id: str, pattern: Any) -> bool:
    if not isinstance(pattern, str) or not pattern:
        return False
    escaped = re.escape(pattern)
    escaped = re.sub(r"\\\{[a-z_]+\\\}", r"[0-9]+", escaped)
    return re.fullmatch(escaped, target_id) is not None


_EFFECT_IDS = {
    "reveal": "reveal",
    "highlight": "highlight",
    "pulse": "pulse",
    "dim_others": "dim",
    "draw_circle": "circle",
    "draw_arrow": "arrow",
    "trace_line": "trace",
    "draw_group_bracket": "bracket",
    "trace_chart_segment": "trace_chart",
    "draw_temperature_range": "temperature_range",
    "reveal_items": "reveal_items",
}

_EFFECT_DESCRIPTIONS = {
    "reveal": "Hiện một vùng hoặc số liệu đang ẩn. Chỉ dùng khi backend đã cho phép công bố nội dung đó.",
    "highlight": "Làm nổi bật nhẹ vùng đang được giải thích. Dùng để hướng sự chú ý vào nhóm, thẻ hoặc số liệu.",
    "pulse": "Nhấp sáng ngắn vùng cần nhấn mạnh. Chỉ dùng khi cần thu hút chú ý tức thời.",
    "dim": "Làm mờ vùng xung quanh để tập trung vào vùng được nói đến.",
    "circle": "Vẽ vòng tròn quanh một vùng cụ thể. Dùng khi cần khoanh rõ nhóm, ngày, điểm hoặc kết quả.",
    "arrow": "Vẽ mũi tên chỉ vào vùng được nói đến.",
    "trace": "Vẽ theo đường hoặc xu hướng trong biểu đồ.",
    "bracket": "Vẽ ngoặc bao quanh một nhóm liên quan.",
    "trace_chart": "Vẽ theo đoạn biểu đồ liên quan.",
    "temperature_range": "Nhấn mạnh khoảng nhiệt độ trên biểu đồ.",
    "reveal_items": "Hiện các vật thể bên trong vùng kết quả đang ẩn. Chỉ dùng khi backend đã cho phép hiện kết quả.",
}


def _effect_id(effect: str) -> str:
    """Map a frontend implementation name to a compact Live-facing ID."""

    try:
        return _EFFECT_IDS[effect]
    except KeyError as exc:
        raise ValueError(f"unsupported presentation effect: {effect}") from exc
