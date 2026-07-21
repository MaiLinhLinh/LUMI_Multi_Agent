Bài toán: Tạo một manage agent có thể điều phối lựa chọn topic sub agent (3 sub agent là sub agent topic thời tiết, sub agent về news, sub agent về wikipedia)
Tạo 3 sub agent lần lượt có thể trả lời các câu hỏi cho người dùng về thời tiết, về news, về wikipedia, sử dung dữ liệu từ các trang dự báo thời tiết, các trang news, và trang wikipedia.

(Mô tả: Dữ liệu thời tiết, hoặc dữ liệu news thường có một độ tĩnh nhất định, ví dụ dự báo thời tiết thì trong vòng mấy tiếng đó sẽ không đổi,... Dữ liệu wikipedia thì luôn tĩnh. Vậy để đạt được cache hit cao nhất, độ trễ bé nhất thì phải đảm bảo system prompt có thể bao quát lớn nhất: ví dụ chuỗi 12345678, thì khi có chuỗi 125678 thì chỉ tĩnh được khoảng chuỗi 12. -> Cần cải thiện độ khớp cao nhất để tăng cache hit, sử dụng KV cache, prefix caching)

Yêu cầu: 
1. 
- Sử dụng api gemini, model gemma-4-26b-a4b-it
```
from openai import OpenAI

client = OpenAI(
    api_key="GEMINI_API_KEY",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

response = client.chat.completions.create(
    model="gemma-4-26b-a4b-it",
    messages=[
        {   "role": "system",
            "content": "You are a helpful assistant."
        },
        {
            "role": "user",
            "content": "Explain to me how AI works"
        }
    ]
)
print(response.choices[0].message)
```
- Độ latance thấp nhất có thể
- Cache hit cao nhất ( KV cache, prefix caching)

2. 
- Có thể trả về hình anh trực quan cho kết quả ( sử dung html)
- Có thể tiếp nhận câu hỏi bằng lời nói và tương tác người dùng ( ví dụ người dùng có thể bấm chọn, thao tác lên hình ảnh kết quả được trả về từ câu hỏi trước và có thể đặt câu hỏi tiếp theo)

