# Tracking — Panel State, Reveal và Kịch bản Tương Tác

## Mục tiêu

Cho phép Plan Agent quyết định trạng thái hiển thị ban đầu của từng block;
Gemini Live tự biên diễn lời nói, thời điểm reveal một hoặc nhiều vùng và hiệu
ứng minh hoạ; backend chỉ xác thực chuyển trạng thái; frontend render/animate;
ASCII map luôn phản ánh đúng panel hiện tại.

Luồng đích:

```text
Plan Agent: block + initial_visibility
        ↓
PanelCompiler: PanelIR có runtime visibility
        ↓
Gemini Live: panel_action(reveal, anchor_ids) + present_visual
        ↓
Backend: validate state transition, cập nhật ActivePanelState
        ↓
Frontend/widget: render trạng thái mới và animation
        ↓
ASCII map mới trả về Gemini Live
```

## Nguyên tắc đã chốt

- `initial_visibility` là thuộc tính chung của `PlanBlock`, không phải props
  riêng của widget. Giá trị hợp lệ: `visible`, `hidden`; mặc định `visible`.
- Plan Agent quyết định block nào hiện hoặc ẩn khi panel khởi tạo, dựa trên ý đồ
  hoạt động. Agent không quyết định thời điểm reveal lúc runtime.
- Gemini Live quyết định hoàn toàn thứ tự reveal: từng anchor, nhiều anchor
  cùng lúc, hoặc các bước cách nhau; đồng thời tự chọn lúc gọi `present_visual`.
- Không có `reveal_group` cố định trong plan.
- Backend không tự chấm đáp án, không tự reveal và không áp đặt trình tự dạy.
- Một anchor ổn định trước/sau reveal; trước reveal nó trỏ vùng placeholder,
  sau reveal nó trỏ nội dung thật của cùng block.
- `answer` chỉ là widget kết quả số/chữ. Ảnh, nhóm vật thể hoặc biểu đồ kết quả
  vẫn là widget bình thường có `initial_visibility: "hidden"`.

## Checkpoints

### PS1 — Contract visibility chung

- [x] Thêm `initial_visibility` vào `PlanBlock` với default `visible`.
- [x] Validate chỉ nhận `visible | hidden`.
- [x] Materialize sang `PanelBlock.visibility` trong `PanelIR`.
- [x] Bổ sung test compiler cho default, hidden và giá trị không hợp lệ.

### PS2 — Widget `answer`

- [x] Đăng ký widget `answer` dùng chung.
- [x] Contract props: `value`; hỗ trợ visibility chung.
- [x] Renderer frontend: `hidden` hiển thị `?`, `visible` hiển thị `value`.
- [x] Tạo anchor `answer` với effect hợp lệ.
- [x] Cho Education opt-in `answer` qua manifest.
- [x] Thêm CSS và test widget/compiler tương ứng.

### PS3 — Render trạng thái ẩn/hiện

- [x] Frontend nhận visibility của từng block trong PanelIR.
- [x] `image`, `object_group`, `text` render placeholder/vùng ẩn an toàn khi
      `hidden`, không làm lộ props thật.
- [x] Giữ DOM target/anchor cho block ẩn để backend và animation dùng ổn định.
- [x] Thêm transition reveal cơ bản ở CSS/widget.
- [x] Kiểm tra panel ban đầu và anchor map.

### PS4 — Plan Agent và contract discovery

- [x] Mở rộng schema/parse `create_plan` để nhận `initial_visibility` tùy chọn.
- [x] `describe_widgets` nêu rõ mọi block hỗ trợ visibility; widget `answer`
      có mô tả props và hành vi khi ẩn/hiện.
- [x] Cập nhật system prompt: Agent thiết kế trạng thái ban đầu, Gemini quyết
      thời điểm reveal.
- [x] Thêm few-shot bài `1 mèo + 2 mèo`, ẩn nhóm 3 mèo và số 3; hai block kết
      quả xếp dọc cùng cột.
- [x] Cập nhật test Plan Agent contract.

### PS5 + PS6 — Tool chung `panel_action` và panel update

- [x] Khai báo tool Gemini Live chung:
      `panel_action(action_id, anchor_ids)`.
- [x] Bản đầu chỉ hỗ trợ `action_id: "reveal"`.
- [x] Validate anchor thuộc ActivePanelState, block đang hidden và action hợp lệ.
- [x] Một lệnh được phép reveal một hay nhiều anchors.
- [x] Reveal lặp lại hoặc anchor không hợp lệ trả tool response bị từ chối rõ ràng.
- [x] Không thay đổi luồng `present_visual` hiện có.
- [x] Lưu PanelIR/state có thể cập nhật trong `ActivePanelState`.
- [x] Sau `panel_action`, tạo PanelIR state mới nhưng giữ `panel_id`, block ID,
      anchor ID và target ID ổn định.
- [x] Gửi event/payload `panel_update` cho browser.
- [x] Function response cho Gemini gồm ASCII map mới và visual effects hợp lệ.
- [x] Không gọi Plan Agent hoặc tạo panel mới khi reveal.

### PS7 — Frontend update và animation

- [x] Nhận `panel_update` và render lại panel; bản đầu có thể render toàn panel.
- [x] Widget reveal có transition rõ ràng, không phụ thuộc vào `present_visual`.
- [x] Sau update, `present_visual` vẫn tìm đúng DOM target của cùng anchor.
- [x] Test reveal nhiều anchor cùng lúc và reveal tuần tự ở backend state/anchor map.

### PS8 — ASCII map theo runtime state

- [x] Stage-map renderer mô tả block hidden bằng placeholder an toàn, không
      đưa giá trị thật vào map.
- [x] Block visible hiển thị nội dung render thật và anchor đúng vùng đó.
- [x] Log map trước/sau action để đối chiếu UI trong test.
- [x] Xác nhận function response của `panel_action` cấp map mới cho Gemini.

### PS9 — End-to-end và hồi quy

- [ ] Kịch bản: `1 mèo + 2 mèo = ?`; nhóm 3 mèo và số 3 hidden.
- [ ] Gemini reveal nhóm ảnh, gọi animation, nói về nhóm.
- [ ] Gemini reveal số, gọi animation, nêu đáp án.
- [ ] Test reveal cùng lúc hai anchors.
- [ ] Test reveal lặp/anchor sai bị từ chối.
- [ ] Chạy toàn bộ test suite liên quan Plan Agent, compiler, renderer, routing
      và presentation.

## Ví dụ Plan Agent cần sinh được sau PS4

```json
{
  "decision": "create_plan",
  "template_description": "Phép cộng trực quan với hai nhóm đầu vào và vùng kết quả đặt dọc ở bên phải.",
  "plan": {
    "blocks": [
      {
        "widget_id": "object_group",
        "grid": { "col": 1, "row": 3, "col_span": 2, "row_span": 3 },
        "props": { "asset_id": "cat", "count": 1 }
      },
      {
        "widget_id": "object_group",
        "grid": { "col": 4, "row": 3, "col_span": 2, "row_span": 3 },
        "props": { "asset_id": "cat", "count": 2 }
      },
      {
        "widget_id": "object_group",
        "initial_visibility": "hidden",
        "grid": { "col": 8, "row": 3, "col_span": 3, "row_span": 3 },
        "props": { "asset_id": "cat", "count": 3 }
      },
      {
        "widget_id": "answer",
        "initial_visibility": "hidden",
        "grid": { "col": 8, "row": 6, "col_span": 3, "row_span": 2 },
        "props": { "value": "3" }
      }
    ]
  }
}
```
