# Tracking — Plan Agent: Hiểu nhu cầu, thiết kế hoạt động, dùng tool rồi mới tạo Surface

## Trạng thái

- Trạng thái: **thiết kế đã chốt, chưa triển khai checkpoint mới**.
- File này thay thế `tracking_web_search_capability.md` đã bỏ.
- Phạm vi: định nghĩa lại vòng lập của Plan Agent theo tư tưởng application-first.
- Không thay thế SurfaceDocument, Compiler, Stage Map, Gemini Live hay luồng panel/state đang chạy. Các phần đó là runtime đích nhận output từ Plan Agent.
- Mỗi checkpoint chỉ được code sau khi người dùng đồng ý checkpoint đó.

---

## 1. Vấn đề cần giải quyết

Plan Agent hiện không được phép chỉ làm việc theo phản xạ:

```text
Intent -> chọn template gần giống -> điền bindings -> render.
```

Cách đó dẫn tới các lỗi:

- template chỉ giống bố cục/asset nhưng không đúng mechanics;
- Agent xếp widget trước khi biết đủ dữ liệu hay chưa;
- Agent dựng interaction chưa có runtime hỗ trợ;
- facts/ảnh bị bịa hoặc không có nguồn;
- UI có button/card trông có vẻ tương tác nhưng không chạy thật.

Luồng mới buộc Plan Agent thiết kế một hoạt động/app khả thi trước, xác định dữ liệu và khả năng runtime cần có, gọi tool để bù phần thiếu, rồi mới quyết định reuse/patch/create surface.

---

## 2. Kiến trúc đích

```text
Trẻ <-> Gemini Live
          |
          | Chỉ cần lời nói / giải thích không cần UI
          +--> Gemini Live tự xử lý
          |
          | Cần tạo hoặc sửa trải nghiệm UI
          +--> route_request(intent, domain_id)
                    |
                    v
                Plan Agent loop
                    |
                    |  1. hiểu nhu cầu
                    |  2. chọn activity/application concept
                    |  3. lập data + interaction requirements
                    |  4. kiểm kê runtime/catalog/template
                    |  5. gọi capability cần thiết
                    |  6. kiểm tra khả thi
                    |  7. use template / patch / create
                    v
                Compiler + post-processors
                    |
                    v
                SurfaceDocument
                  |             |
                  |             +--> Stage Map/ASCII --> Gemini Live
                  v
                Browser render + interaction events
```

---

## 3. Ba thành phần áp dụng từ paper vào Lumi

Paper đặt hệ thống trên ba thành phần: server có tools, system instructions và post-processors. Trong Lumi, chúng tương ứng như sau.

| Thành phần trong paper | Thành phần tương ứng trong Lumi | Vai trò |
|---|---|---|
| Server + tool endpoints | FastAPI/backend, Gemini Live orchestration, DomainGateway, capability handlers, Compiler, WebSocket panel/interaction | Cung cấp năng lực mà Agent có thể gọi và điều phối dữ liệu/runtime. |
| System instructions | Prompt Gemini Live, universal Plan Agent prompt, domain prompt, tool/widget contracts, few-shot | Dạy Agent hiểu nhu cầu, chọn hoạt động, dùng tools và trả output đúng. |
| Post-processors | Compiler validation, materialization, browser diagnostics, structured feedback loop | Kiểm chứng/sửa lỗi theo code thay vì tin prompt tuyệt đối. |

### 3.1. Server và tools trong Lumi

Server không chỉ là Web Search. Web Search chỉ là một capability trong lớp backend.

```text
Gemini Live tools
- route_request
- present_visual
- update_surface_state
- delete_surface

Plan Agent capability tools
- describe_widgets
- describe_template
- search_image                     [dự kiến]
- search_web                       [dự kiến]
- generate_image                   [tương lai]
- read_web_result                  [tương lai]

Runtime/browser endpoints
- WebSocket gửi SurfaceDocument/panel state
- WebSocket nhận click/select/flip/drag-drop
- API/route phục vụ asset và browser diagnostics
```

`call_capability` là native function chung. `capability_id` xác định tool nghiệp vụ thực sự mà Plan Agent chọn, ví dụ `search_image` hoặc `search_web`.

### 3.2. System instructions trong Lumi

System instructions không phải chỉ một prompt. Nó có ba tầng:

```text
Gemini Live presentation instruction
- Lumi nói/giảng như nào.
- Khi nào present_visual, update state, delete surface.
- Cách phản hồi panel interaction của trẻ.

Universal Plan Agent instruction
- Hiểu nhu cầu -> chọn activity/application concept -> requirements.
- Kiểm kê catalog/widget/runtime/template.
- Gọi tool, loop theo verified data, feasibility check.
- Chỉ sau đó mới use_template/create/patch.

Domain + tool/widget policy
- Education: mục tiêu học tập, độ tuổi, mechanics phù hợp.
- Weather/news: freshness, source, location.
- Capability dùng khi nào; widget props/state/actions/children/anchors.
- Khi nào bắt buộc describe_widgets/describe_template.
```

### 3.3. Post-processors trong Lumi

Post-processors là code hậu kiểm/hậu xử lý output sau Agent. Chúng không tự ý đổi activity, nhưng chặn lỗi và trả feedback cấu trúc.

```text
Plan Agent trả Surface Plan
  -> static Compiler validation
       - widget/props/grid/overlap;
       - asset/result ID, URL raw, TTL/type;
       - action/state/runtime support;
       - template bindings/slots.
  -> materialization
       - asset_id -> URL + caption;
       - remote image result -> URL + caption + source;
       - verified web text -> text/source metadata.
  -> SurfaceDocument
  -> browser runtime diagnostics
       - image load failure;
       - render/widget exception;
       - invalid interaction event;
       - state update failure;
       - text overflow/layout error.
  -> structured feedback to Plan Agent when repair is needed
```

Ví dụ feedback:

```json
{
  "error_type": "unsupported_interaction",
  "location": "anchor b",
  "observed": "drag_drop is not registered",
  "allowed_alternatives": ["select", "reveal", "flip"]
}
```

---

## 4. Phân vai

### 3.1. Gemini Live

- Đối thoại với trẻ/người dùng bằng voice hoặc text.
- Quyết định khi nào cần UI bằng `route_request`.
- Sau khi panel có mặt: giảng bài, gọi `present_visual`, `update_surface_state`, `delete_surface` và nhận event interaction đã backend xác minh.
- Không tự tạo layout, URL asset, dữ kiện web hoặc state UI ngoài tool contract.

### 3.2. Plan Agent

- Là người thiết kế trải nghiệm và lập kế hoạch dữ liệu.
- Có thể gọi capability nhiều vòng trước output cuối.
- Chỉ trả một quyết định cuối: dùng template, patch surface hoặc create surface.
- Không render trực tiếp, không tự cấp anchor/component ID, không bypass Compiler.

### 3.3. Backend capability layer

- Expose các capability nghiệp vụ rõ nghĩa qua `call_capability`.
- Nhận query nguyên văn Agent đưa, gọi provider phù hợp và trả `verified_data` có schema.
- Không quyết định hoạt động, không tự quyết “đã đủ dữ liệu”, không tự tạo bài học.

### 3.4. Compiler + post-processors

- Validate output Plan Agent và materialize SurfaceDocument.
- Chặn asset/result ID/URL/state/action không hợp lệ.
- Trả compiler feedback có cấu trúc để Agent sửa trong vòng kế tiếp.
- Không âm thầm đổi activity hoặc thay bố cục Agent đã chọn.

### 3.5. Browser runtime

- Render đúng SurfaceDocument.
- Phát interaction event thật.
- Báo lỗi render/asset/state về backend theo schema.
- Không chứa đáp án bí mật hay logic nghiệp vụ do Agent tự bịa.

---

## 5. Input Plan Agent nhận mỗi lượt

```text
Required:
- intent từ route_request;
- domain_id;
- hội thoại liên quan;
- Asset Catalog hiện có;
- Template Catalog tóm tắt;
- Widget Index;
- capability index của domain;
- verified_data đã nhận trong loop hiện tại.

Conditional:
- Active SurfaceDocument + revision khi đang có panel;
- active surface summary/Stage Map khi cần patch/tiếp tục hoạt động;
- compiler feedback của lần thử trước;
- contract chi tiết từ describe_widgets / describe_template khi Agent yêu cầu.
```

Asset Catalog nhỏ vẫn gửi toàn bộ như hiện tại. Khi catalog lớn mới cân nhắc capability search nội bộ riêng; điều đó không thuộc checkpoint hiện tại.

---

## 6. Hai lớp bắt buộc trong cùng một Plan Agent

Plan Agent là **một Agent duy nhất**, một context liên tục và một vòng tool calling. Tuy nhiên, nó có hai lớp logic với ranh giới bắt buộc.

```text
Phase A — Internal Data / Activity Plan
  -> hiểu nhu cầu;
  -> chọn activity/application concept;
  -> xác định interaction, data, image, logic requirements;
  -> kiểm kê asset/widget/runtime/template/surface hiện có;
  -> gọi tool và tích luỹ verified_data;
  -> loại mechanic không khả thi;
  -> quyết định ready_for_surface hay chưa.

Phase B — Final Surface Plan
  -> nhận toàn bộ context và kết luận của Phase A;
  -> use_template / patch_surface_plan / create_surface_plan;
  -> sinh widgets, layout, props, initial state và bindings.
```

### 6.1. Tại sao không tách thành hai Agent

Phase B cần toàn bộ context của Phase A:

```text
- lý do chọn activity;
- mục tiêu học tập/interaction;
- requirements đã giữ hoặc đã bỏ;
- capability/runtime đã xác minh;
- facts, assets, image result IDs đã verified;
- tiêu chí semantic để chọn template hoặc patch surface;
- compiler feedback nhận trong loop.
```

Hai Agent độc lập sẽ buộc phải truyền một bản tóm tắt dễ mất dữ liệu hoặc bịa lại ngữ cảnh. Vì vậy hai lớp cùng nằm trong một Plan Agent và dùng cùng PlanAgentRequest/verified_data/tool history.

### 6.2. Ranh giới output bắt buộc

Phase A là planning state tạm:

```text
- không gửi frontend;
- không phải SurfaceDocument;
- không phải template;
- không phải một JSON output mới cho browser;
- không cần Gemini Live nhìn thấy.
```

Trong Phase A, output hợp lệ của Agent chỉ là:

```text
- call_capability(...);
- describe_widgets(...);
- describe_template(...);
- hoặc call capability/describe khác đã được domain cho phép.
```

`use_template`, `create_surface_plan` và `patch_surface_plan` chỉ hợp lệ khi Phase A đạt `ready_for_surface`.

### 6.3. Điều kiện `ready_for_surface`

Trước khi chuyển sang Phase B, Agent phải chốt nội bộ:

```text
1. Đã hiểu intent, history và quan hệ với surface hiện tại.
2. Đã chọn activity/application concept phù hợp.
3. Đã xác định interaction và state cần thiết.
4. Đã kiểm tra widget/runtime hỗ trợ hoặc đã loại mechanic không hỗ trợ.
5. Đã có đủ asset, facts, ảnh và verified data cần cho activity đã chọn.
6. Đã biết surface sẽ dùng template, patch hay create và lý do semantic.
```

Nếu một điều kiện chưa đạt, Agent phải ở Phase A và tiếp tục tool loop, đổi activity hoặc giảm scope; không được trả Final Surface Plan để “thử render xem sao”.

### 6.4. Ba lớp bảo đảm chất lượng

```text
1. System instruction
   Ép thứ tự Phase A -> Phase B và cấm final output trước ready_for_surface.

2. Tool loop protocol
   Mỗi vòng Agent chỉ gọi tool tiếp hoặc, khi đủ, trả Final Surface Plan.
   Backend giữ verified_data qua các vòng nhưng không tự quyết đủ/chưa đủ.

3. Compiler/runtime validation
   Nếu Final Surface Plan dùng asset/result/widget/action chưa verified/hỗ trợ,
   Compiler từ chối và feedback để Agent quay lại Phase A.
```

---

## 7. Vòng suy nghĩ bắt buộc của Plan Agent

Các bước dưới đây là reasoning nội bộ của Agent. Không gửi `ApplicationConcept` xuống frontend và không coi nó là Surface Plan.

### Bước 1 — Hiểu nhu cầu

Agent phân tích intent, history và surface hiện tại:

```text
- Người dùng thực sự muốn đạt điều gì?
- Đây là yêu cầu chỉ cần trả lời, một thao tác, visual explanation,
  hay một hoạt động/app tương tác?
- Domain, độ tuổi, ngôn ngữ và mục tiêu học tập là gì?
- Đây là hoạt động mới hay lượt tiếp nối surface hiện tại?
- Có yêu cầu về entity thật, fact, số liệu, thời gian hoặc thông tin mới không?
- Có điểm mơ hồ nào buộc phải hỏi lại không?
```

Không được nhảy ngay từ keywords sang template.

### Bước 2 — Quyết định Activity/Application Concept

Agent chọn trải nghiệm phù hợp nhất với nhu cầu đã hiểu:

```text
Possible modes:
- visual_explanation: panel minh hoạ;
- guided_lesson: bài giảng theo bước;
- recognition_activity: chạm/chọn nhận biết;
- quiz: chọn/nhập đáp án;
- flashcard_activity: lật thẻ;
- simulation_or_game: mô phỏng/game, chỉ khi runtime có mechanics thật;
- domain_utility: weather/news/map/timeline/... .
```

`no_ui` không phải mode hay output của Plan Agent. Gemini Live tự quyết không
gọi `route_request` nếu lượt đó chỉ cần lời nói/giảng giải. Một khi đã route
sang Plan Agent, Agent phải kết thúc bằng một Surface Plan hợp lệ hoặc trả lỗi
khả thi có cấu trúc để Gemini Live xử lý; không có quyết định `no_surface`.

Agent xác định mục tiêu, thứ trẻ phải làm hoặc quan sát, và interaction cốt lõi. Template chưa được chọn ở bước này.

### Bước 3 — Lập requirements

Sau khi có concept, Agent xác định:

```text
Interaction requirements:
- Người dùng cần chạm, chọn, lật, reveal, kéo-thả, nhập đáp án hay chỉ quan sát?
- State nào phải thay đổi: visibility, flipped, selected, feedback, progress...?

Data requirements:
- Fact nào cần dữ liệu có nguồn?
- Fact nào có thể thay đổi theo thời gian?
- Asset nội bộ nào cần?
- Ảnh thật/cụ thể hay minh hoạ sáng tạo nào cần?

UI requirements:
- Widget nào cần?
- Widget nào cần chứa widget con?
- Layout có cần surface mới, hay patch surface hiện tại là đủ?
```

### Bước 4 — Kiểm kê những gì hệ thống thật sự có

Agent kiểm tra, theo thứ tự:

```text
1. Asset Catalog: có asset đúng mục tiêu chưa?
2. Widget Index: widget nào có thể đáp ứng interaction/UI requirements?
3. Widget contract: props, state, action, children, anchors, effects hợp lệ là gì?
4. Runtime: frontend/backend đã thực thi action và state transition đó chưa?
5. Active surface: có thể patch một cách đúng mechanic không?
6. Template catalog: có template nào khớp toàn bộ mechanics + slots + layout không?
```

Nếu muốn dùng widget mà Index chưa đủ contract, Agent phải gọi `describe_widgets(widget_ids)` trước khi tạo final plan.

### Bước 5 — Bù phần thiếu bằng capability cụ thể

Agent chỉ gọi tool sau khi biết thiếu gì:

```text
Thiếu kiến thức/fact có nguồn       -> search_web(query)
Thiếu ảnh thật/cụ thể               -> search_image(query)
Thiếu ảnh sáng tạo/phong cách chung -> generate_image(prompt) [tương lai]
Web result chưa đủ nội dung         -> read_web_result(result_id) [tương lai]
Thiếu widget contract               -> describe_widgets(widget_ids)
Thiếu template detail               -> describe_template(template_id)
```

Backend chuyển nguyên `query`/`prompt` Agent viết tới provider tương ứng; backend không cố đoán Agent cần ảnh hay text từ câu query.

### Bước 6 — Loop dựa vào tool response

Sau mỗi function response, Agent quay lại requirements:

```text
- Kết quả có đáp ứng mục tiêu không?
- Vẫn thiếu dữ liệu nào?
- Query tiếp theo có cần dựa vào title/result/fact vừa nhận không?
- Có capability khác phù hợp hơn không?
- Có phải giảm scope hoặc đổi activity vì runtime/data không đáp ứng không?
```

Backend không tự retry, không tự đánh giá kết quả “đủ”. Agent tự quyết định tiếp tục hay trả output cuối. Runtime có giới hạn tool steps hữu hạn để chống loop; giới hạn cụ thể được chốt ở checkpoint triển khai.

### Bước 7 — Kiểm tra khả thi và loại phần không làm được

Trước khi chọn template/create/patch:

```text
- Data requirements đã đủ chưa?
- Widget/action/state cần thiết có contract và runtime support chưa?
- Interaction có hoạt động thật từ browser -> backend -> Gemini Live không?
- Có surface/template nào thực sự tái sử dụng được không?
```

Nếu chưa hỗ trợ một mechanic, Agent phải bỏ/đổi mechanic đó. Agent không được tạo:

```text
- button/card có vẻ click được nhưng không có event;
- flashcard trông lật được nhưng không có flip action;
- drag/drop giả;
- placeholder data, dummy text hay fact bịa;
- hình/URL/result ID không từ verified data.
```

### Bước 8 — Chọn cách tạo UI

Chỉ sau các bước trên Agent mới chọn đúng một đường:

```text
A. use_template
   Khi template khớp toàn bộ activity mechanics, interaction, slots data,
   widget contract và layout cần thiết.

B. patch_surface_plan
   Khi surface hiện tại vẫn đúng activity mechanics; thay đổi cấu trúc/props/state
   bằng operation hợp lệ là đủ.

C. create_surface_plan
   Khi không có template/surface hiện tại nào đáp ứng đúng requirements.
```

Surface mới tốt có thể được Template Curator/trích template spec sau đó. Template không lưu URL hoặc result ID web tạm.

---

## 8. Ví dụ hoàn chỉnh — vòng đời bướm

### 6.1. User request

```text
“Dạy con về vòng đời của bướm.”
```

### 6.2. Application Concept nội bộ

```text
Application concept:
Hoạt động quan sát tuần tự vòng đời bướm.

Interaction:
Trẻ chạm từng giai đoạn để xem mô tả.

Learning goal:
Trẻ nhận biết thứ tự trứng -> sâu -> nhộng -> bướm.

Data needs:
- Các giai đoạn thật của vòng đời bướm.
- Ảnh/minh hoạ cho từng giai đoạn.
- Nội dung ngắn phù hợp trẻ em.

Runtime feasibility:
- Có widget choice/flashcard/text/image?
- Có state selected/reveal/flipped?
- Click choice có gửi event thật không?
- Nếu chưa có interaction chạm từng giai đoạn thật sự,
  không dựng nút giả; giảm hoạt động xuống quan sát + reveal.
```

### 6.3. Inventory và tool calls

```text
Asset Catalog:
- Không có ảnh vòng đời bướm.

Widget check:
- Có text, image, choice và select event.
- Chưa có widget sequence/timeline chuyên biệt.

Data actions:
1. search_web("vòng đời của bướm các giai đoạn cho trẻ em")
2. search_image("trứng sâu bướm nhộng bướm minh hoạ giáo dục")
3. Nếu một ảnh không đủ cho 4 giai đoạn:
   search_image bằng query cụ thể hơn, hoặc giảm hoạt động thành một minh hoạ
   + reveal, tùy dữ liệu thực nhận.
```

### 6.4. Feasibility outcome A — đủ capability

```text
- Có 4 ảnh hợp lệ và dữ kiện có nguồn.
- choice hoạt động thật.
- Agent create/reuse surface gồm bốn choice theo thứ tự.
- Mỗi choice chứa image + text ngắn.
- Khi trẻ chạm, browser gửi anchor/action select;
  backend validate; Gemini Live nhận event và tiếp tục giảng.
```

### 6.5. Feasibility outcome B — runtime chưa đủ

```text
- Chỉ có một ảnh hợp lệ, hoặc chưa có choice/select support.
- Agent không tạo bốn thẻ giả.
- Agent tạo visual explanation: ảnh + các text stage,
  hoặc ảnh + reveal các bước theo lời Gemini Live.
```

---

## 9. Tool catalogue đích

Capability nghiệp vụ domain gọi qua native function chung:

```json
{
  "capability_id": "<capability_id>",
  "arguments": { }
}
```

Hai công cụ khám phá contract là native tool riêng, không phải capability
domain và không đi qua `call_capability`:

```text
describe_widgets(widget_ids)
describe_template(template_id)
```

### 7.1. `search_image`

```json
{
  "capability_id": "search_image",
  "arguments": {
    "query": "con heo dễ thương cho trẻ em"
  }
}
```

Output thành công:

```json
{
  "image_result": {
    "result_id": "img_a1b2c3",
    "caption": "Cute pig illustration"
  }
}
```

- Backend gửi nguyên query tới Image Search Provider.
- Một call trả một ảnh đứng đầu theo ranking provider.
- `caption` là nguyên văn provider trả; không thêm “theo tìm kiếm”, không suy luận từ query.
- `remote_url` và `source_url` chỉ nằm ở SearchResultStore/Compiler, không ở
  Plan Agent context.

### 7.2. `search_web`

```json
{
  "capability_id": "search_web",
  "arguments": {
    "query": "vòng đời của bướm cho trẻ em"
  }
}
```

Output thành công:

```json
{
  "web_result": {
    "result_id": "web_d4e5f6",
    "title": "Vòng đời của bướm",
    "snippet": "...",
    "source_url": "https://example.org/page"
  }
}
```

- Dùng cho entities, facts, số liệu, sự kiện và dữ liệu có thể thay đổi.
- Với factual/current content, system instruction phải yêu cầu search trước khi hiển thị claim.
- `title`, `snippet` và `source_url` là các trường provider trả về nguyên văn
  sau khi backend parse schema; backend không tự tóm tắt, dịch, diễn giải hay
  thêm fact. Tên `snippet` được dùng thay cho `summary` để không ngụ ý backend
  đã tạo bản tóm tắt.
- Nếu snippet chưa đủ, Agent search tiếp hoặc dùng `read_web_result` sau này.

### 7.3. Capability dự kiến sau bản đầu

```text
generate_image(prompt)       Minh hoạ sáng tạo, nhất quán phong cách.
read_web_result(result_id)   Đọc sâu nguồn đã search, không nhận raw URL.
search_news(query)           Tin tức, source/freshness policy riêng.
weather/location/map tools   Domain capability chuyên biệt, không dùng web search chung.
```

---

## 10. SearchResultStore và nguồn dữ liệu web

Backend giữ kết quả tạm thời, không thêm vào Asset Catalog:

```json
{
  "img_a1b2c3": {
    "type": "image",
    "remote_url": "https://cdn.example.org/pig.jpg",
    "source_url": "https://example.org/page",
    "caption": "Cute pig illustration",
    "expires_at": "..."
  },
  "web_d4e5f6": {
    "type": "web",
    "title": "Vòng đời của bướm",
    "snippet": "...",
    "source_url": "https://example.org/page",
    "expires_at": "..."
  }
}
```

Quy tắc:

- ID ngẫu nhiên, TTL hữu hạn; bản đầu có thể lưu RAM.
- Không tải asset/tài liệu về project.
- Compiler lookup ID và type trước khi materialize.
- ID giả, sai type, hết TTL -> compiler feedback; Agent có thể search lại.
- Plan Agent không nhận raw image URL và không tự bịa data web.

---

## 11. Surface Plan, Compiler và Stage Map

### 9.1. Plan dùng web image

```json
{
  "widget_id": "image",
  "grid": { "col": 3, "row": 3, "col_span": 5, "row_span": 5 },
  "props": {
    "remote_image_result_id": "img_a1b2c3"
  }
}
```

Compiler chỉ chấp nhận một nguồn ảnh:

```text
asset_id                    Asset Catalog nội bộ.
remote_image_result_id      SearchResultStore type=image còn TTL.
```

Raw `url`, `src`, `remote_url` trong Surface Plan bị từ chối.

### 9.2. SurfaceDocument đã materialize

```json
{
  "id": "2",
  "type": "image",
  "layout": { "col": 3, "row": 3, "col_span": 5, "row_span": 5 },
  "props": {
    "source": {
      "kind": "remote",
      "url": "https://cdn.example.org/pig.jpg",
      "caption": "Cute pig illustration",
      "source_url": "https://example.org/page"
    }
  },
  "state": { "visibility": "visible" }
}
```

Browser render và Stage Map cùng đọc SurfaceDocument, nên Gemini Live thấy mô tả đúng panel thật.

### 9.3. Stage Map policy

```text
text                 -> props.content đang render
asset image          -> Asset Catalog.caption
remote image         -> SurfaceDocument.props.source.caption
object_group         -> asset caption + count
answer/number        -> props.value + visibility thật
choice               -> children thật sự render
flashcard            -> mặt đang render theo state.flipped
```

Ví dụ ảnh web:

```text
ẢNH: Cute pig illustration
[anchor: b]
```

Stage Map không lấy mô tả từ intent, tool query, URL hay câu tự tạo của Plan Agent.

---

## 12. System instruction đích cho Plan Agent

System instruction được tách ba tầng.

### 10.1. Universal planning policy

```text
- Luôn hiểu yêu cầu và lịch sử trước khi xem template.
- Chọn activity/application concept trước khi thiết kế layout.
- Lập interaction/data/UI requirements trước khi gọi tool.
- Kiểm tra catalog, widget contracts, runtime support và active surface.
- Chỉ gọi capability cụ thể cho phần đang thiếu.
- Sau mỗi response, tự đánh giá đủ/chưa đủ và có thể search tiếp.
- Không placeholder, fake interaction, data bịa, raw URL hoặc fact không có nguồn.
- Chỉ use_template/create/patch khi activity khả thi và data đủ.
```

### 10.2. Domain policy

```text
education:
- phù hợp độ tuổi, một mục tiêu học tập rõ;
- ưu tiên interaction đơn giản, có phản hồi;
- fact thật cần source; bài tập tự tạo không cần web nếu không nêu fact.

weather/news/map:
- freshness/source/location là bắt buộc;
- ưu tiên domain capability chuyên biệt hơn generic web search.
```

### 10.3. Tool/widget contract policy

```text
- Khi nào dùng từng capability và output có nghĩa gì.
- Props/state/actions/children/anchors/effects của widget.
- describe_widgets bắt buộc trước khi dùng contract chưa biết.
- Compiler feedback là dữ kiện sửa plan, không được bỏ qua.
```

---

## 13. Post-processors theo tinh thần paper, áp dụng cho Lumi

### PP1 — Static Compiler validation trước render

```text
- widget/props/grid/overlap;
- asset/result ID/type/TTL;
- action/state transition hợp lệ;
- raw URL và dữ liệu không verified;
- interaction chưa có runtime support;
- template binding/slot contract.
```

### PP2 — Materialization validation

```text
- asset ID -> URL + caption;
- remote image ID -> URL + caption + source URL;
- web snippet/content đã verified -> text/source metadata;
- không materialize data thiếu, hết hạn hoặc sai type.
```

### PP3 — Browser runtime checks sau render

```text
- image load failed/chặn/tải quá lâu;
- render/widget exception;
- invalid interaction event;
- state update không áp dụng;
- text overflow/layout thực tế.
```

### PP4 — Structured feedback loop

Không tự âm thầm sửa activity. Feedback tối thiểu phải có:

```json
{
  "error_type": "unsupported_interaction",
  "location": "anchor b",
  "observed": "drag_drop is not registered",
  "allowed_alternatives": ["select", "reveal", "flip"]
}
```

Plan Agent dùng feedback để đổi plan ở vòng kế tiếp. Nếu không có alternative, Agent phải giảm scope hoặc báo lại Gemini Live.

---

## 14. Template lifecycle

```text
Activity concept + requirements
  -> template matching (chỉ sau khi mechanics/data rõ)
  -> use template OR create surface
  -> surface chạy đúng
  -> Template Curator đánh giá có nên trích TemplateSpec không
```

Một TemplateSpec tái sử dụng phải mô tả:

```text
- purpose/activity type;
- mechanics và interaction requirements;
- required widgets/actions/state;
- slot IDs + semantic role + binding contract;
- layout/grid;
- immutable props;
- không chứa result ID/URL/data web tạm.
```

---

## 15. Checkpoint triển khai

### AF1 — Activity-first system instruction và output policy

- [x] Viết universal planning policy theo bước 1-8.
- [x] Chốt Phase A (Internal Data/Activity Plan) và Phase B (Final Surface Plan) trong cùng một Plan Agent/context.
- [x] Cấm final surface output trước điều kiện `ready_for_surface`.
- [x] Chốt tool-loop output hợp lệ trong Phase A và đường Compiler feedback quay lại Phase A.
- [x] Tách domain policy education khỏi base policy.
- [x] Sửa few-shot: concept/requirements/tool calls xảy ra trước template decision.
- [x] Template chỉ được reuse sau feasibility check.
- [x] Cập nhật tests prompt/decision nếu có.

**Hoàn thành khi:** Plan Agent không còn được prompt để chọn template ngay sau intent.

**Trạng thái:** Hoàn thành. AF1 chỉ thay đổi planning policy/few-shot và regression test;
không thêm capability, provider search, widget, Compiler rule hay frontend behavior.

### AF2 — Capability catalog và tool contracts

- [x] Chốt Search Provider: Brave Search API.
- [x] Chốt API key policy: chỉ runtime settings đọc `BRAVE_SEARCH_API_KEY`; agent/browser không thấy key, source code không log/copy key.
- [x] Chốt fixed policy: query nguyên văn trong `q`, `search_lang=vi`, `ui_lang=vi-VN`, `country=VN`, `safesearch=strict`, top-1 result.
- [x] Chốt timeout 8 giây và quota 20 request / Live session; quota được nối vào execution bằng context backend-only `session_id`.
- [x] Tạo Brave provider adapter với schema nội bộ web/image tách biệt và mock tests.
- [x] Đăng ký/describe capability index rõ `search_image`, `search_web` qua manifest Education và `DomainGateway`.
- [x] Tool schema chỉ nhận `query`; `search_web` trả title/snippet/source URL, `search_image` chỉ trả result ID/caption/source URL, không trả URL ảnh.
- [x] Test mock tool response, sai arguments, quota và bảo đảm URL ảnh chỉ ở SearchResultStore.

**Thực thi:** Plan Agent chỉ gọi native `call_capability` với capability ID + query. Runtime
đính `CapabilityExecutionContext(session_id=...)` ở backend; model không thấy/không gửi
session ID. Provider/client được tạo lúc capability chạy, vì vậy thiếu key hay lỗi provider
trở thành function response có cấu trúc để Agent có thể đổi query/thử lại, không làm app
khởi động thất bại.

**Hoàn thành khi:** Agent gọi đúng capability theo requirement, không dùng search gộp.

### AF3 — Retrieval loop và verified data context

- [x] Plan Agent nhận/giữ verified data qua nhiều function steps bằng journal append-only `verified_data.search_results`.
- [x] Chốt giới hạn 10 native tool calls / lượt Plan Agent, gồm `describe_widgets`, `describe_template` và `call_capability`.
- [x] Khi hết ngân sách, backend trả function response `tool_limit_reached`; Agent còn đúng một lượt để trả final plan. Nếu vẫn gọi tool, backend từ chối.
- [x] Agent có thể follow-up search từ function responses trước đó; mỗi web/image result được nối theo thứ tự thời gian, không ghi đè kết quả cũ.
- [x] Backend không đánh giá dữ liệu đã đủ hay chưa; chỉ giới hạn quota/tool budget, validate contract và đưa verified results/error trở lại Agent.

**Hoàn thành khi:** Agent tìm nhiều vòng rồi mới trả final plan khi cần.

**Đã kiểm thử:** hai capability liên tiếp tích luỹ web + image result; budget feedback vẫn cho final plan; call tiếp theo sau feedback bị từ chối; batch tool calls không được thực thi vượt ngân sách.

### AF4 — SearchResultStore và remote image references

- [x] Store RAM session-scoped cho image và web results; TTL 30 phút, purge khi đọc/ghi và xoá ngay khi Live session reset.
- [x] `remote_image_result_id` contract cho widget `image`; bắt buộc XOR với `asset_id`.
- [x] Compiler tra ID theo đúng session, validate TTL/type và materialize metadata `source` do Compiler sở hữu cho remote image; áp dụng cả image child trong container.
- [x] Không raw URL trong Plan Agent output: validator từ chối `url`/`source` và patch loại metadata Compiler-owned trước khi dựng lại plan.

**Hoàn thành khi:** UI plan dùng được ảnh web qua ID an toàn.

**Đã kiểm thử:** session khác, sai loại result, TTL hết hạn và reset session đều buộc search lại; Compiler chỉ materialize kết quả hợp lệ; raw URL và hai nguồn ảnh đồng thời bị từ chối.

### AF5 — Runtime-feasibility/no-placeholder enforcement

- [x] Widget Registry expose action/state support đủ để Agent kiểm kê.
- [x] Compiler reject interaction/action không hỗ trợ.
- [x] Prompt cấm fake card/button/drag/drop/flashcard.
- [x] Test unsupported mechanic -> compiler feedback -> Agent alternative.

**Hoàn thành khi:** Agent không thể tạo interaction trông chạy được nhưng runtime không có.

**Quyết định đã chốt:** mechanic thuộc widget, không thuộc Surface Plan. Registry
công bố `interactions`, `state_fields` và transition; Plan chỉ chọn widget cùng
props/layout/children/initial_state. Compiler từ chối field mechanic tự bịa trong
plan bằng `invalid_widget_contract`; Runtime từ chối browser action không thuộc
widget/anchor. Không thêm `required_interactions` hay một action schema song song.

**Đã kiểm thử:** một `image` tự khai báo `action: "flip"` bị Compiler phản hồi
có cấu trúc; lượt repair tiếp theo thay bằng `flashcard` đã đăng ký `flip` và route
hoàn thành.

### AF6 — Stage Map và runtime post-processors

- [x] Stage Map chỉ đọc SurfaceDocument materialized.
- [x] Image remote dùng caption provider nguyên văn.
- [x] Browser diagnostics cho asset/render/state/layout errors.
- [x] Structured compiler/runtime feedback tới Agent khi cần sửa plan.

**Hoàn thành khi:** Gemini Live nhìn map khớp browser và lỗi runtime có đường feedback rõ.

**Quyết định đã chốt:** với `image_load_failed`, browser thử lại đúng URL một lần;
chỉ lần lỗi tiếp theo mới tạo diagnostic repair. Các lỗi renderer/state/layout là lỗi
không thể tự hồi phục nên được gửi ngay.

**Luồng đã triển khai:** Browser gửi `runtime:diagnostic` chỉ với
`surface_id`, `revision`, `component_id`, `anchor_id`, `error_type`, observed scalar
data và `repair_scope: "surface_plan"`. Runtime chỉ nhận diagnostic khớp đúng
SurfaceDocument active; revision cũ, anchor/component sai, lỗi chưa được cho phép
hoặc payload tự do đều bị bỏ qua. Diagnostic hợp lệ trở thành `runtime_feedback`
cho Plan Agent; Agent tự chọn search/patch/create theo contract, còn backend không
tự thay ảnh hay bố cục. Kết quả repair được compile rồi mới gửi `panel_update`.

Sau `panel_update` repair, browser gửi xác nhận `runtime:repair_rendered` kèm đúng
surface/revision. Backend kiểm lại revision còn active rồi gửi Gemini Live event im
lặng `SURFACE_CONTEXT_UPDATE` với Stage Map/effects mới và `turn_complete=false`.
Event này không phải lời trẻ nói, không được tạo audio/tool call; nó chỉ thay map
Gemini dùng ở lượt sau. Vì vậy browser và Gemini luôn cùng revision sau repair.

**Stage Map:** renderer chỉ đọc `SurfaceDocument` cùng Widget Registry policy và
Asset Catalog. Với ảnh web đã materialize, map dùng duy nhất `props.source.caption`
của provider; không lộ query, URL, result ID hay tự thêm mô tả.

**Đã kiểm thử:** remote-caption map, diagnostic active repair, diagnostic revision
cũ bị bỏ qua, và `runtime_feedback` được serialize vào input Plan Agent.

### AF7 — Template lifecycle semantic

- [x] Template matching dùng mechanics/slots/contracts, không chỉ layout.
- [x] Template Curator/trích TemplateSpec sau surface mới chạy đúng.
- [x] Không lưu dữ liệu web tạm vào template.
- [x] E2E: template đúng -> reuse; template gần giống -> create/patch đúng hơn.

**Hoàn thành khi:** không còn reuse template sai mechanic như các lỗi trước.

**Đã triển khai:** `LayoutTemplate` có `semantic_spec` gồm `mechanics`, binding
`slots` và `component_contracts` (widget, children, initial state, interactions).
Spec được tạo từ Widget Registry khi Curator trích surface mới; Catalog và
`describe_template` đều đưa spec cho Plan Agent để so sánh semantic trước khi reuse.
Spec bị validate phải khớp chính xác blocks/bindings đã lưu, nên không thể quảng cáo
mechanic hoặc slot không có thật. Template cũ không có spec vẫn load được với spec
cấu trúc suy ra từ blocks; template mới luôn persist spec đầy đủ.

Template không còn được lưu ngay sau Compiler. `create_surface_plan` có
`template_description` trở thành candidate theo `surface_id` + `revision`; chỉ khi
browser gửi `surface:rendered` đúng revision sau stylesheet, image và layout đều
render thành công thì Runtime mới gọi Curator lưu `tmN`. Revision sai, surface đã đổi
hoặc diagnostic trước xác nhận thì candidate không được lưu. `remote_image_result_id`
được thay bằng binding placeholder; raw URL và ID kết quả theo session không xuất hiện
trong JSON template.

**Đã kiểm thử:** semantic spec choice/select và component tree; remote image result
không bị ghi vào template; Template Index/`describe_template` trả semantic spec;
candidate không lưu trước hoặc khi browser xác nhận sai revision, và lưu được sau xác
nhận đúng. Toàn bộ 126 test Python pass; `node --check` cho `app.js`/
`panel_renderer.js` và `compileall` pass.

### AF8 — End-to-end evaluation

- [ ] Asset có sẵn -> không web search.
- [ ] Thiếu ảnh -> `search_image` -> compile -> render -> map.
- [ ] Thiếu fact -> `search_web` -> data verified -> UI.
- [ ] Interaction chưa support -> Agent giảm scope, không fake UI.
- [ ] Follow-up panel -> Agent patch đúng surface/revision.
- [ ] Gemini Live present/update state/interaction khớp map và browser.

**Hoàn thành khi:** toàn bộ vòng từ voice/text đến surface, interaction và giảng bài chạy nhất quán.

---

## 16. Ngoài phạm vi hiện tại

- Implement `generate_image`, `read_web_result`, news/video/audio/map tools.
- Asset Catalog search/vector database.
- Vision model kiểm tra pixels ảnh web.
- Lưu ảnh/tài liệu web lâu dài vào Asset Catalog.
- Tự động đánh giá chất lượng pedagogy/UI bằng model riêng.

---

## 17. Ba nguyên tắc bắt buộc cần làm rõ trước khi triển khai

Phần này biến ba ý tưởng rút ra từ paper thành quy tắc có thể kiểm tra bằng
code. Chúng **chưa được triển khai** chỉ vì đã viết trong prompt; từng quy tắc
phải có nguồn dữ liệu, điểm validate và test tương ứng.

### 17.1. Không placeholder / không tương tác giả

Đây là nguyên tắc có giá trị cao nhất với Lumi. Một component trông có thể
tương tác chỉ được phép xuất hiện nếu toàn bộ chuỗi sau đã tồn tại thật:

```text
Widget contract khai báo action/state
  -> renderer frontend đã đăng ký và render vùng tương tác
  -> browser phát event có schema hợp lệ
  -> backend/runtime xác minh event trên SurfaceDocument
  -> state transition hoặc Gemini Live nhận event đã có đường xử lý thật.
```

`Widget Registry` là nguồn khai báo server-side cho phần đầu của chuỗi:

```text
widget_id, props, state mở đầu, allowed_actions, transition contract,
children, anchors và effects.
```

Nhưng Registry **không tự chứng minh browser đã làm được**. Khi đăng ký một
action tương tác, cần có integration check xác minh renderer của widget đó đã
đăng ký đúng action/event. Không có check này, Registry nói có `flip` nhưng
browser không có listener vẫn tạo ra UI giả.

Capability matrix không phải một bảng khai báo tay thứ tư. Nó phải được build
hoặc test từ ba nguồn có tên rõ ràng:

```text
Widget Registry          -> declared_actions / state transitions
Frontend widget registry -> implemented DOM event + state application
Backend interaction router -> accepted event schema + dispatch handler
```

Một `(widget_id, action)` chỉ là `supported` khi ba nguồn cùng xác nhận nó.

Quy tắc quyết định ở Phase A:

| Yêu cầu activity | Điều kiện được render | Nếu thiếu |
|---|---|---|
| “Bấm để nghe phát âm” | widget có action audio/play, frontend phát được audio, event/state cần thiết hoạt động | không dựng thẻ/nút; dùng lời Gemini Live hoặc bỏ mechanic |
| Flashcard lật | `flip` có trong contract, renderer đổi mặt theo `state.flipped`, event đi hết chuỗi | không dùng flashcard lật; dùng ảnh + text/reveal nếu có |
| Chọn đáp án | choice/select được đăng ký và backend nhận selection thật | không dựng choice có vẻ bấm được; chuyển sang hỏi bằng lời hoặc visual quan sát |
| Kéo-thả | drag source/drop target, payload validation, state transition đều có thật | không dựng trò kéo-thả; thay bằng select/reveal hoặc nói rõ chưa hỗ trợ |
| “Xem thêm” | có operation/state hoặc route thật mà click sẽ thực hiện | không dựng nút |

Compiler phải từ chối Final Surface Plan yêu cầu `action`, transition, hoặc
interactive widget mà capability matrix chưa xác nhận. Feedback trả về phải
cho Agent biết **cơ chế nào thiếu** và **những action thay thế nào đang có**;
Agent quay lại Phase A để đổi mechanic, không được tự render thử.

Ví dụ feedback:

```json
{
  "error_type": "unsupported_interaction",
  "component_type": "flashcard",
  "requested_action": "flip",
  "reason": "frontend renderer has no registered flip handler",
  "supported_alternatives": ["select", "reveal"]
}
```

### 17.2. Kết quả tool đi theo hai đường dữ liệu

Plan Agent luôn nhận phần dữ liệu cần để suy nghĩ. Browser chỉ nhận dữ liệu
đã được Compiler materialize và thực sự cần render. Không để Agent tự sao chép
URL vào plan, cũng không đẩy URL vào Stage Map.

```text
search_web(query)
  Provider -> SearchResultStore
  -> Plan Agent: result_id, title, snippet, source_url
  -> Agent chọn fact/nội dung ngắn cho text props
  -> Compiler: materialize text/source metadata vào SurfaceDocument
  -> Browser: chỉ render text mà plan đã chọn

search_image(query)
  Provider -> SearchResultStore giữ remote_url, source_url, caption
  -> Plan Agent: image_result.result_id + caption
  -> Agent: chỉ đặt remote_image_result_id trong Surface Plan
  -> Compiler: lookup ID, kiểm type/TTL, đưa URL thật vào SurfaceDocument
  -> Browser: render URL đã materialize
  -> Stage Map: chỉ đọc caption của source đã materialize.
```

Với `search_web`, `snippet` là dữ liệu provider trả về nguyên văn để Agent
chọn và viết nội dung UI; nó không tự xuất hiện trên màn hình nếu Final Surface
Plan không dùng nó. Backend chỉ parse/map response về schema và không tự tóm
tắt, dịch, diễn giải hoặc thêm fact. Nếu sau này `read_web_result` lấy nội
dung trang, nó cũng trả `content` nguyên văn theo extractor/provider; trường
`content_kind` phải nói rõ đó là `provider_extract` hay `source_html_text`.

Với `search_image`, Agent không có quyền tạo `url`, `src`, hay tự mô tả ảnh từ
query. `caption` trong Stage Map là caption/title/alt do provider trả về và đã
được SearchResultStore lưu cùng result; không có tiền tố “Kết quả tìm kiếm”
hoặc “Minh hoạ theo tìm kiếm”.

Mọi component text chứa fact từ web phải mang provenance không render, ví dụ
`source_result_ids: ["web_d4e5f6"]`. Compiler kiểm tra các ID này tồn tại,
thuộc loại web và còn TTL; SurfaceDocument giữ provenance để debug/feedback.
Stage Map chỉ mô tả text thực sự hiển thị, không đọc metadata nguồn.

Mỗi `result_id` chỉ là tham chiếu tạm thời, type-checked và TTL-checked. Nó
không thuộc Asset Catalog và không được ghi vào TemplateSpec.

### 17.3. Post-processors là một pipeline có đường quay lại rõ ràng

Prompt chỉ định hướng; post-processors phải kiểm soát những lỗi lặp lại mà
prompt không thể bảo đảm. Ba lớp có trách nhiệm khác nhau:

| Lớp | Chạy khi nào | Chặn/phát hiện gì | Hành động khi lỗi |
|---|---|---|---|
| Compiler | trước render | contract, props, grid, raw URL, asset/result ID, TTL, action/state và capability matrix | từ chối plan, gửi `compiler_feedback` cho Plan Agent ở Phase A |
| Browser runtime | sau render hoặc sau event | image load, renderer exception, event sai, state không áp dụng, overflow/layout thực tế | gửi `runtime_diagnostic` có surface/revision/component về backend |
| Feedback router | sau diagnostic cần sửa UI | chuẩn hoá lỗi và trạng thái thực nhận, chọn được operation/tool hợp lệ | gọi lại Plan Agent với `runtime_feedback`; không âm thầm đổi UI |

Schema feedback tối thiểu phải gắn được với document thực tế, nhưng không dựa
vào lời mô tả tự do:

```json
{
  "kind": "runtime_feedback",
  "surface_id": "panel-...",
  "revision": 4,
  "component_id": "component-7",
  "anchor_id": "b",
  "error_type": "image_load_failed",
  "observed": { "status": "network_error" },
  "repair_scope": "surface_plan"
}
```

Plan Agent nhận feedback như input Phase A mới: kiểm kê lại dữ liệu/capability,
rồi chọn patch, tạo surface khác, tìm lại ảnh, hoặc giảm scope. Backend không
được tự thay ảnh hay tự đổi bố cục. Riêng lỗi tạm thời không ảnh hưởng logic
(ví dụ một lần ảnh tải chậm) có thể chỉ log/retry theo policy browser; chỉ lỗi
đủ ngưỡng hoặc làm trải nghiệm không dùng được mới route về Agent.

Trước khi route feedback, Feedback Router bắt buộc kiểm tra
`surface_id + revision` vẫn là SurfaceDocument active. Diagnostic của surface
cũ hoặc revision cũ chỉ được log; không được khiến Plan Agent patch nhầm panel
mới.

### 17.4. Hệ quả trực tiếp cho checkpoints

- AF2 phải đăng ký capability theo mục tiêu dữ liệu riêng (`search_web`,
  `search_image`), không có tool search mơ hồ.
- AF4 phải giữ raw URL riêng trong SearchResultStore và materialize nó duy
  nhất qua Compiler.
- AF5 phải bổ sung capability matrix/integration check giữa Widget Registry,
  renderer browser và backend event handler; đây là checkpoint thực thi quy
  tắc “không placeholder”, không chỉ thêm câu cấm vào prompt.
- AF6 phải định nghĩa `runtime_diagnostic` và feedback router có revision,
  component/anchor, error type và repair scope; Stage Map chỉ dùng dữ liệu
  đã materialize trong SurfaceDocument.
