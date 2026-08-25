"""Shared preparation of trusted presentation data for every business domain.

Domains provide trusted render data, an optional template ID, and an adapter.
This module renders an already selected template, discovers its capabilities,
and prepares the trusted ASCII stage map. It deliberately does not choose a
template or decide narration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from .base import DomainPresentationAdapter
from .capabilities import load_template_metadata, presentation_capabilities
from .dynamic_grid import (
    DynamicGridAsset,
    DynamicGridPresentation,
    PreparedDynamicGridPresentation,
    prepare_dynamic_grid,
)
from gemini_live.trace import trace
from .renderer import JinjaPresentationRenderer
from .schemas import RenderedPanel
from gemini_live.template_engine.layout_contract import layout_spec_to_dict
from gemini_live.template_engine.template_manager import TemplateManager


_PRESENT_ID = re.compile(r'data-present-id\s*=\s*["\']([^"\']+)["\']')


@dataclass(frozen=True)
class PreparedPresentation:
    """Trusted output of the shared presentation pipeline."""

    panel: RenderedPanel
    template_metadata: dict[str, Any]
    declared_capabilities: dict[str, dict[str, Any]]
    concrete_animation_capabilities: dict[str, list[str]]
    visual_stage_map: str = ""


@dataclass(frozen=True)
class LivePresentationPack:
    """Server-resolved visual capabilities for one rendered panel.

    Gemini receives the rendered ASCII map and the effect catalog. The full
    anchor-to-DOM map remains server-owned so it never constructs DOM IDs.
    """

    panel_anchor_map: dict[str, dict[str, Any]]
    supported_effects: list[dict[str, str]]
    effect_id_map: dict[str, str]


@dataclass(frozen=True)
class PresentationRequest:
    """Domain output consumed by the shared pipeline after a successful tool call."""

    domain_id: str
    presentation_brief: str = ""
    render_data: dict[str, Any] = field(default_factory=dict)
    template_id: str | None = None
    adapter: DomainPresentationAdapter | None = None
    presentation_instruction: str = ""
    render_panel: bool = True


class PresentationPipeline:
    """Run presentation stages shared by all business domains.

    Domains provide a view model and DomainPresentationAdapter. This class owns
    renderer, metadata, stage-map rendering, and visual validation data.
    """

    def __init__(
        self,
        *,
        renderer: JinjaPresentationRenderer | None = None,
        template_manager: TemplateManager | None = None,
    ) -> None:
        self._renderer = renderer or JinjaPresentationRenderer()
        self._template_manager = template_manager

    async def resolve_template(
        self,
        *,
        request: PresentationRequest,
        recent_history: tuple[dict[str, str], ...] = (),
    ) -> PresentationRequest | DynamicGridPresentation:
        """Resolve only a request without a fixed template through Template LLM.

        Existing business domains still provide ``template_id`` and therefore
        retain their current Jinja render path without a Template LLM call.
        """

        if request.template_id is not None:
            return request
        if self._template_manager is None:
            raise ValueError("PresentationRequest requires TemplateManager when template_id is absent.")

        resolution = await self._template_manager.resolve(request, recent_history=recent_history)
        if resolution.decision == "use_existing":
            if resolution.template_id is None:
                raise ValueError("TemplateManager returned use_existing without template_id.")
            return replace(request, template_id=resolution.template_id)
        if resolution.decision != "create_layout" or resolution.layout is None:
            raise ValueError("TemplateManager returned an unsupported resolution.")

        return DynamicGridPresentation(
            domain_id=request.domain_id,
            layout_spec=layout_spec_to_dict(resolution.layout),
            assets=tuple(
                DynamicGridAsset(
                    id=asset.id,
                    url=asset.public_url(f"/assets/{request.domain_id}"),
                )
                for asset in resolution.assets
            ),
            presentation_instruction=request.presentation_instruction,
        )

    def prepare(
        self,
        *,
        request: PresentationRequest,
    ) -> PreparedPresentation:
        if request.template_id is None:
            raise ValueError("PresentationRequest requires a template before rendering.")
        if request.adapter is not None and request.adapter.domain_id != request.domain_id:
            raise ValueError("Presentation adapter does not belong to the requested domain.")

        panel = self._renderer.render(
            domain_id=request.domain_id,
            template_id=request.template_id,
            data=request.render_data,
        )
        trace("VIEW_MODEL_READY template=%s", request.template_id)
        metadata = load_template_metadata(request.domain_id, request.template_id)
        declared = presentation_capabilities(metadata)
        trace("STAGE_MAP_START")
        stage_context = {}
        if request.adapter is not None:
            stage_context = request.adapter.live_visual_stage_context(
                render_data=request.render_data,
                template_id=request.template_id,
            )
        visual_stage_map = self._renderer.render_visual_stage_map(
            domain_id=request.domain_id,
            template_id=request.template_id,
            data=request.render_data,
            stage_context=stage_context,
        )
        return PreparedPresentation(
            panel=panel,
            template_metadata=metadata,
            declared_capabilities=declared,
            concrete_animation_capabilities=concrete_animation_capabilities(panel.html, declared),
            visual_stage_map=visual_stage_map,
        )

    def prepare_dynamic_grid(
        self,
        *,
        presentation: DynamicGridPresentation,
    ) -> PreparedDynamicGridPresentation:
        """Prepare a validated grid panel without invoking the Jinja renderer.

        Anchor/effect maps and the ASCII stage map are intentionally added in
        Checkpoint 6; this checkpoint only establishes the presentation branch.
        """

        return prepare_dynamic_grid(presentation)

    @staticmethod
    def build_live_presentation_pack(
        prepared: PreparedPresentation,
    ) -> LivePresentationPack:
        """Build the server-only anchor map and public effect catalog."""

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

        return LivePresentationPack(
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

    panel_anchor_map: dict[str, dict[str, Any]] = {}
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
            existing = panel_anchor_map.get(anchor_id)
            if existing is not None and existing.get("target_id") != target_id:
                raise ValueError(f"anchor_id resolves to multiple targets: {anchor_id}")
            if existing is None:
                panel_anchor_map[anchor_id] = {
                    "target_id": target_id,
                    "allowed_effect_ids": effect_aliases,
                }
            else:
                existing["allowed_effect_ids"] = sorted(
                    set(existing["allowed_effect_ids"]) | set(effect_aliases)
                )
    return panel_anchor_map


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
