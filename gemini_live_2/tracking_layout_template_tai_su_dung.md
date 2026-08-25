# Tracking — Layout Template tái sử dụng

Mục tiêu: lưu khung bố cục tái sử dụng, không lưu dữ liệu cụ thể của panel cũ.

## Checkpoint

### LT1 — Metadata prop widget

- [x] Widget Registry phân biệt `template_value_kind`: `structural` và `binding`.
- [x] Đánh dấu các prop hiện có: `text.content`, `image.asset_id`, `image.label`,
  `object_group.asset_id/count/label` là binding; `text.role` là structural.

### LT2 — LayoutTemplate và TemplateExtractor

- [x] Thêm contract `LayoutTemplate` và `TemplateBinding`.
- [x] Template Extractor thay prop biến đổi bằng key ổn định
  `$block_<số>_<prop>`.
- [x] Giữ nguyên grid, widget và prop cấu trúc.

### LT3 — Catalog lưu/nạp LayoutTemplate

- [x] Catalog hỗ trợ `layout_path`, `load_layout_template()` và
  `save_layout_template()`.
- [x] Giữ `plan_path`/`load_plan()` tạm thời để route hiện tại không gãy trước
  khi có Materializer.

### LT4 — `describe_template`

- [x] Thêm native tool chung `describe_template(template_id)` cho cả Gemini và
  Cerebras provider.
- [x] Trả `template_id`, mô tả và contract binding của LayoutTemplate; không
  render hoặc thay panel.

### LT5 — Plan Agent dùng binding

- [x] Mở rộng `use_existing_plan` để Agent trả bindings.
- [x] Với LayoutTemplate, bắt buộc Agent đã gọi `describe_template` và gửi đủ,
  đúng toàn bộ binding key; legacy plan không nhận binding.

### LT6 — Materializer và route flow

- [x] Thay binding vào LayoutTemplate để tạo PresentationPlan đầy đủ trước
  Compiler.
- [x] Chuyển route flow sang `load_layout_template()`; không còn compatibility loader
  sau khi hoàn tất LT7.
- [x] Sau khi `create_plan` được Compiler validate, Extractor tự lưu LayoutTemplate
  mới bằng ID ngắn tuần tự `tm1`, `tm2`… và mô tả do Plan Agent trả về.

### LT7 — Migrate và kiểm thử

- [x] Migrate `two_subject_comparison` sang LayoutTemplate.
- [x] Test "hai con mèo" dùng cùng khung nhưng cả hai `asset_id` là `cat`.
- [x] Xóa compatibility loader `plan_path`/`load_plan()`; catalog chỉ còn LayoutTemplate.
