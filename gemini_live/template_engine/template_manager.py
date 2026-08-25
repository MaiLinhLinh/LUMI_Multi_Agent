"""Domain-neutral selection boundary between a presentation request and Template LLM."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gemini_live.settings import Settings
from gemini_live.template_engine.template_llm import (
    AssetCatalogEntry,
    TemplateDecisionRequest,
    TemplateDecisionService,
    TemplateDecisionServiceError,
    load_asset_catalog_optional,
)

if TYPE_CHECKING:
    from gemini_live.presentation.pipeline import PresentationRequest


class TemplateManagerError(RuntimeError):
    """Raised when a presentation cannot be resolved to a trusted template decision."""


@dataclass(frozen=True)
class TemplateResolution:
    """One validated selection or dynamic-layout result."""

    decision: str
    template_id: str | None = None
    layout: Any | None = None
    assets: tuple[AssetCatalogEntry, ...] = ()


class TemplateManager:
    """Load domain catalogs and delegate the one semantic choice to Template LLM.

    This class deliberately does not score templates or infer a match from the
    brief. It supplies the whole domain catalog and only validates the model's
    selected ID or dynamic Layout Spec.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        domains_root: Path | None = None,
        decision_service: TemplateDecisionService | None = None,
    ) -> None:
        self._domains_root = domains_root or Path(__file__).resolve().parents[1] / "domains"
        self._decision_service = decision_service or TemplateDecisionService(settings)

    async def resolve(
        self,
        request: "PresentationRequest",
        *,
        recent_history: tuple[dict[str, str], ...] = (),
    ) -> TemplateResolution:
        domain_id = _domain_id(request.domain_id)
        brief = request.presentation_brief.strip()
        if not brief:
            raise TemplateManagerError("PresentationRequest requires presentation_brief for template selection.")

        templates_dir = self._domains_root / domain_id / "templates"
        asset_catalog_path = templates_dir / "assets" / "catalog.json"
        try:
            decision = await self._decision_service.decide(
                TemplateDecisionRequest(
                    domain_id=domain_id,
                    presentation_brief=brief,
                    template_catalog_path=templates_dir / "catalog.json",
                    asset_catalog_path=asset_catalog_path,
                    render_data=request.render_data,
                    recent_history=recent_history,
                )
            )
        except TemplateDecisionServiceError as exc:
            raise TemplateManagerError(str(exc)) from exc

        return TemplateResolution(
            decision=decision.decision,
            template_id=decision.template_id,
            layout=decision.layout,
            assets=load_asset_catalog_optional(asset_catalog_path),
        )


def _domain_id(value: object) -> str:
    if not isinstance(value, str):
        raise TemplateManagerError("domain_id must be a string.")
    domain_id = value.strip()
    if not domain_id or "/" in domain_id or "\\" in domain_id or domain_id in {".", ".."}:
        raise TemplateManagerError("domain_id is invalid.")
    return domain_id
