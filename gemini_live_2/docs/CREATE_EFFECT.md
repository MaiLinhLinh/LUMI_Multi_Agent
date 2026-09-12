# Tạo effect extension

Effect do Gemini Live chọn, nhưng chỉ chạy khi Runtime đã xác minh `anchor_id` và effect được `allowed_effect_ids` của anchor cho phép. Effect không tự tìm target, sửa state hay gọi Gemini.

## 1. Tạo khung package

```powershell
python -m gemini_live_2.extension_tools create-effect spotlight
```

Lệnh không ghi đè package đã có và tạo `manifest.json`, `effect.js`, `styles.css`, `README.md`, `tests/`. CSS là tuỳ chọn trong API; scaffold tạo sẵn vì phần lớn effect cần style. Nếu không dùng, xoá trường `styles` khỏi manifest.

## 2. Manifest và ngữ nghĩa cho Gemini

```json
{
  "id": "spotlight",
  "handler": "effect.js",
  "styles": "styles.css",
  "description": "Làm tối vùng xung quanh để làm nổi bật một anchor.",
  "usage_guidance": "Dùng khi cần hướng ánh nhìn vào một vùng cụ thể giữa nhiều nội dung."
}
```

`description` nói effect tạo ra gì; `usage_guidance` nói Gemini nên dùng khi nào. Không có `kind` hay `anchor_kinds`.

## 3. Handler và lifecycle

```javascript
export function run(context, command) {
  // context = { target, overlay, rect }
  context.target.classList.add("lumi-effect-example");
  return () => context.target.classList.remove("lumi-effect-example");
}
```

`target` và `rect` đã được Runtime xác minh. `overlay` là SVG presentation overlay nếu cần vẽ bên ngoài target. Nếu thêm class, SVG node, timer hoặc listener, phải trả cleanup. `AnimationController` gọi cleanup trước effect mới, khi lượt bị ngắt hoặc panel đổi revision.

## 4. Cho widget dùng effect

Widget cần khai báo effect ở cả dependency và anchor policy:

```python
declared_effect_ids=("spotlight",)
anchor_policy=lambda props: (WidgetAnchor("root", ("spotlight",)),)
```

Trường đầu chỉ chặn dependency thiếu lúc startup; danh sách trong `WidgetAnchor` là quyền thực tế khi Runtime xử lý `present_visual`.

## 5. Test và validation

Test geometry target, DOM/CSS effect và cleanup, rồi chạy:

```powershell
python -m gemini_live_2.extension_tools validate
```

`extensions/effects/spotlight/` là ví dụ đầy đủ: nó làm tối vùng ngoài target bằng SVG path và cleanup gỡ path đó.
