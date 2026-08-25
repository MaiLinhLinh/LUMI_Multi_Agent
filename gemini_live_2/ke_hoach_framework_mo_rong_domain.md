# Kế hoạch framework mở rộng domain

## Mục tiêu

Xây một framework để người dùng tương tác bằng giọng nói với Gemini Live, có thể mở rộng thêm domain mà không phải sửa luồng Live, animation, renderer hay ASCII map dùng chung.

Luồng đích:

```text
Voice người dùng + history Gemini Live + context panel đang mở
        ↓
Gemini Live tự phân loại ý định của lượt
        ├─ A. Tương tác panel hiện tại
        │     → dùng VISUAL STAGE MAP hiện có
        │     → present_visual / trả lời
        │     → không route_request, không Plan Agent, không render lại
        ├─ B. Cần panel mới hoặc thay panel
        │     → route_request(domain_id, intent)
        │     → Domain Gateway / capability của domain
        │     → Plan Agent chủ động gọi tool nếu cần
        │     → chọn plan có sẵn hoặc tạo plan mới
        │     → IR Compiler / Materializer → PanelIR mới
        │     → UI Renderer + ASCII Renderer
        │     → context panel mới cho Gemini Live trình bày + present_visual
        └─ C. Không cần panel
              → Gemini Live trả lời trực tiếp
```

`route_request` chỉ mang thông tin định tuyến tối thiểu:

```json
{
  "domain_id": "education",
  "intent": "Tạo hoạt động giúp trẻ nhận biết chó và mèo"
}
```

Plan Agent **luôn** nhận ngữ cảnh hội thoại do backend lấy theo `session_id` nội bộ khi Gemini Live đã gọi `route_request`. Gemini Live không truyền history qua tool arguments và không tự viết lại history. Backend chịu trách nhiệm chọn và cung cấp bản history đáng tin cậy cho Plan Agent.

---

## 0. Chốt contract lõi

Tạo các cấu trúc dùng chung, không gắn tên một domain:

- `RouteRequest`: `domain_id`, `intent`.
- `PresentationPlan`: ý đồ trình bày do backend dựng từ các block Plan Agent trả về
  và `domain_id` đã được kiểm chứng từ route.
- `PanelIR`: panel đã được compiler kiểm chứng, có dữ liệu thật và anchor.
- `AssetCatalog`, `WidgetCatalog`, `TemplateCatalog`.
- `DomainManifest`: khai báo capability và catalog của domain.

Mục tiêu: domain mới dùng các contract này, không thêm nhánh `if domain == ...` vào framework.

## 1. Chuẩn hóa Asset Catalog

Cấu trúc đề xuất:

```text
domains/
  education/
    assets/
      catalog.json
      dog.svg
      cat.svg
  weather/
    assets/
      catalog.json
      sun.svg
      rain.png
      weather-background.jpg
```

Ví dụ catalog:

```json
{
  "id": "dog",
  "kind": "image",
  "caption": "Hình minh họa một chú chó thân thiện",
  "path": "/assets/education/dog.png",
  "mime_type": "image/png",
  "tags": ["animal", "dog", "education"]
}
```

Catalog hỗ trợ SVG, PNG, JPG/JPEG, WebP hoặc định dạng ảnh phù hợp khác. Plan Agent chỉ chọn `asset_id`; renderer mới đổi ID thành URL/file thật và dùng `mime_type` khi cần.

## 2. Xây Widget Registry chung

Widget là một component giao diện tái sử dụng. Mỗi widget tự có:

- renderer HTML/DOM riêng;
- CSS riêng, nhưng dùng design tokens chung để giữ màu sắc, typography và spacing đồng nhất;
- schema props/data binding;
- quy tắc sinh anchor cho widget và các vùng nội bộ;
- danh sách effect được phép trên từng anchor.

Bộ widget khởi đầu:

- `text`
- `image`
- `metric`
- `object_group`
- `chart_line`
- `chart_bar`
- `forecast_card`

Widget Registry có hai mức công khai cho Plan Agent:

- **Widget Index** ngắn chỉ gồm `widget_id` và `purpose`, được gửi ngay từ đầu để
  Agent biết những loại khối nào có thể dùng.
- **Widget contract** chi tiết chỉ được trả khi Agent gọi tool hạ tầng chung
  `describe_widgets(widget_ids)`. Contract này nói rõ props hợp lệ, prop bắt buộc,
  kiểu dữ liệu và nguồn catalog nếu prop tham chiếu asset.

Ví dụ contract chi tiết của widget `image`:

```json
{
  "widget_id": "image",
  "purpose": "Hiển thị một ảnh asset trong vùng lưới.",
  "props": {
    "asset_id": {
      "required": true,
      "type": "string",
      "source": "asset_catalog.id"
    },
    "label": { "required": false, "type": "string" }
  },
  "anchor_policy": "self"
}
```

`anchor_policy`:

- `self`: widget có một anchor.
- `children`: widget tự tạo anchor cho phần tử con.
- `none`: chỉ trang trí, không tương tác.

Ví dụ `weather_day_card` tự render ngày, icon, nhiệt độ và xác suất mưa; đồng thời tự sinh anchor cho toàn thẻ, nhiệt độ và xác suất mưa.

Plan Agent chỉ chọn `widget_id`, đặt widget vào grid và truyền props/data binding.
Trước khi dùng widget trong `create_plan`, Agent phải gọi `describe_widgets` cho widget
đó. Plan Agent không tự tạo block ID, HTML, CSS, DOM target hoặc `anchor_id`.
Compiler sinh block ID tuần tự, materialize widget và tạo `target_id` kỹ thuật cùng
anchor thật trong `PanelIR`; frontend renderer gắn anchor đó vào DOM.

## 3. Chốt Layout Contract

Canvas dùng grid cố định `16 × 10`.

```json
{
  "blocks": [
    {
      "widget_id": "image",
      "grid": { "col": 1, "row": 3, "col_span": 5, "row_span": 5 },
      "props": { "asset_id": "dog", "label": "Chó" }
    }
  ]
}
```

Compiler phải kiểm tra:

- block không vượt grid;
- block không chồng nhau nếu không được cho phép;
- widget và asset tồn tại;
- props đúng schema;
- widget tương tác có anchor ổn định.

## 4. Xây Template Catalog

Template có sẵn là `PresentationPlan` được lưu, không phải rule Python chọn HTML cứng. Catalog chỉ phục vụ Plan Agent tìm kiếm theo mô tả; layout, block grid và slot dữ liệu phải nằm trong plan file riêng để compiler biết cách materialize.

```json
{
  "id": "two_subject_comparison",
  "purpose": "So sánh trực quan hai đối tượng ngang hàng.",
  "supports": ["2 ảnh", "nhãn", "mô tả ngắn"],
  "domains": ["education"],
  "plan_path": "two_subject_comparison.plan.json"
}
```

Ví dụ `two_subject_comparison.plan.json` chứa các block, vị trí grid và data alias/slot mà template cần, như `$title`, `$left_asset`, `$right_asset`. Compiler bind alias vào đúng props của widget theo plan này; không cần đoán từ catalog.

Plan Agent tự chọn:

- `use_existing`: dùng template có sẵn;
- `create_plan`: tạo Presentation Plan mới từ widget/asset đăng ký.

## 5. Xây Domain Manifest

Ví dụ:

```json
{
  "domain_id": "education",
  "assets_catalog": "assets/catalog.json",
  "widgets": ["text", "image", "object_group"],
  "templates_catalog": "templates/catalog.json",
  "tools": ["lookup_learning_content"]
}
```

Framework đọc manifest theo `domain_id`; Education chỉ là domain kiểm chứng đầu tiên. Plan Agent không có prompt riêng theo domain; prompt của nó là prompt chung. Prompt riêng theo domain, nếu cần, chỉ thuộc Gemini Live khi trình bày panel.

## 6. Thêm `route_request` cho Gemini Live

Gemini Live nhận voice, history và context panel đang mở để hiểu ngữ cảnh. Chỉ khi cần dựng panel mới hoặc thay đổi panel, nó mới gọi:

```json
{
  "domain_id": "education",
  "intent": "Tạo hoạt động giúp trẻ nhận biết chó và mèo"
}
```

Với câu hỏi tiếp nối chỉ dùng dữ liệu đang hiển thị, Gemini Live trả lời và gọi `present_visual` trực tiếp từ stage map; không gọi `route_request`. Với hội thoại không cần panel, Gemini Live cũng trả lời trực tiếp. Gemini Live không chọn asset, widget, grid hoặc HTML.

## 7. Xây Domain Gateway / Tool Boundary

Sau routing, chỉ mở tool/capability của domain đã chọn.

Ví dụ Education có thể cung cấp:

- nội dung học đã kiểm chứng;
- tiến độ của trẻ;
- bài tập có đáp án/luật kiểm chứng;
- học liệu được cấp phép.

Nếu yêu cầu không cần dữ liệu ngoài, Gateway trả capability rỗng; Plan Agent vẫn lập panel từ intent và asset catalog.

## 8. Xây Plan Agent

Đầu vào:

- `domain_id`, `intent`;
- history/ngữ cảnh phiên đáng tin cậy do backend lấy theo `session_id` và luôn cấp cho Plan Agent;
- system prompt chung của Plan Agent: quy tắc lập kế hoạch, JSON contract, giới hạn grid, nguyên tắc chọn template/widget/asset và quy tắc gọi tool;
- Domain Manifest;
- Template Catalog một tầng (`id`, `purpose`, `supports`, `domains`), được gửi trực tiếp;
- Widget Index ngắn (`widget_id`, `purpose`); Agent gọi `describe_widgets` để lấy
  contract chi tiết của widget cần dùng;
- Asset Catalog;
- grid contract;
- kết quả tool dữ liệu nếu có.

Plan Agent không cần prompt hội thoại hoặc giọng điệu riêng theo domain: nó chỉ tạo bố cục. Khả năng, widget, asset, template và tool được phép dùng của domain đều do `DomainManifest` và các catalog cung cấp. System prompt chung không chứa kiến thức hay luật riêng của Education/Weather.

Trách nhiệm:

1. Đọc intent, history và Domain Manifest để xác định panel cần thể hiện gì.
2. Quyết định có cần dữ liệu thật hay hành động nghiệp vụ không.
3. Nếu cần, gọi đúng tool được Domain Gateway cho phép; dùng kết quả đã kiểm chứng trong các bước sau.
4. Xác định có template sẵn phù hợp với intent và dữ liệu hiện có không.
5. Nếu có, trả quyết định `use_existing`.
6. Nếu không, chọn widget từ Widget Index, gọi `describe_widgets` và chỉ dùng
   widget sau khi đã nhận contract của nó.
7. Chọn asset được đăng ký khi props contract cho phép tham chiếu Asset Catalog.
8. Chỉ rõ block nào nằm ở những ô grid nào.
9. Gắn props hoặc data binding cho widget theo contract đã nhận.
10. Trả quyết định cho panel mới: chọn plan có sẵn hoặc tạo Presentation Plan mới theo schema.

Protocol native function calling của Plan Agent:

```text
Plan Agent
  → native function_call: describe_widgets(widget_ids)
  → Widget Registry lọc theo allowed_widget_ids của Domain Manifest
  → backend trả FunctionResponse chứa contract chi tiết của widget được phép
  → Plan Agent tiếp tục suy nghĩ
  → native function_call: call_capability(capability_id, arguments)
  → Domain Gateway kiểm tra capability có trong manifest rồi thực thi handler
  → Gateway trả DataBundle + catalog data alias
  → backend gửi FunctionResponse đúng call ID về Plan Agent
  → Plan Agent tiếp tục suy nghĩ với dữ liệu đó
  → có thể gọi thêm tool cùng domain khi thật sự cần
  → trả quyết định cuối cho panel mới: `use_existing_plan` hoặc `create_plan`
```

Plan Agent không gọi tool của domain khác và không tự truy cập database/API. Runtime hiện
giới hạn tối đa 4 tool call cho một lượt Plan Agent (`max_tool_steps`), và giá trị này là
policy cấu hình ở backend, có thể thay đổi mà không đổi contract.

Plan Agent chỉ chạy sau khi Gemini Live đã yêu cầu panel mới/thay panel, nên không có quyết định giữ panel hiện tại. Quyết định cuối chỉ chọn cách có được plan cho panel mới:

```json
{
  "decision": "use_existing_plan",
  "template_id": "two_subject_comparison"
}
```

hoặc:

```json
{
  "decision": "create_plan",
  "plan": {
    "blocks": []
  }
}
```

Ví dụ:

```json
{
  "decision": "create_plan",
  "plan": {
    "blocks": [
    {
      "widget_id": "text",
      "grid": { "col": 1, "row": 1, "col_span": 12, "row_span": 1 },
      "props": { "content": "Cùng tìm hiểu chó và mèo" }
    },
    {
      "widget_id": "image",
      "grid": { "col": 1, "row": 3, "col_span": 5, "row_span": 5 },
      "props": { "asset_id": "dog", "label": "Chó" }
    },
    {
      "widget_id": "image",
      "grid": { "col": 7, "row": 3, "col_span": 5, "row_span": 5 },
      "props": { "asset_id": "cat", "label": "Mèo" }
    }
    ]
  }
}
```

## 9. Xây IR Compiler / Materializer

Compiler nhận hai đầu vào tách biệt:

- `PresentationPlan`: bố cục, widget, asset ID, props tĩnh và data alias ngắn do Plan Agent chọn, ví dụ `$temp`, `$days`, `$left`;
- `DataBundle`: dữ liệu tin cậy của đúng request hiện tại. Backend tạo bundle này từ kết quả các tool mà Plan Agent đã gọi qua Domain Gateway, đồng thời công bố catalog alias ngắn cho Plan Agent. Compiler không tự gọi database, API hay tool.

Ví dụ tool có thể công bố catalog dữ liệu ngắn gọn:

```json
[
  { "key": "temp", "description": "Nhiệt độ hiện tại, °C" },
  { "key": "humidity", "description": "Độ ẩm hiện tại, %" },
  { "key": "days", "description": "Danh sách ngày dự báo" }
]
```

Plan Agent có thể yêu cầu bind dữ liệu đã lấy bằng alias:

```json
{
  "widget_id": "metric",
  "grid": { "col": 1, "row": 1, "col_span": 4, "row_span": 2 },
  "props": {
    "label": "Nhiệt độ",
    "value": "$temp"
  }
}
```

`$temp` phải có trong catalog alias và `DataBundle`; nếu không tồn tại hoặc sai kiểu, compiler từ chối plan thay vì tự suy đoán hay gọi thêm dữ liệu. Đường dẫn dữ liệu dài, nếu có, là chi tiết riêng bên trong compiler và không xuất hiện trong output Plan Agent. Với panel không gọi tool, Plan Agent có thể dùng props tĩnh hợp lệ, ví dụ tiêu đề hoặc `asset_id` đã chọn từ catalog.

Sau đó compiler:

1. Validate layout/widget/asset.
2. Resolve mọi data alias từ `DataBundle` thành giá trị thật.
3. Mở rộng block lặp khi cần, dựa trên một mảng có trong `DataBundle`.
4. Sinh `anchor_id`.
5. Tạo map `anchor_id → target_id → allowed_effects`.
6. Tạo `PanelIR` hoàn chỉnh chứa dữ liệu đã materialize.

Compiler không tự chọn bố cục, asset hoặc nội dung.

## 10. Xây Panel Renderer chung

```text
PanelIR → CSS Grid 16×10 → widget renderer → DOM có data-anchor-id
```

Thiết kế đẹp và đồng nhất nằm trong design system/widget CSS: typography, màu, spacing, shadow, responsive và animation. Plan Agent không sinh CSS/HTML.

## 11. Xây ASCII Renderer chung

```text
PanelIR → ASCII renderer → VISUAL STAGE MAP
```

ASCII map và UI cùng đọc PanelIR. Vì vậy dữ liệu, thứ tự bố cục và anchor luôn đồng nhất.

## 12. Nối Gemini Live Presentation

Tận dụng nguyên vẹn luồng presentation hiện có. Framework mới chỉ thay nguồn tạo panel thành `PanelIR`; không thiết kế lại Gemini Live, `present_visual`, đồng bộ audio hoặc animation.

Sau khi PanelIR được render, Gemini Live nhận:

- core prompt;
- prompt domain;
- VISUAL STAGE MAP;
- danh sách effect hợp lệ;
- trạng thái tương tác nếu có.

Gemini chỉ gọi:

```json
{
  "anchor_id": "a",
  "effect_id": "highlight"
}
```

Backend validate qua PanelIR rồi giữ luồng cue → PCM → frontend hiện có.

Luồng được giữ nguyên:

```text
Gemini gọi present_visual(anchor_id, effect_id)
→ backend validate anchor/effect bằng PanelIR đang active
→ backend trả tool response cho Gemini và giữ visual marker
→ marker được đính vào PCM tiếp theo của lượt đó
→ frontend schedule PCM trong AudioContext
→ effect chạy tại thời điểm PCM bắt đầu phát
```

## 13. Quản lý panel đang hoạt động

Lịch sử hội thoại giúp Gemini hiểu ngữ nghĩa của câu hỏi tiếp nối, nhưng backend vẫn cần một nguồn chân lý về panel thật đang hiển thị để validate `present_visual`. Không suy luận lại panel, anchor hoặc effect từ history.

Lưu `ActivePanelState` tối thiểu theo session:

- `panel_ir`: đã gồm block/widget đang render, domain, dữ liệu đã materialize, anchor map và effect hợp lệ;
- `revision`: phiên bản panel, chống PCM/cue/anchor cũ áp dụng vào panel mới.

Backend không quản lý trạng thái sư phạm như trẻ đúng/sai, số lần trả lời sai hay quyền công bố đáp án. Gemini Live tự suy luận các trạng thái này từ history và prompt; nếu Gemini chọn effect hiện nội dung, frontend thực hiện theo effect được PanelIR cho phép.

Khi người dùng hỏi tiếp về panel hiện tại, Gemini Live chỉ dùng stage map và `present_visual`; không gọi `route_request`, Plan Agent, compiler hoặc renderer. Chỉ khi Gemini Live gọi `route_request` và Plan Agent trả plan hợp lệ cho panel mới, compiler và renderer mới thay PanelIR hiện tại.

## 14. Kiểm thử POC bằng Education

Ba tình huống kiểm chứng framework chung:

1. “Cho bé xem con chó.”
2. “Dạy bé phân biệt chó và mèo.”
3. “Cho bé làm phép cộng bằng hình chó.”

Kiểm tra cho từng tình huống:

- Plan JSON hợp lệ;
- compiler từ chối layout/asset/widget không hợp lệ;
- UI và ASCII map có cùng block/anchor;
- Gemini gọi đúng anchor trước lời nói;
- câu hỏi tiếp nối không tạo panel mới khi không cần.

## 15. Mở rộng domain sau POC

Domain mới chỉ cần bổ sung:

- Domain Manifest;
- asset catalog;
- widget đặc thù nếu thực sự cần;
- tool/capability dữ liệu;
- template catalog;
- prompt domain.

Không phải sửa Gemini Live transport, router, Plan Agent core, compiler, renderer, ASCII renderer hay animation pipeline.
