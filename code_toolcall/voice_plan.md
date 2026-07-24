# Kế hoạch tích hợp Voice — Kiến trúc 2

## Mục tiêu đã chốt

Giữ nguyên logic nghiệp vụ hiện tại: Manager Gemma/Ollama, Weather Agent, Music Agent, tool calling, ChromaDB và lịch sử hội thoại. Gemini Live chỉ đảm nhiệm chuyển giọng nói thành văn bản và chuyển văn bản trả lời thành giọng nói.

```text
Browser microphone
  -> Voice Gateway mới
  -> Gemini Live (speech-to-text)
  -> POST /api/chat/stream hiện có
  -> Manager/Agent Gemma + tools + ChromaDB
  -> Voice Gateway mới
  -> Gemini Live (text-to-speech)
  -> Browser speaker
```

`session_id` của voice phải trùng `session_id` chat hiện tại. Chỉ transcript cuối cùng được gửi vào agent và lưu trong lịch sử; audio thô không được đưa vào lịch sử agent.

## Phạm vi không thay đổi

- Không chuyển tool calling sang Gemini.
- Không thay prompt, logic hay dữ liệu của Manager, Music Agent, Weather Agent trừ khi một lỗi tích hợp voice bắt buộc phải xử lý.
- Không thay API `/api/chat/stream`; voice gọi endpoint này như một client text bình thường.
- UI player nhạc và panel thời tiết vẫn được render từ payload cuối của chat hiện tại.

## Các bước thực hiện

| Bước | Công việc | Kết quả kiểm chứng | Trạng thái |
|---|---|---|---|
| 1 | Khảo sát điểm tích hợp và chốt hợp đồng kiến trúc voice | Kế hoạch này; xác nhận `web_app.py` đã có `/api/chat/stream`, session và NDJSON stream | Hoàn thành |
| 2 | Chốt cấu hình Gemini Live và secrets | Xác định model voice, biến môi trường, quyền API; không lộ API key | Hoàn thành có điều kiện |
| 3 | Thêm Voice Gateway tối thiểu ở backend | Endpoint WebSocket chỉ nhận sự kiện voice, quản lý session/cancel và chưa sửa agent | Hoàn thành |
| 4 | Thêm microphone và trạng thái voice ở frontend | Nút ghi âm, quyền microphone, trạng thái nghe/lỗi; chưa gửi câu hỏi vào agent | Hoàn thành |
| 5 | Kết nối audio -> transcript -> `/api/chat/stream` | Một câu nói tạo đúng một query text trong cùng session và hiển thị transcript | Hoàn thành |
| 6 | Kết nối final answer/text stream -> audio | Câu trả lời của agent được đọc; player/panel hiện có vẫn hoạt động | Hoàn thành |
| 7 | Bổ sung ngắt lời, hủy audio và xử lý lỗi | Nói chen dừng audio cũ; kết nối/API lỗi có thông báo rõ | Chờ |
| 8 | Kiểm thử tích hợp và tài liệu vận hành | Test text/voice nhiều lượt, nhạc, thời tiết, mất kết nối; hướng dẫn chạy | Chờ |

## Quy tắc triển khai từng bước

1. Hoàn thành đúng một bước.
2. Chạy kiểm tra phù hợp với bước đó.
3. Tóm tắt tệp đã đổi, kết quả kiểm tra và rủi ro còn lại.
4. Dừng, chỉ tiếp tục sau khi người dùng xác nhận.

## Phát hiện ở bước 1

- `web_app.py` là Starlette, hiện có `/api/chat/stream` trả NDJSON và truyền `session_id` vào workflow.
- `web/app.js` đã xử lý `text_delta` và `final`; đây là điểm để voice giữ đồng bộ UI text hiện có.
- `Session` hiện lưu messages, weather context, music session và panel. Voice state cần tách riêng, không thêm audio vào `messages`.
- `requirements.txt` đã có `google-genai`, nhưng chưa có lớp Gemini Live, WebSocket endpoint hoặc UI microphone.

## Quyết định cần xác nhận trước bước 2

1. Model Gemini Live nào sẽ được dùng và dự án Google/API key tương ứng. Có thể để cấu hình bằng biến môi trường để đổi model mà không sửa code.
2. Voice Gateway kết nối Gemini Live từ backend để API key không xuất hiện ở browser. Đây là phương án mặc định an toàn.
3. Ngôn ngữ mặc định là tiếng Việt; có cần cho người dùng chọn giọng đọc hay dùng một giọng cố định?

## Kết quả khảo sát cấu hình của bước 2

- `GEMINI_MODEL` hiện cấu hình `gemma-4-26b-a4b-it` và được `GeminiFunctionCallingRuntime` gọi qua `google-genai`. Vì vậy Manager/agent hiện dùng Gemma qua Google GenAI API; Ollama trong dự án đang phục vụ embedding nhạc (`bge-m3`), không phải Manager/agent.
- Môi trường `LumiMultiAgent` có `google-genai` phiên bản `2.11.0`.
- Không dùng lại `GEMINI_MODEL` cho voice: nó phải tiếp tục chỉ định Gemma để không đổi hành vi agent.
- Cấu hình voice sẽ tách riêng ở backend, dự kiến là `GEMINI_LIVE_API_KEY` và `GEMINI_LIVE_MODEL`. API key không được đưa vào JavaScript/browser. Có thể cho phép `GEMINI_LIVE_API_KEY` kế thừa `GEMINI_API_KEY` nếu cùng dự án Google, nhưng chỉ sau khi người dùng xác nhận.

## Quyết định bước 2 đã chốt

- Model mặc định: `gemini-3.1-flash-live-preview`, là model Live hiện hành được tài liệu Gemini Live liệt kê. Nó chỉ được dùng ở Voice Gateway.
- Model agent giữ nguyên: `GEMINI_MODEL=gemma-4-26b-a4b-it` không bị sửa hoặc dùng cho voice.
- API key voice: biến riêng `GEMINI_LIVE_API_KEY`; không có cơ chế kế thừa `GEMINI_API_KEY`.
- Giọng đọc: biến `GEMINI_LIVE_VOICE`, giá trị cố định cho toàn bộ phiên. Tên giọng phải được kiểm chứng với model/key thực tế ở bước 3 trước khi đặt giá trị.
- Quota: rate limit áp theo Google project, không theo API key. Key riêng cần thuộc project riêng có billing/quota phù hợp nếu mục tiêu là cô lập quota voice khỏi agent. Không model nào bảo đảm không có lỗi 429; preview thường có quota hạn chế hơn model ổn định.

## Thay đổi cấu hình bước 2

`rag_manager/config.py` đã nhận thêm ba biến tách biệt: `GEMINI_LIVE_API_KEY`, `GEMINI_LIVE_MODEL` (mặc định `gemini-3.1-flash-live-preview`) và `GEMINI_LIVE_VOICE`. Chúng chưa được sử dụng bởi luồng agent hiện tại, nên không làm thay đổi hành vi chat text.

## Kết quả bước 3

- Thêm WebSocket `GET /ws/voice` trong `web_app.py` và module giao thức `rag_manager/voice_gateway.py`.
- Sự kiện tối thiểu: `voice:start` (gắn session chat), `voice:ping` và `voice:cancel`.
- Gateway kiểm tra `GEMINI_LIVE_API_KEY` và model voice nhưng chưa tạo kết nối Gemini Live, chưa nhận audio và không có quyền gọi agent/tool.
- `voice:cancel` mới quản lý trạng thái socket; việc hủy audio/TTS/Gemini Live thật sẽ được thêm ở bước 7.

## Kết quả bước 4

- Frontend có nút microphone, trạng thái truy cập micro/đang nghe/lỗi và khả năng dừng ghi âm.
- Audio được `MediaRecorder` giữ trong bộ nhớ cục bộ rồi giải phóng khi rời trang; chưa gửi tới WebSocket, Gemini Live hoặc agent.
- Khi browser không hỗ trợ micro hoặc người dùng từ chối quyền, UI hiển thị lỗi tiếng Việt rõ ràng.

## Kết quả bước 5

- Browser thu mono PCM, resample về PCM 16 kHz và truyền binary qua `/ws/voice`; không gửi file audio hay đưa audio vào lịch sử agent.
- Voice Gateway tạo một Gemini Live session chỉ có input audio transcription tiếng Việt, không khai báo tool và không chuyển response của Gemini thành câu trả lời nghiệp vụ.
- Transcript tạm thời được hiển thị ở UI. Khi nhận transcript cuối, frontend gọi đúng `/api/chat/stream` với `session_id` hiện tại; do đó Manager/Gemma/tool/session cũ giữ nguyên.
- Bước này chỉ trả về text qua UI hiện có. Tổng hợp text thành audio sẽ được thực hiện ở bước 6.
- Key mới đã được Gemini chấp nhận; bắt tay Gemini Live thành công.
- `gemini-3.5-live-translate-preview` không chốt được transcript trong kiểm thử audio thật, nên không được dùng làm STT. Cả STT và TTS mặc định dùng `gemini-3.1-flash-live-preview`: STT đọc `input_transcription`, TTS sinh `AUDIO`.
- STT đặt `language_hints=["vi-VN"]` và các cụm thích nghi `Lumi`, `Hà Nội`, `Sơn Tùng M-TP` để ưu tiên nhận diện tiếng Việt và tên riêng của ứng dụng.
- Biến cấu hình: `GEMINI_LIVE_TRANSCRIBE_MODEL` và `GEMINI_LIVE_SPEECH_MODEL` (đều mặc định 3.1 Flash Live). `GEMINI_LIVE_MODEL` cũ vẫn là fallback tương thích cho nhánh TTS.

## Kết quả triển khai bước 6

- Thêm `GeminiLiveSpeaker` tách biệt với transcriber: chỉ nhận text cuối do Gemma tạo, không có tool hay quyền agent, rồi trả PCM audio qua WebSocket.
- Giọng cố định mặc định là `kore`; có thể đổi một lần cho toàn hệ thống bằng `GEMINI_LIVE_VOICE` trong `.env`.
- Frontend xếp các chunk PCM để phát liên tục bằng Web Audio API; UI text, player nhạc và panel thời tiết không thay đổi.
- Chỉ các lượt bắt đầu bằng voice mới tự đọc câu trả lời. Lượt gõ text giữ hành vi im lặng như trước.
- TTS streaming: frontend gom ký tự text vừa hiển thị thành cụm tối đa khoảng 8 từ hoặc đến dấu câu, rồi lần lượt gửi chúng qua cùng một phiên Gemini Live. Vì vậy giọng có thể bắt đầu trước khi toàn bộ câu trả lời và minh hoạ hoàn tất; cụm sau chỉ gửi sau `voice_speech_end` của cụm trước.
- Kiểm tra thật: Gemini Live đã đọc câu tiếng Việt thử nghiệm và trả về 57.122 byte PCM; không lưu audio ra file.
