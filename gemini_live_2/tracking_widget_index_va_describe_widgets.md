# Tracking — Widget Index và `describe_widgets`

> CP-W6 hoàn thành: Plan Agent chỉ trả `widget_id`, `grid`, `props`; Compiler sinh block ID, target ID và anchor. UI/ASCII map chỉ dùng PanelIR đã materialize.

## Mục tiêu

Giảm lượng contract widget trong prompt đầu của Plan Agent:

```text
Plan Agent nhận Widget Index ngắn
  → chọn template có sẵn hoặc xác định widget cần dùng
  → gọi native tool describe_widgets(widget_ids)
  → nhận contract props chi tiết của đúng các widget đó
  → trả Presentation Plan
  → Compiler tạo PanelIR và anchor_id
```

Template Catalog vẫn là catalog một tầng, ngắn gọn. Plan Agent nhận trực tiếp
`id`, `purpose`, `supports`, `domains`; không có tool `describe_templates`.

## Quy ước đã chốt

- `widget_id`: ID widget do Plan Agent chọn, ví dụ `text`, `image`.
- Plan Agent chỉ tạo `widget_id`, `grid`, `props` trong block của plan.
- Plan Agent không tạo block ID hoặc `anchor_id`.
- Compiler sinh block ID tuần tự và anchor ngắn cho Gemini Live.
- `describe_widgets` là tool hạ tầng dùng chung, không thuộc một domain.
- Tool chỉ mô tả widget được phép trong `allowed_widget_ids` của domain hiện hành.
- Capability lấy/tạo dữ liệu vẫn nằm trong `domains/<domain>/tools.py` và vẫn bị manifest kiểm soát.

## Checkpoint

### CP-W1 — Rà contract và cập nhật tài liệu

- [x] Rà `WidgetRegistry`, Presentation Plan contract, Compiler, native tool loop và test hiện có.
- [x] Cập nhật kế hoạch framework và checkpoint chính để phản ánh Widget Index, `describe_widgets` và Template Catalog một tầng.
- [x] Không đổi runtime ở checkpoint này.

**Kết quả tại CP-W1:** Runtime khi đó còn dùng `widget_type` và block `id`.
Mô tả này đã được CP-W6 thay thế: runtime hiện tại dùng `widget_id`; Plan Agent
không sinh block ID, còn Compiler sinh block ID/anchor.

### CP-W2 — Chuẩn hoá Widget Registry

- [x] Thống nhất tên công khai `widget_id`.
- [x] Khai báo cho từng widget: purpose ngắn và contract props chi tiết có thể gửi cho Plan Agent.
- [x] Chỉ rõ prop nào tham chiếu Asset Catalog, ví dụ `image.asset_id` lấy từ `asset_catalog.id`.
- [x] Giữ validator runtime tương ứng với contract công khai.

**Kết quả tại CP-W2:** Registry đã khai báo `widget_id`, `purpose` và props
contract cho `text`, `image`, `object_group`. Compatibility alias được dùng tạm
trước CP-W6 đã được gỡ; runtime hiện tại chỉ dùng `widget_id`.

### CP-W3 — Tạo Widget Index

- [x] Thêm API Registry tạo Widget Index ngắn: `id` + `purpose`.
- [x] Plan Agent chỉ nhận Widget Index ở payload khởi đầu.
- [x] Bảo đảm index bị lọc theo `allowed_widget_ids` của domain.

**Kết quả:** `WidgetRegistry.widget_index()` chỉ xuất `id` và `purpose`; payload
khởi đầu của Plan Agent dùng index này và lấy allow-list từ manifest. Compiler và
Plan Agent dùng cùng Widget Registry. Không còn API catalog cũ trên Widget Registry;
`AssetCatalog.plan_agent_catalog()` là API riêng, hiện vẫn dùng để gửi Asset Catalog
an toàn cho Plan Agent.

### CP-W4 — Thêm native tool `describe_widgets`

- [x] Khai báo FunctionDeclaration chung `describe_widgets(widget_ids)`.
- [x] Xác thực danh sách widget ID và phạm vi domain hiện hành.
- [x] Trả contract chi tiết chỉ cho widget hợp lệ được yêu cầu.
- [x] Trả FunctionResponse theo đúng call ID để Plan Agent tiếp tục suy nghĩ.

**Kết quả:** `describe_widgets` luôn được cấp như native tool chung, kể cả khi
domain chưa có capability nghiệp vụ. Tool xác thực danh sách ID không rỗng,
không trùng lặp và thuộc `allowed_widget_ids`, rồi trả `id`, `purpose`, props
contract theo đúng function call ID. `call_capability` vẫn chỉ được khai báo khi
domain có capability được cấp quyền.

### CP-W5 — Sửa prompt và vòng lặp Plan Agent

- [x] Prompt giải thích rõ vai trò của Template Catalog, Asset Catalog, Widget Index và capability domain.
- [x] Bắt buộc gọi `describe_widgets` trước khi dùng widget trong `create_plan`.
- [x] Cho phép gọi nhiều lượt `describe_widgets` và capability domain trước khi trả quyết định cuối.
- [x] Giữ hai final decision: `use_existing_plan` và `create_plan`.

**Kết quả:** Prompt đầu vào chỉ coi Widget Index là catalog khám phá ngắn. Vòng lặp
ghi nhận từng widget đã được `describe_widgets` trả contract và từ chối một
`create_plan` dùng widget chưa được mô tả. `use_existing_plan` không cần gọi tool
này; native tool loop vẫn cho phép xen kẽ nhiều lượt `describe_widgets` và
`call_capability` trong giới hạn bước hiện có.

### CP-W6 — Chuẩn hoá Presentation Plan và Compiler

- [x] Plan `create_plan` chỉ nhận block gồm `widget_id`, `grid`, `props`.
- [x] `domain_id` của plan được backend lấy từ `route_request` đã kiểm chứng; Plan Agent không được trả lại trường này.
- [x] Bỏ yêu cầu Plan Agent tạo block `id`.
- [x] Compiler sinh block ID, anchor map và PanelIR.
- [x] Cập nhật renderer/ASCII map để tiếp tục dùng PanelIR làm nguồn duy nhất.

### CP-W7 — Kiểm thử và hoàn thiện

- [ ] Test tái sử dụng template có sẵn mà không cần `describe_widgets`.
- [ ] Test Plan Agent gọi `describe_widgets` rồi tạo plan chó/mèo bằng `text` và `image`.
- [ ] Test widget ngoài phạm vi domain và props sai bị từ chối.
- [ ] Test Compiler sinh block ID, target ID, anchor map ổn định.
- [ ] Chạy toàn bộ test suite bằng môi trường `LumiMultiAgent`.

## Ngoài phạm vi đợt này

- Không thay đổi Gemini Live router, ActivePanelState, present_visual, audio queue hoặc animation frontend.
- Không thêm domain capability cụ thể cho Education; hiện domain này chưa có tool.
- Không thêm `describe_templates`.
- Không thay đổi nội dung prompt trình bày của các domain.
