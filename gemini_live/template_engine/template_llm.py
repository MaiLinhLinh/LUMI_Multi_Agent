"""Shared Template LLM boundary; domains supply their own asset catalog path."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

from google import genai
from google.genai import types

from gemini_live.settings import Settings
from gemini_live.template_engine.layout_contract import (
    LayoutSpec,
    LayoutSpecValidationError,
    validate_template_layout_output,
)


_MAX_HISTORY_ITEMS = 6
logger = logging.getLogger("lumi.template_llm")

_TEMPLATE_DECISION_SYSTEM_INSTRUCTION = """
Bạn là Template LLM của Lumi. Nhiệm vụ của bạn là chọn hoặc tạo bố cục trực quan phù hợp cho yêu cầu hiện tại.

Bạn nhận được:
- domain_id: domain đang xử lý.
- presentation_brief: mô tả ý định trình bày.
- render_data: dữ liệu đã được backend kiểm chứng, sẽ được dùng để render giao diện.
- recent_history: ngữ cảnh hội thoại gần nhất.
- templates: các template có sẵn, mỗi template có id, mô tả và loại dữ liệu nó hiển thị được.
- assets: các ảnh/asset được phép dùng.
- canvas: vùng bố cục cố định 12 cột × 10 hàng.

QUY TẮC QUYẾT ĐỊNH

1. Đọc presentation_brief và render_data trước.
2. So sánh chúng với description và data_description của toàn bộ templates.
3. Nếu một template có sẵn phù hợp với cả mục tiêu trình bày và cấu trúc render_data, trả về:
   {"decision":"use_existing","template_id":"<id có trong templates>"}

4. Nếu không có template nào phù hợp, tạo một bố cục grid mới và trả về:
   {"decision":"create_layout","blocks":[...]}

Không được chọn template chỉ vì tên gần giống. Template được chọn phải có khả năng hiển thị đúng loại dữ liệu trong render_data.

KHI TẠO BỐ CỤC MỚI

- Canvas luôn cố định 12 cột × 10 hàng; không trả về canvas.
- Chỉ dùng hai loại block:
  - text: tiêu đề, nhãn hoặc hướng dẫn ngắn.
  - image: một asset có trong assets.
- Mỗi block phải có id, type và grid.
- text phải có content.
- image phải có asset_id và label.
- grid gồm col, row, col_span, row_span; tất cả là số nguyên dương.
- Các block phải nằm trọn canvas và không chồng lấn.
- Chọn asset dựa trên caption trong assets, không tự tạo asset_id.
- Khi có hai hoặc nhiều nội dung cần so sánh, đặt chúng ở các vùng tách biệt, cân đối và dễ quan sát.
- Chỉ tạo text ngắn phục vụ giao diện. Không tự tạo dữ kiện, số liệu, đáp án, mô tả sự thật hoặc nội dung bài học không có trong presentation_brief, render_data hoặc asset caption.
- Không trả về HTML, CSS, style, class, container, widget, anchor_id hay target_id.

ĐẦU RA

Chỉ trả về đúng một JSON object hợp lệ.
Không dùng Markdown, code fence, giải thích hoặc bất kỳ văn bản nào ngoài JSON.

Ví dụ chọn template có sẵn:
{"decision":"use_existing","template_id":"weather_forecast"}

Ví dụ tạo bố cục mới:
{"decision":"create_layout","blocks":[
  {"id":"title","type":"text","content":"Cùng quan sát nhé!","grid":{"col":1,"row":1,"col_span":12,"row_span":1}},
  {"id":"dog","type":"image","asset_id":"dog","label":"Chú chó","grid":{"col":1,"row":3,"col_span":5,"row_span":6}},
  {"id":"cat","type":"image","asset_id":"cat","label":"Chú mèo","grid":{"col":7,"row":3,"col_span":5,"row_span":6}}
]}
""".strip()


# Keep this deliberately flat.  The backend validator enforces the exact
# branch-specific contract after generation; this schema's job is to ensure
# Gemini returns one JSON object rather than prose or an array.
_TEMPLATE_DECISION_OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision"],
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["use_existing", "create_layout"],
        },
        "template_id": {"type": "string"},
        "blocks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "type", "grid"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "enum": ["text", "image"]},
                    "content": {"type": "string"},
                    "asset_id": {"type": "string"},
                    "label": {"type": "string"},
                    "grid": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["col", "row", "col_span", "row_span"],
                        "properties": {
                            "col": {"type": "integer", "minimum": 1},
                            "row": {"type": "integer", "minimum": 1},
                            "col_span": {"type": "integer", "minimum": 1},
                            "row_span": {"type": "integer", "minimum": 1},
                        },
                    },
                },
            },
        },
    },
}

_LAYOUT_OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["blocks"],
    "properties": {"blocks": {"type": "array", "minItems": 1, "items": {
        "type": "object", "required": ["id", "type", "grid"],
        "properties": {
            "id": {"type": "string"}, "type": {"type": "string", "enum": ["text", "image"]},
            "content": {"type": "string"}, "asset_id": {"type": "string"}, "label": {"type": "string"},
            "grid": {"type": "object", "required": ["col", "row", "col_span", "row_span"], "properties": {
                "col": {"type": "integer", "minimum": 1}, "row": {"type": "integer", "minimum": 1},
                "col_span": {"type": "integer", "minimum": 1}, "row_span": {"type": "integer", "minimum": 1},
            }},
        },
    }}},
}


class TemplateLayoutServiceError(RuntimeError):
    """Raised when Template LLM configuration or output is unusable."""


class TemplateDecisionServiceError(RuntimeError):
    """Raised when a template selection/composition decision is unusable."""


@dataclass(frozen=True)
class TemplateLayoutRequest:
    domain_id: str
    template_brief: str
    recent_history: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class TemplateCatalogEntry:
    """One natural-language description of a reusable domain template."""

    id: str
    description: str
    data_description: str

    def for_llm(self) -> dict[str, str]:
        return {
            "id": self.id,
            "description": self.description,
            "data_description": self.data_description,
        }


@dataclass(frozen=True)
class TemplateDecisionRequest:
    """All trusted catalogs required for one Template LLM decision."""

    domain_id: str
    presentation_brief: str
    template_catalog_path: Path
    asset_catalog_path: Path
    render_data: dict[str, Any] = field(default_factory=dict)
    recent_history: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class TemplateDecision:
    """A validated Template LLM decision."""

    decision: str
    template_id: str | None = None
    layout: LayoutSpec | None = None


@dataclass(frozen=True)
class AssetCatalogEntry:
    id: str
    path: str
    caption: str

    def for_llm(self) -> dict[str, str]:
        return {"id": self.id, "caption": self.caption}

    def public_url(self, base_path: str) -> str:
        """Resolve this catalog entry under the domain's public asset route."""

        return f"{base_path.rstrip('/')}/{quote(Path(self.path).name)}"


ClientFactory = Callable[..., Any]


class TemplateLayoutService:
    """Call the configured model; domain assets remain external to this service."""

    def __init__(self, settings: Settings, *, asset_catalog_path: Path, client_factory: ClientFactory = genai.Client) -> None:
        self._settings = settings
        self._asset_catalog_path = asset_catalog_path
        self._client_factory = client_factory

    async def create_layout(self, request: TemplateLayoutRequest) -> LayoutSpec:
        brief = _required_text(request.template_brief, "template_brief", max_length=500)
        if not self._settings.template_llm_api_key:
            raise TemplateLayoutServiceError("GEMINI_API_KEY chưa được cấu hình cho Template LLM.")
        assets = load_asset_catalog(self._asset_catalog_path)
        payload = {
            "domain_id": request.domain_id, "template_brief": brief,
            "recent_history": _safe_history(request.recent_history),
            "canvas": {"columns": 12, "rows": 10},
            "assets": [asset.for_llm() for asset in assets], "allowed_blocks": ["text", "image"],
        }
        client = self._client_factory(api_key=self._settings.template_llm_api_key)
        try:
            response = await client.aio.models.generate_content(
                model=self._settings.template_llm_model,
                contents=json.dumps(payload, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=_TEMPLATE_DECISION_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json", response_json_schema=_LAYOUT_OUTPUT_JSON_SCHEMA,
                ),
            )
        except Exception as exc:
            logger.warning("[TEMPLATE_LLM_REQUEST_FAILED] error_type=%s detail=%s", type(exc).__name__, str(exc)[:500])
            raise TemplateLayoutServiceError("Template LLM không tạo được bố cục.") from exc
        response_text = getattr(response, "text", None)
        if not isinstance(response_text, str) or not response_text.strip():
            raise TemplateLayoutServiceError("Template LLM không trả về Layout Spec.")
        logger.warning("[TEMPLATE_LLM_RAW_OUTPUT] chars=%d output=%s", len(response_text), response_text)
        try:
            return validate_template_layout_output(json.loads(response_text), allowed_asset_ids=(asset.id for asset in assets))
        except (json.JSONDecodeError, LayoutSpecValidationError) as exc:
            logger.warning("[TEMPLATE_LLM_INVALID_LAYOUT] error_type=%s detail=%s", type(exc).__name__, str(exc)[:500])
            raise TemplateLayoutServiceError("Layout Spec từ Template LLM không hợp lệ.") from exc





class TemplateDecisionService:
    """Call Template LLM once to select a catalogued template or compose a grid."""

    def __init__(self, settings: Settings, *, client_factory: ClientFactory = genai.Client) -> None:
        self._settings = settings
        self._client_factory = client_factory

    async def decide(self, request: TemplateDecisionRequest) -> TemplateDecision:
        brief = _required_text(request.presentation_brief, "presentation_brief", max_length=500)
        if not self._settings.template_llm_api_key:
            raise TemplateDecisionServiceError("GEMINI_API_KEY chưa được cấu hình cho Template LLM.")

        templates = load_template_catalog(request.template_catalog_path)
        assets = load_asset_catalog_optional(request.asset_catalog_path)
        payload = {
            "domain_id": request.domain_id,
            "presentation_brief": brief,
            "render_data": request.render_data,
            "recent_history": _safe_history(request.recent_history),
            "canvas": {"columns": 12, "rows": 10},
            "templates": [template.for_llm() for template in templates],
            "assets": [asset.for_llm() for asset in assets],
            "allowed_blocks": ["text", "image"],
        }
        client = self._client_factory(api_key=self._settings.template_llm_api_key)
        try:
            response = await client.aio.models.generate_content(
                model=self._settings.template_llm_model,
                contents=json.dumps(payload, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=_TEMPLATE_DECISION_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_json_schema=_TEMPLATE_DECISION_OUTPUT_JSON_SCHEMA,
                ),
            )
        except Exception as exc:
            logger.warning("[TEMPLATE_LLM_REQUEST_FAILED] error_type=%s detail=%s", type(exc).__name__, str(exc)[:500])
            raise TemplateDecisionServiceError("Template LLM không trả về quyết định bố cục.") from exc

        response_text = getattr(response, "text", None)
        if not isinstance(response_text, str) or not response_text.strip():
            raise TemplateDecisionServiceError("Template LLM không trả về quyết định bố cục.")
        logger.warning("[TEMPLATE_LLM_RAW_DECISION] chars=%d output=%s", len(response_text), response_text)
        try:
            return _validate_template_decision(
                json.loads(response_text),
                template_ids=(template.id for template in templates),
                asset_ids=(asset.id for asset in assets),
            )
        except (json.JSONDecodeError, LayoutSpecValidationError, TemplateDecisionServiceError) as exc:
            logger.warning("[TEMPLATE_LLM_INVALID_DECISION] error_type=%s detail=%s", type(exc).__name__, str(exc)[:500])
            raise TemplateDecisionServiceError("Quyết định của Template LLM không hợp lệ.") from exc


def load_asset_catalog(path: Path) -> tuple[AssetCatalogEntry, ...]:
    """Read any domain asset catalog with the shared id/path/caption schema."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateLayoutServiceError("Không đọc được asset catalog.") from exc
    if not isinstance(raw, dict) or set(raw) != {"assets"} or not isinstance(raw["assets"], list):
        raise TemplateLayoutServiceError("Asset catalog không đúng schema.")
    entries: list[AssetCatalogEntry] = []
    ids: set[str] = set()
    for item in raw["assets"]:
        if not isinstance(item, dict) or set(item) != {"id", "path", "caption"}:
            raise TemplateLayoutServiceError("Mỗi asset phải có id, path và caption.")
        asset_id = _required_text(item["id"], "asset.id")
        if asset_id in ids:
            raise TemplateLayoutServiceError(f"Asset id bị trùng: {asset_id}.")
        relative_path = _required_text(item["path"], "asset.path", max_length=300)
        caption = _required_text(item["caption"], "asset.caption", max_length=300)
        asset_path = path.parent.parent / relative_path.replace("templates/assets/", "assets/")
        if not asset_path.is_file():
            raise TemplateLayoutServiceError(f"Asset không tồn tại: {asset_id}.")
        entries.append(AssetCatalogEntry(id=asset_id, path=relative_path, caption=caption))
        ids.add(asset_id)
    if not entries:
        raise TemplateLayoutServiceError("Asset catalog không có asset nào.")
    return tuple(entries)


def load_asset_catalog_optional(path: Path) -> tuple[AssetCatalogEntry, ...]:
    """Allow a domain to have reusable templates before it has image assets."""

    return () if not path.is_file() else load_asset_catalog(path)


def load_template_catalog(path: Path) -> tuple[TemplateCatalogEntry, ...]:
    """Read LLM-facing template descriptions without inspecting template HTML."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateDecisionServiceError("Không đọc được template catalog.") from exc
    if not isinstance(raw, dict) or set(raw) != {"templates"} or not isinstance(raw["templates"], list):
        raise TemplateDecisionServiceError("Template catalog không đúng schema.")

    entries: list[TemplateCatalogEntry] = []
    ids: set[str] = set()
    for item in raw["templates"]:
        if not isinstance(item, dict) or set(item) != {"id", "description", "data_description"}:
            raise TemplateDecisionServiceError(
                "Mỗi template catalog entry phải có id, description và data_description."
            )
        template_id = _required_text(item["id"], "template.id")
        if template_id in ids:
            raise TemplateDecisionServiceError(f"Template id bị trùng: {template_id}.")
        entries.append(TemplateCatalogEntry(
            id=template_id,
            description=_required_text(item["description"], "template.description", max_length=500),
            data_description=_required_text(item["data_description"], "template.data_description", max_length=500),
        ))
        ids.add(template_id)
    return tuple(entries)


def _validate_template_decision(
    payload: object,
    *,
    template_ids: Iterable[str],
    asset_ids: Iterable[str],
) -> TemplateDecision:
    if not isinstance(payload, dict):
        raise TemplateDecisionServiceError("Template decision must be an object.")

    decision = payload.get("decision")
    if decision == "use_existing":
        if set(payload) != {"decision", "template_id"}:
            raise TemplateDecisionServiceError("use_existing must contain only decision and template_id.")
        template_id = _required_text(payload.get("template_id"), "template_id")
        if template_id not in set(template_ids):
            raise TemplateDecisionServiceError(f"Template id không có trong catalog: {template_id}.")
        return TemplateDecision(decision="use_existing", template_id=template_id)

    if decision == "create_layout":
        if set(payload) != {"decision", "blocks"}:
            raise TemplateDecisionServiceError("create_layout must contain only decision and blocks.")
        return TemplateDecision(
            decision="create_layout",
            layout=validate_template_layout_output(
                {"blocks": payload["blocks"]}, allowed_asset_ids=asset_ids
            ),
        )

    raise TemplateDecisionServiceError("decision must be use_existing or create_layout.")


def _safe_history(history: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    for item in history:
        role = item.get("role") if isinstance(item, dict) else None
        text = item.get("text") if isinstance(item, dict) else None
        if role in {"user", "assistant"} and isinstance(text, str) and text.strip():
            safe.append({"role": role, "text": text.strip()[:700]})
    return safe[-_MAX_HISTORY_ITEMS:]


def _required_text(value: object, name: str, max_length: int = 100) -> str:
    if not isinstance(value, str):
        raise TemplateLayoutServiceError(f"{name} phải là chuỗi.")
    text = value.strip()
    if not text or len(text) > max_length:
        raise TemplateLayoutServiceError(f"{name} phải có từ 1 đến {max_length} ký tự.")
    return text
