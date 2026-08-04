"""Shared trusted-template renderer; it contains no Weather-specific logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .base import PresentationRenderer
from .capabilities import load_template_metadata, presentation_capabilities
from .schemas import RenderedPanel, TemplateCapabilities


class JinjaPresentationRenderer(PresentationRenderer):
    """Render a registered domain template from its already-normalized view model."""

    def __init__(self, template_root: Path | None = None) -> None:
        self._template_root = template_root or (Path(__file__).resolve().parent.parent / "domains")

    def render(self, *, domain_id: str, template_id: str, data: dict[str, Any]) -> RenderedPanel:
        metadata = load_template_metadata(domain_id, template_id)
        template_path = self._template_root / domain_id / "templates" / template_id / "template.html"
        if not template_path.is_file():
            raise ValueError(f"template is not registered: {domain_id}/{template_id}")
        environment = Environment(
            loader=FileSystemLoader(str(template_path.parent)),
            undefined=StrictUndefined,
            autoescape=True,
        )
        html = environment.get_template("template.html").render(
            data=data, weather=data, payload=data, answer=""
        )
        raw_capabilities = presentation_capabilities(metadata)
        targets: dict[str, frozenset[str]] = {}
        for capability in raw_capabilities.values():
            if not isinstance(capability, dict):
                continue
            target_id = capability.get("target_id")
            effects = capability.get("allowed_effects")
            if isinstance(target_id, str) and isinstance(effects, list):
                targets[target_id] = frozenset(item for item in effects if isinstance(item, str))
        return RenderedPanel(
            domain_id=domain_id,
            template_id=template_id,
            html=html,
            capabilities=TemplateCapabilities(domain_id, template_id, targets),
        )
