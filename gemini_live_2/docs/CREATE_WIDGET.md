# Tạo widget extension

Tài liệu này dành cho người thêm widget mà không sửa `PanelCompiler`, Runtime, `web/app.js`, registry hay prompt lõi. Đọc `EXTENSION_API.md` trước khi bắt đầu.

## 1. Tạo khung package

Từ thư mục cha của `gemini_live_2`, chạy:

```powershell
python -m gemini_live_2.extension_tools create-widget timeline
```

ID phải là chữ thường và dấu gạch dưới, ví dụ `word_match`, không có số, khoảng trắng hoặc dấu gạch ngang. Lệnh từ chối ghi đè package đã tồn tại.

```text
extensions/widgets/timeline/
  manifest.json
  contract.py
  renderer.js
  styles.css
  README.md
  tests/
```

## 2. Manifest

Giữ `id` đúng bằng tên thư mục. Bốn trường đều bắt buộc:

```json
{
  "id": "timeline",
  "contract": "contract.py",
  "renderer": "renderer.js",
  "styles": "styles.css"
}
```

Các path chỉ được trỏ tới file trong chính package. `styles.css` phải là UTF-8 không rỗng.

## 3. Contract backend

`contract.py` export `WIDGET_EXTENSION`, là `WidgetDefinition` **chưa gắn ID**. Loader tự `.bind_id()` từ manifest.

Contract phải khai báo `purpose`, `validate_props`, `props`, `state_fields`, `interactions`, `anchor_policy`, `stage_map_policy` và `declared_effect_ids` khi widget cần effect. `declared_effect_ids` chỉ kiểm tra dependency lúc startup; `WidgetAnchor.allowed_effect_ids` mới là quyền effect thật trên từng anchor.

Nếu widget có danh sách động, sinh một anchor cho mỗi item từ props, gắn `data-anchor-id` lên chính DOM item
và dùng `StageMapPolicy.collection_source`, `collection_anchor_prefix`, `collection_text_sources` để Gemini
nhìn thấy từng item cùng anchor của nó.

```python
_VISIBILITY = WidgetStateDefinition(
    name="visibility", value_type="string", default_value="visible",
    allowed_values=("visible", "hidden"),
    transitions={"visible": ("hidden",), "hidden": ("visible",)},
)
```

Không đặt `widget_id` trong contract. Không render HTML, gọi WebSocket, chấm đúng/sai hay điều phối Gemini trong file này.

## 4. Renderer browser

`renderer.js` luôn có hai named export:

```javascript
export const interactionActions = ["flip"];

export function render(component, context) {
  const element = document.createElement("section");
  element.dataset.anchorId = context.anchorsByKey.card.anchor_id;
  element.addEventListener("click", () => {
    context.emitInteraction({ anchor_id: context.anchorsByKey.card.anchor_id, action: "flip" });
  });
  return element;
}
```

`interactionActions` phải đúng thứ tự action trong `WidgetDefinition.interactions`; mỗi action phải có lời gọi literal `emitInteraction({ action: "..." })`. Widget không tương tác dùng `[]`. Core tự thêm `surface_id`, revision và gửi WebSocket; renderer không dùng `panel:interaction`, `app.js`, WebSocket hay Gemini trực tiếp.

## 5. CSS, test và validation

Đặt CSS cục bộ trong `styles.css`, dùng prefix class riêng. Viết test props, state, anchor/effect, DOM render và action. Rồi chạy:

```powershell
python -m gemini_live_2.extension_tools validate
```

Lệnh nạp toàn bộ package qua `ExtensionLoader`: manifest, ID/thư mục, contract, renderer action, stylesheet và dependency effect đều bị kiểm tra trước request. `extensions/widgets/timeline/` là package mẫu hoàn chỉnh.
