# Phân tích và kế hoạch kiến trúc Visualization

## 1. Luồng xử lý input

```text
User input
    ↓
Hard-code domain detector
    ├── chắc chắn domain → Domain Workflow
    └── còn lại → LLM1 Semantic Router
```

Hard-code domain detector chỉ bypass LLM1 khi chắc chắn input là câu hỏi domain. Input mơ hồ hoặc có tín hiệu template/UI phải chuyển sang LLM1. Hard-code không tự quyết định template action.

## 2. LLM1 — Semantic Router

LLM1 nhận query, history cần thiết, pending template state, active template metadata và available template metadata khi cần. LLM1 chỉ làm semantic routing, clarification và requirement extraction; không gọi Registry, Component Search hoặc sinh HTML.

LLM1 trả:

```json
{
  "status": "ready | needs_clarification | cancelled",
  "route": "domain | visualize | null",
  "domain_request": null,
  "template": {
    "action": "show_options | select_existing | design_template | cancel | null",
    "source": "current | selected | none",
    "template_id": null,
    "selection_index": null,
    "requirements": {},
    "extracted_keywords": []
  },
  "missing_information": [],
  "clarifying_question": null
}
```

Quy tắc:

- `show_options`: user muốn xem danh sách template;
- `select_existing`: chọn template có sẵn và không yêu cầu thiết kế lại;
- `design_template`: tạo, sửa, thêm, xóa hoặc customize visualization;
- `cancel`: hủy yêu cầu;
- thiếu thông tin thì trả `needs_clarification` và lưu pending state;
- `source=current` nghĩa là dùng active template trong session;
- không có active template khi user nói “template hiện tại” thì phải hỏi lại, không tự chọn template đầu tiên.

## 3. Visualization Orchestrator

Orchestrator chỉ điều phối theo action của LLM1, không phân tích lại câu user và không gọi LLM để xác định action.

```text
show_options    → Registry → hiển thị danh sách
select_existing → Registry validate → Renderer
design_template → Template Agent Workflow
cancel          → kết thúc yêu cầu
```

Nhánh `show_options` và `select_existing` không gọi LLM2, LLM3 hoặc LLM4. Registry kiểm tra template tồn tại, domain/schema compatibility, required fields và operation được phép.

## 4. Template Agent Workflow

```text
LLM1
    ↓ semantic route + requirements
LLM2 General Planner
    ↓ tool calls do LLM2 quyết định
Typed Execution Plan
    ↓
LLM3 nếu cần sinh base/component
    ↓
LLM4 luôn tạo Fill Plan
    ↓
Assembler luôn tạo template hoàn chỉnh
    ↓
Validator
    ↓
Renderer
```

LLM2 là General Planner, không bị giới hạn vào một danh sách nhỏ các câu user đã biết trước. User có thể yêu cầu vô hạn; LLM2 quy yêu cầu về một kế hoạch thực thi hữu hạn.

LLM2 nhận:

- requirements từ LLM1;
- active template metadata và source/template_id nếu có;
- domain data summary, schema_version và available_fields;
- catalog metadata của template/base/component khi cần;
- tool definitions;
- operation, security và validation constraints.

LLM2 tự quyết định:

- có cần tìm complete template, base template hoặc component không;
- có cần sinh base hoặc component không;
- tài nguyên nào cần đưa vào Fill Plan;
- target là template hiện tại hay base template;
- giữ template hiện tại ở mức nào;
- có cần gọi thêm tool hay không.

LLM2 có thể không gọi tool nếu metadata hiện tại đủ. LLM2 không được tự tạo ID của tài nguyên có sẵn.

## 5. Tool Calling của LLM2

Registry tools:

- `search_templates(domain, schema_version, available_fields, keywords)`;
- `search_base_templates(domain, required_slots, keywords)`;
- `get_template_metadata(template_id)`.

Component tools:

- `search_components(domain, slot, keywords, available_fields)`;
- `get_component_metadata(component_id)`.

Ví dụ kết quả tool:

```json
{
  "candidates": [
    {
      "id": "forecast_chart",
      "kind": "component",
      "supported_slots": ["chart"],
      "required_fields": ["forecast.days[].date"]
    }
  ]
}
```

Quy tắc tool:

- chỉ dùng ID xuất hiện trong tool result;
- lưu mọi tool call trong execution trace;
- tool không tìm thấy phải trả kết quả rỗng có cấu trúc;
- số vòng tool call có giới hạn deterministic;
- tool lỗi thì retry theo cấu hình hoặc trả planner error;
- không có candidate phù hợp thì LLM2 được đánh dấu generation plan cho LLM3;
- không được đoán ID khi tool không trả ID đó.

### Contract tool request/result

Tool request do LLM2 tạo và Executor thực thi:

```json
{
  "tool_call_id": "call_001",
  "tool_name": "search_components",
  "arguments": {
    "domain": "weather",
    "slot": "metrics",
    "keywords": ["rain", "weather", "icon"],
    "available_fields": ["current.weather_code"]
  }
}
```

`tool_name` chỉ được thuộc allow-list: `search_templates`, `search_base_templates`, `get_template_metadata`, `search_components`, `get_component_metadata`.

Tool result trả lại cho LLM2:

```json
{
  "tool_call_id": "call_001",
  "tool_name": "search_components",
  "status": "ok | not_found | error",
  "candidates": [
    {
      "id": "rain_icon",
      "kind": "component",
      "supported_slots": ["metrics"],
      "required_fields": ["current.weather_code"],
      "schema_version": "visualization.component.v1"
    }
  ],
  "error": null
}
```

`not_found` phải trả `candidates: []`. Executor validate request/result, giới hạn số vòng và timeout; LLM2 chỉ được dùng ID xuất hiện trong tool result. Các tool độc lập có thể chạy song song.

## 6. Typed Execution Plan

Typed Execution Plan là JSON có schema cố định để Deterministic Executor thực thi. Nó không phải TODO list. `todo_list` chỉ dùng cho người đọc, log hoặc audit; không được route bằng cách dò chuỗi trong TODO.

Schema chính thức:

```json
{
  "plan_version": "1.0",
  "target": {
    "mode": "existing_template | base_template",
    "template_ref": null,
    "base_ref": null,
    "preserve_existing_structure": false
  },
  "lookup_plan": {
    "templates": [],
    "base_templates": [],
    "components": []
  },
  "generation_plan": {
    "base": null,
    "components": []
  },
  "resource_plan": {
    "reuse_components": []
  },
  "modification_plan": {
    "style": {},
    "content": [],
    "layout": []
  },
  "todo_list": []
}
```

Ý nghĩa các kế hoạch:

- `lookup_plan` chỉ mô tả tài nguyên cần tìm, chưa chứa ID được LLM tự đoán;
- `resource_plan` chỉ chứa tài nguyên đã được tool result hoặc Registry xác nhận và sẽ được sử dụng;
- `generation_plan` chỉ chứa yêu cầu để LLM3 sinh artifact, chưa chứa `artifact_id`;
- `todo_list` chỉ phục vụ log/audit, không được dùng để quyết định workflow.

Ví dụ `lookup_plan`:

```json
{
  "templates": [],
  "base_templates": [],
  "components": [
    {
      "query": "rain icon",
      "domain": "weather",
      "slot": "metrics",
      "required_fields": ["current.weather_code"]
    }
  ]
}
```

Ví dụ `generation_plan`:

```json
{
  "base": null,
  "components": [
    {
      "generation_key": "animated_rain_icon",
      "kind": "component",
      "slot": "metrics",
      "description": "Animated rain icon",
      "required_fields": ["current.weather_code"]
    }
  ]
}
```

`new_template` không phải là mode riêng. “Tạo template mới” được biểu diễn bằng `generation_plan.base` khác `null`, sau đó artifact base được dùng trong mode `base_template`.

Quy tắc nghiệp vụ:

- `mode=existing_template` bắt buộc có `target.template_ref`, `target.base_ref=null` và `preserve_existing_structure=true`;
- `mode=base_template` bắt buộc có `target.base_ref` hoặc `generation_plan.base`, và `target.template_ref=null`;
- base có sẵn dùng `target.base_ref.ref_type=registry` và `target.base_ref.id`;
- base cần sinh dùng `generation_plan.base` với `generation_key`; artifact sinh ra sau đó được đưa vào `target.base_ref` ở runtime;
- `resource_plan.reuse_components` chỉ chứa component đã được tool result hoặc Registry xác nhận;
- `generation_plan.components` chỉ chứa yêu cầu sinh component, không chứa artifact ID;
- `generation_plan.base` khác `null` thì Executor gọi LLM3 để sinh base;
- `generation_plan.components` không rỗng thì Executor gọi LLM3 để sinh component;
- `existing_template` phải resolve được `target.template_ref`; `base_template` phải resolve được `target.base_ref` hoặc artifact base đã validate;
- mọi resource ID phải đến từ tool result hoặc Registry hợp lệ;
- artifact mới phải validate trước khi chuyển bước.

LLM2 không biết trước `artifact_id` do hệ thống cấp. Executor sẽ tạo `assembly_input` sau khi LLM3 hoàn tất.

## 7. Deterministic Executor

Executor chỉ đọc các field typed, không phân tích lại user query. LLM4 luôn chạy sau LLM2 và sau LLM3 nếu LLM3 được gọi.

```text
LLM3 nếu `generation_plan` yêu cầu sinh base/component
    ↓ validate artifact
LLM4 luôn tạo fill_plan
    ↓ validate fill_plan
Assembler luôn tạo template hoàn chỉnh
    ↓
Validator
    ↓
Renderer
```

Assembler có hai chế độ nội bộ:

- `existing_template`: load template hiện tại và áp dụng fill plan để giữ/cập nhật template đó;
- `base_template`: load base template, điền parameters và slots theo fill plan.

Trong chế độ `existing_template`, `preserve_existing_structure=true` có nghĩa là:

- giữ nguyên template ID;
- giữ nguyên component cũ, trừ khi fill plan có operation thay thế hoặc xóa hợp lệ;
- giữ nguyên slot và data binding;
- chỉ được thêm component tại extension point hợp lệ;
- không được dựng lại template từ base hoặc xóa nội dung cũ ngoài fill plan.

Việc thêm component tại extension point không bị xem là vi phạm bảo toàn cấu trúc; đó là thay đổi được cho phép và phải được Validator kiểm tra.

Đây không phải hai executor độc lập. Patcher chỉ là implementation detail bên trong Assembler ở chế độ `existing_template`.

Sau khi resolve Registry ID và artifact ID, Executor tạo `assembly_input` cho LLM4 và Assembler. `assembly_input` được phân biệt theo `target.mode`, không được luôn giả định có field `base`.

Với `existing_template`:

```json
{
  "target": {
    "mode": "existing_template",
    "template_ref": {
      "ref_type": "registry",
      "id": "weather_forecast",
      "kind": "complete_template"
    },
    "preserve_existing_structure": true
  },
  "components": [
    {
      "ref_type": "registry",
      "id": "rain_icon",
      "kind": "component",
      "status": "validated"
    }
  ]
}
```

Với `base_template`:

```json
{
  "target": {
    "mode": "base_template",
    "base_ref": {
      "ref_type": "artifact",
      "artifact_id": "art_req123_base_001",
      "kind": "base_template",
      "status": "validated"
    },
    "preserve_existing_structure": false
  },
  "components": [
      {
        "ref_type": "registry",
        "id": "forecast_chart",
        "kind": "component",
        "status": "validated"
      },
      {
        "ref_type": "artifact",
        "artifact_id": "art_req123_component_001",
        "kind": "component",
        "status": "validated"
      }
    ]
}
```

Chỉ artifact có `status=validated` mới được đưa vào `assembly_input`.

## 8. Các pipeline tổng quát

### 8.1. Sửa style template hiện tại

```text
LLM2 → LLM4 Fill Plan → Assembler(existing_template) → Validator → Renderer
```

Không gọi Component Search hoặc LLM3. LLM4 vẫn được gọi và tạo fill plan cho style modification.

### 8.2. Giữ template hiện tại và thêm/thay component

```text
LLM2
    ↓
Component Search nếu cần
    ↓
LLM3 nếu không tìm thấy component phù hợp
    ↓
Validate artifact
    ↓
LLM4 luôn tạo fill plan
    ↓
Assembler(existing_template)
    ↓
Validator
    ↓
Renderer
```

Đây vẫn là `target.mode=existing_template`, không dựng lại template từ base. Patcher là cơ chế nội bộ của Assembler.

### 8.3. Assemble từ base có sẵn

```text
LLM2 → Registry/Component tools nếu cần
     → LLM3 nếu cần sinh component
     → LLM4 Fill Plan
     → Assembler(base_template)
     → Validator
     → Renderer
```

### 8.4. Không có base phù hợp

```text
LLM2
    ↓ Registry xác nhận không có base phù hợp
LLM3 Generate Base
    ↓ LLM3 Generate Components nếu cần
Validate và đăng ký tạm thời artifact
    ↓
LLM4 Fill Plan
    ↓
Assembler(base_template) → Validator → Renderer
```

LLM2 quyết định cần tạo; LLM3 mới sinh HTML/metadata. LLM2 không tự sinh HTML.

## 9. Assembler

Assembler luôn chạy sau LLM4 và luôn tạo template HTML hoàn chỉnh.

Ở chế độ `existing_template`, Assembler hoạt động như Patcher: load template hiện tại và áp dụng fill plan.

Ở chế độ `base_template`, Assembler load base template, điền parameters và components vào slots.

Assembler ở chế độ `existing_template` không được tự ý đổi template ID, xóa component ngoài fill plan, làm rỗng slot, thay data binding ngoài fill plan, chuyển template thành base mới hoặc chèn component vào vị trí không có slot/extension point hợp lệ.

Các operation trong fill plan ở chế độ `existing_template` có thể gồm:

```json
{
  "plan_type": "fill_plan",
  "target": {
    "mode": "existing_template",
    "template_ref": {
      "ref_type": "registry",
      "id": "weather_forecast",
      "kind": "complete_template"
    }
  },
  "operations": [
    {
      "op": "insert_component",
      "slot": "metrics",
      "component_ref": {
        "ref_type": "registry",
        "id": "rain_icon",
        "kind": "component"
      },
      "position": "append"
    },
    {
      "op": "set_style",
      "property": "background_color",
      "value": "pink"
    }
  ]
}
```

Fill plan ở chế độ `base_template`:

```json
{
  "plan_type": "fill_plan",
  "target": {
    "mode": "base_template",
    "base_ref": {
      "ref_type": "artifact",
      "id": "art_req123_base_001",
      "kind": "base_template",
      "status": "validated"
    }
  },
  "operations": [],
  "parameters": {"page_title": "Weather Dashboard"}
}
```

LLM4 không được tự tạo ID, dùng artifact chưa validate, trả raw HTML/CSS/JavaScript, thay binding ngoài execution plan hoặc sử dụng slot không có trong metadata.

## 10. LLM3 và artifact lifecycle

LLM3 chỉ được gọi khi generation plan yêu cầu sinh base hoặc component. LLM3 trả content, metadata và local key; hệ thống tự cấp ID chính thức.

```json
{
  "key": "animated_rain_icon",
  "component_html": "...",
  "metadata": {
    "kind": "component",
    "domain": "weather",
    "supported_slots": ["metrics"],
    "required_fields": []
  }
}
```

Lifecycle:

```text
generated
→ staged
→ validated
→ used_by_llm4
→ assembled
→ rendered
→ promoted | expired | rejected | quarantined
```

Artifact metadata chính thức:

```json
{
  "artifact_id": "art_req123_component_001",
  "request_id": "req123",
  "kind": "component",
  "source": "llm3",
  "status": "staged",
  "created_at": "...",
  "expires_at": "..."
}
```

API tối thiểu:

```text
stage_artifact(content, metadata) → artifact_id
get_artifact(artifact_id) → artifact
validate_artifact(artifact_id) → validation_result
promote_artifact(artifact_id) → registry_id
expire_artifact(artifact_id)
reject_artifact(artifact_id, reason)
```

Chỉ artifact có `status=validated` mới được đưa vào LLM4, `assembly_input` hoặc Assembler.

Validator phải kiểm tra JSON/schema, ID/path traversal, duplicate ID, slot, required fields, max components, required slots, placeholders và security. Không cho phép script, iframe, inline event handler, form action hoặc network request. Artifact lỗi bị reject và không được đăng ký chính thức; retry LLM3 phải có giới hạn.

## 11. Allow-list style modification

LLM2/LLM4 chỉ trả semantic style value, không trả raw CSS.

```json
{
  "background_color": ["default", "white", "pink", "blue", "green"],
  "surface_color": ["default", "white", "pink", "blue"],
  "text_color": ["default", "dark", "light"],
  "accent_color": ["default", "pink", "blue", "green"],
  "border_radius": ["default", "small", "medium", "large"],
  "spacing_scale": ["compact", "default", "comfortable"]
}
```

Không cho phép raw CSS, CSS selector, HTML/JavaScript, external resource, đổi data binding hoặc thay đổi layout trong style-only operation.

## 12. Contract extension point cho `existing_template`

Template hoàn chỉnh hỗ trợ thêm/thay component phải khai báo extension point:

```json
{
  "id": "weather_forecast",
  "kind": "complete_template",
  "extension_points": [
    {
      "slot": "metrics",
      "selector": "[data-slot='metrics']",
      "allowed_operations": ["insert_component", "replace_component"],
      "max_components": 6
    }
  ],
  "components": [
    {
      "id": "temperature_card",
      "slot": "metrics",
      "binding": "current.temperature"
    }
  ]
}
```

Assembler chỉ được insert/replace khi slot tồn tại, operation được cho phép, component tương thích và required fields đầy đủ. Nếu không có extension point, không được tự chèn HTML; phải trả `extension_point_missing` và giữ template cũ.

## 13. Validation server-side và invariant

Trước patch, hệ thống tạo snapshot gồm:

```json
{
  "template_id": "weather_forecast",
  "component_ids": [],
  "slot_names": [],
  "placeholders": [],
  "data_bindings": [],
  "required_fields": [],
  "schema_version": "weather.forecast.v1",
  "structure_hash": "..."
}
```

Sau patch phải kiểm tra:

- template ID không đổi;
- component cũ không bị mất hoặc thay đổi ngoài plan;
- slot cũ không bị mất;
- data binding và schema version không đổi;
- required fields cũ không đổi;
- HTML structure chỉ thay đổi theo operation trong fill plan;
- component mới có metadata/field contract hợp lệ;
- style chỉ dùng allow-list;
- security validation vẫn pass.

Validation bắt buộc ở các điểm:

- sau LLM2: schema, mode/ID, lookup/generation consistency và không có ID giả;
- sau tool: tool name, arguments, result schema và candidate kind;
- sau LLM3: artifact schema, slot, required fields, duplicate ID, path traversal, script/iframe/event handler, URL nguy hiểm, kích thước và security;
- sau LLM4: `plan_type`, target reference, operation, slot, component reference, allow-list style và binding;
- sau Assembler: HTML parse, required slots/components, binding, placeholder, security và khả năng render.

Với `existing_template`, invariant bắt buộc:

- template ID không đổi;
- component cũ không mất ngoài remove operation được phép;
- slot cũ không mất;
- binding và schema version không đổi ngoài kế hoạch;
- component mới nằm đúng extension point;
- HTML chỉ thay đổi theo fill plan;
- style chỉ dùng allow-list.

Nếu invariant thất bại: reject, không render, giữ template cũ và trả `invariant_failed`.

## 14. Vai trò của từng LLM

| LLM | Nhiệm vụ |
|---|---|
| LLM1 | Semantic route, clarification, requirements |
| LLM2 | General planning, quyết định tool calls, tạo typed execution plan |
| LLM3 | Sinh base/component artifact khi plan yêu cầu |
| LLM4 | Luôn tạo fill plan sau LLM2 và sau LLM3 nếu LLM3 được gọi |
| Domain LLMs | Xử lý domain data và tạo domain response |

## 15. Metadata và prompt

Không truyền toàn bộ HTML hoặc file template nếu metadata là đủ. Prompt nên tách:

```text
[PHẦN CỐ ĐỊNH]
System rules
Output schema
Tool rules
Validation rules

[PHẦN ÍT THAY ĐỔI]
Template/base/component catalog metadata

[PHẦN ĐỘNG]
User requirements
Active template metadata
Domain data summary
Conversation context cần thiết
```

Mục tiêu là để LLM2 bao quát yêu cầu mở rộng của user bằng cách lập kế hoạch tài nguyên và workflow, không phải bằng cách tạo trước một strategy riêng cho từng câu hỏi.
