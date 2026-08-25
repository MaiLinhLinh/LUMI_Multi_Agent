# CP0 — Audit mã nguồn `gemini_live`

Ngày audit: 2026-08-20.

Mục tiêu của audit là giữ toàn bộ ứng dụng Live/Present đang hoạt động theo
đúng cơ chế hiện tại, nhưng thay hoàn toàn panel HTML theo domain bằng
`PresentationPlan → PanelIR → widget CSS Grid`.

## Luồng đang dùng ở project cũ

```text
Browser WebSocket
  → web_app.live_socket()
  → PersistentGeminiLiveConversation
  → Gemini Live tool call
  → LiveSessionOrchestrator
  → Domain registry/dispatcher
  → PresentationPipeline
  → panel HTML hoặc DynamicGrid POC
  → JSON `panel` + stage map/effects
  → browser render

Gemini `present_visual`
  → ActivePresentationState.resolve()
  → visual marker chờ
  → marker đi cùng PCM kế tiếp
  → AudioContext start
  → AnimationController/effect
```

## Phần sẽ sao chép vào `gemini_live_2`

| Phần | Module nguồn | Lý do |
|---|---|---|
| WebSocket lifecycle, reconnect grace, PCM/text commands | `web_app.py` | Giữ một session Live persistent theo browser session. Composition root sẽ thay mới. |
| Gemini Live configuration, VAD, AEC-compatible stream protocol, tool response, marker/PCM | `live/gemini_session.py` | Đây là lõi speech/Present cần giữ nguyên hành vi. Tool registry sẽ thay bằng `route_request` ở CP10. |
| Một receive task cho mỗi Live socket | `live/persistent_transport.py` | Hoàn toàn domain-neutral. |
| State kỹ thuật, transport event vocabulary | `live/session_protocol.py` | Giữ nguyên. |
| Memory session và trace | `live/memory.py`, `trace.py` | Giữ; data/context domain sẽ được thiết kế lại phía trên Gateway. |
| Validate `present_visual` và cue server-side | `live/visual_presentation.py` | Đổi nguồn map từ template metadata sang `PanelIR`, nhưng contract marker giữ nguyên. |
| Animation lifecycle/effect registry | `web/presentation/animation_controller.js`, `web/presentation/effects/` | Không phụ thuộc domain/template cũ. |
| UI shell: panel trái, chat/mic bên phải, AudioContext queue, VAD mic UI, trace bubble | `web/index.html`, `web/app.css`, `web/app.js` | Sao chép toàn bộ. CP6 thay riêng renderer của vùng panel bằng `PanelIR` renderer. |

## Phần chỉ dùng làm tham khảo, không sao chép làm kiến trúc mới

| Phần cũ | Lý do loại bỏ/thay thế |
|---|---|
| `presentation/renderer.py`, `capabilities.py`, Jinja template HTML, metadata template | Ràng buộc panel vào template HTML và `data-present-id` được viết sẵn. Thay bằng widget renderer + PanelIR. |
| `presentation/pipeline.py` và `PresentationRequest` cũ | Chứa nhánh Jinja, Template LLM thử nghiệm, metadata capability và stage map template. Thay bởi Plan Agent, IR Compiler/Materializer. |
| `presentation/base.py` / `DomainPresentationAdapter` | Gắn adapter vào template/stage state cũ. Không cần trong framework mới. |
| `presentation/dynamic_grid.py` | Có ý tưởng đúng (grid 12×10, server anchor map, ASCII map), nhưng chỉ hỗ trợ `text`/`image`, URL Education cố định và tự sinh anchor theo block. Dùng làm tài liệu cho CP4–CP6, không dùng runtime. |
| `template_engine/` cũ | Là Template LLM POC với catalog/template HTML cũ; thay bằng Plan Agent chung, Domain Manifest, PresentationPlan và PanelIR. |
| `domains/*` cũ, domain registry/dispatcher cũ | Giữ làm nguồn tham khảo cho Gateway/domain tool sau này. Không copy để tránh kéo grounded-fact, adapter và HTML pipeline cũ vào project mới. |

## Điểm tích hợp tối thiểu của PanelIR mới

1. `route_request`/Plan Agent tạo `PresentationPlan`.
2. Compiler materialize `PanelIR`, gồm block thật, `anchor_id → target_id → allowed_effects`, và revision.
3. Backend gửi `panel` chuẩn PanelIR cho browser; browser Panel Renderer dựng widget CSS Grid.
4. ASCII Renderer đọc đúng cùng `PanelIR` và gửi map cho Gemini Live.
5. `ActivePanelState` nhận anchor/effect map từ PanelIR; `present_visual` và marker/PCM không đổi.

## Baseline project cũ

Lệnh đã chạy:

```powershell
conda run -n LumiMultiAgent python -m pytest D:\RAG_ManageAgent_Lumi\gemini_live\tests -q
```

Kết quả: **38 passed, 9 failed, 6 subtests passed**.

Chín lỗi đều thuộc pipeline/template Education cũ: metadata cho
`object_group_math` và `repeated_groups_arithmetic` không còn tồn tại tại
đường dẫn mà `presentation.capabilities` yêu cầu. Không sửa project cũ, vì
`gemini_live_2` sẽ không tái sử dụng pipeline/template HTML đó.

## Command chạy app cũ để đối chiếu hành vi

```powershell
Set-Location D:\RAG_ManageAgent_Lumi\gemini_live
conda run -n LumiMultiAgent python web_app.py
```

CP1 sẽ tạo ứng dụng độc lập trong `gemini_live_2`, không import runtime từ
thư mục này và không sao chép/sửa `.env`.
