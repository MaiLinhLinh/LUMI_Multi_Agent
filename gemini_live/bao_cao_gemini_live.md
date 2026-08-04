---
title: "Báo cáo dự án"
subtitle: "Lumi Gemini Live - Hệ thống trợ lí ảo đa domain có trình bày trực quan"
author: "Người thực hiện: Văn Thị Mai Linh"
date: "Cập nhật: 04/08/2026"
lang: vi-VN
---

# BÁO CÁO DỰ ÁN {-}

**Lumi Gemini Live - Hệ thống trợ lí ảo đa domain có trình bày trực quan**

\newpage

# MỤC LỤC {-}

1. Tóm tắt
2. Phát biểu bài toán
3. Kiến trúc hệ thống
   - Tổng quan
   - Lớp giao diện và API
   - Gemini Live và Function Calling
   - LiveSessionOrchestrator, Registry và Session Memory
   - Presentation Pipeline dùng chung
   - Presentation Contract và animation theo scene
4. Các domain hiện có
   - WeatherLiveDomain
   - EducationLiveDomain
5. Cơ chế thêm một domain mới
6. Proof of Concept và hướng phát triển
7. Kết luận

\newpage

# TÓM TẮT

Báo cáo trình bày kiến trúc hiện tại của **Lumi Gemini Live**, một trợ lí hội thoại tiếng Việt có khả năng tiếp nhận text hoặc giọng nói, gọi công cụ theo domain, hiển thị dữ liệu đã kiểm chứng trên HTML/SVG template và minh hoạ theo từng câu đang được đọc.

Khác với luồng chatbot trước đây sử dụng Manager Agent và các sub-agent tách rời, phiên bản này dùng **Gemini Live** làm lớp hội thoại thời gian thực: mô hình nhận audio/text, quyết định gọi function phù hợp và stream audio PCM về trình duyệt. Backend không giao toàn quyền cho mô hình. Các domain vẫn sở hữu công cụ, dữ liệu, quy tắc kiểm chứng và context; Presentation Pipeline dùng chung tạo kịch bản có cấu trúc rồi Compiler chỉ cho phép animation trên semantic target hợp lệ của template.

Hai domain Proof of Concept hiện có là:

- **Weather**: tra cứu dữ liệu thời tiết từ Snapshot Cache/Redis, kế thừa địa điểm và phạm vi thời gian của hội thoại, sau đó render panel thời tiết theo ngày hoặc nhiều ngày.
- **Education**: tạo bài tập cộng/trừ có dữ liệu được code xác minh, render nhóm đối tượng lên template HTML và hỗ trợ hiệu ứng trình bày kết quả.

Mục tiêu chính của kiến trúc không phải để LLM tự sinh HTML hoặc tự quyết định dữ liệu thật. Mục tiêu là phân tách rõ: Gemini Live đảm nhiệm hội thoại và voice; domain đảm nhiệm nghiệp vụ; Presentation Pipeline đảm nhiệm kế hoạch trình bày an toàn; frontend đảm nhiệm render và animation tái sử dụng.

# PHÁT BIỂU BÀI TOÁN

Người dùng thường tiếp nhận kết quả từ chatbot dưới dạng một đoạn văn bản dài. Cách này thiếu trực quan trong các tình huống có dữ liệu có cấu trúc, ví dụ: dự báo theo giờ/ngày, biểu đồ nhiệt độ, bài toán dùng hình minh hoạ hoặc dashboard nghiệp vụ. Bài toán đặt ra là xây dựng một trợ lí có thể nhận yêu cầu tiếng Việt tự nhiên, truy xuất dữ liệu đáng tin cậy, đưa dữ liệu vào template đã chuẩn bị và trình bày kết quả bằng giọng nói kèm animation phù hợp.

Hệ thống cần đồng thời giải quyết năm yêu cầu:

1. Hiểu hội thoại text hoặc voice và gọi đúng tool theo ngữ cảnh.
2. Không để LLM tự bịa dữ liệu hoặc tự sinh cấu trúc HTML không kiểm soát.
3. Chuyển dữ liệu thật thành lời dẫn có nhịp điệu, giống một người thuyết trình thay vì chỉ đọc chỉ số.
4. Liên kết mỗi câu lời dẫn với đúng vùng dữ liệu trên template để highlight, khoanh tròn, vẽ đường hoặc reveal nội dung.
5. Cho phép thêm domain mới mà không phải sửa lõi Gemini Live, renderer hay frontend animation đã có.

Vì vậy, Lumi sử dụng **template-first presentation**. Template HTML/SVG được tạo trước; mỗi vùng có thể được minh hoạ mang một semantic ID như `weather.temperature_trend` hoặc `math.result.number`. LLM chỉ làm việc với fact và cue ở mức ngữ nghĩa. Code Compiler mới là thành phần ánh xạ và xác thực target/effect trước khi frontend thực thi.

# KIẾN TRÚC HỆ THỐNG

## Tổng quan

Kiến trúc Lumi Gemini Live gồm bốn lớp: hội thoại thời gian thực, domain mở rộng, Presentation Pipeline dùng chung và frontend phát audio/animation. Không có Manager Agent riêng trong luồng mới. Gemini Live nhận các tool declaration từ mọi domain đã đăng ký và hoạt động gần với vai trò điều phối hội thoại: hiểu yêu cầu, gọi tool, hỏi lại khi thiếu dữ liệu và đọc narration đã được backend phê duyệt.

Sơ đồ kiến trúc chi tiết được cung cấp kèm báo cáo dưới dạng file `luong_kien_truc_he_thong_gemini_live.svg`. Luồng logic tương ứng là:

```text
Người dùng (text/mic) → Web UI → FastAPI → LiveSessionOrchestrator ↔ Gemini Live
                                            │
                                            ├─ LiveDomainRegistry / LiveToolDispatcher
                                            │    ├─ WeatherLiveDomain → Snapshot Cache / Redis
                                            │    └─ EducationLiveDomain → exercise tool / code verify
                                            │
                                            └─ PresentationPipeline dùng chung
                                                 Renderer → metadata → Domain Adapter
                                                 → Planner LLM → Compiler → scenes hợp lệ
                                                                    │
Gemini Live đọc narration + trigger_scene ← WebSocket marker/audio ← Frontend animation
```

Luồng xử lý chính như sau:

1. Người dùng nhập text hoặc nói qua microphone trên Web UI.
2. FastAPI duy trì WebSocket và chuyển dữ liệu đến Gemini Live.
3. Gemini Live gọi một function, ví dụ `get_weather` hoặc `create_math_exercise`.
4. `LiveSessionOrchestrator` chuyển tool call qua Registry/Dispatcher đến domain sở hữu tool.
5. Domain lấy hoặc tạo dữ liệu đã kiểm chứng, đồng thời tạo `PresentationRequest` cho Pipeline dùng chung.
6. Pipeline render panel, tạo grounded facts, gọi Planner LLM để tạo narration theo scene, rồi Compiler kiểm tra contract.
7. Backend trả fact pack và scene hợp lệ cho Gemini Live. Gemini đọc scene và gọi `trigger_scene(scene_id)`.
8. Frontend nhận marker scene, audio PCM và effect hợp lệ để phát giọng nói, cập nhật avatar, chat và animation trên panel.

## Lớp giao diện và API

`web_app.py` là FastAPI application. Giao diện HTML/CSS/JavaScript được chia thành panel trực quan ở bên trái và hội thoại ở bên phải. Trình duyệt có thể gửi text hoặc audio microphone; backend duy trì session ID để truy xuất history và domain context của người dùng.

Giao diện không tự suy luận dữ liệu. Nó nhận ba loại thông điệp chính từ backend:

- HTML panel đã render từ template.
- Audio PCM stream từ Gemini Live để phát giọng nói.
- Scene marker gồm `scene_id`, `target_id`, `effect` và `gesture` để `AnimationController` thực hiện đúng animation.

`AnimationController` chỉ tìm phần tử có `data-present-id` tương ứng. Các effect hiện dùng gồm `highlight`, `draw_circle`, `draw_arrow`, `trace_line`, `reveal`, `reveal_items` và các effect mở rộng sau này. Vì target phải tồn tại trong HTML đã render, frontend không cần chấp nhận selector tự do do LLM sinh ra.

## Gemini Live và chức năng Function Calling

Gemini Live là transport hội thoại song công. Nó nhận audio/text người dùng, chọn function theo tool declarations, nhận tool response từ backend và trả về audio PCM. Prompt dùng chung yêu cầu Gemini chỉ dựa trên facts do tool trả về và gọi `trigger_scene` với đúng `scene_id` trước khi đọc narration của scene đó.

Gemini Live không sở hữu dữ liệu nghiệp vụ. Nó không đọc Redis trực tiếp, không tính kết quả toán học trực tiếp và không tự tạo HTML. Khi cần dữ liệu, nó gọi tool; khi cần animation, nó gọi trigger semantic scene mà Compiler đã phê duyệt. Cách phân quyền này giữ cho lời nói tự nhiên nhưng giới hạn dữ liệu và animation trong phạm vi an toàn.

## LiveSessionOrchestrator, Registry và Session Memory

`LiveSessionOrchestrator` là lõi dùng chung cho tất cả domain. Nó quản lý các trách nhiệm không thuộc nghiệp vụ:

- đọc/ghi `SessionMemoryStore` theo `session_id`;
- tạo `DomainRequest` gồm câu hỏi mới và history giới hạn;
- gửi tool call tới `LiveToolDispatcher`;
- gọi `PresentationPipeline` sau khi domain có dữ liệu;
- giữ `LivePresentation` và `SceneState` để chỉ scene hợp lệ mới được kích hoạt;
- trả tool response, fact pack và scene instruction về Gemini Live.

`LiveDomainRegistry` giữ danh sách domain đã đăng ký. `LiveToolDispatcher` tra tên tool để biết tool đó thuộc domain nào. Nhờ đó lớp Live core không cần có nhánh `if weather`, `if education` hay sửa lại khi thêm Music/Sales trong tương lai.

`SessionMemoryStore` lưu history ngắn hạn và `domain_contexts`. History giúp Gemini hiểu hội thoại gần đây khi tạo Live session mới. Domain context là trạng thái đã xác nhận theo domain, ví dụ địa điểm/phạm vi dự báo gần nhất của Weather. Tách hai loại trạng thái giúp không trộn chi tiết nghiệp vụ của domain vào lõi Live chung.

## Presentation Pipeline dùng chung

Sau khi tool hoàn thành, domain tạo một `PresentationRequest` gồm `domain_id`, `template_id`, `view_model`, `domain_data`, `compact_data` và Domain Presentation Adapter. `PresentationPipeline` thực hiện cùng một chuỗi cho mọi domain:

1. **JinjaPresentationRenderer** nạp template theo `domain_id + template_id` và render `view_model` thành HTML/SVG panel.
2. Đọc `metadata.json` của template để biết semantic target nào tồn tại và effect nào được phép.
3. Gọi Domain Presentation Adapter tạo các **grounded facts** từ dữ liệu thật.
4. Gọi Planner LLM sinh presentation plan có cấu trúc: fact nào được nói, narration, focus, effect và gesture theo scene.
5. Gọi Presentation Compiler để kiểm tra fact, target, effect và DOM capability. Scene sai target, effect không được khai báo hoặc fact không có căn cứ sẽ bị từ chối/fallback.

Grounded fact là một đơn vị thông tin đã được backend xác nhận, ví dụ “xác suất mưa cao nhất là 98% vào ngày 04/08” hoặc “nhóm A có 7 bông hoa”. Fact có ID ổn định, dữ liệu evidence và visual cue có thể dùng. Planner được quyền chọn và kể lại fact, nhưng không được tạo ra một giá trị mới ngoài fact pack.

## Presentation Contract và animation theo scene

Kết quả Compiler là Presentation Contract. Mỗi scene chứa narration được nói, `target_id` để xác định vùng giao diện, `effect` để minh hoạ và `gesture` cho avatar. Ví dụ rút gọn:

```json
{
  "scene_id": "weather-scene-3",
  "narration": "Xác suất mưa cao nhất là 98 phần trăm vào ngày 04 tháng 08.",
  "target_id": "weather.day.3.rain_risk",
  "effect": "draw_circle",
  "gesture": "point"
}
```

Contract là ranh giới quan trọng: Planner có thể dùng LLM để chọn nội dung và câu dẫn, nhưng frontend chỉ nhận scene đã qua Compiler. Nhờ đó một template mới chỉ cần khai báo capability/semantic target; không cần đưa toàn bộ CSS selector hoặc logic DOM cho Gemini.

# CÁC DOMAIN HIỆN CÓ

## WeatherLiveDomain

`WeatherLiveDomain` hiện thực interface `LiveDomain` và sở hữu toàn bộ logic thời tiết trong `domains/weather/`.

- `tools.py`: khai báo `get_weather`, kiểm tra input và điều phối truy xuất dữ liệu.
- `context.py`: kế thừa an toàn location, date/range và request type đã xác nhận cho câu follow-up.
- `services/`: resolve địa danh, Snapshot Cache và Redis store.
- `view_model.py`: chuẩn hoá dữ liệu cho template ngày hoặc dự báo nhiều ngày.
- `adapter.py`: tạo candidate/grounded facts Weather, visual evidence và fallback plan dựa trên dữ liệu thật.
- `templates/`: chứa `weather_basic`, `weather_single_day`, `weather_forecast`; mỗi template có HTML và `metadata.json` tương ứng.

Luồng Weather minh hoạ: Gemini Live nghe “Thời tiết Hà Nội tuần tới thế nào?”, gọi `get_weather`; Weather tool chuẩn hoá Hà Nội và phạm vi bảy ngày, đọc Snapshot Cache trước rồi Redis khi cache miss. Sau đó domain chọn template dự báo, tạo view model và facts như xu hướng nhiệt độ, pattern mưa, ngày có mưa lớn nhất. Pipeline tạo scene MC và render biểu đồ/card thời tiết. Gemini Live đọc từng scene; frontend khoanh ngày hoặc vẽ đường biểu đồ theo target mà Compiler đã xác nhận.

## EducationLiveDomain

`EducationLiveDomain` là domain thứ hai, chứng minh việc thêm domain không làm thay đổi Live core. POC hiện hỗ trợ bài học cộng/trừ bằng khung **Object Group Math**.

- `tools.py`: khai báo `create_math_exercise`; Gemini có thể đề xuất phép tính đa dạng, còn code kiểm tra toán hạng và tính kết quả chính xác.
- `models.py` và `lessons/object_group_math.py`: biểu diễn dữ liệu bài tập và quy tắc bài học.
- `view_model.py`: chọn asset hợp lệ (hoa, bóng, tên lửa), nhóm A/B, phép toán và nhóm kết quả để render template.
- `adapter.py`: tạo facts như nhóm A, nhóm B, phép toán, biểu thức, số kết quả và nhóm item kết quả.
- `templates/object_group_math/`: HTML/CSS, asset SVG và metadata với các semantic target như `math.group.a`, `math.result.items`, `math.result.number`.

Ví dụ, với bài toán `7 + 2`, code xác nhận kết quả là 9. Panel có sẵn hai nhóm hoa, một vùng đáp án và chín item kết quả ở trạng thái ẩn. Khi Planner chọn fact “kết quả là 9”, Compiler có thể cho phép effect `reveal_items` trên `math.result.items` và `reveal` trên `math.result.number`. LLM không cần biết HTML của từng bông hoa; nó chỉ làm việc với fact/target semantic.

# CƠ CHẾ THÊM MỘT DOMAIN MỚI

## Nguyên tắc mở rộng

Một domain mới không được sửa `LiveSessionOrchestrator`, `LiveToolDispatcher`, `JinjaPresentationRenderer`, `PresentationPipeline`, Presentation Compiler hoặc `SceneState`. Các lớp này là nền tảng dùng chung. Domain chỉ bổ sung những phần biết về nghiệp vụ của chính nó.

Mỗi domain mới cần trả lời bốn câu hỏi:

1. Gemini Live được phép gọi những tool nào?
2. Dữ liệu thật được lấy/kiểm chứng/chuyển thành view model như thế nào?
3. Template nào hiển thị dữ liệu và template có những semantic target/effect nào?
4. Những grounded fact nào có thể được kể lại và animation nào là bằng chứng trực quan hợp lệ cho từng fact?

## Cấu trúc thư mục đề xuất

Ví dụ một domain mới tên `sales` có cấu trúc sau:

```text
domains/sales/
  __init__.py
  domain.py          # implements LiveDomain, khai báo tool + execute_tool
  tools.py           # truy vấn/ghi dữ liệu nghiệp vụ, validation
  prompt.py          # hướng dẫn riêng cho Gemini Live về domain Sales
  view_model.py      # chuẩn hoá dữ liệu cho template
  adapter.py         # grounded facts, target resolver, planner guidance
  context.py          # chỉ cần khi Sales có follow-up state riêng
  models.py           # chỉ cần khi domain có schema nghiệp vụ riêng
  templates/
    sales_dashboard/
      template.html
      metadata.json
```

Các file bắt buộc tối thiểu là `domain.py`, `tools.py`, `view_model.py`, `adapter.py`, `prompt.py` và một template có metadata. `context.py`, `models.py`, `services/`, `lessons/` hoặc asset folders là thành phần tùy chọn, chỉ tạo khi nghiệp vụ thực sự cần.

## Các bước triển khai domain mới

### Bước 1 - Xác định tool và nguồn dữ liệu

Xác định những hành động Gemini được phép gọi, chẳng hạn `get_sales_report(period, region)` hoặc `compare_products(product_a, product_b)`. Tool phải có schema tham số rõ ràng. Backend xác thực quyền truy cập, input, dữ liệu trống và lỗi nguồn dữ liệu trước khi trả kết quả cho Gemini.

### Bước 2 - Hiện thực `LiveDomain`

Tạo class `SalesLiveDomain` trong `domain.py` và implement:

- `domain_id`: định danh ổn định, ví dụ `sales`.
- `tool_declarations`: danh sách function declaration cung cấp cho Gemini Live.
- `prompt_guidance`: quy tắc riêng, ví dụ không phát biểu doanh thu khi tool chưa trả dữ liệu.
- `execute_tool`: nhận tool call, gọi `SalesTools`, lưu context nếu cần và trả `DomainToolResult`.

Nếu tool trả dữ liệu có thể minh hoạ, `execute_tool` tạo `PresentationRequest` thay vì để Gemini tự tóm tắt dữ liệu thô.

### Bước 3 - Tạo view model và template

`view_model.py` chuyển kết quả tool sang dữ liệu template ổn định: label, card, điểm chart, trạng thái cảnh báo. Sau đó tạo `template.html` và `metadata.json`. Mỗi dữ liệu cần minh hoạ phải có `data-present-id`, ví dụ `sales.revenue.total`, `sales.region.2`, `sales.trend.point.5`.

Metadata không chỉ là mô tả. Nó là danh sách quyền animation của template. Ví dụ vùng doanh thu chỉ cho `highlight` và `draw_circle`, còn đường biểu đồ mới cho `trace_line`.

### Bước 4 - Tạo Domain Presentation Adapter

`adapter.py` implement `DomainPresentationAdapter`. Adapter không tự truy vấn nguồn dữ liệu; nó dùng dữ liệu đã được tool xác thực để tạo fact. Ví dụ:

```json
{
  "fact_id": "highest_revenue_region",
  "description": "Miền Bắc có doanh thu cao nhất trong quý",
  "focus": "top_region",
  "entity": {"region_index": 2},
  "visual_evidence": {"target_id": "sales.region.2"}
}
```

Adapter cũng cung cấp `resolve_target` để biến focus/entity thành target cụ thể của template, `planner_guidance` cho văn phong domain và `fallback_plan` khi Planner không trả cấu trúc hợp lệ.

### Bước 5 - Đăng ký ở bootstrap

Trong `bootstrap.py`, khởi tạo `SalesLiveDomain` rồi gọi Registry đăng ký domain. Đây là thay đổi duy nhất ngoài thư mục domain mới. Sau khi đăng ký, Dispatcher tự đưa tool declaration của Sales cho Gemini Live và định tuyến tool call về `SalesLiveDomain`.

### Bước 6 - Kiểm thử theo contract

Tối thiểu cần kiểm tra:

1. Gemini gọi đúng tool Sales với input hợp lệ.
2. Tool response không chứa số liệu bịa và lỗi được diễn giải ngắn gọn.
3. View model render được template hoàn chỉnh.
4. Mỗi grounded fact được phép chọn phải có target/effect tương thích trong metadata.
5. Compiler từ chối target/effect không tồn tại.
6. `trigger_scene` chỉ kích hoạt scene đúng thứ tự và frontend thực thi được effect.
7. Follow-up vẫn đúng nếu domain có context riêng; nếu không, context.py không cần tồn tại.

## Khi nào phải sửa lớp dùng chung?

Thông thường, thêm domain **không cần** sửa lõi. Chỉ có hai trường hợp hợp lệ cần thay đổi lớp dùng chung:

- Một hiệu ứng hoàn toàn mới chưa tồn tại, ví dụ `reveal_items`. Khi đó phải thêm effect vào schema chung, Animation Controller và module effect frontend. Template domain mới chỉ khai báo capability sau khi effect đã được hỗ trợ.
- Một capability trình bày mới có ý nghĩa chung cho nhiều domain, ví dụ một loại cue timeline hoặc một contract audio mới. Khi đó thay đổi phải được thiết kế như interface dùng chung, không chèn logic riêng Sales/Education vào Live core.

Nguyên tắc này tránh việc domain thứ ba hoặc thứ mười làm pipeline phình to và khó bảo trì.

# PROOF OF CONCEPT VÀ HƯỚNG PHÁT TRIỂN

## Phạm vi POC hiện tại

POC xác minh ba ý tưởng kiến trúc:

- Gemini Live có thể tiếp nhận voice/text, gọi tool và stream audio thay vì chỉ là chatbot text.
- Một pipeline trình bày có cấu trúc có thể dùng lại cho Weather và Education dù hai domain dùng nguồn dữ liệu và template hoàn toàn khác nhau.
- Template semantic có thể cho phép lời dẫn và animation gắn với dữ liệu thật mà không cần sinh HTML/CSS tại runtime.

Ví dụ kiểm thử chức năng gồm truy vấn Weather có follow-up theo địa điểm/thời gian, dự báo nhiều ngày có chart/card, và bài học cộng/trừ với asset được chọn bởi code và đáp án được tính xác minh.

## Hạn chế hiện tại

Vì Gemini Live đọc theo scene, giữa các scene có thể vẫn có nhịp ngắt ngắn do tool call/turn boundary. `trigger_scene` là function call có prompt hướng dẫn và backend validation, nhưng không phải timestamp audio ở mức từng từ. Do đó kiến trúc hiện tại phù hợp nhất với đồng bộ **theo câu/scene**, chưa phải đồng bộ tuyệt đối theo từng từ.

Chất lượng narration còn phụ thuộc Planner LLM. Grounded facts và Compiler bảo vệ tính đúng dữ liệu/target, nhưng văn phong MC, độ dài lời dẫn và việc chọn fact cần tiếp tục tinh chỉnh prompt, fact pack và bộ kịch bản đánh giá định tính. Khi cần đồng bộ theo từ hoặc animation phức tạp hơn, có thể phát triển timeline/audio alignment như một tầng tùy chọn, không thay đổi ranh giới Domain Adapter - Pipeline - Frontend.

## Hướng phát triển

Các hướng tiếp theo gồm:

- thêm Music, Sales hoặc Education lesson mới chỉ qua cơ chế Registry;
- mở rộng thư viện effect dùng chung và capability metadata của template;
- tăng chất lượng narration qua few-shot theo từng loại presentation nhưng vẫn giữ fact grounding;
- bổ sung Director Agent sau Planner nếu cần tách quyết định “nói gì” khỏi quyết định “minh hoạ như thế nào”;
- hỗ trợ ảnh/PDF bằng pipeline vision riêng: Grounded SAM/bounding box tạo target, sau đó dùng overlay SVG/canvas nhưng vẫn trả về contract animation tương tự HTML;
- xây dựng bộ test contract cho domain để kiểm tra fact-to-target accuracy, readability và narration-animation coherence trước khi đưa domain vào hệ thống.

# KẾT LUẬN

Lumi Gemini Live chuyển trọng tâm từ chatbot text sang hệ thống trình bày dữ liệu thời gian thực. Gemini Live tạo trải nghiệm voice và function calling; domain giữ dữ liệu/thao tác nghiệp vụ; Presentation Pipeline biến dữ liệu đã xác thực thành một contract có narration và animation hợp lệ; frontend tái sử dụng template semantic để hiển thị an toàn.

Giá trị quan trọng nhất của kiến trúc là khả năng mở rộng có kiểm soát. Khi thêm một domain, nhóm phát triển chỉ cần triển khai tool, view model, adapter và template/metadata của domain đó, sau đó đăng ký vào bootstrap. Lõi Live, renderer, Compiler và animation controller vẫn được tái sử dụng. Điều này tạo nền tảng để Lumi phát triển từ Weather/Education hiện tại sang nhiều domain trực quan khác mà không biến mã nguồn thành các nhánh xử lý đặc thù khó bảo trì.
