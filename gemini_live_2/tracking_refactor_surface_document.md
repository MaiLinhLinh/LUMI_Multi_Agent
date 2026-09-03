# Tracking refactor SurfaceDocument

## 0. Trạng thái

**Đã hoàn thành SD1.** Hệ thống hiện vẫn chạy `SurfaceStructure` +
`SurfaceState` + `PanelIR` + `materialize_panel_ir()`. File này là bản thiết kế
và tracking duy nhất của refactor; chỉ tick checkpoint sau khi hoàn thành và
kiểm tra.

---

## 1. Mục tiêu và kiến trúc đích

Thay các nguồn dữ liệu UI cũ bằng **một `SurfaceDocument` active duy nhất** cho
mỗi surface. Document là nguồn sự thật cho browser payload, `VISUAL STAGE MAP`,
anchor/effect validation, interaction validation, state, revision và lifecycle.

```text
Người dùng: voice / text / click / drag / drop
                    │
                    ▼
              Gemini Live
       lời nói + quyết định hành động UI
                    │
     ┌──────────────┼──────────────────────────┐
     │              │                          │
route_request  present_visual        update_surface_state / delete_surface
     │              │                          │
     ▼              ▼                          ▼
 Plan Agent      Runtime                      Runtime
     │              │                          │
     ▼              └──────────┬───────────────┘
Surface Plan proposal          │
     │                          ▼
     └──────────────►  SurfaceDocument active
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   Browser payload       VISUAL STAGE MAP      anchor/action validation
```

Sau migration hoàn tất sẽ không còn `SurfaceStructure`, `SurfaceState`,
`PanelIR`/`PanelBlock`, `BlockState`, hay `materialize_panel_ir()`. Không giữ
hai nguồn state song song; adapter tạm thời nếu có phải xóa ở SD10.

| Thành phần | Trách nhiệm | Không được làm |
|---|---|---|
| Gemini Live | Lời thoại; route, animation, state update, delete | Sửa DOM, cấp ID/anchor, tự xác minh state |
| Plan Agent | Proposal structure; gọi capability; chọn template chỉ khi khớp toàn bộ | Trả document tin cậy, tạo ID/anchor/revision |
| Compiler/Runtime | Validate, cấp ID/anchor, giữ document, mutation, redaction | Quyết định nội dung dạy/chấm đúng-sai ngữ nghĩa |
| Widget Registry | Contract props/state/action/anchor/Stage Map/children | Lưu state session |
| Browser | Render document redact, phát event tối thiểu, animation tạm | Gửi đúng-sai, props/state tự chế |

---

## 2. Contract `SurfaceDocument`

```json
{
  "surface_id": "panel-...",
  "domain_id": "education",
  "revision": 1,
  "components": [
    {
      "id": "2",
      "type": "flashcard",
      "layout": {"col": 4, "row": 2, "col_span": 9, "row_span": 7},
      "props": {
        "front": {"asset_id": "cat", "text": "Con mèo"},
        "back": {"word": "CAT", "phonetic": "/kæt/", "meaning": "con mèo"}
      },
      "state": {"visibility": "visible", "flipped": false},
      "children": []
    }
  ],
  "anchors": [
    {
      "anchor_id": "b",
      "component_id": "2",
      "anchor_key": "card",
      "allowed_effect_ids": ["highlight", "circle"]
    }
  ]
}
```

### 2.1. Quy tắc component

- `components` trước mắt là danh sách phẳng trên CSS Grid 16×10.
- Component cấp mặt phẳng có `layout` hợp lệ và không chồng lấn.
- Giữ `children` cho widget như `choice`; child nằm trong widget cha, chưa có
  layout toàn panel hay anchor riêng trong scope này.
- `state` là mapping mở, nhưng chỉ được chứa field Registry cho phép.
- `visibility` là state nền tảng bắt buộc của mọi component.
- `anchors` nằm ở document: anchor là quyền Compiler cấp, không phải dữ liệu
  Plan Agent tạo.

### 2.2. Ownership

| Trường | Nguồn quyết định |
|---|---|
| `surface_id` | Runtime tạo |
| `domain_id` | `route_request` đã xác minh |
| `revision` | Runtime quản lý, tăng sau mutation hợp lệ |
| `components[].id` | Compiler cấp |
| `components[].type` | Plan Agent chọn từ Widget Registry |
| `components[].layout` | Plan Agent đề xuất, Compiler validate |
| `components[].props` | Plan Agent đề xuất, Registry validate |
| `components[].state` | Runtime lưu; initial state do Plan Agent đề xuất |
| `components[].children` | Plan Agent đề xuất nếu child policy cho phép |
| `anchors` | Registry yêu cầu, Compiler cấp ID ngắn |

Plan Agent không tạo component ID, anchor ID, anchor key, revision, effect ID
hoặc asset URL.

---

## 3. Plan Agent chỉ trả proposal

Plan Agent không trả `SurfaceDocument` trực tiếp:

```json
{
  "action": "create_surface_plan",
  "template_description": "Một flashcard lớn: ảnh ở mặt trước, từ vựng ở mặt sau.",
  "surface": {
    "components": [
      {
        "widget_id": "flashcard",
        "grid": {"col": 4, "row": 2, "col_span": 9, "row_span": 7},
        "props": {
          "front": {"asset_id": "cat", "text": "Con mèo"},
          "back": {"word": "CAT", "phonetic": "/kæt/", "meaning": "con mèo"}
        },
        "initial_state": {"visibility": "visible", "flipped": false}
      }
    ]
  }
}
```

Compiler theo thứ tự:

1. kiểm tra widget được domain cho phép;
2. kiểm tra Grid 16×10, không tràn/chồng lấn;
3. kiểm tra asset tồn tại khi contract yêu cầu;
4. validate props và children theo Registry;
5. merge `initial_state` với default state widget;
6. validate state field, type và transition;
7. cấp component ID;
8. hỏi widget các anchor cần có;
9. cấp anchor ID ngắn `a`, `b`, `c`…;
10. tạo `SurfaceDocument` revision 1.

Template chỉ được chọn khi `describe_template()` cho thấy khớp toàn bộ intent
và đủ bindings. Materialize luôn tạo document/ID/anchor/revision mới, không tái
sử dụng state hay anchor cũ.

---

## 4. State mở, Registry kiểm soát

Mọi component luôn có `visibility: visible` hoặc `visibility: hidden`. State
khác là riêng của widget; Runtime không còn danh sách hard-code:

| Widget | State được phép |
|---|---|
| `text`, `image`, `object_group`, `answer`, `number_display` | `visibility` |
| `choice` | `visibility`, `selected` |
| `flashcard` | `visibility`, `flipped` |
| `drag_item` sau này | `visibility`, `position`, `drag_status` |
| `progress` sau này | `visibility`, `progress` |

Ví dụ flashcard:

```python
WidgetDefinition(
    widget_id="flashcard",
    state_fields=(
        visibility_state,
        WidgetStateDefinition(
            name="flipped", value_type="boolean", default=False,
            transitions={False: (True,), True: (False,)},
        ),
    ),
)
```

Runtime không có nhánh `if widget == "flashcard"`; nó tra Registry để validate
field, value và transition.

---

## 5. Widget Registry là contract đầy đủ

Mỗi widget đăng ký:

1. `widget_id`;
2. props schema/validator;
3. default state;
4. state field, type, transition;
5. anchor policy;
6. interaction policy;
7. StageMapPolicy cấu trúc;
8. frontend renderer;
9. child policy nếu nhận `children`.

Ví dụ flashcard: props `front/back`; state `visibility/flipped`; anchor `card`;
interaction `flip`; transition `false ↔ true`; Stage Map hai view; renderer
`flashcard.js`.

Thêm widget mới chỉ cần Registry, renderer JS/CSS, đăng ký renderer và test
widget. Không sửa SurfaceDocument, compiler/runtime state chung hay Stage Map
renderer chung.

---

## 6. Anchor và anchor key

`anchor_key` là key kỹ thuật do widget định nghĩa để frontend tìm vùng DOM.
Gemini Live và Plan Agent không thấy/không tạo nó.

| Widget | Anchor key |
|---|---|
| `text` | `text` |
| `image` | `image` |
| `answer` | `answer` |
| `object_group` | `group`, `item_1`, `item_2`… |
| `choice` | `choice` |
| `flashcard` | `card` |

Compiler tạo binding:

```json
{
  "anchor_id": "b",
  "component_id": "2",
  "anchor_key": "card",
  "allowed_effect_ids": ["highlight", "circle"]
}
```

Browser nhận trực tiếp `anchor_id` trên vùng DOM và gửi lại chính `anchor_id`;
không có `target_id` hay bước chuyển đổi trung gian.

---

## 7. Tool và mutation Runtime

### 7.1. `update_surface_state`

Gemini gọi:

```json
{
  "surface_id": "panel-...",
  "base_revision": 1,
  "updates": [{"anchor_id": "b", "changes": {"flipped": true}}]
}
```

Runtime xử lý atomically: lấy document → kiểm tra surface/revision → resolve
anchor → lấy component type → Registry validate field/value/transition → merge
state → tăng revision đúng một lần → sinh payload redact + map mới → gửi
function response Gemini và snapshot browser.

### 7.2. `present_visual`

Không đổi state/revision. Runtime đọc document, xác minh anchor/effect trong
`allowed_effect_ids`, rồi phát cue animation tạm thời.

### 7.3. `delete_surface`

Nhận `surface_id` và `base_revision`, từ chối lệnh stale; chỉ khi hợp lệ mới xóa
document active và báo Gemini/browser surface đã đóng.

---

## 8. Browser interaction

Browser chỉ gửi:

```json
{
  "type": "panel:interaction",
  "surface_id": "panel-...",
  "revision": 1,
  "anchor_id": "b",
  "action": "flip"
}
```

Không gửi `flipped: true`, đáp án, props hay dữ liệu tự tạo. Runtime tra
interaction policy; `flashcard + flip` đảo `flipped`, tăng revision, gửi
snapshot/map mới và event tin cậy cho Gemini.

`choice + select` chỉ được Runtime xác minh tồn tại. Gemini quyết định phản hồi
sư phạm, effect hay có cần đổi `selected`; Runtime không tự chấm đúng/sai.

Browser có thể animate optimistic để phản hồi mượt. Snapshot từ Runtime là
nguồn thật để reconcile. Event stale/không hợp lệ bị từ chối; browser quay lại
snapshot chính thức và Gemini không nhận event giả.

---

## 9. ASCII Stage Map: giữ layout, đổi nguồn mô tả

Giữ canvas Grid 16×10, vị trí theo `col`/`row`/span, anchor ngay dưới vùng,
và mô tả tiêu đề, ảnh, nhóm vật thể, nội dung ẩn, choice. Không gửi HTML, DOM,
JSON, component ID hay anchor key cho Gemini.

```text
Stage Map renderer chung
→ đọc SurfaceDocument
→ đặt vùng theo layout
→ hỏi StageMapPolicy của widget
→ resolve Asset Catalog khi cần
→ wrap nội dung và đặt anchor
```

Renderer chung chỉ biết canvas/grid/wrap/anchor; không hard-code widget ID hay
suy luận UI từ intent/history.

### 9.1. `StageMapPolicy` có cấu trúc

Ví dụ image:

```yaml
kind: image
asset_source: props.asset_id
asset_text_source: asset.caption
text_rendered: false
anchor_key: image
```

Với catalog:

```json
{"id": "mango", "caption": "Một quả xoài"}
```

map sinh:

```text
ẢNH: Một quả xoài
[anchor: b]
```

Không thêm `asset_description` và không đổi schema Asset Catalog. Renderer
không dùng `asset_id`/tags để tạo label. Caption chỉ mô tả ảnh, **không có nghĩa
chữ “Một quả xoài” đang render trên UI**. Chỉ widget text/label thực sự render
chữ mới khiến map mô tả chữ đó.

Ví dụ text:

```yaml
kind: text
text_source: props.content
anchor_key: text
```

```text
CHỮ: “Fruit Quiz: Identify the fruit”
[anchor: a]
```

Flashcard `flipped=false` mô tả ảnh qua `asset.caption`; chỉ nói
`props.front.text` nếu renderer thật sự render nó. `flipped=true` mô tả đúng
word/phonetic/meaning mà mặt sau hiển thị.

Component hidden chỉ có:

```text
NỘI DUNG ĐANG ẨN
[anchor: f]
```

Không lộ đáp án hoặc asset/children hidden.

---

## 10. Browser payload và redaction

Document nội bộ có thể giữ đáp án:

```json
{"type": "answer", "props": {"value": "5"}, "state": {"visibility": "hidden"}}
```

Payload browser bắt buộc redact khi hidden:

```json
{"type": "answer", "props": {}, "state": {"visibility": "hidden"}}
```

Frontend hiển thị `?`; sau reveal payload mới có `props.value: "5"`. Asset URL
cũng chỉ resolve/gửi khi component được phép render theo state.

---

## 11. Patch structure và template reuse

Patch dùng `add_component`, `remove_component`, `replace_component`,
`update_props`, `update_layout`.

- Có `base_revision`.
- Agent tham chiếu component cũ bằng anchor trong `active_surface_summary`.
- Component mới không có ID/anchor; Compiler cấp sau validate.
- Patch structure không tự đổi state; state là Runtime tool riêng.
- Runtime validate toàn document kết quả trước commit.

Template là grid + widget type + props placeholder, không có state phiên trước,
anchor cũ, component ID hay revision. Chỉ sau Compiler thành công mới lưu
template; materialize luôn tạo document mới.

---

## 12. Checkpoint triển khai

- [x] **SD1 — Contract và test document**
  - Tạo `SurfaceDocument`, `ComponentNode`, `AnchorBinding`.
  - State mapping mở có `visibility`; giữ `children` cho choice.
  - Test ID unique, layout, anchor reference, state object, revision dương.
  - Chưa nối Runtime/Browser.
  - Hoàn thành: thêm `ComponentChild` tổng quát cho mọi widget cha có thể
    chứa widget con; `AnchorBinding.component_id` là compatibility view của
    field legacy `block_id`, nên contract mới serialize `component_id` mà
    luồng PanelIR cũ chưa bị đổi trong checkpoint này.
  - Kiểm chứng: `test_panel_contracts`, `test_panel_compiler` và
    `test_live_routing` — 48 test pass.

- [x] **SD2 — Widget Registry đầy đủ**
  - Migrate default state, state definitions, transition, interaction, children,
    StageMapPolicy của text/image/object_group/answer/number_display/choice.
  - Test Registry chặn props/state/children sai.
  - Hoàn thành: `WidgetDefinition` có `default_state`, initial-state
    materialization, interaction capability và `StageMapPolicy` cấu trúc;
    `ComponentChild` được policy children kiểm tra theo type. `choice` có
    state `selected=false` và action `select`; các widget khác có
    `visibility=visible` mặc định. Image/object group khai báo nguồn mô tả
    ảnh là `Asset.caption`, không phải nhãn UI.
  - Tương thích tạm thời: `interaction_event` vẫn là property đọc action duy
    nhất cho Runtime cũ; SD7 mới chuyển Runtime sang `interactions`.
  - Kiểm chứng: `test_widget_registry`, `test_panel_contracts`,
    `test_panel_compiler`, `test_plan_agent`, `test_live_routing` — 74 test
    pass.

- [x] **SD3 — Compiler sinh document**
    - Proposal/template → validate widget/grid/asset/props/initial state/children
      → cấp component/anchor → document revision 1.
    - Không tạo PanelIR mới.
    - Hoàn thành: thêm `PanelCompiler.compile_surface_document()` độc lập với
      đường `compile()` cũ; materialize state qua Widget Registry, chuyển
      children sang `ComponentChild`, cấp identity/anchor bởi Compiler và test
      lỗi state/asset. Runtime chưa gọi đường mới cho tới SD4.

- [x] **SD4 — Runtime active document**
    - Active surface chỉ giữ document; migrate create/template reuse/patch.
    - Validate document hoàn chỉnh trước commit; gỡ structure/state/materialize
      khi hết caller.
    - Hoàn thành: `ActivePanelState` chỉ lưu `SurfaceDocument` + purpose;
      create/template/patch compile document trước rồi mới commit atomically.
      `PanelIR`, structure và state chỉ còn adapter tức thời để renderer/tool
      cũ hoạt động trong các checkpoint tiếp theo, không còn là session state.

- [x] **SD5 — Browser payload và renderer**
  - Payload từ document, redact hidden.
  - Migrate renderer panel/widget sang `component.state`; giữ Grid 16×10.
  - Hoàn thành: Runtime gửi envelope `ui_type: "surface_document"` với
    `surface.components`, `surface.anchors` và revision nằm trong document;
    browser renderer đọc trực tiếp `type`, `layout`, `state` và
    `component_id`, không còn đọc `blocks`, `widget_id` hay `visibility` của
    payload PanelIR. Component hidden chỉ gửi identity/type/layout,
    `state.visibility="hidden"` và props rỗng; không gửi children, non-default
    state hoặc asset URL. Renderer vẫn dùng CSS Grid 16×10 và widget renderer
    lấy visibility từ `component.state`.
  - Tương thích tạm thời: Stage Map và Runtime tool validation vẫn dùng adapter
    PanelIR cho tới SD6–SD7; `panel_client_payload()` legacy chưa bị xóa vì SD10
    mới là checkpoint dọn toàn bộ legacy, nhưng không còn là browser payload
    của luồng active.
  - Kiểm chứng: 92 test Python pass; `node --check` pass cho app, renderer và
    toàn bộ widget JS đã migrate; `git diff --check` sạch.

- [x] **SD6 — Stage Map policy**
  - Renderer chung chỉ canvas/grid/wrap/anchor; Registry trả policy.
  - Image dùng `Asset.caption`.
  - Test image/text/hidden answer/object group/choice và item anchor.
  - Hoàn thành: `render_visual_stage_map()` đọc trực tiếp
    `SurfaceDocument`, tra `StageMapPolicy` từ `WidgetRegistry` và resolve
    `asset.caption` qua `AssetCatalog`. Renderer không còn nhánh theo
    `widget_id`, không dùng `asset_id`/`label` để suy ra chữ hiển thị và vẫn
    đặt vùng theo Grid 16×10. Policy quy định nguồn text/asset/count, tiêu đề
    mô tả và quoting; container chỉ compose policy của child. Nội dung hidden
    chỉ sinh `NỘI DUNG ĐANG ẨN` cùng anchor, không lộ props/children.
  - Runtime route, reconnect context và state-update response đều sinh map từ
    document active cùng Registry/catalog của domain. Tool validation vẫn còn
    adapter tạm thời cho đến SD7 đúng phạm vi checkpoint.
  - Kiểm chứng: test map cho text, image caption không thành nhãn UI, answer
    hidden/reveal, choice child và object group với item anchor; toàn bộ 93
    test Python pass, `git diff --check` sạch.

- [x] **SD7 — Tool và interaction Runtime**
  - `present_visual`, `update_surface_state`, `delete_surface` và
    `resolve_panel_interaction` hiện đọc trực tiếp `ActivePanelState.document`:
    resolve `anchor_id → component_id → ComponentNode`, không còn dùng
    `panel_ir`, `SurfaceState` hay `BlockState` trong Runtime.
  - `update_surface_state` dùng `WidgetRegistry.validate_state_changes()` với
    `component.type` và `component.state`, kiểm tra `surface_id`/revision/anchor
    trước khi commit. Mọi update được kiểm tra xong mới thay toàn bộ components
    và tăng document revision đúng một lần; response chứa payload redact, map
    và revision mới của cùng document.
  - Interaction browser vẫn chỉ nhận `surface_id`, revision, `anchor_id` và
    action. Runtime tra `WidgetDefinition.allows_interaction()` rồi mới gửi
    event tin cậy; không nhận props, nội dung hoặc state do browser tự tạo.
    `choice + select` vẫn chỉ xác minh, chưa tự đổi `selected` hay chấm đúng/sai.
  - `present_visual` chỉ xác minh anchor/effect và không đổi revision;
    `delete_surface` từ chối revision stale trước khi xoá document active.
  - Kiểm chứng trực tiếp với `SurfaceDocument`: reveal/repeat transition,
    interaction choice, surface/revision stale, anchor lạ, field state cấm và
    update trùng component. Toàn bộ 93 test Python pass; `git diff --check`
    sạch.

- [x] **SD8 — Flashcard**
  - Props front/back, `flipped`, anchor card, action flip, Stage Map hai view.
  - Renderer JS/CSS, click/touch/keyboard, animation, reconciliation.
  - Hoàn thành: Registry có `WidgetAssetReferenceDefinition` để mỗi widget
    tự khai báo path asset Compiler cần kiểm tra, và `WidgetInteractionDefinition.state_rule`
    dạng dữ liệu. Interpreter chung hiện hỗ trợ operation `toggle`; Runtime
    không có nhánh theo `flashcard`. Flashcard đăng ký `flip → {"flipped":
    {"op":"toggle"}}`, state `visibility/flipped`, anchor `card`, asset
    `props.front.asset_id`, cùng contract bắt buộc cho hai mặt `front` và
    `back`.
  - `StageMapPolicy` hỗ trợ state view và nhiều nguồn chữ render thật. Khi
    `flipped=false`, map chỉ mô tả caption ảnh mặt trước và `front.text`; khi
    `flipped=true`, map chỉ mô tả `word`, `phonetic`, `meaning` ở mặt sau. Cùng
    anchor card giữ nguyên qua lần lật.
  - Browser có `flashcard.js`/CSS 3D: click, touch (qua click) và Enter/Space
    gửi event tối thiểu `flip`; snapshot document mới là nguồn state để render
    lại và animation lật. Cache-buster renderer/style/app đã tăng.
  - Runtime thêm `apply_panel_interaction()`: action không có state rule như
    `choice.select` chỉ được xác minh như SD7; action có rule được validate,
    cập nhật atomically qua `update_surface_state`, tăng revision, gửi
    `panel_update` cho browser và trusted event kèm Stage Map/effects revision
    mới cho Gemini Live.
  - Education manifest đã cho phép `flashcard`; Plan Agent chưa được dạy chọn
    widget này cho đến SD9.
  - Kiểm chứng: contract flashcard, Compiler asset path lồng nhau, map hai
    mặt, interaction flip/revision/map/stale revision; 96 test Python pass,
    `py_compile`, `node --check` và `git diff --check` sạch.

- [x] **SD9 — Plan Agent**
  - Cập nhật index/describe/prompt/few-shot cho initial state, children, answer
    hidden có value thật, flashcard và choice.
  - Repair giữ intent; không patch rỗng hoặc né yêu cầu.
  - Hoàn thành: tách cơ chế prompt thành `SurfacePlanPromptBuilder` gồm prompt
    lõi trong `plan_agent/prompts.py` và prompt theo domain khai báo qua
    `manifest.json`. Education tải `domains/education/plan_prompt.py`; cả Gemini
    và Cerebras nhận cùng prompt đã ghép.
  - Widget Index vẫn ngắn nhưng đánh dấu widget có children/action để Agent biết
    cần khám phá; `describe_widgets` nay trả props, `initial_state` gồm default
    và các state field hợp lệ, child widget được phép và interaction contract.
    Nó không gửi Stage Map policy hay chi tiết renderer không cần cho planning.
  - Prompt Education có few-shot cho flashcard `front/back` + `flipped:false`,
    choice với child image/text và đáp án ẩn bằng `initial_state.visibility`;
    value vẫn là đáp án thật. Prompt lõi bắt buộc describe widget trước khi tạo,
    dùng state contract, giữ intent khi nhận compiler feedback và cấm patch rỗng.
  - Kiểm chứng: test discovery initial state/flip và widget index; Gemini/Cerebras
    cùng nhận domain prompt. Toàn bộ 105 test Python pass, `py_compile` và
    `git diff --check` sạch.

- [x] **SD10 — Xóa legacy và regression**
  - Xóa PanelIR/PanelBlock/SurfaceBlock/BlockState/SurfaceStructure/SurfaceState
    sau khi hết caller; xóa renderer hard-code cũ.
  - Regression: reveal, choice, flashcard, route, follow-up animation,
    reconnect, stale event.
  - Hoàn thành: xoá `PanelIR`, `PanelBlock`, `PanelChoiceChild`, `SurfaceBlock`,
    `BlockState`, `SurfaceStructure`, `SurfaceState`, hai adapter chuyển đổi và
    `panel_client_payload()`. `PanelCompiler` chỉ còn
    `compile_surface_document()`; `ActivePanelState` chỉ nhận/lưu document.
  - `AnchorBinding` nay lưu trực tiếp `component_id` thay vì một field `block_id`
    cũ. Patch Runtime đổi cách đặt tên identity nội bộ sang component, nhưng
    Surface Plan vẫn dùng `blocks` vì đó là contract đầu vào của Plan Agent.
  - Toàn bộ test compiler/template/runtime đổi sang dựng và kiểm tra
    `SurfaceDocument` trực tiếp. Không còn symbol legacy hoặc `panel_ir`/`block_id`
    trong mã Python/JS chạy.
  - Kiểm chứng: regression route, template reuse, patch, reveal, choice,
    flashcard, delete, reconnect và stale event; toàn bộ 102 test Python pass,
    `py_compile` và `git diff --check` sạch.

## 13. Tiêu chí hoàn thành

- Runtime có một document active làm nguồn UI/state/map.
- Widget có state mới không đòi sửa class state/compiler/Stage Map chung.
- Map khớp UI renderer ở revision hiện tại.
- Ảnh mô tả bằng `Asset.caption`, nhưng caption không bị hiểu là label UI.
- Dữ liệu hidden không lọt browser payload hoặc Stage Map.
- Template reuse materialize document mới.
- Flashcard lật được; state/revision/map đồng bộ Gemini Live.
