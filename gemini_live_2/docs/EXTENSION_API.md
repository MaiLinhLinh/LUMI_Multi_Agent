# Lumi Extension API

Tài liệu này là contract để thêm widget hoặc effect vào Lumi mà không sửa `PlanAgentService`, `LiveSessionOrchestrator`, `PanelCompiler`, `web/app.js` hay các registry hard-code. Core vẫn sở hữu validation, revision, WebSocket và Gemini Live.

## 1. Phạm vi và chính sách package

Mỗi extension là một package local. Không quản lý nhiều phiên bản: tác giả sửa package cùng ID để ghi đè bản đang có, sau đó khởi động lại app và tạo Surface mới.

ID được khai báo **một lần** trong manifest và phải khớp:

```text
^[a-z]+(?:_[a-z]+)*$
```

Ví dụ hợp lệ: `flashcard`, `number_display`, `object_group`. Không dùng số, chữ hoa, dấu gạch ngang, khoảng trắng hay ký tự tiếng Việt.

```text
extensions/
  widgets/
    <widget_id>/
      manifest.json
      contract.py
      renderer.js
      styles.css
      README.md       # khuyến nghị
      tests/          # khuyến nghị
  effects/
    <effect_id>/
      manifest.json
      effect.js
      styles.css       # chỉ khi cần
      README.md       # khuyến nghị
      tests/          # khuyến nghị
```

Thư mục xác định loại extension; manifest không có trường `kind`. `README.md` và `tests/` là khuyến nghị cho tác giả extension, không phải runtime entry point và Loader không yêu cầu chúng.

## 2. Widget package

### Manifest

`extensions/widgets/<widget_id>/manifest.json` phải có đúng bốn trường:

```json
{
  "id": "flashcard",
  "contract": "contract.py",
  "renderer": "renderer.js",
  "styles": "styles.css"
}
```

Mọi path là tương đối với thư mục package và phải trỏ tới file trong chính package. Widget luôn có stylesheet riêng.

### Backend contract

`contract.py` export đúng một đối tượng `WIDGET_EXTENSION`, là `WidgetDefinition` chưa gắn ID. Nó không lặp lại ID từ manifest và không chứa DOM, CSS, WebSocket, prompt Gemini hay nghiệp vụ toàn cục.

```text
WidgetDefinition (unbound)
  purpose: str
  mechanics: list[str]
  props_schema: object
  children_schema: object | null
  state_schema: object
  actions: list[WidgetAction]
  anchor_policy: object
  stage_map_policy: object
  asset_policy: object
  discovery_summary() -> WidgetDiscoverySummary
  public_contract() -> WidgetPublicContract
```

`ExtensionLoader` gọi `WIDGET_EXTENSION.bind_id(manifest.id)` rồi đăng ký `WidgetDefinition` đã gắn ID vào `WidgetRegistry`.

`discovery_summary()` là phần ngắn đưa vào context đầu của Plan Agent:

```json
{
  "id": "flashcard",
  "purpose": "Thẻ lật hai mặt để học hoặc ôn tập.",
  "mechanics": ["flip", "self_reveal"],
  "interaction_actions": ["flip"],
  "allows_children": false
}
```

`WidgetDefinition.public_contract()` là **backend contract** đầy đủ: ngoài schema lập kế hoạch, nó còn có `stage_map_policy` và `asset_references` để Compiler/Stage Map xác minh và mô tả SurfaceDocument.

Plan Agent gọi `describe_widgets({"widget_ids":[...]})` để nhận **planning contract**, không phải toàn bộ backend contract. Planning contract chỉ gồm:

- `props`;
- `initial_state` (default và các field state hợp lệ);
- `allowed_child_widget_ids`, nếu widget là container;
- `interactions`.

Agent không cần nhận `stage_map_policy`, `asset_references`, anchor policy hay chi tiết renderer: các phần này chỉ do Compiler/Runtime dùng sau khi Plan Agent đã trả Surface Plan.

`actions` mô tả thao tác UI có thể xảy ra, không tự chấm đúng/sai hoặc tự đổi panel. Runtime xác minh event; Gemini Live quyết định ý nghĩa nghiệp vụ.

`anchor_policy` chỉ sinh các target anchor mà widget thực sự render. Effect không phải quyền của widget hay anchor: mọi effect đã được `EffectRegistry` nạp đều có thể chạy trên mọi anchor hiện có.

Với widget render danh sách động, `anchor_policy(props)` có thể trả một anchor cho từng phần tử, ví dụ
`milestone_1`, `milestone_2`, … . Renderer phải gắn `data-anchor-id` lên đúng DOM item tương ứng.
`StageMapPolicy` có `collection_source`, `collection_anchor_prefix` và `collection_text_sources` để Stage Map
mô tả dữ liệu hiển thị của từng item ngay cạnh anchor đó. Đây là contract tổng quát cho danh sách, không phải
quy tắc riêng của `timeline`.

### Frontend renderer

`renderer.js` có named export chuẩn:

```javascript
export function render(component, context) {
  // context = { anchorsByKey, renderChild,
  //             emitInteraction({ anchor_id, action }) }
  // return HTMLElement
}
```

Renderer chỉ dựng DOM từ component đã compile. Khi người dùng thao tác, gọi `context.emitInteraction(...)`. Core tự thêm `surface_id` và revision rồi gửi WebSocket. Renderer không biết `app.js`, `panel:interaction`, WebSocket hay Gemini.

Mỗi renderer cũng bắt buộc export khai báo tĩnh `interactionActions`. Danh sách này phải bằng đúng
`actions` trong backend contract, và mỗi action phải xuất hiện trong một lời gọi
`emitInteraction({ anchor_id, action: "..." })` của renderer. Loader kiểm tra ba điểm đó lúc startup;
Browser kiểm lại `interactionActions` khi dynamic-import module. Widget không có tương tác export mảng rỗng:

```javascript
export const interactionActions = ["flip"];

export function render(component, context) {
  // Khi người dùng lật thẻ:
  context.emitInteraction({ anchor_id, action: "flip" });
  return HTMLElement;
}
```

## 3. Effect package

### Manifest

`extensions/effects/<effect_id>/manifest.json` có schema:

```json
{
  "id": "circle",
  "handler": "effect.js",
  "styles": "styles.css",
  "description": "Khoanh một vùng cụ thể trên panel.",
  "usage_guidance": "Dùng khi chỉ chính xác một đối tượng, đáp án hoặc vùng cần quan sát."
}
```

`id`, `handler`, `description`, `usage_guidance` bắt buộc; `styles` tùy chọn. Metadata `description` và `usage_guidance` được Runtime đưa vào Presentation Context để Gemini chọn effect theo ý nghĩa, không cần prompt hard-code `circle` hay `highlight`.

### Handler và lifecycle

`effect.js` có named export chuẩn:

```javascript
export function run(context, command) {
  // context = { target, overlay, rect }
  // return cleanup function, hoặc không trả gì
}
```

Target đã được core tìm từ anchor hợp lệ. `AnimationController` sở hữu lifecycle: trước effect mới, khi lượt bị ngắt, hoặc Surface đổi revision, core gọi cleanup của effect cũ nếu có. Effect không tự chọn target và không tự dọn effect khác.

Effect không có `anchor_kinds`, `supports` hay policy theo widget. Runtime chỉ chạy effect khi `anchor_id` thuộc SurfaceDocument hiện tại và `effect_id` tồn tại trong `EffectRegistry`. Vì effect là target-agnostic, Gemini có thể chọn bất kỳ effect đã cài cho bất kỳ anchor hiện có.

## 4. Nạp và phân phối extension

`ExtensionLoader` quét riêng hai thư mục package, validate manifest/ID/entry point, nạp widget contract vào `WidgetRegistry` và effect metadata vào `EffectRegistry`.

Sau validation, Loader công bố Extension Catalog chỉ gồm URL module/style an toàn. Browser nhận catalog một lần khi mở app hoặc kết nối WebSocket, dynamic-import renderer và effect handler, đồng thời nạp CSS trước Surface đầu tiên. CSS effect được nạp vào document cho overlay và vào ShadowRoot của panel cho target widget.

Mọi widget và effect đang cài đều là package thật với named export `render` hoặc `run`; không có
registry trung tâm chứa danh sách widget/effect hard-code. Core chỉ giữ hạ tầng trung lập như
Loader, Compiler, Runtime, `AnimationController`, cùng CSS layout/design token của Surface.

URL `/extensions/...` không phải static-directory công khai: backend chỉ phục vụ
đúng `renderer.js`, `effect.js` và stylesheet đã được Loader xác minh. Browser không thể
lấy `contract.py`, manifest, README hay file tùy ý từ thư mục extension.

```text
ExtensionLoader -> Extension Catalog -> Browser registry     (một lần)
SurfaceDocument -> { component.type, props, state, ... }     (mỗi panel)
```

`SurfaceDocument` không mang JavaScript, CSS, manifest hay module URL. Browser dùng renderer đã nạp sẵn theo `component.type`.

## 5. Trách nhiệm và giới hạn

| Thành phần | Được làm | Không được làm |
|---|---|---|
| `contract.py` | Khai báo contract backend | Render DOM, điều phối session |
| `renderer.js` | Dựng DOM, phát interaction chuẩn | Tự sửa panel/chấm đúng sai/gọi Gemini |
| `effect.js` | Chạy effect trên target đã xác minh | Tự chọn anchor/vượt policy |
| `manifest.json` | Khai báo ID, entry point, metadata | Chứa logic hoặc secrets |
| Core | Validate, revision, WebSocket, Gemini context | Ghi đè nội dung package |

Lỗi package như ID trùng, thiếu file bắt buộc, manifest sai, effect không tồn tại nhưng được widget cho phép, hoặc contract không hợp lệ phải bị `ExtensionLoader` chặn ở lúc startup/validation, trước request người dùng.

## 6. Luồng runtime

```text
Widget package -> ExtensionLoader -> WidgetRegistry -> Plan Agent
Effect package -> ExtensionLoader -> EffectRegistry -> Presentation Context -> Gemini Live

Plan Agent -> SurfacePlan -> Compiler -> SurfaceDocument -> Browser renderer
Browser action -> Runtime validation -> Gemini Live
Gemini present_visual -> Runtime anchor/effect validation -> AnimationController -> effect.run
```

Extension không thay đổi luồng `SurfacePlan -> Compiler -> SurfaceDocument -> Browser`.
