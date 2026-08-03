User
  ↓
Weather Agent / LLM
  ├─ thiếu location hoặc time scope
  │    → LLM hỏi lại → chat + TTS + avatar speaking → kết thúc
  │
  └─ gọi get_weather
       ↓
WeatherTools: validate + normalize + Redis retrieval
  ├─ needs_clarification
  │    → code trả câu hỏi fallback → chat + TTS + avatar speaking → kết thúc
  │
  ├─ unavailable/error
  │    → code trả thông báo → chat + TTS + avatar speaking → kết thúc
  │
  └─ completed + weather data đã xác thực
       ↓
VisualTools (code)
  ├─ chọn template deterministic
  ├─ render HTML
  └─ tải template capabilities
       ↓
Presentation Planner Agent (Gemma API)
  ├─ chọn dữ liệu đáng nói theo câu hỏi
  ├─ viết narration theo các step
  ├─ chọn semantic focus
  └─ chọn gesture intent
       ↓
Presentation Compiler (code)
  ├─ validate plan
  ├─ focus → data-present-id
  ├─ gesture intent → avatar gesture hợp lệ
  └─ effect → CSS/SVG effect hợp lệ
       ↓
Frontend Presentation Controller
  ├─ hiển thị HTML panel
  ├─ hiển thị text chat
  ├─ TTS theo step
  ├─ SVG avatar nói/ra gesture
  └─ highlight/reveal/draw trên template

Avatar MVP
Tạo avatar SVG trong frontend, không cần gọi model hay server riêng.
Các state tối thiểu:
idle       : đứng yên
thinking   : đang xử lý
speaking   : miệng chuyển động khi TTS phát
explain    : gesture giải thích
point_left : chỉ sang trái
point_right: chỉ sang phải
Mỗi state là CSS class trên SVG:
<div id="avatar-stage" data-avatar-state="idle">
  <svg id="lumi-avatar">
    <!-- body, eyes, mouth, left arm, right arm -->
  </svg>
</div>
avatar.setState("speaking");
avatar.playGesture("point_right");
avatar.stopSpeaking();
Trong MVP, “lip-sync” là mouth loop trong lúc audio phát, chưa cần phân tích phoneme. Avatar sẽ đủ tự nhiên khi ba thứ khớp nhau:
TTS đang nói
+ avatar speaking/gesture
+ template highlight đúng vùng
Sửa chủ yếu ở:
[index.html](D:\\RAG_ManageAgent_Lumi\\code_toolcall\\web\\index.html): thêm khu vực avatar.
[app.css](D:\\RAG_ManageAgent_Lumi\\code_toolcall\\web\\app.css): SVG states và animation.
[app.js](D:\\RAG_ManageAgent_Lumi\\code_toolcall\\web\\app.js): AvatarController.
Không cần cài dependency mới cho avatar SVG/CSS.
2. Chuẩn hoá Presentation Plan
Planner không trả HTML/CSS/JavaScript. Nó chỉ trả ý định trình bày có schema rõ ràng.
{
  "schema_version": "presentation_plan.v1",
  "steps": [
    {
      "narration": "Ngày mai ở Hà Nội vẫn có thể đi picnic, nhưng nên chuẩn bị áo mưa mỏng.",
      "focus": "day_summary",
      "entity": {
        "day_index": 0
      },
      "emphasis": "high",
      "gesture": "explain"
    },
    {
      "narration": "Xác suất mưa tăng vào buổi chiều, vì vậy bạn nên ưu tiên đi buổi sáng.",
      "focus": "rain_risk",
      "entity": {
        "day_index": 0
      },
      "emphasis": "high",
      "gesture": "point_right"
    }
  ]
}
Giới hạn MVP:
tối đa 3 steps;
mỗi step 1–2 câu ngắn;
focus, emphasis, gesture phải thuộc enum;
chỉ dùng facts từ tool result;
không suy đoán dữ liệu thời tiết không có trong payload.
Tạo các module mới:
rag_manager/presentation/
  schemas.py
  planner.py
  compiler.py
  capabilities.py
schemas.py: Pydantic models của Planner output.
planner.py: gọi Gemma API và parse structured output.
compiler.py: validate và chuyển semantic plan thành lệnh UI.
capabilities.py: đọc capability metadata theo template.
pydantic và google-genai đã có trong [requirements.txt](D:\\RAG_ManageAgent_Lumi\\code_toolcall\\requirements.txt), nên không cần cài thêm package cho phần này.
3. Khai báo capability cho mỗi template
Mỗi template thêm hai phần.
Thứ nhất là HTML semantic anchors:
<section data-present-id="weather.overview">...</section>

<article data-present-id="weather.day.0">...</article>
<div data-present-id="weather.day.0.rain">...</div>

<section data-present-id="weather.temperature_chart">...</section>
day.0, day.1 được Jinja tạo theo index của forecast day.
Thứ hai là metadata, ví dụ trong weather_forecast/metadata.json:
{
  "presentation_capabilities": {
    "overview": {
      "target_id": "weather.overview",
      "allowed_effects": ["reveal", "highlight"]
    },
    "day_summary": {
      "target_pattern": "weather.day.{day_index}",
      "allowed_effects": ["highlight", "zoom"]
    },
    "rain_risk": {
      "target_pattern": "weather.day.{day_index}.rain",
      "allowed_effects": ["highlight", "pulse"]
    },
    "temperature_trend": {
      "target_id": "weather.temperature_chart",
      "allowed_effects": ["highlight", "draw"]
    }
  }
}
Khi tạo template mới, chỉ cần:
thêm data-present-id;
khai báo capabilities;
dùng effect đã có.
Không cần sửa Planner hoặc frontend cho một template mới.
4. Presentation Compiler
Compiler là hàng rào giữa LLM và giao diện.
Planner nói:
{
  "focus": "rain_risk",
  "entity": {"day_index": 0},
  "gesture": "point_right"
}
Compiler tạo:
{
  "narration": "Xác suất mưa tăng vào buổi chiều...",
  "target_id": "weather.day.0.rain",
  "effect": "pulse",
  "gesture": "point_right"
}
Các kiểm tra bắt buộc:
focus có nằm trong metadata không?
day_index có tồn tại trong data không?
target có thực sự tồn tại trong template không?
effect có được capability đó cho phép không?
gesture có thuộc danh sách avatar hỗ trợ không?
Nếu lỗi:
focus không hợp lệ
→ target fallback = weather.overview
→ effect = highlight
→ gesture = explain
Bot vẫn trả lời; chỉ giảm độ chính xác của animation, không làm hỏng UI.
5. Sửa luồng backend hiện tại
Hiện tại graph là:
weather → visual → END
Đổi thành:
weather → visual → presentation_planner → END
Ở [graph.py](D:\\RAG_ManageAgent_Lumi\\code_toolcall\\rag_manager\\graph.py):
giữ nguyên nhánh needs_clarification, error, unavailable;
chỉ đi sang visual khi Weather Tool trả completed;
visual chọn template và render panel như hiện tại;
node mới presentation_planner nhận:query;
relevant history;
agent_result.data;
visualization_payload.template_id;
template capabilities;

node Planner trả presentation_plan;
Compiler trả compiled_presentation_plan.
Điểm cần refactor: lượt LLM hiện tại sau tool đang trả plain-text answer trong run_weather(). Lượt đó sẽ được thay bằng Planner sinh structured steps có narration.
Như vậy không cần một call Planner “chồng” lên câu trả lời hiện có:
Hiện tại:
LLM gọi tool → LLM sinh answer text

Sau thay đổi:
LLM gọi tool → Planner sinh presentation steps + narration
6. Streaming và đồng bộ frontend
Hiện frontend chỉ nhận panel trong event final, và TTS chỉ đọc toàn bộ câu trả lời sau khi đã xong. Cần đổi thành event có kiểu rõ ràng:
{"type": "panel_ready", "panel": {"ui_type": "weather", "html": "..."}}
{
  "type": "presentation_step",
  "step": {
    "narration": "Ngày mai có thể đi picnic...",
    "target_id": "weather.day.0",
    "effect": "highlight",
    "gesture": "explain"
  }
}
Frontend xử lý:
panel_ready
→ render template ngay

presentation_step 1
→ thêm narration vào chat
→ TTS đọc narration
→ avatar speaking + explain
→ reveal/highlight target

audio step 1 kết thúc
→ clear/persist effect nhẹ
→ chạy step 2
TTS WebSocket hiện có sẽ được tái sử dụng, nhưng thay vì chỉ gọi speakCompletedResponse(finalAnswer), tạo PresentationQueue để gửi từng step.narration tuần tự.
7. Effect UI MVP
Chỉ chốt 5 effect ban đầu:
reveal        : fade/slide vào
highlight     : viền + background sáng
pulse         : nhấp nhẹ vùng quan trọng
dim_others    : làm mờ các vùng khác
draw          : SVG path/arrow xuất hiện
Không cho Planner tự đặt duration, CSS transform, selector hay code.
8. Thứ tự triển khai
Thêm SVG Avatar + AvatarController, chưa có Planner.
Thêm data-present-id và effect CSS vào ba weather template.
Thêm metadata presentation_capabilities.
Viết Compiler và unit test mapping/fallback.
Tách lượt “final answer sau tool” thành Presentation Planner structured output.
Thêm node Planner vào LangGraph.
Thêm event panel_ready và presentation_step.
Đổi TTS sang queue theo step.
Test các nhánh: hỏi lại bởi LLM, hỏi lại bởi code, lỗi service, current weather, hourly, single-day và multi-day forecast.
Không cần Colab, host Gemma, GPU hay cài model. Chỉ cần API key Google hiện có, backend đang chạy và phần frontend SVG/CSS/JavaScript.



Dưới đây là kế hoạch triển khai riêng cho lớp “presentation tools” của Lumi, áp dụng trước cho HTML template weather.
Mục tiêu: Planner Agent quyết định “nói gì, cần nhấn vùng nào, dùng hiệu ứng nào”; code và frontend quyết định “có được phép không và vẽ thế nào”.
Presentation Planner Agent
    ↓ PresentationPlan (JSON an toàn)
Presentation Compiler
    ↓ CompiledStep (DOM target + effect hợp lệ)
Frontend Presentation Engine
    ↓
HTML template + SVG overlay + avatar + TTS
1. Quy ước semantic trong template
Mỗi template cần gắn ID ổn định cho những vùng có thể được trình bày. Đây là điều kiện duy nhất khi tạo template mới; không phải sửa logic frontend.
Ví dụ weather_forecast.html:
<section data-present-id="weather.overview">
  ...
</section>

<article data-present-id="weather.day.0">
  <h3 data-present-id="weather.day.0.summary">Hôm nay</h3>

  <div data-present-id="weather.day.0.temperature">
    26–32°C
  </div>

  <div data-present-id="weather.day.0.rain">
    Khả năng mưa: 78%
  </div>

  <div data-present-id="weather.day.0.wind">
    Gió: 18 km/h
  </div>
</article>

<svg data-present-id="weather.temperature_chart">
  ...
</svg>
Nguyên tắc:
data-present-id là ID semantic, không dùng CSS selector do LLM tạo.
ID phải mô tả ý nghĩa, không mô tả giao diện: weather.day.0.rain, không phải blue-card-right.
Data động dùng index hoặc khóa ổn định: weather.day.{day_index}.rain.
Một template chỉ khai báo vùng thực sự có ích cho lời trình bày, không cần gắn ID cho mọi div.
2. Khai báo capability của từng template
Mỗi template có metadata để giới hạn những gì Planner được phép yêu cầu.
WEATHER_FORECAST_CAPABILITIES = {
    "overview": {
        "target_id": "weather.overview",
        "allowed_effects": ["reveal", "highlight"]
    },
    "day_summary": {
        "target_pattern": "weather.day.{day_index}.summary",
        "allowed_effects": ["highlight", "pulse", "draw_underline"]
    },
    "temperature": {
        "target_pattern": "weather.day.{day_index}.temperature",
        "allowed_effects": ["highlight", "pulse", "draw_circle"]
    },
    "rain_risk": {
        "target_pattern": "weather.day.{day_index}.rain",
        "allowed_effects": [
            "highlight",
            "pulse",
            "draw_circle",
            "draw_arrow"
        ]
    },
    "temperature_trend": {
        "target_id": "weather.temperature_chart",
        "allowed_effects": ["highlight", "zoom_to", "trace_chart"]
    }
}
Planner chỉ nhận metadata này, không nhận HTML đầy đủ. Nhờ vậy agent không cần hiểu cấu trúc CSS/DOM cụ thể.
3. Presentation Planner Agent
Planner chạy sau khi:
Weather Agent đã gọi tool thành công.
Code đã validate, chuẩn hóa dữ liệu.
Code đã chọn template.
Template đã render hoặc ít nhất đã xác định template_id.
Đầu vào của Planner:
{
  "user_query": "Ngày mai ở Hà Nội có cần mang ô không?",
  "validated_data": {
    "location": "Hà Nội",
    "days": [
      {
        "date": "2026-07-31",
        "rain_probability": 78,
        "temperature_min": 26,
        "temperature_max": 32
      }
    ]
  },
  "template_id": "weather_forecast",
  "capabilities": {
    "rain_risk": {
      "allowed_effects": ["highlight", "pulse", "draw_circle"]
    }
  }
}
Đầu ra là structured JSON, không phải HTML/CSS/JavaScript:
{
  "schema_version": "presentation_plan.v1",
  "steps": [
    {
      "narration": "Ngày mai tại Hà Nội có khả năng mưa khoảng 78 phần trăm.",
      "focus": "rain_risk",
      "entity": { "day_index": 0 },
      "effect": "draw_circle",
      "gesture": "point_right"
    },
    {
      "narration": "Bạn nên mang ô, nhất là nếu ra ngoài vào buổi chiều.",
      "focus": "temperature",
      "entity": { "day_index": 0 },
      "effect": "highlight",
      "gesture": "explain"
    }
  ]
}
Quy tắc prompt quan trọng:
Dùng duy nhất dữ liệu đã validate.
Không tự tạo số liệu thời tiết.
Chỉ dùng focus và effect có trong capabilities.
Tối đa 2–3 bước cho câu hỏi đơn giản.
Mỗi bước là 1–2 câu dễ đọc bằng TTS.
Không trả HTML, CSS, selector, JavaScript hoặc lệnh frontend.
Với Gemini, ban đầu nên dùng Structured Output/Pydantic schema. Không cần khai báo native function calling cho phần này ngay.
4. Presentation Compiler — lớp an toàn bắt buộc
Compiler là code Python, nhận output của Planner rồi biên dịch thành lệnh frontend.
Nó thực hiện:
Parse JSON theo schema.
Kiểm tra focus có thuộc template hiện tại.
Kiểm tra effect có được phép cho focus đó.
Kiểm tra entity có hợp lệ, ví dụ day_index không vượt dữ liệu thật.
Tạo target_id thực tế.
Fallback khi output agent lỗi.
Ví dụ:
Planner:
focus = rain_risk
entity.day_index = 0
effect = draw_circle

Compiler:
target_id = weather.day.0.rain
effect = draw_circle
Đầu ra compiler:
{
  "narration": "Ngày mai tại Hà Nội có khả năng mưa khoảng 78 phần trăm.",
  "target_id": "weather.day.0.rain",
  "effect": "draw_circle",
  "gesture": "point_right"
}
Nếu Planner yêu cầu trace_chart cho vùng không có chart, compiler không thực thi yêu cầu đó. Nó fallback về:
{
  "target_id": "weather.day.0.rain",
  "effect": "highlight"
}
Hoặc chỉ đọc narration nếu không có target hợp lệ. Vì thế LLM không thể làm hỏng layout hay chạy mã tùy ý.
5. Bộ presentation tools/frontend effects
MVP nên làm sáu effects dưới đây:
Effect	Cách frontend thực hiện
reveal	Thêm class để vùng xuất hiện dần
highlight	Thêm viền, nền sáng hoặc glow
dim_others	Phủ overlay nhẹ lên panel, trừ vùng mục tiêu
pulse	Scale/glow ngắn trong khoảng 0.5–1 giây
draw_circle	SVG overlay vẽ ellipse quanh DOM element
draw_arrow	SVG overlay animate đường mũi tên tới DOM element

Giai đoạn sau:
Effect	Khi nên thêm
draw_underline	Khi template có KPI hoặc đoạn văn bản ngắn
zoom_to	Khi panel nhiều card hoặc màn hình nhỏ
trace_chart	Khi chart là SVG/canvas có API rõ ràng
show_badge	Khi cần cảnh báo “Mưa cao”, “Nên mang ô”

Không đưa duration, màu sắc, CSS class vào output LLM. Các giá trị này do frontend định nghĩa cố định để giao diện nhất quán.
6. SVG overlay cho draw_circle và draw_arrow
Frontend có một SVG overlay phủ toàn khu vực panel:
<div id="presentation-stage">
  <iframe id="weather-panel"></iframe>

  <svg id="presentation-overlay" aria-hidden="true">
    <!-- circle, arrow được tạo động tại đây -->
  </svg>
</div>
Khi nhận một step:
Tìm phần tử theo data-present-id.
Lấy tọa độ bằng getBoundingClientRect().
Quy đổi tọa độ về presentation-stage.
Tạo SVG ellipse hoặc path.
Animate stroke-dasharray/stroke-dashoffset để có cảm giác đang được vẽ.
Xóa overlay sau khi chuyển sang bước tiếp theo.
Lưu ý riêng cho code hiện tại: panel weather đang được render bằng iframe srcdoc. Frontend cha không thể tìm DOM bên trong iframe bằng selector thông thường nếu chưa xử lý đúng thời điểm tải. Có hai lựa chọn:
MVP: sau sự kiện iframe.onload, truy cập iframe.contentDocument, tìm data-present-id bên trong và đo tọa độ tương đối với iframe.
Tốt hơn về lâu dài: render HTML template trực tiếp vào một <div id="panel-root"> thay vì iframe. Khi đó highlight, overlay, zoom và responsive sẽ đơn giản, ổn định hơn.
Tôi nghiêng về chuyển weather panel từ iframe srcdoc sang một vùng DOM trực tiếp khi triển khai animation.
7. Event từ backend sang frontend
Hiện backend chỉ gửi final panel. Cần thêm hai event NDJSON.
{
  "type": "panel_ready",
  "panel": {
    "ui_type": "weather",
    "template_id": "weather_forecast",
    "html": "<section>...</section>"
  }
}
{
  "type": "presentation_step",
  "step": {
    "narration": "Ngày mai tại Hà Nội có khả năng mưa khoảng 78 phần trăm.",
    "target_id": "weather.day.0.rain",
    "effect": "draw_circle",
    "gesture": "point_right"
  }
}
Luồng người dùng thấy:
Tool trả dữ liệu thành công
    ↓
Panel HTML xuất hiện ngay
    ↓
Avatar bắt đầu đọc bước 1
    ↓
Vòng tròn được vẽ quanh xác suất mưa
    ↓
Avatar đọc bước 2
    ↓
Highlight nhiệt độ hoặc khuyến nghị
    ↓
Hoàn tất
Panel không phải chờ Planner nói xong mới hiện.
8. Avatar phối hợp với tool
Mỗi step có gesture, nhưng avatar chỉ nhận enum an toàn:
idle
thinking
speaking
explain
point_left
point_right
celebrate
concerned
Ví dụ mapping:
draw_circle + vùng bên phải panel → point_right
draw_arrow + vùng bên trái panel → point_left
highlight số liệu → explain
cảnh báo mưa lớn → concerned
TTS và animation dùng cùng hàng đợi PresentationQueue:
Nhận step
  → thêm narration vào chat
  → chạy effect trên panel
  → đổi pose avatar
  → phát TTS narration
  → audio kết thúc
  → xóa effect tạm thời
  → chạy step kế tiếp
Do đó avatar không chỉ nói, mà chuyển động đồng bộ với vùng đang được chỉ ra.
9. Thay đổi mã nguồn dự kiến
rag_manager/
  presentation/
    schemas.py           # PresentationPlan, PresentationStep
    capabilities.py      # capability của từng template
    planner.py           # gọi Gemini tạo structured plan
    compiler.py          # validate + map semantic → DOM target

  agents/
    visual_agent.py      # bổ sung metadata capability trong output

  graph.py               # thêm presentation_planner_node
  state.py               # presentation_plan, compiled_steps

web/
  index.html             # avatar stage, panel-root, SVG overlay
  app.js                 # PresentationQueue, OverlayController, AvatarController
  app.css                # highlight/pulse/reveal/avatar/effect styles

rag_manager/templates/
  weather_*.html         # data-present-id
10. Thứ tự triển khai an toàn
Thêm data-present-id và presentation_capabilities cho các template weather.
Làm frontend effects thủ công với một JSON mẫu, chưa cần LLM.
Viết PresentationCompiler và unit test toàn bộ mapping/fallback.
Tạo Planner Gemini trả structured JSON.
Nối Planner vào graph sau bước visual.
Thêm event panel_ready để render panel sớm.
Thêm presentation_step và PresentationQueue.
Thêm avatar SVG 2D, pose và mouth animation đồng bộ TTS.
Test các case: thời tiết một ngày, forecast nhiều ngày, câu hỏi mơ hồ, tool lỗi, output Planner sai schema.
Clarification và error vẫn giữ luồng hiện tại: hiện text trong chat + avatar/TTS đọc câu hỏi/lỗi, nhưng không render panel mới, không gọi Planner, không chạy presentation tools.


Nếu biến thành hướng nghiên cứu, câu hỏi nghiên cứu có thể là:
Liệu một grounded multi-agent presentation director, với storyboard có kiểm chứng và render critic, có cải thiện factual grounding, narration-animation coherence, user comprehension và perceived naturalness so với single-agent hoặc rule-based animation hay không?

Baseline nên gồm:
Template + animation code thuần, không LLM Director.
Planner hiện tại một agent.
Director Agent không critic.
Director + Plan Critic.
Director + Plan Critic + Render Critic.
Metric:
factual grounding: lời nói có đúng dữ liệu thật không;
target accuracy: nói fact nào có focus đúng vùng đó không;
narration-animation coherence;
visual stability/readability;
thời gian phản hồi và chi phí;
đánh giá người dùng: dễ hiểu, tự nhiên, giống MC, không rối.
Khuyến nghị kiến trúc: chưa nên tiếp tục CP-10.2 chỉ như một hiệu ứng CSS riêng lẻ. Nên chốt trước Action DSL và Director/Plan Critic contract; rồi animation vòng vẽ dần sẽ là renderer plugin đầu tiên của kiến trúc đó.

Có, hướng đó tốt hơn rõ rệt cho mục tiêu nghiên cứu và mở rộng của Lumi.
Nó tách đúng bốn trách nhiệm:
Planner bảo đảm nội dung/fact đúng dữ liệu.
Director quyết định cách diễn đạt trực quan, không làm lẫn với việc “nói gì”.
TTS Performer tạo audio và mốc thời gian thật.
Compiler biến mọi thứ thành một Timeline Contract có thể kiểm tra, lưu lại, phát lại và benchmark.
Điểm mạnh nhất là animation không còn phụ thuộc ước lượng “một câu dài bao lâu”, mà bám theo audio thực tế: đang nói đến “96%” thì mới bắt đầu vẽ vòng/đưa bút tới vùng xác suất mưa.
Tuy nhiên, không nên làm cả bốn tầng một lúc. Lộ trình hợp lý:
Giữ baseline hiện tại: Planner + Compiler deterministic + Browser TTS.
Thêm TTS Performer trả audio/duration, vẫn chưa cần Director.
Thêm Director Agent + Action DSL.
Thêm timestamps mức từ/câu và nâng Timeline Contract.
Cuối cùng mới thêm Critic để đo/fix lỗi plan và render.
Như vậy mỗi cải tiến đều đo được nó giúp ích gì, thay vì tăng độ phức tạp nhưng không biết thành phần nào tạo giá trị.

**Áp dụng cho Lumi**: Bạn cần phân tách rạch ròi các Agent trên Colab/Server thành một dây chuyền sản xuất:**Planner Agent (Gemma)**: Chỉ tập trung đọc dữ liệu thời tiết/bài học để chọn ra các Fact đáng nói nhất và viết lời thoại (Narration Text).**Director Agent (Code/LLM nhỏ)**: Nhận lời thoại từ Planner, đối chiếu với danh sách các thẻ HTML hiện có để chèn thêm các ý định hoạt họa (effect, target_id).**TTS Performer**: Chuyển lời thoại thành file tiếng + Trích xuất mốc thời gian mili-giây [2603.25870v1_draw_representations.pdf].**Presentation Compiler (Code cứng)**: Gộp file tiếng, mốc thời gian, và các ý định hoạt họa lại thành một file **Timeline Contract duy nhất** (như mẫu ở Bài 1) rồi đẩy xuống Frontend [2604.25220v1_animation.pdf].
Hiện nay đã làm được như này chưa? ( ngoại trừ director agent là để phát triển sau) (chỉ trả lời)