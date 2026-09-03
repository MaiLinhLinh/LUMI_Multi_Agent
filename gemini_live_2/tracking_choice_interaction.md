# Tracking — Choice Interaction

## Mục tiêu

Cho phép Plan Agent tạo các thẻ lựa chọn gồm widget con; trẻ có thể chạm ảnh,
nhãn hoặc vùng trống của thẻ. Browser gửi sự kiện chọn về backend, backend chỉ
xác minh lựa chọn thuộc panel đang mở rồi thông báo lựa chọn đó cho Gemini Live.
Gemini tự đánh giá, phản hồi và chọn hiệu ứng tiếp theo.

## Checkpoints

### C1 — Contract Choice

- [x] Thêm `children` vào contract Plan/Panel; `anchor_id` do Compiler sinh là
  định danh lựa chọn duy nhất.
- [x] Chỉ cho child `image`, `text`, `number_display`, `object_group`.

### C2 — Widget Registry

- [x] Đăng ký widget `choice`, anchor toàn thẻ, effect `highlight`/`circle`
  và event `select`.

### C3 — Compiler

- [x] Validate choice không có props riêng; validate child widget.
- [x] Compile children vào PanelIR; không tạo anchor riêng cho child.

### C4 — Payload Browser

- [x] Gửi children đã compile và asset của child xuống frontend.

### C5 — Renderer Choice

- [x] Render thẻ lựa chọn dọc; ảnh/nhóm ở trên, chữ ở dưới.
- [x] Click/touch/Enter/Space phát `panel:interaction` trong browser.

### C6 — WebSocket Client

- [x] `app.js` nghe `panel:interaction` trong Shadow DOM của panel.
- [x] Gửi event interaction chỉ gồm `panel_id`, `anchor_id`, `action:"select"`
  qua WebSocket khi socket đang mở.

### C7 — Backend + Gemini Context

- [x] Xác minh panel active, `anchor_id` và action `select`; backend tự tra
  block choice nội bộ từ anchor map.
- [x] Gửi Gemini Live event cấu trúc `choice_selected` gồm `anchor_id` và
  children đã compile, không kèm đánh giá đúng/sai hoặc lời thoại backend.
- [x] Cập nhật ASCII map để phản ánh child hiển thị và anchor của toàn thẻ.

### C8 — Plan Agent

- [x] Thêm `choice` vào Widget Index, contract discovery và few-shot hoạt động chọn.
- [x] Bắt buộc Agent describe cả `choice` lẫn mọi widget con trước khi tạo plan.

### C9 — End-to-end

- [ ] Test chọn ảnh, nhãn, vùng trống; event sai bị từ chối.
- [ ] Xác nhận Gemini nhận đúng lựa chọn và `present_visual` khoanh cả thẻ.
