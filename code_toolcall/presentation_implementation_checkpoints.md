# Checkpoint triển khai Presentation Tools cho Lumi

## Mục tiêu và phạm vi đã chốt

Áp dụng trước cho kết quả weather đã `completed`:

```text
weather data đã validate
  -> VisualTools chọn và render template như hiện tại
  -> Presentation Planner tạo các bước trình bày có cấu trúc
  -> Presentation Compiler kiểm tra và biên dịch step an toàn
  -> frontend phát TTS/avatar/effect theo từng step
```

Không nằm trong phạm vi đợt này:

- Ảnh/PDF, SAM hoặc visual grounding.
- Thay đổi WeatherTools validation, clarification, Redis retrieval, router hoặc Music flow.
- Cho LLM sinh HTML, CSS, JavaScript hay DOM selector tự do.
- Cài hoặc host model/GPU/Colab.

## Các nguyên tắc không được phá vỡ

- Chỉ planner được gọi khi `agent_result.status == "completed"` và có panel weather hợp lệ.
- `needs_clarification`, `unavailable`, `error` giữ nguyên: hiện chat/TTS như hiện tại, không tạo panel mới, không gọi Planner.
- `VisualTools.select_weather_template()` vẫn là code deterministic.
- Compiler là hàng rào bắt buộc giữa LLM và UI. Frontend chỉ nhận `target_id`, `effect`, `gesture` đã được compiler duyệt.
- Thêm template không được yêu cầu sửa Planner/graph/frontend nếu chỉ dùng effect đã có.
- Mỗi checkpoint chỉ sửa các file trong phạm vi được nêu; không tiện tay refactor mã không liên quan.

---

## CP-00 — Khóa phương án tích hợp với luồng hiện tại

**Trạng thái:** Chờ xác nhận trước khi sửa backend.

### Hiện trạng đã xác minh

- `weather_node` gọi `run_weather()` trong `rag_manager/graph.py`.
- `run_weather()` hiện để LLM sinh `final_answer` sau tool thành công.
- Sau đó graph chạy `visual_node`, rồi kết thúc.
- `/api/chat/stream` chỉ gửi `text_delta` và `final`; panel weather chỉ có trong `final`.

### Hai phương án

| Phương án | Thay đổi luồng hiện tại | Số lượt LLM sau tool | Khuyến nghị cho đợt đầu |
|---|---:|---:|---|
| A. Sidecar planner | Không. Giữ nguyên `run_weather()` và `final_answer`; planner tạo presentation plan bổ sung. | 2 | Không chọn cho MVP. |
| B. Planner thay final answer | Có. Sửa runtime/Weather Agent để Planner sinh narration thay cho lượt final text hiện tại. | 1 | Đã phê duyệt cho MVP. |

### Quyết định đã được phê duyệt

Thực hiện **B**: Presentation Planner thay lượt LLM hiện tại sinh `final_answer` sau tool thành công.

- LLM vẫn gọi `get_weather` như hiện tại.
- Sau tool `completed`, runtime dừng trước lượt sinh plain-text answer cũ.
- `VisualTools` chọn/render template như hiện tại.
- Planner dùng weather facts đã validate + template capabilities để sinh `PresentationPlan`.
- Ghép `steps[].narration` thành `final_answer` lưu vào history/session.
- Từng narration vẫn phải được phát thành `text_delta` để text xuất hiện dần trên chat; không chờ `final` mới thấy câu trả lời.

Như vậy vẫn có một lượt LLM sau tool, nhưng lượt đó trả structured presentation plan có narration thay vì câu trả lời plain text độc lập.

### Tiêu chí qua checkpoint

- Đã xác nhận phương án B ngày 2026-07-30.
- Các checkpoint bên dưới đã dùng B làm phương án triển khai.

---

## CP-01 — Baseline và hợp đồng dữ liệu

**Mục tiêu:** ghi nhận hành vi hiện tại và cố định contract mới, chưa đổi hành vi người dùng.

### File được phép sửa

- `rag_manager/presentation/schemas.py` — file mới.
- `rag_manager/presentation/__init__.py` — file mới nếu cần.
- `tests/test_presentation_schemas.py` — file mới.

### Công việc

1. Tạo Pydantic models:
   - `PresentationPlan` (`schema_version = "presentation_plan.v1"`, tối đa 3 steps).
   - `PresentationStep` (`narration`, `focus`, `entity`, `emphasis`, `gesture`, `effect` tùy policy đã chốt).
   - `CompiledPresentationStep` (`narration`, `target_id`, `effect`, `gesture`).
2. Khóa enum MVP:
   - effect: `reveal`, `highlight`, `pulse`, `dim_others`, `draw_circle`, `draw_arrow`.
   - gesture: `idle`, `speaking`, `explain`, `point_left`, `point_right`, `concerned`.
3. Viết test parse dữ liệu hợp lệ, reject enum sai, reject trên 3 steps và narration rỗng.

### Không làm tại checkpoint này

- Không gọi Gemini.
- Không thêm node LangGraph.
- Không sửa `run_weather`, `web_app.py`, `app.js` hoặc template.

### Tiêu chí qua checkpoint

- Toàn bộ test schema pass.
- Schema JSON được duyệt là contract giữa Planner, Compiler và frontend.

---

## CP-02 — Semantic anchors và capability của ba template weather

**Mục tiêu:** để template mô tả vùng có thể trình bày, nhưng chưa chạy animation.

### File được phép sửa

- `rag_manager/visualization/assets/templates/weather/weather_basic/template.html`
- `rag_manager/visualization/assets/templates/weather/weather_single_day/template.html`
- `rag_manager/visualization/assets/templates/weather/weather_forecast/template.html`
- Ba `metadata.json` tương ứng.
- `tests/test_weather_presentation_templates.py` — file mới.

### Công việc

1. Bổ sung `data-present-id` cho các vùng đang có thật trong template, không thêm UI mới chỉ để có ID.
2. Bổ sung `presentation_capabilities` trong metadata.
3. Capability tối thiểu được gắn theo từng layout thực tế:
   - `overview`
   - `day_summary` (chỉ template có day card)
   - `temperature`
   - `rain_risk` (chỉ khi template hiển thị chỉ số này)
   - `wind` (nếu hiển thị)
   - `temperature_trend` (chỉ nếu template có chart tương ứng)
4. Test: mọi target/cấu trúc pattern trong metadata phải có anchor tương ứng sau Jinja render với sample data.

### Ràng buộc

- Không tự đặt capability cho số liệu không xuất hiện trên giao diện.
- Không đổi layout/logic chọn template.
- Không gắn ID dựa trên màu sắc hoặc vị trí CSS.

### Tiêu chí qua checkpoint

- Ba template render được như cũ với `StrictUndefined`.
- Metadata và HTML khớp nhau.
- Xác nhận danh sách semantic focus cuối cùng trước khi planner được viết.

---

## CP-03 — Presentation Compiler thuần code và test fallback

**Mục tiêu:** hoàn thiện hàng rào an toàn trước khi có LLM.

### File được phép sửa

- `rag_manager/presentation/capabilities.py` — file mới.
- `rag_manager/presentation/compiler.py` — file mới.
- `tests/test_presentation_compiler.py` — file mới.

### Công việc

1. Đọc capability theo `template_id` từ metadata đã có.
2. Compiler nhận `PresentationPlan`, template metadata và compact validated data.
3. Compiler kiểm tra:
   - focus tồn tại;
   - effect được capability cho phép;
   - entity hợp lệ, ví dụ `day_index` nằm trong số ngày thực;
   - `target_id` được tạo từ pattern hợp lệ;
   - gesture thuộc enum frontend hỗ trợ.
4. Fallback bắt buộc:
   - target không hợp lệ -> `overview` nếu tồn tại;
   - effect không hợp lệ -> `highlight` hoặc `reveal` được metadata cho phép;
   - gesture không hợp lệ -> `explain`.
   - narration vẫn được giữ nếu hợp lệ.
5. Chưa gọi compiler từ graph; test trực tiếp bằng fixture.

### Tiêu chí qua checkpoint

- Test cover valid mapping, index vượt phạm vi, focus/effect/gesture sai và template thiếu capability.
- Compiler không sinh selector, HTML, CSS hoặc JavaScript.
- Có ví dụ compiled JSON được frontend có thể dùng trực tiếp.

---

## CP-04 — Prototype frontend effect không phụ thuộc backend/LLM

**Mục tiêu:** chứng minh các presentation tools hoạt động trên template thật trước khi nối agent.

### File được phép sửa

- `web/index.html`
- `web/app.css`
- `web/app.js`

### Công việc

1. Giữ `iframe#weatherFrame` hiện tại trong MVP; không chuyển panel sang DOM trực tiếp khi chưa có quyết định riêng.
2. Thêm `PresentationOverlayController` ở frontend:
   - chờ `weatherFrame.onload`;
   - tìm `data-present-id` qua `weatherFrame.contentDocument`;
   - quy đổi `getBoundingClientRect()` từ iframe về `content-stage`;
   - vẽ SVG overlay trong frame cha.
3. Implement 6 effect MVP theo whitelist:
   - CSS: `reveal`, `highlight`, `pulse`, `dim_others`;
   - SVG overlay: `draw_circle`, `draw_arrow`.
4. Có hàm debug nội bộ nhận một `CompiledPresentationStep` mẫu để test thủ công. Không expose như API production.
5. Respect `prefers-reduced-motion`: thay draw/pulse thành highlight tĩnh.

### Điểm cần kiểm tra kỹ

`iframe sandbox=""` đang cùng origin vì dùng `srcdoc`, nhưng đây là điểm kỹ thuật cần test thực tế trước khi phụ thuộc vào `contentDocument`. Nếu browser mục tiêu không cho đọc/đo DOM iframe, dừng ở checkpoint này và hỏi bạn trước khi đổi kiến trúc panel.

### Tiêu chí qua checkpoint

- Với panel weather render sẵn, sáu effect hoạt động bằng JSON mẫu.
- Resize window và đổi template không để overlay cũ còn lại.
- Không ảnh hưởng panel music.

---

## CP-05 — Avatar SVG 2D và điều khiển cục bộ

**Mục tiêu:** thêm avatar presentation nhưng chưa làm TTS queue theo step.

### File được phép sửa

- `web/index.html`
- `web/app.css`
- `web/app.js`

### Công việc

1. Thêm SVG avatar inline và `AvatarController`.
2. Hỗ trợ states: `idle`, `thinking`, `speaking`, `explain`, `point_left`, `point_right`, `concerned`.
3. `speaking` chỉ là mouth-loop khi audio hiện tại phát, chưa phoneme lip-sync.
4. Đảm bảo avatar không che content stage trên desktop/mobile.
5. Không thay đổi protocol WebSocket voice hiện có.

### Tiêu chí qua checkpoint

- Avatar hoạt động với test button/debug local.
- Khi TTS hiện có bắt đầu/kết thúc, state speaking khởi động/dừng đúng.
- Không thêm dependency hoặc server/model mới.

---

## CP-06 — Planner Gemini và thay lượt final answer của Weather runtime

**Mục tiêu:** thay riêng lượt LLM sinh plain-text final answer sau tool bằng Planner structured output, chưa thay đổi protocol stream/frontend.

### File được phép sửa

- `rag_manager/presentation/planner.py` — file mới.
- `rag_manager/presentation/prompts.py` hoặc prompt asset mới — nếu cần.
- `tests/test_presentation_planner.py` — mock test, không gọi API.
- `rag_manager/llm/function_calling_runtime.py`.
- `rag_manager/agents/weather_agent.py`.
- Có thể thêm một script kiểm tra thủ công trong `scripts/`, chỉ khi cần.

### Công việc

1. Tách runtime tại mốc tool `completed`: trả weather result/tool trace/facts, không tự gọi lại LLM để sinh plain-text answer.
2. Planner nhận: query, history rút gọn, data weather đã validate/compact, `template_id`, capabilities.
3. Ép output theo `PresentationPlan` bằng structured output nếu model hiện tại hỗ trợ; nếu không, dùng function declaration/schema hiện hữu theo cách tối thiểu.
4. Prompt bắt buộc: chỉ dùng tool facts; không HTML/CSS/JS/selector; tối đa 3 step; chọn focus/effect trong capability.
5. Ghép narration đã validate thành `final_answer` để compatibility với session/history hiện có.
6. Parse/API lỗi: không trả raw JSON ra người dùng. Dùng fallback narration sinh từ facts bởi code ở mức tối thiểu, rồi compiler tạo plan an toàn.

### Không làm tại checkpoint này

- Chưa thêm Planner node vào `graph.py`.
- Chưa thay đổi event NDJSON/frontend.
- Không đổi nhánh clarification, unavailable, error hoặc music.

### Tiêu chí qua checkpoint

- Mock test cover tool completed dừng đúng chỗ, valid plan, invalid JSON, model trả enum sai, timeout/API error.
- Test regression: LLM clarification trước tool và code clarification/error sau tool vẫn trả text như cũ.
- Chạy thử có kiểm soát với một kết quả weather và kiểm tra narration không bịa facts.
- Duyệt prompt và chất lượng kế hoạch trước tích hợp graph.

---

## CP-07 — Tích hợp Planner vào graph và phát text stream

**Mục tiêu:** chỉ thay nhánh weather `completed` bằng Planner đã có; narration vẫn stream ra chat.

### File được phép sửa

- `rag_manager/state.py`
- `rag_manager/graph.py`
- Có thể `rag_manager/agents/visual_agent.py` hoặc `rag_manager/tools/visual_tools.py` chỉ để đưa metadata/capability đã tồn tại ra payload.
- Test graph mới.

### Thay đổi dự kiến

```text
weather completed -> visual -> presentation_planner -> END
weather clarification/error/unavailable -> END (không đổi)
music -> visual -> END (không đổi)
```

1. Thêm state `presentation_plan` và `compiled_presentation_plan`.
2. `visual_node` trả đủ `template_id`/capabilities cần thiết, không đổi quy tắc render.
3. Node Planner chỉ chạy khi payload là weather hợp lệ; node này đặt `final_answer` từ narration đã compile.
4. Callback stream hiện có phát từng `step.narration` theo thứ tự dưới domain `weather`; frontend hiện tại tiếp tục nhận `text_delta` không cần hiểu schema plan ở giai đoạn này.
5. Planner/compiler lỗi được log và dùng fallback plan/narration an toàn; không trả raw output hoặc làm gãy panel.
6. Đo timing riêng: `presentation_planner_ms`, `presentation_compiler_ms`.

### Tiêu chí qua checkpoint

- Regression test cho weather completed, LLM clarification, code clarification, unavailable/error và music.
- Weather completed stream narration thành `text_delta`, rồi `final` có `final_answer` tương ứng.
- Với plan lỗi, UI vẫn có chat answer + weather panel như bản hiện tại.

---

## CP-08 — Mở rộng NDJSON cho panel/step và PresentationQueue

**Mục tiêu:** panel hiện sớm; frontend diễn từng step theo thứ tự.

### File được phép sửa

- `web_app.py`
- `web/app.js`
- Test endpoint stream nếu cơ sở test phù hợp.

### Event mới đề xuất

```json
{"type":"panel_ready","panel":{"ui_type":"weather","template_id":"weather_forecast","html":"..."}}
{"type":"presentation_step","step":{"narration":"...","target_id":"weather.day.0.rain","effect":"draw_circle","gesture":"point_right"}}
```

### Lưu ý thiết kế

- Đây là checkpoint có ảnh hưởng đến protocol stream hiện tại. Chỉ thực hiện sau khi duyệt contract event.
- `final` vẫn giữ để tương thích frontend/session hiện có trong giai đoạn chuyển tiếp.
- `panel_ready` chỉ phát cho weather completed có panel hợp lệ.
- `presentation_step` chỉ phát từ compiled plan, không phát raw LLM output.
- `PresentationQueue` làm tuần tự: thêm narration chat -> effect/avatar -> TTS -> chờ audio xong -> step kế tiếp.

### Tiêu chí qua checkpoint

- Panel xuất hiện trước phần trình bày.
- Không đọc lặp lại toàn bộ `final_answer` sau khi đã đọc từng step.
- Clarification/error không phát panel/event presentation.
- Refresh session vẫn render active panel như hiện tại.

---

## CP-09 — Kiểm thử chấp nhận và rollback

**Mục tiêu:** xác nhận production behavior và có đường tắt an toàn.

### Test scenario bắt buộc

1. Weather current/hourly/single-day/multi-day.
2. Câu hỏi ưu tiên mưa, nhiệt độ, gió và tổng quan.
3. LLM hỏi làm rõ trước tool.
4. WeatherTools hỏi làm rõ sau tool validation.
5. Redis/API unavailable và tool error.
6. Planner trả JSON sai, timeout hoặc bịa focus/effect.
7. Template thiếu một anchor/capability.
8. Panel weather rồi user chuyển sang music.
9. Mobile, resize panel, `prefers-reduced-motion`.

### Rollback

- Feature flag nội bộ `presentation_enabled` mặc định chỉ bật sau CP-08.
- Tắt flag: giữ nguyên chat, TTS hoàn chỉnh hiện tại và panel cuối cùng; không gọi Planner, không phát effect/animation.
- Không rollback bằng sửa WeatherTools hoặc xóa dữ liệu session.

### Tiêu chí hoàn tất MVP

- Tất cả nhánh không-weather hoạt động như trước.
- Weather completed có panel, narration theo step, avatar và animation đúng target.
- Không có raw LLM output đi thẳng đến DOM/CSS/JS.
- Có test cho compiler và regression flow quan trọng.

---

## Phần chỉ xem xét sau MVP

- CP-B1: chuyển `iframe srcdoc` sang panel DOM trực tiếp nếu overlay iframe không đủ ổn định hoặc cần zoom/trace chart phức tạp.
- CP-B2: thêm `zoom_to`, `draw_underline`, `trace_chart`, `show_badge` sau khi sáu effect MVP ổn định.
- CP-B3: mở rộng capability cho domain khác; mỗi domain chỉ cần template anchors/capabilities và adapter dữ liệu riêng, không nhân bản graph.

---

## Bổ sung kiến trúc đã chốt — Streaming từng step an toàn

Luồng presentation được điều chỉnh thành:

```text
Gemini stream JSON
  -> backend incremental parser
  -> một step JSON đã hoàn chỉnh
  -> Pydantic validate PresentationStep
  -> Presentation Compiler kiểm tra capability và tạo CompiledPresentationStep
  -> gửi presentation_step đã duyệt
  -> frontend: chat text + effect/avatar + TTS ngay cho step đó
```

Frontend **không** đọc JSON stream thô từ Gemini và không tự parse/duyệt plan. Điều này giữ hàng rào an toàn: một phần JSON chưa hoàn chỉnh, enum sai, target không tồn tại hoặc effect không được template cho phép sẽ không bao giờ đến DOM/TTS/avatar.

Hệ quả đối với các checkpoint sau:

- CP-06 phải cung cấp stream JSON từ Gemini và incremental parser ở backend; parser chỉ phát nội bộ khi tách được một object step hoàn chỉnh.
- CP-07 gọi Pydantic và Compiler cho từng step hoàn chỉnh, phát narration tương ứng thành `text_delta` và giữ các step đã duyệt trong state. `final_answer` vẫn là phần narration ghép theo thứ tự sau khi stream kết thúc.
- CP-08 phát `panel_ready` trước và phát từng `presentation_step` đã compile ngay khi backend duyệt, không đợi cả `PresentationPlan`. Frontend xếp hàng từng event để TTS/effect không chồng lên nhau.
- Nếu stream/parse/model lỗi trước khi có step hợp lệ, backend dùng fallback narration/compiled step an toàn; không gửi JSON thô cho người dùng.

Checkpoint CP-01 chỉ định nghĩa schema nên không cần thay đổi. CP-02 đến CP-05 vẫn giữ nguyên.

---

## Bổ sung quyết định CP-04 — Weather dùng Shadow DOM trực tiếp

Weather panel không còn dùng `iframe srcdoc`. Backend vẫn render Jinja template HTML như hiện tại; frontend đưa phần style và body của template vào `ShadowRoot` mở của `weatherTemplateHost`.

- CSS template được cô lập trong weather panel, không tác động chat hoặc music.
- Presentation controller truy cập `shadowRoot` để tìm `data-present-id`; SVG overlay vẫn nằm ở panel cha để vẽ circle/arrow.
- Music giữ iframe YouTube riêng, không thay đổi renderer hay sandbox của music.
- Khi đổi weather sang music hoặc xóa panel, frontend xóa Shadow DOM content và overlay để không giữ effect cũ.

---

## CP-04.1 — Anchor và capability theo interval cho `weather_single_day`

**Mục tiêu:** cho phép Planner chỉ chính xác một khung giờ trong dự báo một ngày, thay vì chỉ nhấn chỉ số tổng quan của cả ngày.

**Thứ tự:** thực hiện sau CP-04 và trước CP-06. Không tích hợp Gemini/graph trong checkpoint này.

### File được phép sửa

- `rag_manager/visualization/assets/templates/weather/weather_single_day/template.html`
- `rag_manager/visualization/assets/templates/weather/weather_single_day/metadata.json`
- `tests/test_weather_presentation_templates.py`
- `tests/test_presentation_compiler.py` — chỉ để bổ sung fixture/test mapping pattern hai index.

### Contract semantic mới

Mỗi interval đã hiển thị trong hourly strip nhận các anchor:

```text
weather.day.{day_index}.interval.{interval_index}.summary
weather.day.{day_index}.interval.{interval_index}.temperature
weather.day.{day_index}.interval.{interval_index}.rain_risk
weather.day.{day_index}.interval.{interval_index}.wind      (chỉ khi template thực sự hiển thị gió theo giờ)
weather.day.{day_index}.interval.{interval_index}.condition
```

Ở layout hiện tại `weather_single_day`, template chỉ hiển thị time, icon/condition, temperature và rain probability cho từng interval. Do đó checkpoint này chỉ công bố capability: `hourly_summary`, `hourly_temperature`, `hourly_rain_risk`, `hourly_condition`; không công bố `hourly_wind` cho đến khi giao diện có số gió theo giờ thật.

Ví dụ planner output hợp lệ sau checkpoint:

```json
{
  "focus": "hourly_rain_risk",
  "entity": {"day_index": 0, "interval_index": 3},
  "effect": "draw_circle"
}
```

### Công việc

1. Gắn `data-present-id` động theo `loop.index0` vào từng phần tử hourly phù hợp; không đổi layout/nội dung hiển thị.
2. Thêm `presentation_capabilities` với `target_pattern` có cả `{day_index}` và `{interval_index}`, khai báo chính xác entity fields và effect whitelist.
3. Mở rộng Compiler để chỉ thay thế pattern có hai index khi cả hai là integer hợp lệ và nằm trong compact weather data; sai index/focus/effect phải fallback `overview` như CP-03.
4. Test render template thật có các anchor interval và test compiler cho interval hợp lệ, interval vượt phạm vi, field thiếu/sai kiểu.

### Tiêu chí qua checkpoint

- Không có capability nào trỏ vào data không hiển thị.
- Câu hỏi như “giờ nào mưa nhiều nhất/nóng nhất/mát nhất/trời nắng” có thể được Planner tương lai map vào target interval tương ứng.
- Compiler vẫn không nhận selector, HTML, CSS hay JavaScript từ Planner.
