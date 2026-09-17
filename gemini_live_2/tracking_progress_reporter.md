# Tracking — ProgressReporter framework

## 0. Phạm vi chốt

Chỉ thêm `ProgressReporter` theo đúng luồng hiện có:

```text
Gemini → route_request(domain_id, intent)
       → trả {"status":"planning"}
       → chạy background Plan Agent
       → render panel
       → SURFACE_READY
```

Giữ nguyên `route_request`, Orchestrator, Plan prompt, schema tool gửi model, vòng tool loop và cách Plan Agent reasoning, Compiler, Browser, nội dung và điều kiện tạo `SURFACE_READY`.

Framework nhỏ được thêm duy nhất:

```text
tool result thành công → ToolResultAvailable → ProgressReporter → PLAN_PROGRESS gửi Gemini
```

Không có execution plan, phần trăm tiến độ, gộp báo cáo, debounce hay thay đổi cách Gemini giao việc cho Plan Agent.

---

## 1. Event nguồn: `ToolResultAvailable`

### Hook và vị trí

`PlanAgentRequest` đã có `telemetry_callback`. Trong cả hai vòng tool loop của `plan_agent/service.py` (Gemini provider và Cerebras provider), giữ nguyên thứ tự reasoning:

```text
_execute_tool(...)
→ có response_data
→ emit plan_tool_result_received để log
→ append function response/tool message vào context
→ Plan Agent nghĩ bước tiếp
```

Chỉ chèn một event nội bộ sau khi có `response_data` và **trước** khi result được append vào context model:

```python
ToolResultAvailable(
    plan_run_id=...,
    tool_name=...,
    arguments=...,
    response=...,
)
```

Payload qua callback:

```json
{
  "event": "tool_result_available",
  "plan_run_id": "...",
  "tool_name": "...",
  "arguments": {},
  "response": {}
}
```

Đây là event có cấu trúc, không parse lại log text. `plan_tool_result_received` giữ nguyên để render log hiện có. `tool_result_available` chỉ dành cho ProgressReporter.

### Điều kiện phát

Chỉ phát khi `response` không có `error`. Không phát khi tool mới bắt đầu, arguments không hợp lệ, search service/capability lỗi, route bị hủy hoặc Plan Agent vượt tool limit. Gemini chỉ nhận fact thật đã có kết quả.

---

## 2. Metadata `describe_widgets`

### File

`plan_agent/tools.py`

### Thay đổi

Thêm metadata nội bộ tách khỏi schema gửi provider:

```python
TOOL_RESULT_PROGRESS_METADATA = {
    DESCRIBE_WIDGETS: {
        "on_results_message": (
            "Đã xem các thành phần giao diện cần thiết để chuẩn bị hoạt động."
        ),
    },
}
```

Không thêm field này vào `DESCRIBE_WIDGETS_TOOL`, vì dict đó dùng để sinh function schema cho model.

Trong phạm vi lần này:

```text
describe_widgets → có progress message
describe_template → giữ nguyên, không tự thêm message
```

---

## 3. `live/progress_reporter.py`

### Trách nhiệm

Module độc lập chỉ đổi tool-result event thành progress report. Nó không gọi transport, không nói với người dùng và không can thiệp Plan Agent.

### Object nội bộ

```python
ToolResultAvailable(
    plan_run_id,
    tool_name,
    arguments,
    response,
)

PlanProgress(
    plan_run_id,
    message,
)
```

`response` cho Reporter lấy fact từ đúng tool call hiện tại, như title/caption. `plan_run_id` của `PlanProgress` chỉ dùng nội bộ để loại report stale; Gemini không nhận field này.

### API và formatter

```python
reporter.build(event: ToolResultAvailable) -> PlanProgress | None
```

| Tool | Chỉ đọc từ response của call đó | `PlanProgress.message` |
| --- | --- | --- |
| `search_web` | `verified_data.data.search_results[0].title` | `Đã tìm được nguồn “{title}”.` |
| `search_image` | `verified_data.data.search_results[0].caption` | `Đã tìm được hình ảnh “{caption}”.` |
| `describe_widgets` | metadata `on_results_message` | Câu metadata đã chốt |
| Tool khác | Không formatter | `None`, không gửi Gemini |

Đọc response của đúng call để không lẫn title/caption từ bundle search tích lũy.

Reporter không được tính phần trăm, gộp event, debounce, suy ra nguồn đã được chọn/kết quả đúng, hay tự nói với người dùng. Nó chỉ tạo `PlanProgress` hoặc `None`.

---

## 4. Gemini session nhận event

### File

`live/gemini_session.py`

`_complete_route_request()` đã có closure `telemetry(...)`, nhận event Plan Agent rồi chuyển sang `_emit_plan_telemetry()`.

Mở rộng đúng nhánh này:

```text
plan_tool_result_received
→ giữ nguyên render log

tool_result_available
→ tạo ToolResultAvailable
→ ProgressReporter.build(...)
→ nếu có PlanProgress: đưa vào FIFO
```

Không gửi raw `response` sang browser log hoặc Gemini.

### FIFO delivery

Thêm state:

```python
self._pending_plan_progress: deque[PlanProgress] = deque()
```

Mỗi report là phần tử riêng, không summary chung.

| Trạng thái Gemini khi report đến | Xử lý |
| --- | --- |
| Đang nói đoạn chuyển tiếp sau `planning` | append FIFO, chưa gửi transport |
| Đang nói bất kỳ lượt nào | append FIFO, không chen transport |
| Rảnh | schedule delivery task của `GeminiLiveSession`, gửi ngay qua persistent transport |

Delivery chạy trong task của session, không chạy trực tiếp trong tool loop. Plan Agent chỉ phát event và reasoning tiếp; không chờ Gemini nhận/phản hồi.

---

## 5. `GEMINI_TURN_COMPLETE`

Cả hai receive loop phải dùng cùng logic:

- `consume_audio_stream()` cho mic;
- `_consume_until_settled()` cho text-only.

Sau turn complete:

```text
1. Gemini được đánh dấu rảnh.
2. Lấy PLAN_PROGRESS từ FIFO.
3. Gửi từng message riêng theo thứ tự FIFO.
4. Sau đó mới xét SURFACE_READY/SURFACE_FAILED đang pending.
```

Ví dụ:

```text
FIFO: nguồn → ảnh → describe_widgets
GEMINI_TURN_COMPLETE
→ send PLAN_PROGRESS #1
→ send PLAN_PROGRESS #2
→ send PLAN_PROGRESS #3
→ send SURFACE_READY
```

Không gộp: mỗi report là một lần `send_text` riêng vào persistent transport.

---

## 6. Quan hệ với `SURFACE_READY`

Không đổi nội dung hoặc điều kiện tạo:

```text
Plan Agent xong → Compiler xong → Browser nhận panel render
→ panel context có Stage Map/revision/effects → SURFACE_READY
```

Chỉ đổi thứ tự delivery: progress cùng run đang chờ được gửi trước `SURFACE_READY`. Không có progress chờ thì `SURFACE_READY` chạy y nguyên. Không sửa Browser, `surface:rendered`, panel payload hay `present_visual`.

---

## 7. Route mới hủy route cũ

`_cancel_pending_route_task()` hiện đã hủy task cũ, xóa `_pending_surface_ready` và route bridge pending. Bổ sung:

```text
xóa mọi PlanProgress có plan_run_id của task cũ
```

Trước delivery kiểm tra report còn thuộc route hiện hành.

```text
route A đang search → report A chờ
Gemini gọi route B → cancel A → bỏ report A
→ A không thể gửi PLAN_PROGRESS hoặc SURFACE_READY
```

---

## 8. Điểm dừng: prompt Gemini

### File dự kiến

`live/registry.py`

**Chưa được phép sửa.** Sau khi runtime hoàn tất, phải dừng và hỏi người dùng cách xử lý prompt Gemini cho `PLAN_PROGRESS`.

Nội dung đã chốt để tham khảo, chưa áp dụng:

```text
Trong khi Plan Agent xử lý sau status="planning", Gemini có thể nhận PLAN_PROGRESS.
Đó là báo cáo nội bộ đáng tin cậy, không phải lời người dùng.

Không đọc nguyên văn event; không nhắc tool, JSON, URL hay dữ liệu kỹ thuật.
Chỉ nói điều báo cáo xác nhận; không suy ra kết quả đã được chọn, xác minh hoàn toàn hoặc đã xuất hiện trên panel.

Gemini tự quyết định có nói với người dùng hay không. Có thể im lặng nếu chưa hữu ích. Nếu nói, dùng lời tự nhiên, ngắn và phù hợp lúc người dùng chờ.
```

Không thêm prompt vào tool response `{"status":"planning"}`. Nếu được duyệt, đây là core prompt ổn định từ đầu phiên.

---

## 9. File trong phạm vi

| File | Thay đổi |
| --- | --- |
| `plan_agent/tools.py` | Metadata nội bộ cho `describe_widgets` |
| `plan_agent/service.py` | Phát `tool_result_available` sau successful result ở cả Gemini/Cerebras loops |
| `live/progress_reporter.py` | Module mới: events và formatter title/caption/metadata |
| `live/gemini_session.py` | Nhận event, FIFO, delivery sau turn complete, loại stale report |
| `live/registry.py` | **Điểm dừng, không tự sửa** |
| `tests/test_plan_agent.py` | Test event chỉ phát sau successful result |
| `tests/test_live_routing.py` | Test FIFO, turn complete, cancellation, thứ tự trước `SURFACE_READY` |

---

## 10. Checklist thực hiện

- [x] Xác nhận vị trí phát event trong hai tool loops.
- [x] Thêm metadata `describe_widgets`, tách provider schema.
- [x] Tạo `live/progress_reporter.py` và test formatter.
- [x] Phát event chỉ cho successful result.
- [x] Nhận event/dựng `PlanProgress` trong session.
- [x] FIFO, delivery khi rảnh và sau turn complete.
- [x] Progress trước `SURFACE_READY` cùng run.
- [x] Dọn stale progress khi cancel route.
- [x] Viết và chạy test Plan/Live routing trong phạm vi thay đổi (29 tests pass).
- [ ] **Dừng, hỏi người dùng trước khi sửa `live/registry.py`.**

## 11. Tiêu chí nghiệm thu

- [ ] Plan Agent gọi tool và reasoning y nguyên trước đây.
- [ ] `search_web` thành công tạo đúng một report chỉ title.
- [ ] `search_image` thành công tạo đúng một report chỉ caption.
- [ ] `describe_widgets` tạo đúng câu metadata; `describe_template` không tự report.
- [ ] Không report tool start, error hoặc cancelled task; không gộp/dedup/percent.
- [ ] Gemini đang nói không bị chen lời; Gemini rảnh nhận report ngay.
- [ ] Route mới không nhận report hay `SURFACE_READY` từ route cũ.
- [ ] Không có progress chờ thì `SURFACE_READY` giữ nguyên hành vi.
