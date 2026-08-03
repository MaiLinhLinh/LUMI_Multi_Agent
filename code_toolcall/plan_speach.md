Đúng. Với CTC, nên có một bản text riêng để đọc và align, ví dụ:
Hiển thị chat: Khả năng mưa: 96%
spoken_text: Khả năng mưa là chín mươi sáu phần trăm.
alignment_text: kha nang mua la chin muoi sau phan tram
Không nên gửi nguyên 96%, 30.4°C, 31/7 cho Gemini rồi hy vọng nó đọc đồng nhất. Code phải chuẩn hoá chúng trước; prompt chỉ là lớp hỗ trợ.
Kiến trúc đích
Planner
  → narration gốc cho chat
  → spoken_text đã chuẩn hoá cách đọc
  → scene/effect/target

Presentation Compiler
  → Presentation Contract
  → script gồm các spoken_text theo thứ tự scene

Gemini Live (một lượt đọc toàn bộ script)
  → stream PCM 24 kHz
  ├─ Frontend: buffer rồi phát audio
  └─ Colab CTC Worker: nhận cùng stream audio + alignment_text

CTC Worker
  → scene_confirmed(scene_id, audio_time_ms, confidence)

Frontend
  → playhead đạt audio_time_ms
  → kích hoạt animation của scene tương ứng
Gemini Live trả audio dạng PCM 16-bit, little-endian ở 24 kHz; ta dùng đúng dòng PCM đó cho cả loa và CTC. Live API cũng có output_audio_transcription, nhưng chỉ nên bật để debug Gemini có đọc lệch script không, không dùng làm timestamp chính. Gemini Live capabilities
Bước 1 — Tách ba dạng text trong Presentation Contract
Mỗi scene nên có dạng:
{
  "scene_id": "rain-risk",
  "display_text": "Khả năng mưa: 96%.",
  "spoken_text": "Khả năng mưa hôm nay lên tới chín mươi sáu phần trăm.",
  "alignment_text": "kha nang mua hom nay len toi chin muoi sau phan tram",
  "actions": [
    {
      "target": "weather.day.0.rain_risk",
      "effect": "draw_circle"
    }
  ]
}
Quy tắc:
display_text: giữ đẹp, ngắn, có ký hiệu.
spoken_text: câu tiếng Việt đầy đủ để Gemini đọc.
alignment_text: từ spoken_text, hạ chữ, bỏ dấu câu và chuẩn hoá Unicode/romanization cho model CTC.
Code normalizer cần xử lý ít nhất:
Dữ liệu	spoken_text
96%	chín mươi sáu phần trăm
30.4°C	ba mươi phẩy bốn độ C
1.65 m/s	một phẩy sáu lăm mét trên giây
4.3 mm	bốn phẩy ba mi li mét
31/7	ngày ba mươi mốt tháng bảy
24.5–30.4°C	từ hai mươi bốn phẩy năm đến ba mươi phẩy bốn độ C

Quan trọng: normalizer là code dùng chung cho mọi scene Weather, không để Planner tự đoán cách đọc số.
Bước 2 — Khóa script trước khi gọi Gemini Live
Sau khi Planner và Compiler hoàn thành toàn bộ scene:
Scene 1 spoken_text
+ Scene 2 spoken_text
+ Scene 3 spoken_text
= full_spoken_script
Gửi Gemini Live một yêu cầu duy nhất, kiểu:
Đọc nguyên văn kịch bản sau bằng tiếng Việt tự nhiên.
Không thêm, bớt, đổi thứ tự hay diễn giải lại câu nào.

<KỊCH_BẢN>
...
</KỊCH_BẢN>
Một lượt Live duy nhất giúp giọng nhất quán và không có khoảng hở giữa bốn lần gọi TTS.
Tuy nhiên đây không phải cam kết tuyệt đối: Gemini vẫn có thể đọc lệch nhẹ. Vì vậy CTC Worker phải trả confidence; nếu confidence thấp thì không chạy hiệu ứng quá chính xác, mà fallback về animation theo scene ước lượng.
Bước 3 — Tạo Colab GPU cho CTC Worker
Trong Colab:
Chọn Runtime → Change runtime type → T4 GPU.
Không đưa GEMINI_API_KEY lên Colab.
Colab chỉ làm một việc: nhận audio PCM và tìm mốc scene.
Lumi local vẫn giữ Gemini key, gọi Gemini Live và chuyển bản sao audio sang Colab.
Cell kiểm tra GPU:
!nvidia-smi
import torch, torchaudio
print(torch.__version__)
print(torchaudio.__version__)
print(torch.cuda.is_available())
Cài phần worker:
!pip install -q fastapi "uvicorn[standard]" websockets uroman pyngrok
Sau đó tải MMS Forced Alignment. TorchAudio có bundle MMS_FA, dùng emission của audio và transcript đã biết để tìm timestamp; model download khá lớn, khoảng hơn 1 GB tùy phiên bản. TorchAudio CTC forced-alignment tutorial
import torch
import torchaudio

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

bundle = torchaudio.pipelines.MMS_FA
alignment_model = bundle.get_model(with_star=False).to(DEVICE).eval()
SAMPLE_RATE = bundle.sample_rate

print("CTC sample rate:", SAMPLE_RATE)
MMS_FA là lựa chọn POC tốt vì nó là model alignment đa ngôn ngữ. Với tiếng Việt, cần thử nghiệm thực tế vì chữ có dấu và cách Gemini phát âm số/ngày tháng ảnh hưởng mạnh đến confidence.
Bước 4 — Làm POC offline trước, chưa stream
Đây là bước bắt buộc. Đừng làm WebSocket realtime ngay.
Cho Gemini Live đọc một script Weather gồm 3–4 scene.
Lưu toàn bộ PCM output thành file WAV.
Gửi WAV + alignment_text của toàn script cho Colab.
Colab trả timestamp bắt đầu/kết thúc từng scene.
So khớp bằng tai và bằng log.
Ví dụ contract đầu vào:
{
  "sample_rate": 24000,
  "scenes": [
    {
      "scene_id": "intro",
      "alignment_text": "thoi tiet ha noi hom nay co mua phun"
    },
    {
      "scene_id": "rain-risk",
      "alignment_text": "kha nang mua hom nay len toi chin muoi sau phan tram"
    },
    {
      "scene_id": "temperature",
      "alignment_text": "nhiet do dao dong tu hai muoi bon phay nam den ba muoi phay bon do c"
    }
  ]
}
Kết quả mong muốn:
{
  "scenes": [
    {
      "scene_id": "intro",
      "start_ms": 0,
      "end_ms": 3100,
      "confidence": 0.91
    },
    {
      "scene_id": "rain-risk",
      "start_ms": 3100,
      "end_ms": 6500,
      "confidence": 0.88
    }
  ]
}
audio_time_ms không do Gemini gửi. Nó được tính từ vị trí mẫu audio:
audio_time_ms = sample_offset / sample_rate × 1000
Ví dụ Gemini output 24 kHz, CTC xác định scene 2 bắt đầu ở sample thứ 74,400:
74,400 / 24,000 × 1000 = 3,100 ms
Nếu CTC resample audio từ 24 kHz xuống sample rate của model, timestamp vẫn quy đổi ngược về timeline audio gốc.
Bước 5 — Viết Colab Worker WebSocket
Protocol tối thiểu:
Lumi local  → Colab: start
Lumi local  → Colab: binary PCM chunks
Lumi local  → Colab: end

Colab → Lumi local: ready
Colab → Lumi local: scene_confirmed
Colab → Lumi local: error
Gói start:
{
  "type": "start",
  "presentation_id": "uuid",
  "sample_rate": 24000,
  "scenes": [
    {
      "scene_id": "rain-risk",
      "alignment_text": "kha nang mua hom nay len toi chin muoi sau phan tram"
    }
  ]
}
Event trả về:
{
  "type": "scene_confirmed",
  "presentation_id": "uuid",
  "scene_id": "rain-risk",
  "audio_time_ms": 3100,
  "confidence": 0.88
}
Phải có shared secret trong header hoặc message start. Không mở WebSocket Colab công khai không xác thực.
Để Lumi local gọi được Colab, expose worker qua tunnel HTTPS/WSS, ví dụ ngrok. Colab sẽ reset sau một thời gian, nên đây là hạ tầng POC, chưa phải deployment lâu dài.
Bước 6 — Chuyển từ offline sang streaming CTC
Sau khi POC offline khớp tốt, mới làm streaming.
Lumi nhận từng PCM chunk từ Gemini Live và lập tức nhân đôi:
Gemini PCM chunk
  ├─ gửi frontend để phát
  └─ gửi CTC Worker để align
Không được chờ CTC xử lý xong mới chuyển audio cho frontend.
Frontend cần giữ một audio buffer khoảng 0.8–1.5 giây:
audio Gemini đã nhận: 5.0 giây
frontend mới phát tới: 3.8 giây
CTC phát hiện scene tiếp theo ở: 4.2 giây
→ event đến trước khi loa phát tới scene đó
→ animation kịp bắt đầu đúng lúc
Đây là lý do buffer rất quan trọng. Nếu phát audio ngay khi nhận, CTC chỉ biết scene mới sau khi lời nói đã qua.
Ở phiên bản scene-level:
Scene 1 bắt đầu tại 0 ms, biết trước nên chạy ngay khi audio bắt đầu.
CTC xác nhận mốc vào scene 2.
Frontend đợi playhead đạt mốc đó rồi đổi animation.
Không cần word-level alignment lúc đầu.
Bước 7 — Chính sách confidence và fallback
CTC không được phép làm UI “nhảy loạn” khi Gemini đọc lệch. Đặt chính sách:
confidence >= 0.80
  → chạy animation scene bình thường

0.60 <= confidence < 0.80
  → chỉ highlight nhẹ, không vẽ vòng chính xác

confidence < 0.60 hoặc worker mất kết nối
  → fallback theo timeline ước lượng của Compiler
Mỗi lượt phải log:
presentation_id
scene_id
expected alignment text
confirmed audio_time_ms
confidence
frontend actual_playhead_ms
cue drift_ms
Metric quan trọng nhất:
drift_ms = frontend actual effect start − aligned audio_time_ms
Mục tiêu scene-level ban đầu: đa số scene lệch dưới khoảng 300–500 ms; sau đó mới đầu tư word-level.
Thứ tự triển khai an toàn
Thêm spoken_text và alignment_text; viết test chuẩn hoá số, ngày, đơn vị.
Giữ VieNeu hiện tại, chỉ dùng nó hoặc Gemini để tạo WAV thử nghiệm.
Làm notebook Colab align offline một file WAV.
Đánh giá tiếng Việt, số, phần trăm, ngày tháng và confidence.
Tạo CTC Worker WebSocket trên Colab.
Thêm bridge Lumi → Gemini Live → frontend + CTC Worker bằng feature flag.
Thêm audio prebuffer và event scene_confirmed.
Chỉ khi scene-level ổn mới làm alignment theo từ/cụm từ.
Điểm then chốt: CTC giúp animation khớp audio thật, nhưng không thay Planner/Compiler. Planner vẫn quyết định “nói fact gì”, Compiler vẫn kiểm tra target/effect an toàn; CTC chỉ trả lời chính xác câu hỏi “giọng Gemini đang đọc đến scene nào, ở mili-giây nào”.