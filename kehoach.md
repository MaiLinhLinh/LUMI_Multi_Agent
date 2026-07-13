Kiến trúc đích nên là:
Cấu hình 63 tỉnh: location_id + aliases + lat/lon
        ↓
Background Worker gọi OpenWeather bằng lat/lon mỗi 3 giờ
        ↓
Chuẩn hóa + giữ nguyên raw response
        ↓
Ghi snapshot Redis theo location_id
        ↓
Weather Agent extract cụm địa danh
        ↓
resolve_location tool → location_id
        ↓
weather tools đọc Redis
        ↓
Weather Agent tổng hợp câu trả lời
Kế hoạch triển khai
1. Chốt danh mục 63 địa điểm
Mỗi địa điểm phải có ID ổn định, không phụ thuộc tên OpenWeather:
{
  "id": "nghe_an",
  "name": "Nghệ An",
  "aliases": [
    "Nghe An",
    "nghean",
    "tỉnh Nghệ An"
  ],
  "latitude": 18.6796,
  "longitude": 105.6813,
  "center_type": "administrative_center",
  "coordinate_source": "manual_verified",
  "active": true
}
Yêu cầu:
Đúng 63 location_id duy nhất.
Tọa độ nằm trong phạm vi Việt Nam.
Chọn trung tâm hành chính/tỉnh lỵ, không dùng tâm hình học.
Tọa độ phải được kiểm tra thủ công, không tự động lấy ứng viên đầu tiên từ Geocoding.
Ghi rõ nguồn và ngày xác minh tọa độ.
Không gọi Geocoding API trong request người dùng.
Đầu ra: cập nhật [weather_locations_vn.json](D:/RAG_ManageAgent_Lumi/code/rag_manager/services/weather_locations_vn.json).
2. Viết validator cho file địa điểm
Trước khi worker chạy, kiểm tra:
Có đúng 63 địa điểm.
Không trùng id.
Không trùng alias sau khi chuẩn hóa.
latitude và longitude hợp lệ.
Không thiếu tên hoặc tọa độ.
Khoảng cách giữa các tọa độ không bất thường.
Tất cả alias đều trỏ được về một location_id.
Nếu cấu hình sai, worker dừng trước khi gọi OpenWeather.
3. Chuyển OpenWeather service sang tọa độ
Trong weather_api.py, bổ sung hoặc chuyển sang:
fetch_weather_by_coordinates(latitude, longitude)
fetch_weather_forecast_by_coordinates(latitude, longitude, days)
Request sử dụng:
lat={latitude}
lon={longitude}
units=metric
lang=vi
Không còn dùng:
q=Nghệ An,VN
Cần giữ hai dạng dữ liệu:
raw_data: toàn bộ JSON OpenWeather trả về, không sửa đổi.
normalized_data: schema ổn định cho Weather Agent và visualization.
4. Chuẩn hóa đầy đủ dữ liệu thời tiết
Current weather nên chuẩn hóa:
Thời điểm quan sát UTC và giờ địa phương.
Nhiệt độ, cảm giác như, thấp/cao.
Trạng thái, mô tả, icon/code.
Độ ẩm, áp suất, tầm nhìn.
Tốc độ, hướng và gió giật.
Mây, mưa, tuyết nếu có.
Bình minh, hoàng hôn.
Tọa độ nguồn.
Forecast nên giữ:
Toàn bộ interval theo dữ liệu API.
Ngày và giờ địa phương.
Nhiệt độ, cảm giác như.
Xác suất và lượng mưa.
Độ ẩm, áp suất, gió, mây.
Tổng hợp theo ngày.
Phạm vi ngày thực sự có trong snapshot.
Không để LLM tự tính min/max hoặc tổng lượng mưa nếu code có thể tính chính xác.
5. Thiết kế snapshot worker
Mỗi chu kỳ 3 giờ:
Đọc và validate file 63 tỉnh.
Với từng tỉnh, gọi Current và Forecast bằng tọa độ.
Phân loại lỗi:401: API key.
429: quota.
timeout/5xx: lỗi tạm thời, retry có giới hạn.
payload thiếu trường: lỗi dữ liệu.

Chuẩn hóa dữ liệu.
Tạo snapshot_id.
Ghi snapshot staging vào Redis.
Chỉ chuyển active snapshot khi dữ liệu đạt chính sách chất lượng.
Nên retry theo exponential backoff cho timeout, 429 và 5xx; không retry vô hạn.
6. Chọn chính sách khi một tỉnh tải lỗi
Khuyến nghị không bỏ cả snapshot chỉ vì một tỉnh lỗi tạm thời.
Nên áp dụng:
Tỉnh thành công: dùng dữ liệu mới.
Tỉnh thất bại: lấy bản last-known-good.
Đánh dấu:
{
  "stale": true,
  "stale_since": "...",
  "refresh_error": {}
}
Nếu dữ liệu cũ vượt ngưỡng, ví dụ 6–12 giờ, đánh dấu unavailable.
Manifest phải ghi số tỉnh mới, stale và unavailable.
Trong lần bootstrap đầu tiên, nên yêu cầu đủ 63 tỉnh hoặc có danh sách ngoại lệ được xác nhận rõ ràng.
7. Thiết kế lại Redis theo location_id
Không lưu payload lặp lại cho từng alias.
Cấu trúc đề xuất:
weather:snapshot:active
    → snapshot_id

weather:snapshot:{id}:manifest
    → metadata snapshot

weather:snapshot:{id}:location:nghe_an
    → current + forecast + raw + normalized

weather:location_aliases
    nghean       → nghe_an
    nghe-an      → nghe_an
    tinh-nghe-an → nghe_an
Tọa độ nằm trong metadata của record:
{
  "location": {
    "id": "nghe_an",
    "name": "Nghệ An",
    "latitude": 18.6796,
    "longitude": 105.6813
  }
}
Redis được tra theo location_id; tọa độ chỉ dùng ở worker.
TTL nên dài hơn chu kỳ refresh. Ví dụ refresh 3 giờ nhưng giữ snapshot cũ đủ lâu để rollback hoặc sử dụng last-known-good.
8. Tạo resolve_location tool
Tool nhận cụm địa danh do Weather Agent extract:
{
  "raw_location": "nghe annn"
}
Tool thực hiện:
Chuẩn hóa Unicode, viết hoa/thường, dấu cách.
So khớp chính xác với name/alias.
Fuzzy matching với danh mục 63 tỉnh.
Trả kết quả có cấu trúc:
{
  "status": "resolved",
  "location_id": "nghe_an",
  "canonical_name": "Nghệ An",
  "score": 0.94
}
Nếu nhập nhằng:
{
  "status": "ambiguous",
  "candidates": [
    {"location_id": "...", "name": "...", "score": 0.72}
  ]
}
Weather Agent không được tự tạo location_id.
9. Đổi weather tools sang nhận location_id
Các tool nên là:
resolve_location(raw_location)
get_current_time(timezone_name)
get_current_weather(location_id)
get_weather_forecast(location_id, days)
Quy tắc:
get_current_weather và get_weather_forecast chỉ nhận ID hợp lệ.
Tool xác nhận ID tồn tại trong catalog.
Tool lấy active snapshot.
Tool đọc Redis, không gọi OpenWeather.
Tool trả kèm:snapshot ID;
thời gian tạo snapshot;
tọa độ đại diện;
stale;
phạm vi forecast có sẵn;
lỗi nếu dữ liệu unavailable.

Việc lọc “ngày mai”, “thứ năm tới”, “3 ngày tới” nên được code/tool thực hiện theo ngày cụ thể sau khi Agent xác định yêu cầu.
10. Sửa Weather Agent prompt
Prompt nên quy định rõ:
Extract cụm địa danh từ câu hỏi.
Luôn gọi resolve_location trước weather tools.
Không tự đoán location_id hoặc tọa độ.
Nếu ambiguous, hỏi lại người dùng.
Dùng get_current_time cho ngày tương đối.
Chỉ trả lời bằng dữ liệu Redis do tool trả về.
Thông báo nếu snapshot stale/unavailable.
Không tuyên bố đã gọi OpenWeather trực tiếp.
Giải thích dữ liệu tỉnh đại diện cho tọa độ trung tâm hành chính.
Không xuất hidden reasoning.
Không nên nhúng toàn bộ dữ liệu thời tiết hay danh sách tọa độ vào system prompt. Giữ system prompt cố định để thuận lợi cho prefix cache.
11. Phân chia lại trách nhiệm Manager và Weather Agent
Manager chỉ cần:
Xác định đây là câu hỏi thời tiết.
Chuyển toàn bộ câu hỏi sang Weather Agent.
Có thể cung cấp location_hint, nhưng không canonicalize.
Weather Agent chịu trách nhiệm:
Hiểu câu hỏi.
Extract địa danh.
Resolve địa danh qua tool.
Chọn current hay forecast tool.
Tổng hợp câu trả lời.
Như vậy tránh việc Manager và Weather Agent cùng xử lý địa danh hai lần.
12. Giữ tương thích visualization
Weather envelope nên bổ sung nhưng không phá schema hiện tại:
{
  "location": {
    "id": "nghe_an",
    "name": "Nghệ An",
    "latitude": 18.6796,
    "longitude": 105.6813
  },
  "source": {
    "provider": "openweathermap",
    "snapshot_id": "...",
    "generated_at": "...",
    "stale": false
  }
}
Renderer vẫn chỉ nhận dữ liệu đã chuẩn hóa, loop, escape và thay placeholder.
13. Kiểm thử
Cần có các nhóm test:
Catalog có đúng 63 tỉnh.
Tọa độ và ID hợp lệ.
Worker gọi API bằng lat/lon, không còn q.
Raw response không bị sửa đổi.
Snapshot chỉ active sau khi ghi hoàn chỉnh.
Alias không làm lặp payload Redis.
Resolver xử lý:nghean;
nghe annn;
tp hcm;
sai gon;
nhập nhằng hoặc không tìm thấy.

Weather tools từ chối ID không hợp lệ.
“hôm nay”, “ngày mai”, “thứ năm tới”, “3 ngày tới”.
Redis lỗi, snapshot stale, thiếu forecast.
Visualization vẫn render đúng.
14. Thứ tự triển khai an toàn
Nên thực hiện theo thứ tự:
Chuẩn hóa file 63 tỉnh và tọa độ.
Viết catalog validator.
Bổ sung API gọi bằng tọa độ.
Cập nhật worker nhưng chưa thay luồng chatbot.
Thiết kế Redis schema mới.
Nạp và kiểm tra một snapshot hoàn chỉnh.
Tạo resolve_location tool.
Chuyển weather tools sang location_id.
Sửa Weather Agent prompt.
Chạy integration test.
Chuyển chatbot sang Redis-only.
Sau khi ổn định mới xóa luồng gọi OpenWeather bằng tên và schema Redis cũ.
Tiêu chí hoàn thành quan trọng nhất: request của người dùng không gọi OpenWeather, không tự suy luận tọa độ, không lưu lặp payload theo alias, và mọi dữ liệu trả lời đều truy vết được về location_id, tọa độ, snapshot và raw response.


2:27 PM






Phân chia lại trách nhiệm Manager và Weather Agent
Manager chỉ cần:
Xác định đây là câu hỏi thời tiết.
Chuyển toàn bộ câu hỏi sang Weather Agent.
Có thể cung cấp location_hint, nhưng không canonicalize.
Hiện lại manager agent chỉ làm việc là phân loại câu hỏi để chọn sub agent mà?






2:31 PM






Đúng, nhưng hiện tại Manager không chỉ chọn sub-agent. Nó còn tạo ManagerPlan gồm:
topics
execution_mode
primary_intent
dependencies
location
news_query
wiki_topic
Weather Agent hiện ưu tiên lấy intent["location"] do Manager trích xuất; nếu không có mới dùng toàn bộ câu hỏi.
Vì vậy, với kiến trúc mới:
Giữ Manager phân loại, chọn agent và lập kế hoạch như hiện tại.
location từ Manager chỉ là gợi ý thô, ví dụ "nghe annn".
Manager không fuzzy match, không tạo location_id, không tra tọa độ.
Weather Agent nhận toàn bộ câu hỏi và location_hint.
Weather Agent gọi resolve_location để lấy location_id hợp lệ.
Do đó không cần thay đổi lớn trách nhiệm Manager; chỉ cần bảo đảm trường location của Manager không được coi là địa danh đã chuẩn hóa.