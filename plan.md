**giữ một node weather; Manager nhận thấy người dùng muốn hỏi thời tiết nhưng thiếu thông tin rõ ràng thì hỏi trước những thông tin thiếu rõ ràng, và Manager chỉ nhận biết có biểu thức địa điểm/thời gian, không chuẩn hóa địa điểm, không tạo location_id và không validate lịch; Weather Agent tự extract location và time và xác thực lại; chỉ khi cả location\_id và canonical time hợp lệ mới đọc Redis và tiếp tục Aggregate → Visualization. còn không hợp lệ thì phải hỏi lại**

### Lưu ý khi triển khai: Manager phải nhận user message chứa query + history; Weather Agent phải truyền trực tiếp history vào messages và không append query thêm lần nữa

# luồng:
Input + history
    → Semantic Router(query, history)
    → Manager Agent(query, history)
        │
        ├─ Không có weather
        │   → tiếp tục routing hiện tại
        │
        └─ Có weather
            → Manager kiểm tra presence
                ├─ có location expression?
                └─ có time expression?
                    │
                    ├─ Thiếu location hoặc time
                    │   → manager_status = needs_clarification
                    │   → final_response = câu hỏi bổ sung
                    │   → không gọi node weather
                    │   → END
                    │
                    └─ Có cả hai expression
                        → route vào node weather hiện tại


Node weather
    run_weather_llm_pipeline(
    state,
    store,
    settings,
    client
)
    │
    ├─ Chuẩn bị query + relevant_history
    │
    ├─ LLM call 1: client.chat_json(...)
    │   └─ Trích xuất:
    │       ├─ location_text
    │       ├─ time_text
    │       └─ request_type_candidate
    │
    ├─ Python kiểm tra kết quả extraction
    │   │
    │   ├─ LLM API lỗi
    │   │   → status = error
    │   │   → code = llm1_api_error
    │   │   → không gọi location resolver
    │   │   → không gọi Redis
    │   │
    │   ├─ JSON lỗi hoặc không phải object
    │   │   → status = error
    │   │   → code = llm1_invalid_json
    │   │   → không gọi resolver/Redis
    │   │
    │   ├─ Thiếu field hoặc sai kiểu dữ liệu
    │   │   → status = error
    │   │   → code = llm1_schema_error
    │   │   → không gọi resolver/Redis
    │   │
    │   ├─ JSON hợp lệ nhưng thiếu location/time
    │   │   → status = needs_clarification
    │   │   → validation_result ghi rõ field bị thiếu
    │   │   → không gọi resolver
    │   │   → không gọi Redis
    │   │
    │   └─ Extraction đầy đủ
    │       → tiếp tục validate
    │
    ├─ Python resolve + validate
    │   │
    │   ├─ WeatherLocationResolver.resolve(location_text)
    │   │   │
    │   │   ├─ Resolver lỗi hoặc response sai
    │   │   │   → status = error
    │   │   │   → không gọi time validator/Redis
    │   │   │
    │   │   ├─ Không tìm thấy hoặc location mơ hồ
    │   │   │   → status = needs_clarification
    │   │   │   → không gọi time validator/Redis
    │   │   │
    │   │   └─ Location hợp lệ
    │   │       → lấy location_id
    │   │
    │   └─ WeatherTimeValidator.validate(
    │         time_text,
    │         request_type_candidate
    │       )
    │       ├─ Time không hợp lệ, mơ hồ,
    │       │   sai thứ/ngày hoặc vượt quá 5 ngày
    │       │   → status = needs_clarification
    │       │   → không gọi Redis
    │       │
    │       ├─ Validator lỗi hoặc response sai
    │       │   → status = error
    │       │   → không gọi Redis
    │       │
    │       └─ Time hợp lệ
    │           → tạo canonical_request:
    │               ├─ request_type
    │               ├─ location_id
    │               ├─ start_date nếu forecast
    │               └─ days nếu forecast
    │           → status nội bộ = ready_for_redis
    │
    ├─ Python gọi Redis trực tiếp
    │   │
    │   ├─ Chỉ thực hiện khi status nội bộ = ready_for_redis
    │   │
    │   ├─ request_type = current
    │   │   → store.get_current(location_id)
    │   │
    │   └─ request_type = forecast
    │       → store.get_forecast(
    │             location_id,
    │             days,
    │             start_date
    │         )
    │
    ├─ Python kiểm tra Redis response
    │   │
    │   ├─ Không có snapshot/location/ngày yêu cầu
    │   │   → status = unavailable
    │   │
    │   ├─ Forecast không trả đúng khoảng ngày yêu cầu
    │   │   → status = unavailable
    │   │
    │   ├─ Redis mất kết nối, JSON lỗi,
    │   │   response sai, payload sai hoặc timezone sai
    │   │   → status = error
    │   │
    │   └─ Response hợp lệ
    │       → status = completed
    │       → tạo weather_data envelope
    │
    ├─ Chuẩn hóa pre_llm2_status
    │   └─ Chỉ gồm:
    │       needs_clarification
    │       unavailable
    │       error
    │       completed
    │
    ├─ LLM call 2: client.chat_text(...)
    │   ├─ Nhận:
    │   │   ├─ query
    │   │   ├─ relevant_history
    │   │   ├─ extraction
    │   │   ├─ pre_llm2_status
    │   │   ├─ validation_result
    │   │   ├─ canonical_request
    │   │   ├─ redis_result
    │   │   ├─ redis_error
    │   │   └─ processing_error
    │   │
    │   ├─ needs_clarification
    │   │   → hỏi lại đúng location/time còn thiếu hoặc mâu thuẫn
    │   │
    │   ├─ unavailable
    │   │   → thông báo cache/snapshot không khả dụng
    │   │   → không hỏi lại location/time đã hợp lệ
    │   │
    │   ├─ error
    │   │   → thông báo lỗi hệ thống
    │   │
    │   └─ completed
    │       → trả lời chỉ từ dữ liệu Redis
    │
    └─ Nếu LLM call 2 lỗi API, timeout hoặc output rỗng
        → weather_status = error
        → code = llm2_api_error hoặc llm2_invalid_output
        → không fallback clarification
        → không fallback câu trả lời thời tiết
        → ghi log pre_llm2_status và final_status


    └─ Graph routing sau node weather
        │
        ├─ needs_clarification
        │   → final_response đã có
        │   → không Aggregate
        │   → không Visualization
        │   → END
        │
        ├─ unavailable
        │   → final_response đã có
        │   → không Visualization
        │   → END
        │
        ├─ error
        │   → tạo weather_error và thông báo lỗi hệ thống
        │   → final_response
        │   → không Visualization weather
        │   → END
        │
        └─ completed
            → Aggregate
            → Visualization
            → END
## Quy định 
1. Manager đánh giá trên query + history
Manager không chỉ kiểm tra câu "Ngày mai" mà phải tổng hợp ngữ cảnh liên quan:
History cung cấp ý định weather và địa điểm Hà Nội.
Query mới nhất cung cấp thời gian "Ngày mai".
Kết quả: đã có cả location và time → chuyển vào Weather Agent.

2. Weather Agent nhận được câu hỏi gốc qua history
Với schema:
{
  "query": "Ngày mai",
  "history": [
    {
      "role": "user",
      "content": "Thời tiết Hà Nội thế nào?"
    },
    {
      "role": "assistant",
      "content": "Bạn muốn xem thời tiết Hà Nội vào thời điểm nào?"
    },
    {
      "role": "user",
      "content": "Ngày mai"
    }
  ]
}
Weather agent phải extract trên toàn bộ đoạn hội thoại liên quan, không chỉ trên query. Vì vậy nó tổng hợp được:
{
  "location_text": "Hà Nội hoặc null",
  "time_text": "ngày mai hoặc null",
  "request_type_candidate": "current | forecast | null"
}
Sau đó mới resolve location, validate time và tạo ready_for_redis

3. Không đưa query vào message LLM hai lần
Về interface, cả Manager và Weather Agent vẫn nhận:
query + history
Nhưng vì history đã chứa query mới nhất, khi dựng danh sách message gửi LLM thì không append thêm "Ngày mai" lần nữa. query được giữ riêng để:
Xác định input mới nhất.
Ưu tiên thông tin mới hơn lịch sử.
Kiểm tra query có tiếp nối ngữ cảnh cũ hay chuyển chủ đề.
Phục vụ routing và logging.


4. Cần quy định rõ Python mới là nguồn quyết định request_type; request_type_candidate của LLM2 chỉ là gợi ý. Đồng thời phải chốt:
“hiện tại/bây giờ” → current
ngày cụ thể/ngày mai/khoảng ngày → forecast
“hôm nay” dùng forecast
#### khoảng ngày tính days theo số ngày ( ví dụ ngày mai thì days = 1)
13/7 đến 15/7 → start_date=2026-07-13, days=3
3 ngày tới → từ ngày mai, days=3

cách hiểu “thứ Tư” khi không có “tuần này/tuần tới" -> thì phải hỏi lại.

5. Semantic Router phải dựa trên history cho khoảng 2-3 câu tiếp nối trước khi chạy nhánh phân loại nhanh chỉ dựa trên query. Nếu không, query "Ngày mai" vẫn có thể không được nhận ra là weather.

6. WeatherDataToolGate nên lấy tham số thực thi trực tiếp từ weather_validation.request; không nên tin tham số do LLM2 gửi. Tool name sai thì chặn, tool name đúng thì code dùng bộ tham số đã validate.
7. Cần chốt nhánh multi-intent: nếu người dùng vừa hỏi weather thiếu dữ liệu vừa hỏi news/wiki, Manager sẽ dừng lại hỏi ý định người dùng là gì.

8. Quy định đúng về reference_datetime
Nên bổ sung:
WEATHER_TIMEZONE = "Asia/Ho_Chi_Minh"
EXPECTED_TIMEZONE_OFFSET_SECONDS = 25200

reference_datetime = datetime.now(ZoneInfo(WEATHER_TIMEZONE))

reference_datetime:
Do Python tạo một lần trong validator.
Không nhận từ LLM2.
Dùng để tính “hôm nay”, “ngày mai” theo giờ Việt Nam.
Canonical start_date phải cùng hệ ngày địa phương với forecast.days[].date trong snapshot.
Sau khi đọc Redis, kiểm tra timezone_offset_seconds == 25200 và ngày đầu tiên đúng start_date

# schema output

## Conversation input schema
{
  "query": "Ngày mai",
  "history": [
    {
      "role": "user",
      "content": "Thời tiết Hà Nội thế nào?"
    },
    {
      "role": "assistant",
      "content": "Bạn muốn xem thời tiết Hà Nội vào thời điểm nào?"
    },
    {
      "role": "user",
      "content": "Ngày mai"
    }
  ]
}
Quy định:
query là input mới nhất.
history là toàn bộ hội thoại và đã chứa query ở message cuối.
history[-1] phải là user message có content == query.
Chỉ chấp nhận role user và assistant.
Khi tạo message cho LLM, không append query lần thứ hai.
Query mới nhất được ưu tiên nếu mâu thuẫn với thông tin cũ.
History cũ chỉ được dùng nếu có liên quan đến yêu cầu hiện tại.

## Manager output
{
  "topics": ["weather"],
  "execution_mode": "single",
  "primary_intent": "weather",
  "dependencies": [],
  "news_query": "",
  "wiki_topic": "",
  "reason": "Người dùng hỏi thời tiết.",
  "weather_requirements": {
    "status": "needs_clarification",
    "has_location_expression": true,
    "has_time_expression": false,
    "missing_fields": ["time"],
    "clarification_question": "Bạn muốn xem thời tiết Hà Nội vào thời điểm nào?"
  }
}

Ba giá trị hợp lệ của weather_requirements.status:
not_applicable
needs_clarification
ready_for_weather
ready_for_weather chỉ có nghĩa là trong query + history đã xuất hiện địa điểm và thời gian. Nó không có nghĩa là location/time đã được xác thực và hoàn toàn không tương đương với ready_for_redis


Ví dụ thiếu location
Input:
Thời tiết ngày 13/7/2026 thế nào?
Manager output:
{
  "weather_requirements": {
    "status": "needs_clarification",
    "has_location_expression": false,
    "has_time_expression": true,
    "missing_fields": ["location"],
    "clarification_question": "Bạn muốn xem thời tiết ngày 13/07/2026 ở đâu?"
  }
}

Ví dụ thiếu time
Input:
Thời tiết Hà Nội thế nào?
Manager output:
{
  "weather_requirements": {
    "status": "needs_clarification",
    "has_location_expression": true,
    "has_time_expression": false,
    "missing_fields": ["time"],
    "clarification_question": "Bạn muốn xem thời tiết Hà Nội vào thời điểm nào?"
  }
}

Ví dụ đủ biểu thức nhưng chưa chắc hợp lệ
Input:
Thứ 4 ngày 17/7/2026 thời tiết Hà Nội thế nào?
Manager chỉ xác định:
{
  "weather_requirements": {
    "status": "ready_for_weather",
    "has_location_expression": true,
    "has_time_expression": true,
    "missing_fields": [],
    "clarification_question": null
  }
}
Manager không phát hiện mâu thuẫn. Weather Agent sẽ xử lý việc đó.

Thời gian nào được Manager coi là “có biểu thức”?
Manager coi là có time expression nếu người dùng cung cấp một cụm thời gian có khả năng xử lý:
hiện tại
bây giờ
hôm nay
tối nay
ngày mai
3 ngày tới
thứ Tư
thứ Tư tới
13/7/2026
từ 13/7 đến 15/7
... các cụm thời gian chính xác hoặc tương đối có thể tính toán
Manager chưa cần quyết định cụm đó có hoàn toàn xác định hay hợp lệ.

Manager clarification branch trong graph
Không cần thêm node clarification. manager_classify_node có thể trả thẳng:
{
  "manager_status": "needs_clarification",
  "final_response": "Bạn muốn xem thời tiết Hà Nội vào thời điểm nào?"
}

Conditional routing:
manager_classify
    ├─ manager_status=needs_clarification → END
    └─ còn lại → weather/news/wiki/parallel/sequential
  
Khi main.py nhận kết quả, final_response vẫn được thêm vào history như hiện tại. Lượt sau Manager nhận lại history.

## Weather Agent input schema
{
  "query": "Ngày mai",
  "history": [
    {
      "role": "user",
      "content": "Thời tiết Hà Nội thế nào?"
    },
    {
      "role": "assistant",
      "content": "Bạn muốn xem thời tiết Hà Nội vào thời điểm nào?"
    },
    {
      "role": "user",
      "content": "Ngày mai"
    }
  ]
}
Quy định:
LLM2 tự trích xuất lại location từ query + history.
LLM2 không được tạo ready_for_redis.
LLM2 không được tự tạo location_id

## Schema duy nhất LLM2 được phép trả khi extraction
{
  "location_text": "Hà Nội hoặc null",
  "time_text": "ngày mai hoặc null",
  "request_type_candidate": "current | forecast | null"
}
Các trường được phép null


ví dụ {
  "location_text": "Hà Nội",
  "time_text": "thứ Tư ngày 17/7/2026",
  "request_type_candidate": "forecast"
}

LLM2 chỉ trích xuất nguyên văn. Python chịu trách nhiệm parse và đối chiếu lịch.
LLM2 không được trả:
location_id
start_date
days
time_status
ready_for_redis

## Input của hàm validator tổng hợp
{
  "location_text": "Hà Nội",
  "time_text": "ngày mai",
  "request_type_candidate": "forecast"
}

### Output khi location không hợp lệ
{
  "status": "needs_clarification",
  "stage": "location",
  "code": "location_not_found",
  "details": {
    "requested_text": "Paris",
    "candidates": []
  }
}
llm2 nhận kết quả và sinh: Mình chưa xác định được địa điểm “Paris”. Bạn muốn xem thời tiết ở tỉnh hoặc thành phố nào?

Không cần validator sinh sẵn clarification_question.

### Output khi time không hợp lệ
{
  "status": "needs_clarification",
  "stage": "time",
  "code": "weekday_date_conflict",
  "details": {
    "provided_date": "2026-07-17",
    "provided_weekday": "thứ Tư",
    "actual_weekday": "thứ Sáu",
    "matching_weekday_date": "2026-07-15"
  }
}
LLM2 sinh câu hỏi:
Ngày 17/7/2026 là thứ Sáu. Bạn muốn xem thứ Tư ngày 15/7/2026 hay thứ Sáu ngày 17/7/2026?

### Output hợp lệ — current
{
  "status": "ready_for_redis",
  "request": {
    "request_type": "current",
    "location_id": "ha_noi"
  }
}

### Output hợp lệ — forecast
{
  "status": "ready_for_redis",
  "request": {
    "request_type": "forecast",
    "location_id": "ha_noi",
    "start_date": "2026-07-14",
    "days": 1
  }
}

## Lưu vào tool_state
{
  "weather_validation": {
    "status": "ready_for_redis",
    "request": {
      "request_type": "forecast",
      "location_id": "ha_noi",
      "start_date": "2026-07-14",
      "days": 1
    }
  }
}
Object này chỉ do Python validator tạo

## Sau đó
LLM2 nhận ready_for_redis
    → đề xuất get_current_weather hoặc get_weather_forecast
    → code gate đối chiếu tool call với weather_validation["status"] == "ready_for_redis"
    → khớp thì gọi data tool hiện tại