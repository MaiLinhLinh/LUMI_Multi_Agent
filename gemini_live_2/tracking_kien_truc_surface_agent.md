# Tracking — Kiến trúc Surface Agent

> Kế hoạch nguồn: [ke_hoach_kien_truc_surface_agent.md](ke_hoach_kien_truc_surface_agent.md)
>
> Quy ước: chỉ triển khai một checkpoint sau khi được xác nhận. Mỗi checkpoint
> hoàn thành phải cập nhật trạng thái, thay đổi chính, kiểm thử và quyết định còn mở.

## Trạng thái tổng quan

| Checkpoint | Nội dung | Trạng thái | Kiểm thử | Ghi chú |
|---|---|---|---|---|
| SA1 | Chuẩn hoá `SurfaceState` | Hoàn thành | 29 tests pass | Tách contract, giữ compatibility `PanelIR`. |
| SA2 | Gộp reveal vào `update_surface_state` | Hoàn thành | 31 targeted tests pass | Thay tool reveal riêng bằng state transition. |
| SA3 | Surface lifecycle contract | Hoàn thành | 35 targeted tests pass | Contract create/patch/delete, 5 patch operation và semantics merge props. |
| SA4 | `ActiveSurfaceSummary` cho Plan Agent | Hoàn thành | 36 targeted tests pass | Lưu purpose từ route intent; summary chỉ nạp khi route. |
| SA5 | Mở rộng output Plan Agent: create/patch | Hoàn thành | 50 targeted tests pass | Output lifecycle mới; create có adapter tạm đến SA6. |
| SA6 | Runtime materialization + revision | Hoàn thành | 52 targeted tests pass | Create/patch materialize cùng Compiler, giữ identity và rollback atomic. |
| SA7 | Gemini Live tools và prompt | Hoàn thành | 53 targeted tests pass | Giữ `route_request` là tên chuẩn; thêm `delete_surface`. |
| SA8 | Contract tương tác Browser | Hoàn thành | 42 targeted tests pass | `surface_id` + revision, action do Widget Registry xác nhận. |
| SA9 | Tests và migration | Hoàn thành | 63 targeted tests; 86 full-suite tests pass | Thêm test map/revision create → patch → state → delete, cập nhật migration notes. |
| SA10 | Tái sử dụng và tự lưu template | Hoàn thành | 89 full-suite tests pass | Agent dùng template qua bindings; create hợp lệ tự lưu khung, kể cả choice children. |

## SA10 — Tái sử dụng và tự lưu template

- [x] Thêm output `use_existing_surface_template(template_id, bindings)` cho Plan Agent.
- [x] Bắt Agent gọi `describe_template` trước khi dùng template và chỉ trả bindings biến đổi.
- [x] Runtime materialize template đã xác minh rồi mới Compiler tạo `PanelIR`.
- [x] Sau `create_surface_plan` compile thành công, trích `LayoutTemplate` và lưu với ID ngắn `tmN`.
- [x] Tách binding cả trong `choice.children`, không cố định asset/text lựa chọn cũ.
- [x] Binding optional có thể không gửi; binding required vẫn được Runtime kiểm tra.

**Đã hoàn thành:** Template phù hợp không còn buộc Agent lặp lại blocks/grid. Với layout mới, Runtime chỉ lưu sau khi Compiler đã render thành công; lỗi lưu catalog không làm mất panel đang hiển thị. `TemplateExtractor` bảo toàn `initial_visibility` và các widget con của `choice`. Prompt Plan Agent đã buộc thứ tự phân tích yêu cầu → describe template → chỉ reuse khi khung đáp ứng đủ, đồng thời phân biệt rõ widget discovery của create với reuse template.

## SA1 — Chuẩn hoá `SurfaceState`

- [x] Khảo sát `PanelIR`, active panel state và state widget hiện có.
- [x] Tách `SurfaceStructure` và `SurfaceState` nhưng vẫn giữ tương thích renderer hiện tại.
- [x] Xác định state thuộc widget: `visibility`, `selected`, `flipped`, `position`, `feedback`, `progress`.
- [x] Cập nhật contract/serialization cần thiết.
- [x] Thêm test không làm thay đổi UI hiện tại.

**Hoàn thành khi:** Runtime có structure và state tách bạch; từ hai phần này vẫn materialize được `PanelIR` như trước.

## SA2 — Gộp reveal vào `update_surface_state`

- [x] Định nghĩa payload `update_surface_state(surface_id, base_revision, updates)`.
- [x] Khai báo state fields và transition hợp lệ trong Widget Registry.
- [x] Chuyển `hidden → visible` thành một state update chuẩn.
- [x] Gỡ hoặc chuyển hướng action/tool reveal riêng, không tạo hai luồng state song song.
- [x] Test reveal một anchor, nhiều anchor và transition không hợp lệ.

**Hoàn thành khi:** Reveal dùng cùng đường `update_surface_state`; Runtime cập nhật revision và trả map mới.

## SA3 — Surface lifecycle contract

- [x] Chốt schema `create_surface_plan`.
- [x] Chốt schema `patch_surface_plan` cho thêm/bớt/sắp lại component.
- [x] Chốt schema `delete_surface`.
- [x] Chốt enum patch: `add_block`, `remove_block`, `replace_block`, `move_block`, `update_props` cùng payload hợp lệ của từng operation.
- [x] Chốt semantics `surface_id`, `base_revision` và revision mới, gồm xử lý stale revision.
- [x] Viết validation và unit test cho từng operation.

`update_props` dùng `changes` để gộp vào props hiện có; không thay toàn bộ props.

**Hoàn thành khi:** Mọi thay đổi structure/state có contract rõ, được validate trước render.

## SA4 — `ActiveSurfaceSummary`

- [x] Tạo summary nghiệp vụ từ active surface: purpose, domain, revision, anchor/widget/description và state cần thiết.
- [x] Không đưa DOM, CSS, `target_id` hay payload frontend kỹ thuật vào summary.
- [x] Nạp summary theo `session_id` chỉ khi Gemini gọi route request.
- [x] Test surface trống, surface đang active và surface đã xoá.

**Hoàn thành khi:** Plan Agent nhận đủ ngữ cảnh để quyết định create hay patch mà không thấy chi tiết render kỹ thuật.

**Đã hoàn thành:** `ActivePanelState` lưu `purpose` chính là `route.intent` đã chuẩn hoá. `ActiveSurfaceSummary` chỉ gồm surface/domain/revision/purpose, các anchor-widget-mô tả nghiệp vụ và state khác mặc định. Orchestrator chuẩn bị summary chính xác khi `route_request`; Plan Agent sẽ dùng nó trong payload ở SA5.

## SA5 — Mở rộng Plan Agent output

- [x] Cập nhật prompt/schema để Plan Agent trả `create_surface_plan` hoặc `patch_surface_plan`.
- [x] Cấp `active_surface_summary`, resource indexes, verified data và capability domain.
- [x] Giữ vòng capability native để Agent lấy dữ liệu tin cậy khi cần.
- [x] Chỉ cấp `widget_index` và `template_index` ngắn trong context ban đầu.
- [x] Giữ native tool `describe_widgets(widget_ids)` để Agent lấy widget contract chi tiết khi cần.
- [x] Giữ native tool `describe_template(template_id)` để Agent lấy structure/binding template khi cần.
- [x] Chuẩn hoá compiler feedback để Agent có thể sửa plan lỗi.
- [x] Thêm few-shot cho create và patch.

**Hoàn thành khi:** Agent không còn chỉ tạo plan mới; có thể chọn patch khi intent thực sự là thay đổi structure surface đang mở.

**Đã hoàn thành:** `PlanAgentResult` nay trả `command` theo Surface lifecycle contract, không còn `use_existing_plan`/`create_plan`. Payload của cả Gemini và Cerebras đều có `active_surface_summary`; native capability/widget/template discovery vẫn giữ nguyên. `describe_template` trả thêm cấu trúc block để Agent biến template thành `create_surface_plan` đầy đủ. Trong lúc chờ SA6, route flow chỉ adapter `create_surface_plan` thành `PresentationPlan` để compiler/render hiện có tiếp tục hoạt động; `patch_surface_plan` được giữ nguyên và sẽ do Runtime thực thi ở SA6.

## SA6 — Runtime materialization

- [x] Implement apply create plan → structure/state → `PanelIR`.
- [x] Implement apply patch plan → structure/state → `PanelIR`.
- [x] Implement apply state update → `PanelIR`.
- [x] Persist active surface theo `session_id`, `surface_id`, revision.
- [x] Trả `surface_id`, revision, ASCII map và effects sau thay đổi UI.
- [x] Test rollback/không lưu khi validation hoặc materialization thất bại.

**Hoàn thành khi:** Runtime là nguồn sự thật duy nhất và frontend/ASCII map luôn được dựng từ cùng `PanelIR` revision mới nhất.

**Đã hoàn thành:** Runtime nay áp dụng `create_surface_plan` và `patch_surface_plan` qua cùng `PanelCompiler`, rồi lưu `SurfaceStructure` + `SurfaceState` trong `ActivePanelState`. Patch kiểm tra `surface_id`/`base_revision`, gộp props theo contract, và materialize toàn bộ candidate trước khi lưu. Block/anchor còn tồn tại giữ ID của chính chúng; block/anchor mới do Compiler cấp. Nếu validate grid/widget/asset thất bại, panel đang hiển thị và revision không đổi. Create, patch và state update đều trả PanelIR cùng map/effects/revision.

## SA7 — Gemini Live tools và prompt

- [x] Khai báo đúng năm nhánh: `no_ui`, `present_visual`, `update_surface_state`, `delete_surface`, `route_request`.
- [x] Đảm bảo `present_visual` là animation tạm thời, không tăng revision/map.
- [x] Đảm bảo `update_surface_state` và delete nhận response map mới trước khi Gemini tiếp tục.
- [x] Bổ sung hướng dẫn router: chỉ route khi cần surface mới hoặc thay structure.
- [x] Bổ sung prompt domain present dùng map/revision hiện tại.
- [x] Test reconnect: nạp lại prompt domain + active map/revision.

**Hoàn thành khi:** Gemini Live có thể trực tiếp điều phối hội thoại và state của surface hiện tại mà không gọi Plan Agent không cần thiết.

**Đã hoàn thành:** `route_request` là tên Live chuẩn duy nhất cho panel mới hoặc đổi cấu trúc. `update_surface_state` trả map/effects/revision mới trước khi Gemini tiếp tục; `present_visual` chỉ tạo animation cue tạm thời. Thêm `delete_surface`, xác minh surface/revision, xoá panel ở browser và trả empty stage map. Khi reconnect, context có `surface_id`, `base_revision`, prompt domain, map và effects.

## SA8 — Contract tương tác Browser

- [x] Chuẩn hoá event tối thiểu cho click/select; contract sẵn sàng cho drag và drop khi widget tương ứng được đăng ký.
- [x] Browser chỉ gửi `surface_id`, revision, `anchor_id`, action và dữ liệu thao tác tối thiểu.
- [x] Runtime xác minh surface active, revision, anchor và allowed action từ Widget Registry.
- [x] Gửi trusted interaction event cho Gemini Live, không tự chấm đúng/sai ở backend.
- [x] Chuyển choice interaction hiện có sang contract mới.
- [x] Test event giả, stale revision, anchor/action không hợp lệ.

**Hoàn thành khi:** Click/drag/drop được browser gửi an toàn; Gemini nhận đúng sự kiện và tự quyết phản hồi hoặc state update tiếp theo.

**Đã hoàn thành:** Browser gửi `surface_id`, `revision`, `anchor_id`, `action`; không còn dùng `panel_id` ở interaction boundary. Runtime chỉ chấp nhận revision đang active và kiểm tra action bằng `WidgetDefinition.interaction_event`, thay vì hard-code widget `choice`. Event đáng tin cậy gửi Gemini có surface/revision/widget/action cùng nội dung UI đã compile; browser không thể gửi nội dung học hay kết luận đúng/sai. Hiện `choice/select` là interaction đã có renderer; drag/drop sẽ dùng cùng contract khi widget của chúng được đăng ký.

## SA9 — Tests và migration

- [x] Regression test render `PanelIR`, stage map, `present_visual` và audio/WebSocket.
- [x] Test create, patch, update state, delete và revision.
- [x] Test map luôn khớp PanelIR sau mỗi UI operation.
- [x] Test reconnect và stale event.
- [x] Gỡ các nhánh reveal/state cũ sau khi test thay thế pass.
- [x] Cập nhật tài liệu kiến trúc và migration notes.

**Hoàn thành khi:** Luồng Surface Agent thay thế an toàn luồng cũ, không còn state/reveal song song và toàn bộ regression test pass.

**Đã hoàn thành:** Regression hiện bao phủ render PanelIR/stage map, cue `present_visual`, WebSocket panel update, reconnect, stale revision/event, create/patch/state update/delete và rollback patch lỗi. Test tích hợp mới kiểm đúng map được trả ở từng revision trong chuỗi create → patch → state update → delete. Không còn tool hoặc runtime path reveal riêng; reveal dùng `update_surface_state`.

## Quyết định đã chốt

- `PanelIR` là output trung gian duy nhất để frontend render và để sinh ASCII map.
- Gemini Live quyết định lời thoại, ý đồ sư phạm, animation tạm thời và thời điểm đổi state.
- Plan Agent chỉ chạy sau `route_request`; có thể gọi capability domain nhiều lần.
- Runtime validate, persist structure/state/revision và không tự chấm đúng/sai hay quyết định cách dạy.
- `reveal` là `update_surface_state` với `visibility: visible`, không phải luồng riêng.
- `present_visual` chỉ là animation tạm thời, không đổi map/revision.
- Plan Agent chỉ nhận `ActiveSurfaceSummary`, không nhận DOM/CSS/payload kỹ thuật.

## Nhật ký thực hiện

| Ngày | Checkpoint | Kết quả | Ghi chú |
|---|---|---|---|
| 2026-08-27 | Khởi tạo | Tạo tracking | Chưa triển khai checkpoint nào. |
| 2026-08-27 | SA1 | Hoàn thành | Thêm `SurfaceStructure`, `SurfaceState`, `BlockState` và materializer tương thích `PanelIR`; 29 regression tests pass. |
| 2026-08-27 | SA2 | Hoàn thành | Thay `panel_action(reveal)` bằng `update_surface_state`; visibility transition do Widget Registry validate; 31 targeted tests pass. Full suite có 1 test catalog fail do các template `tm*` do người dùng tạo còn trong catalog, không liên quan SA2. |
| 2026-08-27 | SA3 | Hoàn thành | Thêm contract parse/serialize cho create, patch và delete; gồm 5 patch operation. `update_props` gộp `changes`; 35 targeted tests pass. Chưa nối Plan Agent hay Runtime apply patch. |
| 2026-08-27 | SA7 | Hoàn thành | Chuẩn hoá Live theo `route_request`, `update_surface_state`, `present_visual`, `delete_surface` và `no_ui`; reconnect nạp surface/revision/map/prompt. 53 targeted tests pass. |
| 2026-08-27 | SA8 | Hoàn thành | Chuẩn hoá browser interaction theo `surface_id` + revision; Runtime kiểm action từ Widget Registry và gửi trusted event chung cho Gemini. |
| 2026-08-27 | SA9 | Hoàn thành | Thêm regression kiểm map/revision xuyên create → patch → state → delete; cập nhật migration notes Surface Agent. |
| 2026-08-27 | SA10 | Hoàn thành | Thêm `use_existing_surface_template`, Runtime materializer và tự lưu template sau compile thành công; binding hỗ trợ choice children. |
