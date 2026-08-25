"""A domain-neutral native-tool agent that plans one replacement panel."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from google import genai
from google.genai import types
from openai import AsyncOpenAI

from gemini_live_2.catalogs.domains import DomainRegistry, ManifestError
from gemini_live_2.catalogs.templates import TemplateCatalogError
from gemini_live_2.gateway import (
    CapabilityDescriptor,
    DomainGateway,
    GatewayConfigurationError,
    GatewayPermissionError,
)
from gemini_live_2.panel.contracts import (
    ContractValidationError,
    DataAlias,
    DataBundle,
    PresentationPlan,
)
from gemini_live_2.settings import Settings
from gemini_live_2.widgets import WidgetPropsError, WidgetRegistry


logger = logging.getLogger("lumi.plan_agent")
_MAX_TOOL_STEPS = 4
_CALL_CAPABILITY_NAME = "call_capability"
_DESCRIBE_WIDGETS_NAME = "describe_widgets"
_DESCRIBE_TEMPLATE_NAME = "describe_template"

_SYSTEM_INSTRUCTION = """
Nếu input có compiler_feedback, plan trước đó đã bị backend từ chối.
Hãy đọc error_code và details, rồi trả một plan mới đã sửa đúng lỗi đó. Ví dụ
grid_overlap nêu hai block và các ô lưới chồng nhau: phải đổi vị trí hoặc kích
thước để các block không còn dùng chung ô. Không trả lại nguyên plan cũ.

Bạn là Plan Agent của Lumi, là người sẽ dựa vào câu hỏi người dùng, dựa vào lịch sử và dữ liệu nếu có để lên kịch bản, kế hoạch để trực quan hoá nội dung trả lời cho người dùng. Nhiệm vụ của bạn là quyết định cách tạo một panel trực quan mới:
Bạn hãy suy nghĩ lên kế hoạch cho intent/ câu hỏi để xây dựng plan phù hợp.
Sau đó dựa trên plan của bạn, mà quyết định chọn một trong hai cách:
- dùng một Presentation Plan có sẵn nếu template phù hợp rõ ràng, và template đó có thể biểu đạt đầy đủ intent bằng các binding hiện có, bạn phải kiểm tra đủ thông tin, không được chọn template có sẵn chỉ vì đoán nó phù hợp.
- tạo một plan mới bằng các widget và asset được cấp.
Nếu intent cần một vùng/nội dung/trạng thái mà template không có slot
để hiện, không được chọn template đó. Phải create_plan.

Bắt buộc gọi describe_widgets khi cần dùng một widget mới để nhận props hợp lệ.

Bạn không trả lời trực tiếp cho người dùng, không tạo HTML/CSS và không tạo anchor_id, target_id hoặc block id kỹ thuật.
Bạn cũng thiết kế trạng thái hiển thị ban đầu của từng block: mặc định là visible.
Chỉ dùng initial_visibility="hidden" khi ý đồ hoạt động cần trì hoãn nội dung đó,
ví dụ đáp án hoặc nhóm kết quả. Gemini Live, không phải bạn, sẽ quyết định lúc reveal.
Nếu intent yêu cầu vị trí tương đối hoặc hình dạng cụ thể — như tam giác,
hàng dọc, vòng tròn, chữ cái, góc trái/phải — không dùng object_group.
Hãy tạo nhiều image block, mỗi block một asset và một GridRect riêng để
bố cục hình học hiện trực tiếp trên canvas.
ĐẦU VÀO

- intent: yêu cầu người dùng đã được Gemini Live chuẩn hoá. Dùng để hiểu panel cần thể hiện điều gì.
- recent_history: ngữ cảnh hội thoại gần đây do backend cung cấp. Chỉ dùng để hiểu ý định kế thừa; không coi history là dữ liệu thật.
- domain manifest: phạm vi domain hiện tại; chỉ dùng asset, widget, template và capability được domain này cho phép.
- asset catalog: ảnh, icon hoặc học liệu có sẵn. Chỉ chọn asset_id có trong catalog.
- template catalog: các plan có sẵn gồm id, purpose và supports. Nếu một template phù hợp rõ ràng, chọn nó.
- widget_index: danh sách ngắn gồm widget_id và purpose. Index không chứa props chi tiết.
- canvas: đây là khung màn hình panel mà người dùng sẽ nhìn thấy, được chia thành lưới cố định 16 cột × 10 hàng. Hãy dùng lưới này để quyết định bố cục trực quan: mỗi block cần ghi rõ vùng chiếm trên màn hình bằng vị trí và kích thước grid, nằm trọn trong khung và không chồng lấn block khác.
- capabilities: tool nghiệp vụ được phép gọi để lấy hoặc tạo dữ liệu đã xác minh.
- verified_data: dữ liệu thật đã có và các alias ngắn được phép dùng trong props.

CÁCH LÀM

1. Đọc intent, history, dữ liệu và template catalog.
2. Suy nghĩ, lên kế hoạch cho panel trực quan để biểu đạt intent. Xác định các block cần dùng, loại widget, asset, nhãn, số lượng, trạng thái hiển thị ban đầu và vị trí trên canvas.
3. Nếu một template có sẵn phù hợp rõ ràng:
   - Bắt buộc gọi describe_template(template_id) trước khi trả kết quả cuối.
   - Đọc toàn bộ binding contract mà tool trả về.
   - Trả use_existing_plan với template_id và đầy đủ từng binding key đúng một lần.
   - Không được trả use_existing_plan chỉ có template_id.
4. Nếu dữ liệu chưa đủ để lập panel, gọi call_capability với capability_id và arguments hợp lệ. Có thể gọi nhiều tool theo từng bước.
5. Nếu cần tạo plan mới, chọn widget cần dùng từ widget_index.
6. Trước khi dùng bất kỳ widget nào trong plan mới, bắt buộc gọi describe_widgets với widget_id đó để nhận props hợp lệ.
7. Sau khi đã có đủ dữ liệu và contract widget, tạo plan mới.
8. Hãy sắp xếp bố cục các block trên canvas sao cho trực quan, không chồng lấn và phù hợp với câu hỏi người dùng.
Hãy cung cấp số khối phù hợp nội dung cần hiển thị, ví dụ text dài thì phải cung cấp  block text đủ lớn. 
Không gọi capability nếu intent chỉ cần asset hoặc nội dung đã có.
Không gọi tool ngoài danh sách được cấp.
Không tự tạo dữ liệu được xem là sự thật, asset không tồn tại, hoặc thông tin không có trong input.

ĐẦU RA CUỐI

Khi đã đủ dữ liệu, trả đúng một JSON object, không Markdown, code fence hay giải thích.
Ký tự đầu tiên của phản hồi cuối phải là `{` và ký tự cuối phải là `}`.
Không được dùng ```json, ```, Markdown, nhãn “JSON”, lời giải thích hoặc bất kỳ ký tự nào ngoài một JSON object duy nhất.
Chọn template có sẵn:
{"decision":"use_existing_plan","template_id":"...","bindings":{"$block_...":"..."}}

Tạo plan mới:
{"decision":"create_plan","template_description":"...","plan":{"blocks":[...]}}

Với create_plan:
- template_description mô tả ngắn, tổng quát khung bố cục vừa tạo để lần sau có thể tái sử dụng cho nội dung khác. Mô tả cách sắp xếp, không nhắc asset hoặc nội dung cụ thể của lượt này.
- plan chỉ có khóa blocks.
- Mỗi block chỉ có widget_id, grid, props và initial_visibility tùy chọn.
- initial_visibility chỉ nhận "visible" hoặc "hidden"; không ghi trường này khi block visible.
- Không trả domain_id: backend tự gắn domain từ route_request đã kiểm chứng.
- Không trả block id, target_id, anchor_id, HTML, CSS, DOM hay đường dẫn file.
- Compiler sẽ tự sinh các ID kỹ thuật và anchor.
VÍ DỤ 1: 

Intent: “Tạo hoạt động để trẻ so sánh chó và mèo.”

Template catalog có:
{"id":"two_subject_comparison","purpose":"So sánh trực quan hai đối tượng ngang hàng.","supports":["2 ảnh","nhãn","mô tả ngắn"]}

Trước khi chọn template, tự đánh giá nhu cầu của panel:
- cần một tiêu đề;
- cần đúng hai ảnh là một ảnh chó và một ảnh mèo;
- cần nhãn cho từng ảnh;


Nhận thấy Template two_subject_comparison biểu đạt được so sánh giữa hai đối tượng, có vẻ phù hợp, nhưng chưa chắc chắn.
Vì vậy gọi:
describe_template({"template_id":"two_subject_comparison"})

Sau khi nhận binding contract và xác nhận contract có đủ slot tiêu đề, hai ảnh và hai nhãn, thì mới chốt là chọn template này,kết quả cuối:
{
  "decision":"use_existing_plan",
  "template_id":"two_subject_comparison",
  "bindings":{
    "$block_1_content":"Cùng quan sát bạn Chó và bạn Mèo nhé!",
    "$block_2_asset_id":"dog",
    "$block_2_label":"Bạn Chó",
    "$block_3_asset_id":"cat",
    "$block_3_label":"Bạn Mèo"
  }
}
Nếu sau khi nhận biding contract, thấy template không có đủ slot để biểu đạt intent, thì không chọn template này. Cần tạo plan mới.

VÍ DỤ 2:

Intent: “Dạy trẻ phép cộng 1 con mèo + 2 con mèo.”

Template catalog có:
{"id":"two_subject_comparison","purpose":"So sánh trực quan hai đối tượng ngang hàng.","supports":["2 ảnh","nhãn","mô tả ngắn"]}

Trước khi chọn template, tự đánh giá nhu cầu của panel:
- cần hai nhóm đối tượng có số lượng 1 mèo và 2 mèo;
- cần dấu cộng và dấu bằng;
- cần một nhóm kết quả mèo có thể ban đầu ẩn;
- cần một đáp án số có thể ban đầu ẩn.

Template two_subject_comparison là so sánh giữa hai đối tượng nên không phù hợp với intent này.
Nó không biểu đạt được nhóm đối tượng, phép tính, trạng thái ẩn hay đáp án.
Không chọn template này và không gọi describe_template cho nó.

Cần tạo plan mới. Trước hết gọi:
describe_widgets({"widget_ids":["text","object_group","image","answer"]})

Sau khi nhận widget contract, kết quả cuối có thể là:
{
  "decision":"create_plan",
  "template_description":"Một phép tính trực quan nằm ngang: hai nhóm đối tượng, dấu phép tính và vùng kết quả xếp dọc ở bên phải.",
  "plan":{
    "blocks":[
      {
        "widget_id":"text",
        "grid":{"col":1,"row":1,"col_span":16,"row_span":1},
        "props":{"content":"Cùng tính với những bạn mèo nhé!","role":"title"}
      },
      {
        "widget_id":"object_group",
        "grid":{"col":1,"row":3,"col_span":4,"row_span":4},
        "props":{"asset_id":"cat","count":1,"label":"1 bạn mèo"}
      },
      {
        "widget_id":"image",
        "grid":{"col":5,"row":4,"col_span":2,"row_span":2},
        "props":{"asset_id":"plus"}
      },
      {
        "widget_id":"object_group",
        "grid":{"col":7,"row":3,"col_span":4,"row_span":4},
        "props":{"asset_id":"cat","count":2,"label":"2 bạn mèo"}
      },
      {
        "widget_id":"image",
        "grid":{"col":11,"row":4,"col_span":2,"row_span":2},
        "props":{"asset_id":"equals"}
      },
      {
        "widget_id":"object_group",
        "initial_visibility":"hidden",
        "grid":{"col":13,"row":3,"col_span":4,"row_span":3},
        "props":{"asset_id":"cat","count":3,"label":"3 bạn mèo"}
      },
      {
        "widget_id":"answer",
        "initial_visibility":"hidden",
        "grid":{"col":13,"row":6,"col_span":4,"row_span":2},
        "props":{"value":"3"}
      }
    ]
  }
}

""".strip()


class PlanAgentError(RuntimeError):
    """Raised for configuration, model, tool-loop, or decision failures."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanAgentError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanAgentError(f"{field_name} must be an object.")
    return value


def _safe_history(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, tuple):
        raise PlanAgentError("recent_history must be a tuple.")
    safe: list[dict[str, str]] = []
    for item in value[-6:]:
        if not isinstance(item, Mapping):
            raise PlanAgentError("each history item must be an object.")
        role = item.get("role")
        text = item.get("text")
        if role not in {"user", "assistant"} or not isinstance(text, str) or not text.strip():
            raise PlanAgentError("history entries require role user/assistant and non-empty text.")
        safe.append({"role": role, "text": text.strip()[:1200]})
    return tuple(safe)


def _bundle_for_agent(bundle: DataBundle) -> dict[str, Any]:
    return {"data": dict(bundle.data), "aliases": [alias.to_dict() for alias in bundle.alias_catalog]}


def _merge_bundles(current: DataBundle, update: DataBundle) -> DataBundle:
    """Keep all verified tool results without silently overwriting values."""

    if current.domain_id != update.domain_id:
        raise PlanAgentError("domain capability returned data for another domain.")
    duplicate_keys = set(current.data).intersection(update.data)
    if duplicate_keys:
        raise PlanAgentError(
            "capability result conflicts with existing data keys: " + ", ".join(sorted(duplicate_keys)) + "."
        )
    aliases: tuple[DataAlias, ...] = current.alias_catalog + update.alias_catalog
    alias_ids = [alias.id for alias in aliases]
    if len(alias_ids) != len(set(alias_ids)):
        raise PlanAgentError("capability result conflicts with an existing data alias.")
    return DataBundle(domain_id=current.domain_id, data={**current.data, **update.data}, aliases=aliases)


@dataclass(frozen=True, slots=True)
class PlanAgentRequest:
    """Backend-owned context for one request that must create/replace a panel."""

    domain_id: str
    intent: str
    recent_history: tuple[dict[str, str], ...] = ()
    initial_bundle: DataBundle | None = None
    validation_feedback: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _text(self.domain_id, "domain_id"))
        object.__setattr__(self, "intent", _text(self.intent, "intent"))
        object.__setattr__(self, "recent_history", _safe_history(self.recent_history))
        if self.initial_bundle is not None:
            if not isinstance(self.initial_bundle, DataBundle):
                raise PlanAgentError("initial_bundle must be a DataBundle.")
            if self.initial_bundle.domain_id != self.domain_id:
                raise PlanAgentError("initial_bundle must match domain_id.")
        if self.validation_feedback is not None:
            if not isinstance(self.validation_feedback, Mapping):
                raise PlanAgentError("validation_feedback must be an object.")
            object.__setattr__(self, "validation_feedback", dict(self.validation_feedback))


@dataclass(frozen=True, slots=True)
class UseExistingPlanDecision:
    template_id: str
    bindings: Mapping[str, Any] = field(default_factory=dict)
    decision: str = "use_existing_plan"

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_id", _text(self.template_id, "template_id"))
        if not isinstance(self.bindings, Mapping):
            raise PlanAgentError("use_existing_plan.bindings must be an object.")
        normalized_bindings: dict[str, Any] = {}
        for key, value in self.bindings.items():
            binding_key = _text(key, "binding key")
            if not binding_key.startswith("$block_"):
                raise PlanAgentError("binding keys must start with '$block_'.")
            normalized_bindings[binding_key] = value
        object.__setattr__(self, "bindings", normalized_bindings)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"decision": self.decision, "template_id": self.template_id}
        if self.bindings:
            data["bindings"] = dict(self.bindings)
        return data


@dataclass(frozen=True, slots=True)
class CreatePlanDecision:
    plan: PresentationPlan
    template_description: str
    decision: str = "create_plan"

    def __post_init__(self) -> None:
        if not isinstance(self.plan, PresentationPlan):
            raise PlanAgentError("create_plan.plan must be a PresentationPlan.")
        object.__setattr__(
            self,
            "template_description",
            _text(self.template_description, "create_plan.template_description"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "template_description": self.template_description,
            "plan": self.plan.to_dict(),
        }


PlanDecision: TypeAlias = UseExistingPlanDecision | CreatePlanDecision


@dataclass(frozen=True, slots=True)
class PlanAgentResult:
    """A final decision together with the verified data used to plan it."""

    decision: PlanDecision
    data_bundle: DataBundle


ClientFactory = Callable[..., Any]
CerebrasClientFactory = Callable[..., Any]


def _parse_decision(value: object, *, domain_id: str) -> PlanDecision:
    data = _mapping(value, "Plan Agent final response")
    decision = data.get("decision")
    if decision == "use_existing_plan":
        if set(data) not in ({"decision", "template_id"}, {"decision", "template_id", "bindings"}):
            raise PlanAgentError("use_existing_plan requires decision, template_id and optional bindings.")
        return UseExistingPlanDecision(
            template_id=_text(data.get("template_id"), "template_id"),
            bindings=_mapping(data.get("bindings", {}), "use_existing_plan.bindings"),
        )
    if decision == "create_plan":
        if set(data) != {"decision", "template_description", "plan"}:
            raise PlanAgentError("create_plan requires decision, template_description and plan.")
        plan_data = _mapping(data.get("plan"), "create_plan.plan")
        if set(plan_data) != {"blocks"}:
            raise PlanAgentError("create_plan.plan requires exactly blocks.")
        try:
            plan = PresentationPlan.from_dict({"domain_id": domain_id, "blocks": plan_data["blocks"]})
        except ContractValidationError as exc:
            raise PlanAgentError(str(exc)) from exc
        return CreatePlanDecision(
            plan=plan,
            template_description=_text(data.get("template_description"), "create_plan.template_description"),
        )
    raise PlanAgentError("final response must choose use_existing_plan or create_plan.")


def _native_tools(capabilities: tuple[CapabilityDescriptor, ...]) -> types.Tool:
    """Expose shared widget discovery and only the capabilities granted by the domain."""

    declarations = [
        types.FunctionDeclaration(
            name=_DESCRIBE_WIDGETS_NAME,
            description="Lấy contract props chi tiết cho các widget được phép dùng trong panel mới.",
            parametersJsonSchema={
                "type": "object",
                "additionalProperties": False,
                "required": ["widget_ids"],
                "properties": {
                    "widget_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                },
            },
        ),
        types.FunctionDeclaration(
            name=_DESCRIBE_TEMPLATE_NAME,
            description="Return the binding contract for one reusable layout template.",
            parametersJsonSchema={
                "type": "object",
                "additionalProperties": False,
                "required": ["template_id"],
                "properties": {"template_id": {"type": "string"}},
            },
        ),
    ]
    if capabilities:
        declarations.append(types.FunctionDeclaration(
            name=_CALL_CAPABILITY_NAME,
            description=(
                "Gọi một capability đã được cấp quyền để lấy hoặc tạo dữ liệu tin cậy "
                "cần cho Presentation Plan. Chỉ dùng capability_id trong danh sách được cấp."
            ),
            parametersJsonSchema={
                "type": "object",
                "additionalProperties": False,
                "required": ["capability_id", "arguments"],
                "properties": {
                    "capability_id": {"type": "string", "enum": [item.id for item in capabilities]},
                    "arguments": {"type": "object"},
                },
            },
        ))
    return types.Tool(functionDeclarations=declarations)


def _cerebras_tools(capabilities: tuple[CapabilityDescriptor, ...]) -> list[dict[str, Any]]:
    """Return the same native tools in Cerebras/OpenAI chat-completions form."""

    declarations: list[dict[str, Any]] = [{
        "type": "function",
        "function": {
            "name": _DESCRIBE_WIDGETS_NAME,
            "description": "Lấy contract props chi tiết cho các widget được phép dùng trong panel mới.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["widget_ids"],
                "properties": {
                    "widget_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                },
            },
        },
    }, {
        "type": "function",
        "function": {
            "name": _DESCRIBE_TEMPLATE_NAME,
            "description": "Return the binding contract for one reusable layout template.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["template_id"],
                "properties": {"template_id": {"type": "string"}},
            },
        },
    }]
    if capabilities:
        declarations.append({
            "type": "function",
            "function": {
                "name": _CALL_CAPABILITY_NAME,
                "description": (
                    "Gọi một capability đã được cấp quyền để lấy hoặc tạo dữ liệu tin cậy "
                    "cần cho Presentation Plan. Chỉ dùng capability_id trong danh sách được cấp."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["capability_id", "arguments"],
                    "properties": {
                        "capability_id": {"type": "string", "enum": [item.id for item in capabilities]},
                        "arguments": {"type": "object"},
                    },
                },
            },
        })
    return declarations


def _function_calls(response: Any) -> tuple[Any, ...]:
    """Support the SDK convenience field and the underlying candidate parts."""

    direct = getattr(response, "function_calls", None)
    if direct:
        return tuple(direct)
    candidates = getattr(response, "candidates", None) or ()
    if candidates:
        parts = getattr(getattr(candidates[0], "content", None), "parts", None) or ()
        return tuple(part.function_call for part in parts if getattr(part, "function_call", None) is not None)
    return ()


def _model_content(response: Any, calls: tuple[Any, ...]) -> types.Content:
    candidates = getattr(response, "candidates", None) or ()
    content = getattr(candidates[0], "content", None) if candidates else None
    if isinstance(content, types.Content):
        return content
    return types.Content(role="model", parts=[types.Part(function_call=call) for call in calls])


class PlanAgent:
    """Run a constrained native-function-call loop for one replacement panel."""

    def __init__(
        self,
        settings: Settings,
        *,
        domain_registry: DomainRegistry,
        domain_gateway: DomainGateway,
        widget_registry: WidgetRegistry,
        client_factory: ClientFactory = genai.Client,
        cerebras_client_factory: CerebrasClientFactory = AsyncOpenAI,
        max_tool_steps: int = _MAX_TOOL_STEPS,
    ) -> None:
        if max_tool_steps < 0:
            raise PlanAgentError("max_tool_steps must not be negative.")
        self._settings = settings
        self._domain_registry = domain_registry
        self._domain_gateway = domain_gateway
        self._widget_registry = widget_registry
        self._client_factory = client_factory
        self._cerebras_client_factory = cerebras_client_factory
        self._max_tool_steps = max_tool_steps

    async def plan(self, request: PlanAgentRequest) -> PlanAgentResult:
        """Return the final decision plus the trusted bundle for the Compiler."""

        if self._settings.planner_provider == "cerebras":
            return await self._plan_with_cerebras(request)
        if self._settings.planner_provider != "gemini":
            raise PlanAgentError("PLANNER_PROVIDER must be 'gemini' or 'cerebras'.")

        if not self._settings.plan_agent_api_key:
            raise PlanAgentError("GEMINI_API_KEY is not configured for the Plan Agent.")
        try:
            resources = self._domain_registry.load(request.domain_id)
            capabilities = self._domain_gateway.capability_catalog(request.domain_id)
        except (ManifestError, GatewayConfigurationError, GatewayPermissionError) as exc:
            raise PlanAgentError(str(exc)) from exc

        bundle = request.initial_bundle or self._domain_gateway.empty_bundle(request.domain_id)
        payload = {
            "domain": resources.manifest.for_plan_agent(),
            "intent": request.intent,
            "recent_history": list(request.recent_history),
            "canvas": {"columns": 16, "rows": 10},
            "assets": resources.assets.plan_agent_catalog(),
            "template_catalog": resources.templates.for_plan_agent(),
            "widget_index": self._widget_registry.widget_index(
                resources.manifest.allowed_widget_ids
            ),
            "capabilities": [capability.for_plan_agent() for capability in capabilities],
            "verified_data": _bundle_for_agent(bundle),
        }
        if request.validation_feedback is not None:
            payload["compiler_feedback"] = dict(request.validation_feedback)
        messages: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=json.dumps(payload, ensure_ascii=False))])
        ]
        client = self._client_factory(api_key=self._settings.plan_agent_api_key)
        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION,
            tools=[_native_tools(capabilities)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        tool_call_count = 0
        described_widget_ids: set[str] = set()
        described_template_ids: set[str] = set()

        while True:
            response = await self._generate_response(client, messages, config)
            calls = _function_calls(response)
            if not calls:
                return self._final_result(
                    response,
                    request.domain_id,
                    resources,
                    bundle,
                    described_widget_ids=described_widget_ids,
                    described_template_ids=described_template_ids,
                )
            if tool_call_count + len(calls) > self._max_tool_steps:
                raise PlanAgentError("Plan Agent exceeded the allowed number of capability calls.")

            messages.append(_model_content(response, calls))
            function_responses: list[types.Part] = []
            for call in calls:
                name = getattr(call, "name", None)
                arguments = _mapping(getattr(call, "args", None), f"{name} arguments")
                if name == _DESCRIBE_WIDGETS_NAME:
                    response_data = self._describe_widgets(
                        widget_ids=arguments.get("widget_ids"),
                        allowed_widget_ids=resources.manifest.allowed_widget_ids,
                    )
                    described_widget_ids.update(item["id"] for item in response_data["widgets"])
                elif name == _DESCRIBE_TEMPLATE_NAME:
                    response_data = self._describe_template(
                        template_id=arguments.get("template_id"),
                        resources=resources,
                    )
                    described_template_ids.add(response_data["template_id"])
                elif name == _CALL_CAPABILITY_NAME:
                    capability_id = _text(arguments.get("capability_id"), "capability_id")
                    capability_arguments = _mapping(arguments.get("arguments"), "capability arguments")
                    try:
                        update = self._domain_gateway.execute(
                            domain_id=request.domain_id,
                            capability_id=capability_id,
                            arguments=capability_arguments,
                        )
                    except (GatewayConfigurationError, GatewayPermissionError) as exc:
                        raise PlanAgentError(str(exc)) from exc
                    bundle = _merge_bundles(bundle, update)
                    response_data = {
                        "capability_id": capability_id,
                        "verified_data": _bundle_for_agent(update),
                    }
                else:
                    raise PlanAgentError("Plan Agent called an unsupported native function.")
                function_responses.append(types.Part(
                    function_response=types.FunctionResponse(
                        name=name,
                        id=getattr(call, "id", None),
                        response=response_data,
                    )
                ))
            tool_call_count += len(calls)
            messages.append(types.Content(role="user", parts=function_responses))

    async def _plan_with_cerebras(self, request: PlanAgentRequest) -> PlanAgentResult:
        """Run the same agent loop through Cerebras' OpenAI-compatible API."""

        if not self._settings.cerebras_api_key:
            raise PlanAgentError("CEREBRAS_API_KEY is not configured for the Plan Agent.")
        try:
            resources = self._domain_registry.load(request.domain_id)
            capabilities = self._domain_gateway.capability_catalog(request.domain_id)
        except (ManifestError, GatewayConfigurationError, GatewayPermissionError) as exc:
            raise PlanAgentError(str(exc)) from exc

        bundle = request.initial_bundle or self._domain_gateway.empty_bundle(request.domain_id)
        payload = {
            "domain": resources.manifest.for_plan_agent(),
            "intent": request.intent,
            "recent_history": list(request.recent_history),
            "canvas": {"columns": 16, "rows": 10},
            "assets": resources.assets.plan_agent_catalog(),
            "template_catalog": resources.templates.for_plan_agent(),
            "widget_index": self._widget_registry.widget_index(resources.manifest.allowed_widget_ids),
            "capabilities": [capability.for_plan_agent() for capability in capabilities],
            "verified_data": _bundle_for_agent(bundle),
        }
        if request.validation_feedback is not None:
            payload["compiler_feedback"] = dict(request.validation_feedback)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        client = self._cerebras_client_factory(
            api_key=self._settings.cerebras_api_key,
            base_url="https://api.cerebras.ai/v1",
        )
        tool_call_count = 0
        described_widget_ids: set[str] = set()
        described_template_ids: set[str] = set()

        while True:
            try:
                completion = await client.chat.completions.create(
                    model=self._settings.cerebras_planner_model,
                    messages=messages,
                    tools=_cerebras_tools(capabilities),
                    parallel_tool_calls=False,
                )
            except Exception as exc:
                logger.warning(
                    "[PLAN_AGENT_REQUEST_FAILED] provider=cerebras error_type=%s detail=%s",
                    type(exc).__name__, str(exc)[:500],
                )
                raise PlanAgentError("Plan Agent did not return a planning response.") from exc

            choices = getattr(completion, "choices", None) or ()
            if not choices:
                raise PlanAgentError("Cerebras returned no planning choice.")
            message = choices[0].message
            calls = tuple(getattr(message, "tool_calls", None) or ())
            if not calls:
                return self._final_result_from_text(
                    getattr(message, "content", None),
                    request.domain_id,
                    resources,
                    bundle,
                    described_widget_ids=described_widget_ids,
                    described_template_ids=described_template_ids,
                )
            if tool_call_count + len(calls) > self._max_tool_steps:
                raise PlanAgentError("Plan Agent exceeded the allowed number of capability calls.")

            messages.append(message.model_dump(exclude_none=True))
            for call in calls:
                function = getattr(call, "function", None)
                name = getattr(function, "name", None)
                raw_arguments = getattr(function, "arguments", None)
                try:
                    arguments = _mapping(json.loads(raw_arguments), f"{name} arguments")
                except (TypeError, json.JSONDecodeError, PlanAgentError) as exc:
                    raise PlanAgentError(f"Cerebras returned invalid arguments for {name}.") from exc
                if name == _DESCRIBE_WIDGETS_NAME:
                    response_data = self._describe_widgets(
                        widget_ids=arguments.get("widget_ids"),
                        allowed_widget_ids=resources.manifest.allowed_widget_ids,
                    )
                    described_widget_ids.update(item["id"] for item in response_data["widgets"])
                elif name == _DESCRIBE_TEMPLATE_NAME:
                    response_data = self._describe_template(
                        template_id=arguments.get("template_id"),
                        resources=resources,
                    )
                    described_template_ids.add(response_data["template_id"])
                elif name == _CALL_CAPABILITY_NAME:
                    capability_id = _text(arguments.get("capability_id"), "capability_id")
                    capability_arguments = _mapping(arguments.get("arguments"), "capability arguments")
                    try:
                        update = self._domain_gateway.execute(
                            domain_id=request.domain_id,
                            capability_id=capability_id,
                            arguments=capability_arguments,
                        )
                    except (GatewayConfigurationError, GatewayPermissionError) as exc:
                        raise PlanAgentError(str(exc)) from exc
                    bundle = _merge_bundles(bundle, update)
                    response_data = {
                        "capability_id": capability_id,
                        "verified_data": _bundle_for_agent(update),
                    }
                else:
                    raise PlanAgentError("Plan Agent called an unsupported native function.")
                messages.append({
                    "role": "tool",
                    "tool_call_id": getattr(call, "id", None),
                    "content": json.dumps(response_data, ensure_ascii=False),
                })
            tool_call_count += len(calls)

    def _describe_widgets(
        self,
        *,
        widget_ids: object,
        allowed_widget_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        if not isinstance(widget_ids, list) or not widget_ids:
            raise PlanAgentError("describe_widgets.widget_ids must be a non-empty array.")
        normalized_ids = tuple(_text(item, "widget_id") for item in widget_ids)
        if len(normalized_ids) != len(set(normalized_ids)):
            raise PlanAgentError("describe_widgets.widget_ids must not contain duplicates.")
        allowed = set(allowed_widget_ids)
        widgets: list[dict[str, Any]] = []
        for widget_id in normalized_ids:
            if widget_id not in allowed:
                raise PlanAgentError(f"widget_id '{widget_id}' is not allowed by the active domain.")
            try:
                widget = self._widget_registry.get(widget_id)
            except WidgetPropsError as exc:
                raise PlanAgentError(str(exc)) from exc
            widgets.append({
                "id": widget.widget_id,
                "purpose": widget.purpose,
                "props": widget.public_props_contract(),
                "initial_visibility": {
                    "type": "string",
                    "required": False,
                    "default": "visible",
                    "allowed_values": ["visible", "hidden"],
                    "description": (
                        "Initial display state for this block. The Plan Agent chooses it; "
                        "Gemini Live decides when a hidden block is revealed."
                    ),
                },
            })
        return {"widgets": widgets}

    @staticmethod
    def _describe_template(*, template_id: object, resources: Any) -> dict[str, Any]:
        requested_id = _text(template_id, "template_id")
        try:
            template = resources.templates.load_layout_template(requested_id)
        except TemplateCatalogError as exc:
            raise PlanAgentError(str(exc)) from exc
        return {
            "template_id": template.template_id,
            "description": template.description,
            "bindings": [binding.to_dict() for binding in template.bindings],
        }

    async def _generate_response(
        self,
        client: Any,
        messages: list[types.Content],
        config: types.GenerateContentConfig,
    ) -> Any:
        try:
            return await client.aio.models.generate_content(
                model=self._settings.plan_agent_model,
                contents=messages,
                config=config,
            )
        except Exception as exc:
            logger.warning("[PLAN_AGENT_REQUEST_FAILED] error_type=%s detail=%s", type(exc).__name__, str(exc)[:500])
            raise PlanAgentError("Plan Agent did not return a planning response.") from exc

    @staticmethod
    def _final_result(
        response: Any,
        domain_id: str,
        resources: Any,
        bundle: DataBundle,
        *,
        described_widget_ids: set[str],
        described_template_ids: set[str],
    ) -> PlanAgentResult:
        response_text = getattr(response, "text", None)
        return PlanAgent._final_result_from_text(
            response_text,
            domain_id,
            resources,
            bundle,
            described_widget_ids=described_widget_ids,
            described_template_ids=described_template_ids,
        )

    @staticmethod
    def _final_result_from_text(
        response_text: object,
        domain_id: str,
        resources: Any,
        bundle: DataBundle,
        *,
        described_widget_ids: set[str],
        described_template_ids: set[str],
    ) -> PlanAgentResult:
        if not isinstance(response_text, str) or not response_text.strip():
            raise PlanAgentError("Plan Agent returned neither a function call nor a final JSON decision.")
        logger.info("[PLAN_AGENT_RAW_DECISION] chars=%d output=%s", len(response_text), response_text)
        try:
            decision = _parse_decision(json.loads(response_text), domain_id=domain_id)
        except (json.JSONDecodeError, PlanAgentError) as exc:
            logger.warning("[PLAN_AGENT_INVALID_DECISION] error_type=%s detail=%s", type(exc).__name__, str(exc)[:500])
            raise PlanAgentError("Plan Agent returned an invalid final decision.") from exc
        if isinstance(decision, UseExistingPlanDecision):
            if not resources.templates.contains(decision.template_id):
                raise PlanAgentError("Plan Agent selected a template_id that is not in the domain template catalog.")
            if decision.template_id not in described_template_ids:
                raise PlanAgentError("Plan Agent must call describe_template before using a layout template.")
            template = resources.templates.load_layout_template(decision.template_id)
            expected_binding_keys = {binding.key for binding in template.bindings}
            actual_binding_keys = set(decision.bindings)
            if actual_binding_keys != expected_binding_keys:
                raise PlanAgentError("use_existing_plan bindings must exactly match the layout template contract.")
        if isinstance(decision, CreatePlanDecision):
            used_widget_ids = {block.widget_id for block in decision.plan.blocks}
            missing_widget_ids = sorted(used_widget_ids - described_widget_ids)
            if missing_widget_ids:
                raise PlanAgentError(
                    "Plan Agent must call describe_widgets before using: "
                    + ", ".join(missing_widget_ids)
                )
        return PlanAgentResult(decision=decision, data_bundle=bundle)
