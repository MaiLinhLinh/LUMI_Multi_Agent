"""Planning guidance owned by the Education domain."""

EDUCATION_PLAN_INSTRUCTION = """
Đây là domain giáo dục cho trẻ em. Ưu tiên bố cục đơn giản, dễ nhìn, có đủ chỗ cho
chữ và hình; không ép một hoạt động giáo dục vào template cũ nếu template đó không
phù hợp toàn bộ mục tiêu học tập và trạng thái tương tác.

VÍ DỤ TẠO MỚI

Sau khi đã describe_widgets(["text", "image"]), một hoạt động giới thiệu một đối
tượng có thể tạo tiêu đề ở hàng trên và một ảnh lớn ở giữa. Template description chỉ
mô tả khung tái sử dụng, không ghi tên asset hoặc nội dung riêng của lượt đó.

VÍ DỤ DÙNG TEMPLATE

Sau khi đã gọi describe_template, chỉ tái dùng template nếu nó có đúng các vùng,
widget và trạng thái cần cho hoạt động. Kết quả use_existing_surface_template chỉ
điền bindings biến đổi; không lặp lại layout của template.

VÍ DỤ PATCH

Khi surface hiện tại vẫn là cùng hoạt động và chỉ thay đổi một vùng nhỏ, dùng
update_props với đúng anchor_id/revision của active_surface_summary. Khi chuyển sang
một hoạt động hay bố cục cốt lõi khác, tạo surface mới, không trả operations rỗng.

Khi câu tiếp theo vẫn dùng các thẻ choice hiện có nhưng đổi ảnh/nội dung bên trong,
dùng `replace_children`, không dùng update_props để chèn field `children`. Ví dụ:
`{"op":"replace_children","anchor_id":"b","children":[{"widget_id":"image","props":{"asset_id":"cat"}}]}`.
Mỗi operation luôn dùng key `op`; update_props dùng key `changes`, ví dụ
`{"op":"update_props","anchor_id":"a","changes":{"content":"Câu hỏi mới"}}`.

QUY TẮC BÀI TẬP CÓ ĐÁP ÁN ẨN

`initial_state: {"visibility":"hidden"}` chỉ quyết định trạng thái hiển thị ban
đầu của block; nó không thay đổi dữ liệu thật trong `props`.

Với widget `answer` hoặc `number_display` dùng để công bố kết quả:
- `props.value` bắt buộc là đáp án thật, chính xác của hoạt động.
- Tuyệt đối không đặt `props.value` là "?", "…", "ẩn" hoặc placeholder khác.
- Khi block đang hidden, frontend tự hiển thị dấu `?`; khi Gemini Live đổi
  visibility thành visible, frontend mới nhận và hiển thị `props.value` thật.
- Ví dụ bài `2 + 3` phải chứa `props: {"value":"5"}` ngay từ lúc tạo surface,
  dù block answer có `initial_state: {"visibility":"hidden"}`.

Với phép tính được trình bày theo hàng ngang, các toán hạng, ký hiệu phép tính,
dấu bằng và block đáp án phải cùng một hàng grid. Không đặt đáp án xuống hàng bên
dưới dấu bằng, trừ khi intent yêu cầu rõ một bố cục dọc.

VÍ DỤ — FLASHCARD

Intent: “Học từ CAT bằng thẻ lật.” Sau khi gọi
`describe_widgets({"widget_ids":["flashcard"]})`, có thể tạo một surface mới với
một `flashcard` chiếm vùng trung tâm. Props phải có đúng hai mặt do contract yêu cầu:

{
  "action": "create_surface_plan",
  "template_description": "Một thẻ từ vựng lớn ở giữa để lật giữa minh hoạ và kiến thức.",
  "surface": {
    "blocks": [
      {
        "widget_id": "flashcard",
        "grid": { "col": 4, "row": 2, "col_span": 9, "row_span": 7 },
        "props": {
          "front": { "asset_id": "cat", "text": "Con mèo" },
          "back": { "word": "CAT", "phonetic": "/kæt/", "meaning": "con mèo" }
        },
        "initial_state": { "flipped": false }
      }
    ]
  }
}

Chỉ dùng flashcard khi intent thật sự cần học/ôn một thẻ có thể lật. Lần lật sau đó
là interaction `flip` của widget, không phải Plan Agent tạo một surface mới.

VÍ DỤ — HOẠT ĐỘNG CHỌN

Intent: “Chọn đúng con mèo.” Sau khi gọi
`describe_widgets({"widget_ids":["text","choice","image"]})`, dùng nhiều block
`choice`. Mỗi choice là toàn bộ vùng bấm được và chứa các child mà contract cho phép:

{
  "action": "create_surface_plan",
  "template_description": "Tiêu đề phía trên và các thẻ lựa chọn ảnh đặt ngang hàng.",
  "surface": {
    "blocks": [
      {
        "widget_id": "text",
        "grid": { "col": 1, "row": 1, "col_span": 16, "row_span": 1 },
        "props": { "content": "Con hãy chọn bạn mèo nhé!", "role": "title" }
      },
      {
        "widget_id": "choice",
        "grid": { "col": 2, "row": 3, "col_span": 6, "row_span": 5 },
        "props": {},
        "children": [{ "widget_id": "image", "props": { "asset_id": "cat" } }]
      },
      {
        "widget_id": "choice",
        "grid": { "col": 10, "row": 3, "col_span": 6, "row_span": 5 },
        "props": {},
        "children": [{ "widget_id": "image", "props": { "asset_id": "dog" } }]
      }
    ]
  }
}

Không đặt correct_choice_id, đáp án bí mật, hoặc luật chấm vào choice. Browser chỉ
gửi lựa chọn tin cậy; Gemini Live đánh giá và điều phối bài học tiếp theo.
""".strip()
