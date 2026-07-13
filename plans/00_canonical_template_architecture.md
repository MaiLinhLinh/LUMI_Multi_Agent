# Canonical Architecture: Semantic Router and Visualization

> Đây là nguồn sự thật duy nhất cho kiến trúc hiện hành. Các plan khác trong
> thư mục này chỉ là historical/reference và không được dùng để quyết định
> flow mới nếu có mâu thuẫn với file này.

## 1. Mục tiêu

Hệ thống cần phân biệt câu hỏi domain với yêu cầu visualization/template,
hiểu đầy đủ ý định template bằng một Semantic Router LLM, rồi điều phối đúng
workflow mà không phân tích lại cùng một input nhiều lần.

## 2. Luồng chuẩn

```text
User input
  ↓
System command handler
  ├── exit / clear / help / command cố định
  ↓
High-confidence hard-code domain detector
  ├── chắc chắn là domain
  │     → Domain workflow
  │
  └── không chắc chắn
        → Semantic Router LLM (một lần gọi)
              ↓
              Semantic JSON
              ├── route=domain
              │     → Domain workflow
              │
              └── route=visualize
                    → Visualization Orchestrator
```

Hard-code detector phải bảo thủ: chỉ bypass LLM với domain request rõ ràng và
không có tín hiệu visualization. Nếu không chắc chắn, luôn chuyển cho LLM.

## 3. Semantic Router contract

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

- `route=domain`: `domain_request` là input chuẩn hóa cho Domain Manager.
- `show_options`: không cần extract design requirements.
- `select_existing`: chỉ extract `template_id` hoặc `selection_index`; không
  cần requirements thiết kế.
- `design_template`: extract requirements, source template và keywords.
- `status=needs_clarification`: phải có một câu hỏi ngắn, dễ hiểu.
- `status=cancelled`: kết thúc yêu cầu.
- LLM không tạo HTML, không gọi tool và không tự tạo asset ID.
- Output luôn phải được validate trước khi đi tiếp.

## 4. Visualization Orchestrator

Orchestrator chỉ nhận semantic JSON đã validate và điều phối, không gọi lại
LLM để phân tích intent.

```text
action=show_options
  → Template Registry
  → lọc theo domain/schema/available fields
  → hiển thị danh sách

action=select_existing
  → validate template_id/index với candidate list
  → Registry lookup
  → deterministic Renderer

action=design_template
  → Template Agent workflow

action=cancel hoặc needs_clarification
  → trả message và cập nhật session state
```

Các mode `choose/create/customize/interpret` không phải semantic intent đầu
vào. Semantic intent nằm trong `template.action`; mode chỉ là thông tin thực
thi hành/kết quả nếu cần cho output backward compatibility.

## 5. Template Agent workflow

`design_template` bao gồm cả tạo mới và chỉnh sửa template hiện tại. Hai yêu
cầu này dùng cùng một pipeline:

```text
semantic_result.template.requirements
  → inspect actual domain data
  → search complete templates
  → search base templates
  → search/filter components
  → decide reuse/assemble/create strategy
  → select components
  → generate todo list
  → generate fill plan
  → deterministic assemble
  → validate security/syntax/contract
  → repair khi cần
  → render preview
  → save artifact and metadata
```

Template Agent không gọi lại requirement extraction cho graph semantic path.
Requirements từ Semantic Router là input chính. Requirement extractor cũ chỉ
được giữ nếu cần tương thích với caller legacy độc lập và phải được đánh dấu
non-primary.

Nếu có complete template phù hợp nhưng user yêu cầu thay đổi, template đó chỉ
là candidate/source; không được kết thúc sớm bằng `Existing template match
selected.`.

## 6. Conversation state

Session cần lưu tối thiểu:

```text
active_template_id
active_template_path
available_templates
pending_template_state
template_requirements
template_clarification_round
last_domain_result
```

Khi Semantic Router hỏi clarification, phải lưu:

```json
{
  "status": "collecting_requirements",
  "requirements": {},
  "missing_information": [],
  "source_template_id": null,
  "clarification_round": 1
}
```

Lượt trả lời tiếp theo phải merge requirements mới với state cũ trước khi
chạy Template Agent.

## 7. LLM và fallback policy

- Semantic Router là một lần gọi LLM cho input không chắc chắn.
- Domain workflow và Template Agent vẫn có các LLM call chuyên biệt của
  chính chúng; “một LLM” nghĩa là một semantic analyzer cho bước route/action,
  không phải toàn bộ ứng dụng chỉ được gọi API một lần.
- Invalid JSON/API failure phải tạo controlled error hoặc clarification; không
  được tự động render sai template.
- Fallback heuristic chỉ dành cho offline/test và không được thay thế semantic
  path trong runtime có LLM.

## 8. Acceptance tests

Phải có test cho:

```text
1. Domain rõ ràng → bypass Semantic Router.
2. Domain không rõ → Semantic Router route=domain.
3. “Có những mẫu template nào?” → show_options.
4. “Dùng mẫu 2” → select_existing.
5. “Dùng template hiện tại nhưng đổi nền hồng” → design_template.
6. “Tạo template mới” → needs_clarification nếu thiếu requirements.
7. Clarification follow-up → merge state cũ/mới.
8. Invalid LLM JSON → controlled fallback/error.
9. Orchestrator không gọi lại Semantic Router.
10. Template Agent nhận requirements đã extract.
```
