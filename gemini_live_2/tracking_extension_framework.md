# Tracking — Extension Framework: Widget và Animation

## Trạng thái

- Trạng thái: **EF1–EF9 đã hoàn thành; đang chờ chốt để bắt đầu EF10**.
- Phạm vi: biến widget và animation/effect thành extension có thể cài thêm mà người mở rộng không cần hiểu Gemini Live, Plan Agent, WebSocket, Compiler hay Runtime nội bộ.
- Không thay đổi luồng `SurfacePlan -> Compiler -> SurfaceDocument -> Browser` đã có. Extension chỉ được cắm vào các contract công khai của luồng này.

---

## 1. Vấn đề hiện tại

Domain hiện tương đối dễ thêm vì chủ yếu là dữ liệu và hướng dẫn:

```text
manifest + prompt + assets + templates
```

Trong khi đó, thêm widget hoặc effect vẫn phải sửa nhiều nơi chung:

```text
Widget:
  backend contract/registry
  compiler validation
  Stage Map policy
  frontend renderer
  frontend CSS/registry

Effect:
  frontend handler
  frontend effect registry
  allowed effects trong widget/anchor policy
```

Vì các điểm mở rộng chưa được đóng gói thành contract/package, người thêm widget phải hiểu luồng nội bộ và sửa registry hard-code. Đây là điều framework cần loại bỏ.

---

## 2. Mục tiêu kiến trúc

```text
                         Lumi Core
 ┌──────────────────────────────────────────────────────────┐
 │ Gemini Live | Plan Agent | Compiler | Runtime | WebSocket │
 │ ExtensionLoader | WidgetRegistry | EffectRegistry         │
 └──────────────────────────────────────────────────────────┘
                    │                         │
          load/validate                 load/validate
                    │                         │
                    v                         v
       WidgetExtension package       EffectExtension package
```

Tiêu chí chính:

> Người khác thêm một widget hoặc effect bằng package mới; không sửa `PlanAgentService`, `LiveSessionOrchestrator`, `PanelCompiler`, `web/app.js` hay danh sách registry hard-code.

Core vẫn sở hữu điều phối, validation, revision, WebSocket và giao tiếp Gemini. Extension chỉ mô tả/triển khai mảnh ghép UI của mình.

---

## 3. Contract công khai cần chốt

### 3.1. `WidgetExtension`

Mỗi widget phải khai báo:

 - metadata; `id` chỉ nằm trong `manifest.json`;
- schema `props` và `children`;
- schema/default `state`;
- `actions` được phép;
- generic state transition cho action nào runtime được phép tự áp dụng;
- anchor policy và effect IDs được phép trên từng anchor;
- Stage Map policy;
- frontend renderer và stylesheet.

Frontend renderer chỉ nhận component đã được Compiler validate:

```text
{ id, type, layout, props, state, children } + anchors + surface_id
```

Nếu widget phát thao tác, renderer chỉ phát event chuẩn:

```text
{ surface_id, anchor_id, action }
```

Renderer không tự chấm đúng/sai, không tự sửa SurfaceDocument và không gửi nội dung/tuyên bố nghiệp vụ từ browser.

### 3.2. `EffectExtension`

Mỗi effect phải khai báo:

- `id` và metadata;
- frontend handler;
- cleanup contract để Runtime browser hủy effect khi turn bị ngắt hoặc panel đổi revision.

Handler chỉ nhận target DOM đã được runtime xác minh:

```text
{ target, overlay, rect, command }
```

và trả về hàm cleanup nếu đã tạo DOM/timer/animation.

### 3.3. Quan hệ widget — effect

Effect được cài không đồng nghĩa Gemini có thể dùng trên mọi widget:

```text
EffectRegistry: effect nào tồn tại.
Widget anchor policy: effect nào được phép trên vùng đó.
Runtime: xác minh anchor + effect trước khi Browser chạy.
```

---

## 4. Cấu trúc package đích

```text
extensions/
  widgets/
    <widget_id>/
      manifest.json
      contract.py
      renderer.js
      styles.css                 # bắt buộc
      README.md                 # khuyến nghị
      tests/                    # khuyến nghị

  effects/
    <effect_id>/
      manifest.json
      effect.js
      styles.css                 # chỉ khi cần
      README.md                 # khuyến nghị
      tests/                    # khuyến nghị
```

Ví dụ package widget:

```text
extensions/widgets/timeline/
  manifest.json
  contract.py
  renderer.js
  styles.css
```

Ví dụ package effect:

```text
extensions/effects/spotlight/
  manifest.json
  effect.js
```

`manifest.json` là entry point duy nhất mà `ExtensionLoader` cần biết. Không để extension sửa source core hay tự import vào registry trung tâm.

**Chính sách phiên bản:** giai đoạn này không có trường `version` và không hỗ trợ nhiều
phiên bản của cùng một extension. Mỗi `id` tương ứng đúng một package local đang được
nạp khi ứng dụng khởi động. Khi tác giả sửa widget/effect, bản mới ghi đè bản cũ; cần
khởi động lại app và tạo Surface mới để dùng contract/renderer mới.

**Chính sách stylesheet:** mọi widget bắt buộc có `styles.css` và manifest phải khai
báo file này. Widget không được dựa vào CSS riêng viết trong core; nhờ vậy package tự
đủ để render. Effect chỉ khai báo `styles.css` khi handler cần class, keyframe hoặc
style riêng.

**Schema manifest bắt buộc:** Loader chỉ chấp nhận JSON object với đúng các trường sau
(không cho field runtime không được định nghĩa). Mọi đường dẫn là tương đối với thư mục
package và phải trỏ tới file thật trong chính package.

```json
// extensions/widgets/<widget_id>/manifest.json
{
  "id": "flashcard",
  "contract": "contract.py",
  "renderer": "renderer.js",
  "styles": "styles.css"
}
```

Toàn bộ bốn trường widget đều bắt buộc. `contract.py` export `WIDGET_EXTENSION` là
`WidgetDefinition` chưa gắn ID; renderer
và stylesheet được Browser nạp từ catalog mà Loader công bố.

```json
// extensions/effects/<effect_id>/manifest.json
{
  "id": "circle",
  "handler": "effect.js",
  "styles": "styles.css",
  "description": "Khoanh một vùng cụ thể trên panel.",
  "usage_guidance": "Dùng khi chỉ chính xác một đối tượng, đáp án hoặc vùng cần trẻ quan sát."
}
```

Effect bắt buộc `id`, `handler`, `description`, `usage_guidance`; `styles` là tùy chọn.
`README.md` và `tests/` là phần khuyến nghị của author kit/package layout; chúng không
phải runtime entry point, không nằm trong manifest và Loader không yêu cầu.

### 4.1. API cụ thể của package widget

`contract.py` phải export đúng một đối tượng `WIDGET_EXTENSION` kiểu `WidgetDefinition`
chưa gắn ID. Đây là nguồn sự thật
backend của widget; không chứa DOM, CSS, WebSocket, Gemini prompt hay logic nghiệp vụ
toàn cục.

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
  declared_effect_ids: list[str]  # dependency check khi startup

  discovery_summary() -> WidgetDiscoverySummary
  public_contract() -> WidgetPublicContract
```

`manifest.json` là nguồn duy nhất của `id`. `contract.py` không lặp lại ID; khi nạp
package, `ExtensionLoader` gọi `WIDGET_EXTENSION.bind_id(id)` rồi đăng ký bản đã gắn ID
vào `WidgetRegistry`. Nhờ vậy Plan Agent, Compiler và Browser vẫn dùng cùng một
`widget_id`, nhưng tác giả extension chỉ khai báo nó một lần.

`id` phải khớp `^[a-z]+(?:_[a-z]+)*$`: chỉ gồm các từ chữ thường ngăn cách bởi một dấu
`_`, ví dụ `flashcard`, `number_display`, `object_group`. Không dùng số, chữ hoa, dấu
gạch ngang, khoảng trắng hay ký tự tiếng Việt. Quy tắc này áp dụng giống nhau cho widget
và effect.

`discovery_summary()` là dữ liệu ngắn, được `WidgetRegistry.widget_index()` đưa vào
payload/prompt ban đầu của Plan Agent. Nó chỉ gồm:

```json
{
  "id": "flashcard",
  "purpose": "Thẻ lật hai mặt để học hoặc ôn tập.",
  "mechanics": ["flip", "self_reveal"],
  "interaction_actions": ["flip"],
  "allows_children": false
}
```

Mục đích: Agent biết widget nào phù hợp với activity, nhưng chưa nhận schema dài.
`renderer.js`, `styles.css`, đường dẫn file và chi tiết DOM tuyệt đối không xuất hiện
trong prompt này.

`public_contract()` là backend contract đầy đủ của WidgetDefinition. Nó được
Compiler và Stage Map dùng nội bộ; không được gửi nguyên vẹn cho Plan Agent.

Sau khi Agent chủ động chọn widget, `describe_widgets({"widget_ids":[...]})`
trả **planning contract**. Nó gồm:

```text
id, purpose
props
initial_state: default + state fields hợp lệ
allowed_child_widget_ids (nếu widget là container)
interactions: action, mô tả, generic state_rule (nếu có)
```

`stage_map_policy`, `asset_references`, anchor policy, effect policy và renderer
không nằm trong planning contract. Chúng vẫn ở backend contract để Compiler xác
minh props/asset, sinh anchor và dựng VISUAL STAGE MAP sau khi Surface Plan hợp lệ.

`WidgetExtension` không được tự quyết định đúng/sai, tự thay SurfaceDocument, hay gửi
event thẳng tới Gemini. `actions` chỉ mô tả thao tác UI có thể xảy ra; Runtime xác minh
event, còn Gemini Live quyết định ngữ nghĩa của thao tác.

`declared_effect_ids` chỉ để `ExtensionLoader` kiểm tra dependency một lần khi startup.
Nó không cấp quyền effect trên anchor; quyền cuối vẫn nằm ở `allowed_effect_ids` của
anchor được `anchor_policy` tạo khi có props thật.

`renderer.js` của mọi widget phải có named export chuẩn sau:

```javascript
export function render(component, context) {
  // context = {
  //   anchorsByKey,
  //   renderChild,
  //   emitInteraction({ anchor_id, action })
  // }
  // return HTMLElement
}
```

Renderer chỉ dựng DOM từ `component` đã compile. Khi trẻ thao tác, renderer gọi
`context.emitInteraction(...)`; core tự thêm `surface_id` và revision, rồi chuyển event
qua WebSocket. Tác giả extension không biết/không được dùng tên event nội bộ
`panel:interaction`, `app.js`, WebSocket hay Gemini.

### 4.2. API cụ thể của package effect

`effect.js` phải export một `EffectExtension` frontend. Effect không có quyền tự chọn
target; target luôn do Runtime browser tìm từ `anchor_id` đã được backend xác minh.

```text
EffectExtension
  run(context, command) -> cleanup | void

context = { target, overlay, rect }
command = { anchor_id, effect_id, panel_id, panel_revision, turn_id, ... }
cleanup = () => void
```

`effect.js` có named export chuẩn:

```javascript
export function run(context, command) {
  // context = { target, overlay, rect }
  // return cleanup function, hoặc không trả gì nếu không có tài nguyên cần dọn
}
```

`AnimationController` của core sở hữu lifecycle: trước một effect mới, khi lượt bị
ngắt hoặc khi panel đổi revision, core gọi cleanup của effect cũ nếu có. `run()` không
biết và không tự dọn effect package khác.

`manifest.json` của effect là nguồn metadata mà backend có thể công bố cho Gemini Live:

```json
{
  "id": "circle",
  "handler": "effect.js",
  "description": "Khoanh một vùng cụ thể trên panel.",
  "usage_guidance": "Dùng khi đang chỉ chính xác một đối tượng, đáp án hoặc vùng cần trẻ quan sát."
}
```

Không có trường `kind`: `ExtensionLoader` quét riêng `extensions/widgets/` và
`extensions/effects/`, nên chính thư mục package xác định loại extension.

Khi Runtime dựng Presentation Context/Stage Map, chỉ các effect đồng thời (1) đã được
`EffectRegistry` nạp và (2) được `anchor_policy` của widget cho phép mới được gửi cho
Gemini. Mỗi effect hợp lệ phải kèm `id`, `description`, `usage_guidance`. Prompt chung
chỉ giữ luật gọi `present_visual`; không hard-code ý nghĩa của `circle`, `highlight`,
hay effect cụ thể khác.

Không có trường `anchor_kinds` ở giai đoạn này. Khi gọi `present_visual`, Runtime đã
xác minh `anchor_id` có trên SurfaceDocument và effect có trong `allowed_effect_ids`
của chính anchor đó; Browser tìm DOM target bằng `data-anchor-id`. Vì vậy effect chung
như `circle` có thể khoanh bất kỳ vùng nào mà widget đã cấp phép. Chỉ khi sau này có
effect thật sự đòi một cấu trúc DOM đặc biệt mới cần thiết kế compatibility contract mới.

### 4.3. Vai trò từng file trong package

| File | Được ai dùng | Trách nhiệm | Không được làm |
|---|---|---|---|
| `manifest.json` | ExtensionLoader, registry | Nhận diện package, ID, entry point, metadata | Chứa logic chạy hay secrets |
| `contract.py` | ExtensionLoader, WidgetRegistry, Compiler, Plan Agent tool | Khai báo backend contract; Widget Registry rút discovery summary và planning contract cho Agent | Render DOM hoặc điều phối session |
| `renderer.js` | Browser renderer | Dựng DOM từ component đã compile; phát event chuẩn | Tự sửa panel/chấm đúng sai/gọi Gemini |
| `styles.css` | Browser | Style cục bộ của widget/effect | Ghi đè global style không thuộc package |
| `effect.js` | AnimationController | Chạy effect trên target đã xác minh, trả cleanup | Tự chọn anchor hay vượt policy |
| `README.md` | Tác giả extension | Hướng dẫn cài, contract, ví dụ | Là nguồn runtime |
| `tests/` | CI/author | Contract, render, event, cleanup | Thay validation runtime |

### 4.4. Luồng dữ liệu widget cho Plan Agent

```text
manifest.json + contract.py
  -> ExtensionLoader validate và nạp WidgetExtension
  -> WidgetRegistry
       -> widget_index(): discovery_summary của mọi widget được phép
          -> SurfacePlanPromptBuilder -> context ban đầu của Plan Agent
       -> get(widget_id).public_contract()  # backend contract
          -> DescribeWidgetsTool rút planning contract
          -> function response sau tool call
```

Không có backend contract nào được nhét trước vào prompt chỉ để “phòng khi cần”. Agent chỉ
nhận planning contract của widget mà nó gọi `describe_widgets`; Compiler vẫn là nơi dùng
backend contract để xác minh Surface Plan.

### 4.5. Catalog extension cho Browser

`ExtensionLoader` xác minh package ở backend rồi công bố một `Extension Catalog` chỉ
gồm URL module/style an toàn của package đã nạp. Browser nhận catalog **một lần** khi
mở app/kết nối WebSocket, dynamic-import `renderer.js`/`effect.js` và nạp stylesheet
tương ứng trước khi nhận panel đầu tiên.

```text
ExtensionLoader -> Extension Catalog -> Browser registry (một lần)
SurfaceDocument -> { component.type: "flashcard", props, state, ... } (mỗi panel)
```

`SurfaceDocument` tuyệt đối không mang lại source JavaScript, CSS, manifest hay URL
module của extension. Khi thấy `component.type`, Browser dùng renderer đã nạp sẵn. Sửa
package yêu cầu khởi động lại app theo chính sách extension local một phiên bản.

---

## 5. Trách nhiệm của ExtensionLoader

`ExtensionLoader` là lớp core mới, chịu trách nhiệm:

1. Quét package trong `extensions/widgets/` và `extensions/effects/`.
2. Validate manifest, ID, entry point và dependency.
3. Nạp backend widget contract vào `WidgetRegistry`.
4. Nạp metadata effect vào `EffectRegistry`.
5. Công bố renderer/effect/style hợp lệ cho Browser.
6. Chặn lỗi ngay lúc startup hoặc validate, ví dụ:
   - trùng `widget_id` / `effect_id`;
   - thiếu renderer hoặc stylesheet bắt buộc;
   - action được khai báo nhưng renderer không khai báo hoặc không phát action tương ứng;
   - widget cho phép một effect chưa tồn tại;
   - state/anchor policy không hợp lệ.

`ExtensionLoader` không quyết định nghiệp vụ, không compile panel và không nhận event trực tiếp từ browser.

---

## 6. Luồng runtime sau refactor

```text
ExtensionLoader
  -> nạp WidgetExtension + EffectExtension vào các registry

Plan Agent
  -> chỉ thấy public widget/effect contracts từ registry
  -> trả SurfacePlan dùng widget đã đăng ký

Compiler
  -> validate props/state/actions/anchor/effect theo extension contract
  -> materialize SurfaceDocument

Browser
  -> dùng renderer đã đăng ký để dựng DOM
  -> action phát event chuẩn {surface_id, revision, anchor_id, action}

Runtime
  -> validate revision/anchor/action
  -> áp dụng generic state rule nếu widget khai báo
  -> gửi event đáng tin cậy cho Gemini Live

Gemini Live
  -> quyết định ngữ nghĩa, lời nói, present_visual hoặc update_surface_state

Effect handler
  -> chỉ chạy khi Runtime đã xác minh anchor/effect
```

---

## 7. Checkpoint triển khai

### EF1 — Chốt API extension bằng tài liệu

- [x] Tạo `docs/EXTENSION_API.md`.
- [x] Định nghĩa schema `WidgetExtension`, `EffectExtension`, manifest và chính sách ghi đè local không versioning.
- [x] Chốt interface renderer JS, effect handler JS, cleanup và event chuẩn.
- [x] Chốt cách Browser nhận asset/style/module extension.

**Hoàn thành:** người khác đọc tài liệu có thể biết chính xác cần tạo file gì mà chưa cần mở source runtime.

### EF2 — Xây `ExtensionLoader` và manifest validator

- [x] Tạo loader quét package widget/effect.
- [x] Validate ID, trùng ID, entry point, named export và dependency.
- [x] Loader xuất `WidgetRegistry`, `EffectRegistry` và asset/module catalog cho frontend.
- [x] Viết test lỗi manifest/contract.

**Hoàn thành:** package sai bị chặn rõ ràng trước lúc nhận request người dùng.

### EF3 — Registry không còn hard-code

- [x] Refactor backend `WidgetRegistry` nhận extension đã nạp.
- [x] Refactor frontend widget/effect registry nhận catalog từ loader.
- [x] Không sửa `app.js` khi thêm widget/effect mới.
- [x] Giữ adapter tương thích cho widget/effect lõi trong thời gian chuyển đổi.

**Hoàn thành:** registry trung tâm không còn danh sách import phải sửa mỗi lần thêm
extension. Catalog được gửi trước `live:session_ready`; Browser nạp `render`/`run` và CSS
một lần. Core adapter là lớp chuyển tiếp duy nhất còn liệt kê widget/effect lõi; nó chỉ được
xóa hoàn toàn sau các checkpoint chuyển đổi EF6–EF8 dưới đây.

### EF4 — Tách một widget chuẩn: `flashcard`

- [x] Đưa contract backend, Stage Map policy, renderer và CSS của `flashcard` vào một package.
- [x] Giữ `flip`, `flipped`, image source và anchor contract đang có.
- [x] Kiểm thử render, flip, revision, interaction event và Stage Map.

**Hoàn thành:** `extensions/widgets/flashcard/` là nơi sở hữu duy nhất contract, renderer
và stylesheet. Entry core adapter, core registration và CSS flashcard toàn cục đã được xóa.

### EF5 — Tách một effect chuẩn: `circle`

- [x] Đưa handler frontend, metadata và CSS effect vào `extensions/effects/circle/`.
- [x] Kiểm thử handler export `run`, cleanup gỡ overlay; Runtime vẫn xác minh anchor/effect policy trước khi chạy.

**Hoàn thành:** `present_visual` chạy `circle` từ effect package; core adapter không còn metadata,
handler hay CSS riêng của `circle`.

### EF6 — Tách toàn bộ effect còn lại

- [x] Chuyển `highlight`, `pulse`, `draw_arrow`, `trace_line` thành
  package trong `extensions/effects/<effect_id>/`.
- [x] Mỗi package sở hữu manifest, `run`, metadata ngữ nghĩa và stylesheet khi cần; README và tests được khuyến nghị.
- [x] Chuyển hoặc thay thế mọi CSS/keyframe/hàm phụ trợ mà handler cần; chỉ giữ trong core các style
  hạ tầng chung của overlay, không giữ style đặc thù của một effect package.
- [x] Xóa từng metadata entry, adapter handler và legacy effect source tương ứng khỏi core sau khi
  package đã qua kiểm thử.

**Hoàn thành:** `EffectRegistry` và Browser catalog chỉ nhận 5 effect từ
`extensions/effects/`. `web/core_extensions/effects/`, `_CORE_EFFECTS`, legacy handler/helper
và CSS/keyframe riêng của effect đã được xóa. Core chỉ còn overlay hạ tầng và
`AnimationController` quản lý lifecycle.

### EF7 — Tách toàn bộ widget còn lại

- [x] Chuyển `text`, `image`, `object_group`, `answer`, `number_display`, `choice` thành package
  trong `extensions/widgets/<widget_id>/`.
- [x] Mỗi package là nơi sở hữu duy nhất contract backend, validation props/state/actions/anchor policy,
  renderer và stylesheet; README và tests được khuyến nghị.
- [x] Giữ nguyên public contract, Stage Map và hành vi tương tác đang hợp lệ trước khi xóa mã cũ.
- [x] Xóa từng `widgets/<widget_id>.py`, core renderer adapter, CSS widget toàn cục và entry registry
  tương ứng sau khi package thay thế đã qua kiểm thử.

**Hoàn thành khi:** `WidgetRegistry` và Browser catalog chỉ nhận toàn bộ 7 widget từ
`extensions/widgets/`; không còn `_CORE_WIDGET_IDS`, `web/core_extensions/widgets/` hay đăng ký
widget lõi hard-code.

**Hoàn thành:** toàn bộ 7 widget được Loader nạp từ `extensions/widgets/`; `web_app.py` dùng trực tiếp
registry/catalog đó. Sáu contract/renderer legacy, `web/core_extensions/widgets/`, `_CORE_WIDGET_IDS`
và stylesheet widget dùng chung đã được xóa. Core chỉ còn `web/surface_document.css` cho layout,
design tokens và trạng thái Surface trung lập, không thuộc widget cụ thể.

### EF8 — Xóa lớp tương thích và mã legacy còn lại

- [x] Bỏ `core_extensions.py` và các import/composition chỉ phục vụ adapter chuyển tiếp.
- [x] `web_app.py` dùng trực tiếp registry/catalog từ `ExtensionLoader`; không merge core catalog.
- [x] Kiểm kê và xóa source/CSS/registry legacy đã không còn owner; giữ lại riêng hạ tầng trung lập:
  `PanelCompiler`, `AnimationController`, loader, registry động và style layout/overlay dùng chung.
- [x] Thêm kiểm thử startup khẳng định package thiếu hoặc trùng ID bị chặn, không có fallback lõi ẩn.

**Hoàn thành:** `core_extensions.py` đã bị xóa. `web_app.py` chỉ lấy `widget_registry`,
`effect_registry` và Browser catalog từ một lần `ExtensionLoader(...).load()`. Không còn package
hoặc registry lõi dự phòng: widget/effect thiếu bị Loader chặn trước runtime; ID manifest phải trùng
ID thư mục nên không thể cài hai package cùng một ID. Test startup xác nhận registry và catalog của
dự án chỉ gồm đúng các package trong `extensions/`.

### EF9 — Integration capability matrix

- [x] Tạo test nối ba phía: backend contract, browser renderer, runtime event router.
- [x] Một `(widget_id, action)` chỉ được công bố là supported khi cả ba phía pass.
- [x] Chặn UI tương tác giả ngay từ Compiler/loader.

**Hoàn thành:** mọi `renderer.js` export `interactionActions`. Loader đọc tĩnh danh sách này,
so khớp chính xác với `WidgetDefinition.interactions`, đồng thời kiểm tra từng action có lời gọi
`emitInteraction` tương ứng và Runtime có thể áp dụng state rule từ state mặc định. Catalog mang
`interaction_actions`; Browser kiểm lại export của module sau dynamic-import. Vì vậy action không
có renderer, renderer khai báo sai, renderer không phát event, hoặc state rule không chạy đều chặn
app ngay ở startup, trước khi Plan Agent có thể dùng widget đó.

### EF10 — Author kit

- [x] Viết `docs/CREATE_WIDGET.md` và `docs/CREATE_EFFECT.md`.
- [x] Thêm package ví dụ `timeline` và `spotlight`.
- [x] Tạo scaffold/CLI sau khi contract ổn định: `create-widget`, `create-effect`, `validate`.

**Hoàn thành:** `timeline` là widget mẫu có contract, anchor/Stage Map, renderer và CSS; `spotlight`
là effect mẫu có metadata semantic, SVG overlay và cleanup. `python -m gemini_live_2.extension_tools`
scaffold package mới mà không ghi đè package cũ; `validate` gọi `ExtensionLoader` để kiểm tra toàn bộ
package đang cài. `docs/CREATE_WIDGET.md` và `docs/CREATE_EFFECT.md` ghi rõ quy trình mà không cần sửa core.

---

## 8. Ngoài phạm vi giai đoạn này

- Cho extension chạy Python/JavaScript tùy ý không sandbox.
- Marketplace hoặc cài extension từ Internet.
- Cho extension thay prompt lõi, quyền gọi Gemini Live tool hoặc quyền truy cập secrets.
- Tự động chấm đúng/sai hay tự sinh state machine nghiệp vụ cho mọi widget.

Những quyền này cần thiết kế bảo mật/permission riêng, không gộp vào extension renderer/effect ban đầu.
