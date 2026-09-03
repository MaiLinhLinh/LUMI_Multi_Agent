# Kế hoạch kiến trúc Surface Agent

## 1. Mục tiêu

Chuẩn hoá hệ thống thành ba vai trò tách biệt:

```text
Người dùng (voice / text)
        ↓
Gemini Live — hội thoại, dạy học và điều phối hành động
        ↓ khi cần surface mới hoặc đổi cấu trúc
Plan Agent — lập kế hoạch surface và lấy dữ liệu tin cậy nếu cần
        ↓
Runtime — validate, lưu structure + state, render UI và sinh map
        ↓
Browser — hiển thị UI, nhận click / drag / drop
```

Mục tiêu không phải để backend tự quyết nội dung dạy học. Gemini Live là bên quyết định lời thoại và thời điểm hành động; Runtime chỉ là nguồn trạng thái đáng tin cậy và nơi thi hành các chuyển đổi được cho phép.

## 2. Phân công trách nhiệm

### Gemini Live: Teaching / Interaction Director

Gemini Live có năm nhánh hành động:

```text
no_ui
present_visual
update_surface_state
delete_surface
route_request
```

- `no_ui`: chỉ nói chuyện, không tạo hoặc đổi UI.
- `present_visual`: gọi animation tạm thời trên một anchor đang có. Không thay state hay layout.
- `update_surface_state`: đổi trạng thái hợp lệ của component hiện có, ví dụ `visibility`, `selected`, `flipped`, `position`, `feedback`, `progress`.
- `delete_surface`: đóng surface hiện tại khi không còn phù hợp.
- `route_request`: gọi khi cần tạo panel mới hoặc đổi cấu trúc panel. Gemini cung cấp domain và intent đã hiểu từ lời người dùng.

Gemini Live tự quyết kịch bản dạy: khi nào hỏi, gợi ý, công bố đáp án, phản hồi click/drag/drop và gọi animation. Gemini không được tự sửa UI; mọi thay đổi đi qua Runtime.

### Plan Agent: Surface Planner

Plan Agent chỉ chạy sau `route_request`.

Nó nhận:

- intent đã được Gemini Live chuẩn hoá;
- history đáng tin cậy do backend lấy theo session;
- domain manifest, asset index, widget index, template index và grid contract;
- `active_surface_summary` nếu đang có panel;
- dữ liệu đã xác minh hiện có;
- capability của domain được phép gọi.

`active_surface_summary` chỉ là bản tóm tắt nghiệp vụ, không chứa CSS/DOM/target kỹ thuật:

```json
{
  "surface_id": "s12",
  "revision": 8,
  "domain_id": "education",
  "purpose": "Dạy phép cộng bằng hình ảnh động vật",
  "structure_summary": [
    {"anchor_id": "a", "widget": "object_group", "description": "1 con mèo"},
    {"anchor_id": "b", "widget": "image", "description": "dấu cộng"},
    {"anchor_id": "c", "widget": "object_group", "description": "2 con mèo"},
    {"anchor_id": "d", "widget": "answer", "description": "kết quả"}
  ],
  "state_summary": {"d.visibility": "hidden"}
}
```

Plan Agent có thể gọi nhiều capability domain để lấy/tạo dữ liệu tin cậy. Khi đủ dữ liệu, nó trả một trong ba quyết định:

- `use_existing_surface_template`: template đã describe đáp ứng toàn bộ yêu cầu; Agent chỉ trả `template_id` và bindings biến đổi, Runtime materialize thành surface;
- `create_surface_plan`: surface mới hoàn toàn, gồm structure và state khởi tạo. Sau khi Compiler render thành công, Runtime trích khung và lưu template tái sử dụng;
- `patch_surface_plan`: thay đổi cấu trúc surface hiện tại, ví dụ thêm/bớt/sắp lại component.

Plan Agent không xử lý các thao tác hội thoại ngắn như reveal, highlight hoặc chấm ý nghĩa của lựa chọn trẻ em.

### Runtime: nguồn sự thật của UI

Runtime lưu theo session/surface:

- `SurfaceStructure`: component/widget, grid, props cấu trúc, anchor và allowed actions;
- `SurfaceState`: giá trị runtime như visible/hidden, flipped, selected, position, feedback, progress;
- `revision`: phiên bản tăng sau mọi thay đổi UI có ý nghĩa;
- `PanelIR`: representation đã materialize để frontend render và để sinh map.

Runtime chịu trách nhiệm:

- validate `create`, `patch`, `update_surface_state`, `delete`;
- chỉ cho phép transition đã được widget contract khai báo;
- render UI frontend từ structure + state;
- nhận và xác minh event click / drag / drop của browser;
- gửi stage map mới, effects hợp lệ và revision mới cho Gemini khi UI thay đổi.

Runtime không tự suy luận đáp án đúng/sai hoặc quyết định cách dạy.

## 3. Tách Structure và State

| Loại thay đổi | Bên quyết định | Bên thực thi | Ví dụ |
|---|---|---|---|
| `create_surface` | Plan Agent | Runtime | Tạo hoạt động mới về vòng đời bướm |
| `patch_surface` | Plan Agent | Runtime | Thêm bảng so sánh hoặc đổi bố cục |
| `update_surface_state` | Gemini Live | Runtime | `hidden → visible`, lật flashcard, chọn đáp án |
| `delete_surface` | Gemini Live | Runtime | Đóng panel khi chuyển chủ đề |
| `present_visual` | Gemini Live | Frontend qua Runtime validation | Khoanh/hightlight tạm thời |

`reveal` không còn là tool riêng. Nó là một trường hợp của `update_surface_state`:

```json
{
  "surface_id": "s12",
  "updates": [
    {"anchor_id": "g", "changes": {"visibility": "visible"}}
  ]
}
```

## 4. Luồng runtime chi tiết

### 4.1. Không cần UI

```text
Voice/text → Gemini Live → no_ui → lời nói
```

Không route, không gọi Plan Agent, không render lại.

### 4.2. Tương tác với surface hiện tại

```text
Voice/text → Gemini Live đọc map revision hiện tại
          → present_visual hoặc update_surface_state
          → Runtime validate + lưu state + render
          → Runtime trả surface_id + revision + map mới + effects
          → Gemini Live tiếp tục theo map mới
```

`present_visual` không làm thay đổi structure/state, nên không cần gửi lại map.

### 4.3. Surface mới hoặc thay cấu trúc

```text
Voice/text → Gemini Live
          → route_request(domain_id, intent)
          → backend chuẩn bị history + active_surface_summary + resources
          → Plan Agent (có thể gọi capability domain nhiều lần)
          → use_existing_surface_template, create_surface_plan hoặc patch_surface_plan
          → Runtime validate + materialize + render + lưu revision
          → Runtime trả map/effects/prompt domain cho Gemini Live
          → Gemini Live bắt đầu trình bày
```

### 4.4. Event từ browser

```text
Click / drag / drop
  → Browser gửi surface_id, revision, anchor_id, action, dữ liệu thao tác tối thiểu
  → Runtime kiểm tra surface active, anchor, action và target hợp lệ
  → Gemini Live nhận event đáng tin cậy + map revision hiện tại
  → Gemini tự quyết phản hồi và có thể gọi update_surface_state/present_visual
```

Browser không bao giờ tự gửi “đúng/sai” hay nội dung học do browser tự tạo.

## 5. Quy tắc đồng bộ map

Gemini Live luôn dựa vào `VISUAL STAGE MAP` của revision mới nhất.

| Operation | Có trả map mới? |
|---|---|
| `create_surface` | Có |
| `patch_surface` | Có |
| `update_surface_state` | Có |
| `delete_surface` | Có, báo surface đã đóng |
| `present_visual` | Không |
| UI event không đổi UI | Không; chỉ gửi event đáng tin cậy |
| UI event dẫn đến update state | Có, sau update state |

Response đổi UI tối thiểu gồm:

```json
{
  "surface_id": "s12",
  "revision": 9,
  "visual_stage_map": "...",
  "visual_effects": ["highlight", "circle", "reveal"]
}
```

## 6. Checkpoint triển khai

- [x] **SA1 — Chuẩn hoá SurfaceState:** Tách state runtime rõ ràng khỏi structure/PanelIR hiện có; giữ compatibility render.
- [x] **SA2 — Gộp reveal vào `update_surface_state`:** Bỏ tool/action reveal riêng; expose state transition có validate theo widget contract.
- [x] **SA3 — Surface lifecycle contract:** Định nghĩa `create_surface_plan`, `patch_surface_plan`, `delete_surface` và revision semantics. `update_props` gộp `changes` vào props hiện có, không thay toàn bộ object.
- [x] **SA4 — ActiveSurfaceSummary:** Tạo summary đáng tin cậy và cấp vào Plan Agent chỉ khi route request.
- [x] **SA5 — Mở rộng Plan Agent output:** Hỗ trợ quyết định create hoặc patch, compiler feedback, capability loop và contract mới. Agent chỉ nhận widget/template index ngắn, rồi gọi `describe_widgets` hoặc `describe_template` trước khi cần contract chi tiết.
- [x] **SA6 — Runtime materialization:** Apply create/patch/state update thành PanelIR, persist structure/state/revision, trả map mới.
- [x] **SA7 — Gemini Live tools & prompts:** Chuẩn hoá 5 nhánh `no_ui`, `present_visual`, `update_surface_state`, `delete_surface`, `route_request`.
- [x] **SA8 — Browser interaction contract:** Chuẩn hoá click/select event có `surface_id` + revision và validation Runtime theo Widget Registry; giữ choice hiện tại tương thích. Drag/drop sẽ dùng cùng contract khi có widget được đăng ký.
- [x] **SA9 — Tests & migration:** Test revision, stale event, state transition không hợp lệ, patch, delete, map synchronization và regression present.
- [x] **SA10 — Tái sử dụng và tự lưu template:** Agent trả bindings tối thiểu khi template đã đủ; Runtime materialize template. Plan mới chỉ được trích/lưu thành `tmN` sau khi Compiler thành công, kể cả binding trong `choice.children`.

## 7. Nguyên tắc triển khai

- Không chuyển toàn bộ sản phẩm sang một protocol bên ngoài ngay lập tức.
- Giữ transport audio/WebSocket Gemini Live hiện có; chỉ chuẩn hoá event/contract nội bộ.
- PanelIR vẫn là output trung gian duy nhất cho frontend và ASCII map.
- Widget Registry là nơi khai báo state fields, allowed transitions, actions và event capability.
- Không để backend tự áp đặt logic sư phạm; backend chỉ xác minh và duy trì nguồn sự thật.

## 8. Contract input/output chi tiết

Phần này là contract triển khai. Mọi payload kỹ thuật phải được validate; Gemini
và Plan Agent không tự đọc/ghi DOM, CSS hoặc `target_id`.

### 8.1. Gemini Live

**Input cho mỗi lượt**

- audio hoặc text của người dùng;
- history hội thoại của phiên Live;
- prompt core và prompt present của domain active nếu đang có surface;
- `VISUAL STAGE MAP`, `visual_effects`, `surface_id`, `revision` mới nhất;
- event UI đã được Runtime xác minh nếu người dùng click/drag/drop.

**Output hợp lệ**

- lời nói/text (`no_ui`);
- `present_visual` cho animation tạm thời;
- `update_surface_state` để đổi state surface đang mở;
- `delete_surface` để đóng surface;
- `route_request(domain_id, intent)` khi cần surface mới hoặc đổi structure.

### 8.2. `route_request` — Gemini Live → backend → Plan Agent

Gemini chỉ gửi intent đã hiểu, không tự gửi history/catalog:

```json
{
  "domain_id": "education",
  "intent": "Tạo hoạt động minh hoạ phép cộng 1 mèo và 2 mèo"
}
```

Backend tự bổ sung input của Plan Agent theo session:

```json
{
  "domain_id": "education",
  "intent": "Tạo hoạt động minh hoạ phép cộng 1 mèo và 2 mèo",
  "recent_history": ["..."],
  "active_surface_summary": {
    "surface_id": "s12",
    "revision": 8,
    "domain_id": "education",
    "purpose": "Dạy phép cộng bằng hình ảnh động vật",
    "structure_summary": [
      {"anchor_id": "a", "widget": "object_group", "description": "1 con mèo"},
      {"anchor_id": "b", "widget": "image", "description": "dấu cộng"}
    ],
    "state_summary": {"d.visibility": "hidden"}
  },
  "domain_manifest": {"...": "..."},
  "asset_index": [{"id": "cat", "purpose": "ảnh mèo"}],
  "widget_index": [{"id": "object_group", "purpose": "nhóm vật thể"}],
  "template_index": [{"id": "tm2", "purpose": "..."}],
  "grid_contract": {"columns": 16, "rows": 10},
  "verified_data": {},
  "capabilities": []
}
```

Plan Agent có ba nhóm native function call, có thể gọi nhiều lần trước output cuối:

- capability domain: lấy hoặc tạo `verified_data` tin cậy;
- `describe_widgets(widget_ids)`: chỉ sau khi Agent đã chọn widget từ `widget_index` ngắn; response trả props, state, action, anchor và children hợp lệ của đúng các widget được hỏi;
- `describe_template(template_id)`: chỉ sau khi Agent thấy một template trong `template_index` phù hợp; response trả cấu trúc và binding cần điền.

Mỗi function response bổ sung context làm việc của Agent. Agent chỉ trả plan cuối khi đã có
đủ dữ liệu thật và contract UI cần thiết; không được tự đoán props/widget binding chưa xem.

### 8.3. `use_existing_surface_template` — Plan Agent → Runtime

Chỉ dùng sau khi Agent đã gọi `describe_template` và xác nhận khung đó đáp ứng
toàn bộ yêu cầu. Agent không lặp blocks/grid; chỉ gửi các binding dữ liệu biến đổi.

```json
{
  "action": "use_existing_surface_template",
  "template_id": "tm2",
  "bindings": {
    "$block_1_content": "Cùng quan sát hai bạn mèo nhé!",
    "$block_2_asset_id": "cat",
    "$block_3_asset_id": "cat"
  }
}
```

Runtime load template từ catalog, kiểm required/optional bindings, materialize thành
`PresentationPlan`, rồi dùng cùng `PanelCompiler → PanelIR` như mọi surface mới.

### 8.4. `create_surface_plan` — Plan Agent → Runtime

Tạo surface mới giữ format plan hiện tại; chỉ bọc rõ operation. `blocks` vẫn gồm
`widget_id`, `grid`, `props` và state khởi tạo khi cần.

```json
{
  "action": "create_surface_plan",
  "template_description": "Một nhóm vật thể minh hoạ phép cộng, có kết quả bên phải.",
  "surface": {
    "blocks": [
      {
        "widget_id": "object_group",
        "grid": {"col": 1, "row": 3, "col_span": 4, "row_span": 4},
        "initial_visibility": "visible",
        "props": {"asset_id": "cat", "count": 1}
      }
    ]
  }
}
```

Runtime validate, tạo `surface_id`, materialize `PanelIR`, persist structure/state,
tạo revision đầu tiên và trả PanelIR cho browser cùng map/effects cho Gemini. Chỉ sau
khi compile thành công, Runtime dùng `TemplateExtractor` để lưu khung thành ID `tmN`.
Extractor thay props biến đổi của block và `choice.children` thành bindings; không lưu
asset/text cụ thể của lượt cũ vào template.

### 8.5. `patch_surface_plan` — Plan Agent → Runtime

Patch không gửi lại toàn bộ surface. Nó dùng `surface_id` và `base_revision`, rồi
chỉ mô tả thay đổi structure:

```json
{
  "action": "patch_surface_plan",
  "surface_id": "s12",
  "base_revision": 8,
  "operations": [
    {
      "op": "add_block",
      "block": {
        "widget_id": "text",
        "grid": {"col": 1, "row": 9, "col_span": 16, "row_span": 1},
        "props": {"content": "Hãy thử đếm tất cả các bạn mèo nhé!", "role": "instruction"}
      }
    },
    {"op": "remove_block", "anchor_id": "f"},
    {
      "op": "replace_block",
      "anchor_id": "d",
      "block": {
        "widget_id": "image",
        "grid": {"col": 11, "row": 3, "col_span": 3, "row_span": 3},
        "props": {"asset_id": "cat"}
      }
    }
  ]
}
```

`anchor_id` chỉ là định danh nghiệp vụ của block đang có. Runtime đối chiếu nó với
surface active; Plan Agent không dùng DOM/CSS/`target_id`. Runtime trả PanelIR,
revision, map và effects mới, hoặc compiler feedback có cấu trúc nếu patch lỗi.

`update_props` luôn là patch dạng gộp: chỉ các key trong `changes` thay đổi; các
props khác của widget giữ nguyên. Sau khi gộp, Runtime sẽ validate lại toàn bộ
props theo Widget Registry trước khi materialize.

### 8.5. `update_surface_state` — Gemini Live → Runtime

```json
{
  "surface_id": "s12",
  "base_revision": 8,
  "updates": [
    {"anchor_id": "g", "changes": {"visibility": "visible"}},
    {"anchor_id": "h", "changes": {"selected": true}}
  ]
}
```

Runtime chỉ cho phép field và transition do Widget Registry khai báo. Reveal là
`visibility: visible`, không phải tool riêng. Khi hợp lệ, Runtime lưu state, tăng
revision, materialize PanelIR mới và trả map/effects/revision mới cho Gemini.

### 8.6. `delete_surface` — Gemini Live → Runtime

```json
{"surface_id": "s12", "base_revision": 9}
```

Runtime đóng surface, clear UI frontend và trả xác nhận/revision để Gemini không
dùng lại map cũ.

### 8.7. `present_visual` — Gemini Live → Runtime/Frontend

```json
{"surface_id": "s12", "anchor_id": "b", "effect_id": "circle"}
```

Runtime validate anchor/effect rồi gửi animation cue. Operation này không sửa
structure/state, không tăng revision và không gửi lại ASCII map.

### 8.8. Browser interaction — Browser → Runtime → Gemini Live

```json
{
  "surface_id": "s12",
  "revision": 9,
  "anchor_id": "b",
  "action": "select"
}
```

Runtime xác minh surface active, revision, anchor và action đã đăng ký bởi widget.
Nếu hợp lệ, Runtime gửi Gemini một trusted UI event kèm mô tả component; không tự
chấm đúng/sai. Nếu event không đổi UI, không cần map mới. Gemini tự quyết lời
thoại và có thể gọi `present_visual` hoặc `update_surface_state` tiếp theo.

### 8.10. Migration đã hoàn tất

- `PanelIR` vẫn là đầu ra duy nhất cho browser và ASCII map; browser không nhận
  HTML template hay target ID.
- Reveal cũ đã được thay bằng `update_surface_state` với transition
  `visibility: hidden → visible`; không còn state/reveal path song song.
- `present_visual` chỉ phát animation cue theo PCM, không sửa state, revision hay map.
- Interaction browser dùng `surface_id` + `revision` + `anchor_id` + `action`;
  Runtime tra action trong Widget Registry trước khi báo Gemini.

### 8.9. Output thống nhất của Runtime khi UI đổi

Mọi create, patch hoặc state update thành công phải trả:

```json
{
  "surface_id": "s12",
  "revision": 9,
  "panel_ir": {"...": "payload frontend đã materialize"},
  "visual_stage_map": "...",
  "visual_effects": ["highlight", "circle"]
}
```

Frontend render `panel_ir`; Gemini Live dùng `visual_stage_map` cùng revision đó.
Hai đầu ra luôn được sinh từ cùng một PanelIR để không lệch màn hình thực.
