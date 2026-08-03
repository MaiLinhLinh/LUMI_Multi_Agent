# Checkpoint: Presentation Director & Evaluation

## Mục tiêu

Xây dựng và đánh giá kiến trúc presentation có thể tái sử dụng cho Lumi: dữ liệu đã được domain agent xác thực, Planner quyết định nội dung kể, Director quyết định cách thể hiện bằng Action DSL, Compiler/renderer thực thi an toàn. Plan Critic chỉ được bổ sung khi phép đo chứng minh Director còn sai đáng kể.

Nguyên tắc xuyên suốt:

- Không thay đổi luồng Weather Agent, validation, template rendering hoặc xử lý hỏi làm rõ nếu checkpoint không yêu cầu.
- Không gửi output LLM thô trực tiếp cho frontend.
- Template chỉ công bố semantic anchors và capabilities; frontend renderer quyết định chi tiết SVG/CSS/Web Animations.
- Mỗi checkpoint hoàn tất phải tổng hợp thay đổi, kiểm thử và xin phê duyệt trước checkpoint kế tiếp.

## Luồng mục tiêu

```text
Weather Agent / domain agent
  -> dữ liệu đã validate
  -> Presentation Planner: narration + facts theo từng step
  -> Director Agent: semantic action plan
  -> Presentation Compiler: validate, fallback, chuyển thành action hợp lệ
  -> Frontend: TTS theo step + avatar + renderer
```

Ở chế độ streaming (bổ sung sau khi luồng cơ bản ổn định):

```text
Gemini stream JSON
  -> backend incremental parser
  -> một step JSON hoàn chỉnh
  -> Pydantic validate
  -> Compiler duyệt
  -> gửi presentation_step
  -> frontend chạy TTS/effect ngay
```

## CP-R1 — Hoàn thiện baseline renderer

Mục tiêu: renderer đủ ổn định để trở thành môi trường so sánh công bằng, chưa thêm Director hay Critic.

Việc thực hiện:

- Chuẩn hoá vocabulary action tối thiểu: `highlight`, `dim`, `reveal`, `draw_circle`, `point` cùng `emphasis` và `gesture` cơ bản.
- Cài `draw_circle` bằng SVG overlay với `stroke-dasharray`/`stroke-dashoffset`, tạo cảm giác vòng được vẽ dần.
- Cài `point`: bút/con trỏ di chuyển đến anchor, dừng hợp lý và tự dọn sau action.
- Bảo đảm action cũ được cleanup khi action mới thay thế; tránh chồng overlay, che text hoặc nháy layout.
- Hoàn thiện queue TTS theo presentation step: lời nói của step nào mới kích hoạt action của step đó.
- Avatar 2D có trạng thái tối thiểu: idle, speaking, pointing, emphasis.

Điều kiện hoàn thành:

- Một câu trả lời weather nhiều step có TTS, vòng vẽ dần và bút chỉ đúng thứ tự.
- Không có raw selector hoặc raw JavaScript do LLM sinh được thực thi ở browser.

## CP-R2 — Chuẩn hoá semantic anchors cho Weather

Mục tiêu: template weather công bố đủ vùng semantic để Planner/Director có thể focus mà không biết DOM nội bộ.

Việc thực hiện:

- Rà soát và chuẩn hoá anchors cho tổng quan: nhiệt độ hiện tại, mô tả, cảm giác.
- Rà soát anchors cho chỉ số: mưa, xác suất mưa, gió, độ ẩm, áp suất, thấp/cao.
- Bổ sung anchors theo từng interval dự báo giờ, ví dụ `weather.hourly.03:00.precipitation_probability`.
- Công bố template capabilities: anchor nào hỗ trợ `draw_circle`, `point`, `highlight`, `reveal`.
- Compiler chỉ nhận semantic target trong capability đã công bố; target không hợp lệ phải reject/fallback an toàn.

Điều kiện hoàn thành:

- Câu như “Giờ nào mưa nhiều nhất?” có thể focus chính xác interval tương ứng.
- Thêm target cho dữ liệu động không đòi hỏi LLM biết HTML selector.

## CP-R3 — Bộ benchmark và logging

Mục tiêu: tạo mốc đo trước khi thay đổi AI điều phối.

Việc thực hiện:

- Tạo bộ 30–50 câu hỏi weather đa dạng: tổng quan, một fact, so sánh, rủi ro, nhiệt độ/gió/độ ẩm/mưa và câu thiếu thông tin.
- Định nghĩa fixture dữ liệu weather đã validate để các biến thể được so sánh trên cùng input.
- Lưu cho mỗi lượt: query, dữ liệu nguồn, narration, plan, compiled actions, kết quả validation, latency và token/cost nếu lấy được.
- Có cơ chế chụp hoặc lưu visual state từng step để đánh giá sau này.

Điều kiện hoàn thành:

- Chạy được baseline/template cố định và Planner hiện tại trên cùng bộ dữ liệu.
- Log đủ để truy vết một lỗi từ lời nói đến target/effect đã render.

## CP-R4 — Đo baseline và Planner hiện tại

Mục tiêu: có số liệu trước khi đưa Director vào.

Các biến thể:

1. Template + luật animation cố định, không có LLM điều phối animation.
2. Presentation Planner hiện tại.

Chỉ số:

- Factual grounding: narration có đúng dữ liệu đã validate không.
- Target accuracy: fact được nói có focus đúng vùng không.
- Action validity: action qua Compiler / tổng action.
- Narration-animation coherence: lời nói và hành động có khớp không.
- Visual stability/readability: chồng overlay, nháy, che chữ, khó đọc.
- Latency và chi phí.
- Đánh giá người dùng: dễ hiểu, tự nhiên, giống MC thời tiết, không rối.

Điều kiện hoàn thành:

- Có bảng kết quả và lỗi điển hình của hai biến thể.
- Kết quả được dùng để chốt vocabulary và prompt của Director, không suy đoán cảm tính.

## CP-R5 — Director Agent và Action DSL

Mục tiêu: tách rõ quyết định “nói gì” khỏi quyết định “thể hiện như thế nào”.

Phạm vi Planner:

- Đọc query, history cần thiết, dữ liệu đã validate và template context.
- Sinh narration theo từng step và gắn facts mà step đó đề cập.
- Không quyết định chi tiết animation.

Phạm vi Director:

- Đọc narration step, facts, template capabilities và semantic anchors.
- Sinh Action DSL: target, effect, emphasis, gesture, thứ tự/timing ở mức semantic.
- Không được sinh fact mới, selector CSS, JavaScript hoặc effect ngoài vocabulary.

Ví dụ giao tiếp:

```json
{
  "step_id": "s2",
  "narration": "Xác suất mưa lúc 3 giờ là 73%, nên bạn nên mang theo ô.",
  "facts": ["hourly.03:00.precipitation_probability"]
}
```

```json
{
  "step_id": "s2",
  "actions": [
    {
      "target": "weather.hourly.03:00.precipitation_probability",
      "effect": "draw_circle",
      "gesture": "point",
      "emphasis": "strong"
    }
  ]
}
```

Điều kiện hoàn thành:

- Director hoạt động end-to-end trên Weather.
- Pydantic và Compiler chặn/fallback mọi action không hợp lệ.
- Frontend chỉ nhận compiled presentation step.

## CP-R6 — So sánh Director với Planner

Mục tiêu: xác định Director có tạo lợi ích thực tế hay không.

Các biến thể chạy trên cùng query, fixture, template và renderer:

1. Baseline luật cố định.
2. Planner hiện tại.
3. Planner nội dung + Director Action DSL.

Thực hiện:

- Chạy benchmark CP-R3.
- Tự động tính factual grounding, action validity, target accuracy khi có thể.
- Đánh giá coherence, stability và preference bằng người dùng; có thể thêm VLM đánh giá hỗ trợ nhưng không thay thế đánh giá thủ công.
- So sánh latency/cost để biết chất lượng tăng có đáng đổi lấy độ trễ không.

Điều kiện hoàn thành:

- Có báo cáo kết quả, lỗi tiêu biểu và kết luận Director tốt hơn/không tốt hơn Planner ở điều kiện nào.

## CP-R7 — Quyết định có cần Plan Critic

Chỉ bắt đầu khi CP-R6 cho thấy lỗi lặp lại đủ đáng kể, ví dụ: Director focus nhầm fact, action quá rối, narration và animation lệch nhau hoặc chọn effect không phù hợp.

Nếu được phê duyệt, luồng trở thành:

```text
Director Action Plan
  -> Plan Critic: approve / bounded repair / reject
  -> Compiler
  -> renderer
```

Ràng buộc Critic:

- Chỉ sửa trong Action DSL và capabilities hiện có.
- Không được tạo dữ liệu hoặc đổi narration để che lỗi dữ liệu.
- Có giới hạn số lần repair để tránh tăng latency vô hạn.

Điều kiện hoàn thành:

- Có phép đo Director không Critic so với Director + Critic.
- Chỉ giữ Critic nếu chất lượng tăng đủ bù latency và chi phí.

## CP-R8 — Tổng quát hoá sang domain/template khác

Mục tiêu: chứng minh kiến trúc không chỉ là demo Weather.

Việc thực hiện:

- Chọn một domain thứ hai sau Weather (ví dụ Music), không tự triển khai khi chưa được phê duyệt.
- Domain mới chỉ cần cung cấp facts đã validate, template capabilities và semantic anchors.
- Tái sử dụng Planner/Director/Compiler vocabulary; chỉ mở rộng DSL khi có gap đã quan sát.
- Đo lại các metric chính và ghi nhận action/anchor nào thực sự tái sử dụng được.

Điều kiện hoàn thành:

- Có bằng chứng phần lõi presentation hoạt động qua ít nhất hai domain, không cần viết lại luồng điều phối chính.

## Quy tắc dừng và phê duyệt

- Hoàn tất mỗi checkpoint phải dừng, tổng hợp file đã thay đổi, hành vi đạt được, kiểm thử và rủi ro còn lại.
- Chỉ tiếp tục checkpoint tiếp theo sau khi người dùng phê duyệt rõ ràng.
- Nếu phát hiện yêu cầu làm thay đổi luồng code chính, schema công khai hoặc domain behavior ngoài checkpoint, phải báo và hỏi trước khi sửa.
