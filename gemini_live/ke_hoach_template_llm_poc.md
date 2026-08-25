# Kế hoạch PoC Template LLM — Bài học chó và mèo

## 1. Mục tiêu

Thử nghiệm một luồng tạo giao diện Education không dựa vào template HTML có sẵn, không cần widget bài học có sẵn và chưa lưu lại template tự sinh.

Ví dụ yêu cầu:

> Hãy dạy bé về chó và mèo.

Kết quả cần có là một panel trực quan được dựng từ bố cục grid, dùng ảnh chó và mèo có sẵn, có tiêu đề/nhãn ngắn do Template LLM viết, đồng thời có ASCII stage map và anchor để Gemini Live trình bày, gọi hiệu ứng.

## 2. Phạm vi PoC

Có trong PoC:

- Domain cố định: `education`.
- Một canvas grid logic 12 cột × 10 hàng.
- Hai block nguyên thủy: `text`, `image`.
- Asset catalog tối thiểu có ảnh chó và mèo, mỗi asset gồm `id`, đường dẫn nội bộ và caption.
- Template LLM tự chọn asset theo caption, tự đặt block vào grid và tự viết copy giao diện ngắn.
- Backend validate Layout Spec, frontend render panel, backend sinh ASCII stage map và panel anchor map.
- Gemini Live nhận stage map/effect catalog để trình bày và gọi `present_visual`.

Không có trong PoC:

- Không có kho widget chuyên biệt theo bài học.
- Không tìm/chọn template sẵn có.
- Không lưu Layout Spec thành template tái sử dụng.
- Không sinh HTML/CSS bằng LLM.
- Không cho Template LLM tự tạo kiến thức thực tế về chó/mèo ngoài title, label và hướng dẫn quan sát ngắn.
- Không thay thế luồng Weather hoặc các template Education hiện có.

## 3. Quyền hạn của từng thành phần

| Thành phần | Chịu trách nhiệm | Không được làm |
| --- | --- | --- |
| Gemini Live | Hiểu lời nói người dùng, chọn domain và tạo brief cho Template LLM | Tự render UI hoặc tự đặt DOM target |
| Template LLM | Chọn asset hợp lệ, bố cục grid, title/label/copy ngắn | Sinh HTML/CSS, dùng asset không có trong catalog, tạo kiến thức chưa xác minh |
| Backend | Gọi Template LLM, validate spec, sinh anchor/stage map, gửi render request | Tự đoán asset từ từ khóa bằng code |
| Frontend renderer | Dựng CSS Grid và các block nguyên thủy theo Design System | Tin tưởng spec chưa qua backend validate |

## 4. Luồng xử lý

```text
Người dùng nói: “Hãy dạy bé về chó và mèo”
  ↓
Gemini Live hiểu ý định
  ↓
Gemini Live gọi request_template_layout
  { domain_id: "education",
    template_brief: "Người dùng đang hỏi: hãy dạy bé về chó và mèo" }
  ↓
Backend lấy:
  - template_brief
  - history gần nhất
  - prompt Template LLM của Education
  - canvas 12 × 10
  - asset catalog Education
  ↓
Template LLM trả Layout Spec JSON
  ↓
Backend validate và tạo render request
  ↓
Frontend render panel bằng grid + Design System Education
  ↓
Backend sinh ASCII VISUAL STAGE MAP và panel_anchor_map từ cùng Layout Spec
  ↓
Gemini Live nhận stage map + visual effects, sau đó trình bày/call present_visual
```

## 5. Tool mới cho Gemini Live

Khai báo một tool điều phối:

```json
{
  "name": "request_template_layout",
  "description": "Request a temporary visual layout for the current user request.",
  "parameters": {
    "type": "object",
    "properties": {
      "domain_id": {
        "type": "string",
        "enum": ["education"]
      },
      "template_brief": {
        "type": "string",
        "description": "Một câu tiếng Việt tóm tắt điều người dùng muốn thấy/học trên màn hình."
      }
    },
    "required": ["domain_id", "template_brief"]
  }
}
```

Gemini là bên sinh `template_brief`. Backend chỉ chuyển tiếp brief, không dùng quy tắc từ khóa để tự đoán chó/mèo.

## 6. Đầu vào của Template LLM

Template LLM không nhận HTML/CSS, không nhận raw audio và không nhận ảnh dưới dạng base64 nếu catalog là đủ cho bước chọn.

```json
{
  "domain_id": "education",
  "template_brief": "Người dùng đang hỏi: hãy dạy bé về chó và mèo.",
  "recent_history": [
    { "role": "user", "text": "Hãy dạy bé về chó và mèo" }
  ],
  "canvas": {
    "columns": 12,
    "rows": 10
  },
  "assets": [
    {
      "id": "dog",
      "caption": "Minh hoạ một chú chó thân thiện dành cho trẻ em."
    },
    {
      "id": "cat",
      "caption": "Minh hoạ một chú mèo thân thiện dành cho trẻ em."
    }
  ],
  "allowed_blocks": ["text", "image"],
  "domain_prompt": "Tạo một màn hình học trực quan, đơn giản, vui tươi, phù hợp trẻ em. Chỉ tạo title, nhãn và hướng dẫn quan sát ngắn; không tạo kiến thức thực tế chưa được cung cấp."
}
```

## 7. Layout Spec Template LLM trả về

Ví dụ hợp lệ:

```json
{
  "blocks": [
    {
      "id": "b1",
      "type": "text",
      "content": "Cùng tìm hiểu chó và mèo",
      "grid": { "col": 1, "row": 1, "col_span": 12, "row_span": 1 }
    },
    {
      "id": "b2",
      "type": "text",
      "content": "Con hãy quan sát hai bạn nhé!",
      "grid": { "col": 1, "row": 2, "col_span": 12, "row_span": 1 }
    },
    {
      "id": "b3",
      "type": "image",
      "asset_id": "dog",
      "label": "Chó",
      "grid": { "col": 1, "row": 3, "col_span": 5, "row_span": 5 }
    },
    {
      "id": "b4",
      "type": "image",
      "asset_id": "cat",
      "label": "Mèo",
      "grid": { "col": 7, "row": 3, "col_span": 5, "row_span": 5 }
    }
  ]
}
```

Không chấp nhận các trường CSS tùy ý như `color`, `background`, `font_size`, `style` hoặc HTML string.

## 8. Validate ở backend

Backend phải từ chối spec nếu có một trong các lỗi sau:

- `type` không thuộc `text`, `image`.
- `asset_id` không tồn tại trong asset catalog đã cấp cho Template LLM.
- Toạ độ hoặc span vượt canvas 12 × 10.
- Hai block chồng lấn.
- Image không có `asset_id`; text không có `content`.
- Nội dung text vượt giới hạn ngắn của PoC.
- Có trường không thuộc schema được cho phép.

Backend không kiểm chứng tri thức trong title/label; nhưng prompt và validator giới hạn chúng ở copy giao diện ngắn, không phải dữ kiện bài học.

## 9. Render frontend

Tạo một renderer grid dùng chung, chỉ phục vụ PoC:

- Canvas dùng CSS Grid với 12 cột × 10 hàng.
- Theme Education được frontend áp cố định: nền, font, bo góc, khoảng đệm, khung ảnh và responsive.
- Grid không hiển thị các đường ô; nó chỉ là hệ toạ độ đặt block.
- `text` và `image` được render bằng component/block nội bộ, không dùng HTML sinh bởi LLM.
- Ở màn hình hẹp, renderer tự reflow các block cạnh nhau thành các hàng dọc theo quy tắc CSS cố định.

## 10. Anchor và ASCII stage map

Template LLM không tạo anchor.

Backend sinh anchor ngắn, ổn định từ block hợp lệ, ví dụ:

```text
b3 (ảnh chó) → anchor a
b4 (ảnh mèo) → anchor b
```

ASCII map phải sinh từ Layout Spec đã validate và cùng dữ liệu được renderer dùng:

```text
VISUAL STAGE MAP — CÙNG TÌM HIỂU CHÓ VÀ MÈO

                 Cùng tìm hiểu chó và mèo
             Con hãy quan sát hai bạn nhé!

┌──────────────────────────┐    ┌──────────────────────────┐
│           Chó            │    │           Mèo            │
│     [asset: dog]         │    │     [asset: cat]         │
│       [anchor: a]        │    │       [anchor: b]        │
└──────────────────────────┘    └──────────────────────────┘

ANCHOR LEGEND
a = vùng ảnh và nhãn Chó
b = vùng ảnh và nhãn Mèo
```

`panel_anchor_map` giữ ở backend sẽ map `a`/`b` tới DOM target thật và effect được phép. Gemini Live chỉ thấy map và các effect hợp lệ.

## 11. Integration với Gemini Live presentation

Sau khi panel đã render:

- Backend trả cho Gemini Live `presentation_instruction`, `visual_stage_map`, `visual_effects` (hiện chỉ có highlight và circle)
- Gemini Live chỉ được nói về nội dung xuất hiện trong stage map.
- Khi nói về chó hoặc mèo, Gemini Live gọi `present_visual(anchor_id, effect_id)` trước câu nói tương ứng.
- Backend dùng panel anchor map để validate và gửi marker effect tới frontend theo luồng hiện có.

## 12. Checkpoint triển khai

Mỗi checkpoint chỉ bắt đầu sau khi checkpoint trước đã được kiểm tra và chấp thuận. Không mở rộng sang kho template tái sử dụng, widget chuyên biệt hoặc domain khác trong PoC này.

### Checkpoint 1 — Catalog asset Education

**Trạng thái: Hoàn tất**

**Mục tiêu:** Có catalog tối thiểu, đáng tin cậy để Template LLM chỉ được chọn đúng ảnh chó và mèo có sẵn.

- Xác định và tái sử dụng hai asset hiện có hoặc bổ sung hai asset thuộc Education.
- Khai báo catalog gồm `id`, đường dẫn nội bộ và caption.
- Chưa thêm tool, Template LLM, renderer hay thay đổi luồng Gemini Live.

**Hoàn thành khi:** Backend đọc được catalog và hai asset hiển thị được theo đường dẫn nội bộ.

### Checkpoint 2 — Layout Spec và validator backend

**Trạng thái: Hoàn tất**

**Mục tiêu:** Xác lập contract an toàn cho một bố cục grid 12 × 10 do Template LLM trả về.

- Template LLM chỉ trả `blocks`; backend tự gắn canvas cố định 12 × 10 rồi validate `text`, `image` và toạ độ grid.
- Tạo validator: đúng block type, asset hợp lệ, không vượt canvas, không chồng lấn trái phép, đủ trường bắt buộc, không có trường CSS/HTML tùy ý.
- Viết test đơn vị cho một spec chó–mèo hợp lệ và các lỗi chính.

**Hoàn thành khi:** Spec mẫu ở mục 7 được chấp nhận; các spec sai bị từ chối với lỗi rõ ràng.

### Checkpoint 3 — Tool yêu cầu bố cục và Template LLM service

**Trạng thái: Hoàn tất**

**Mục tiêu:** Chuẩn bị an toàn service Template LLM và contract tool, chưa expose tool cho Gemini Live khi handler/presentation chưa tồn tại.

- Định nghĩa nội bộ `request_template_layout(domain_id, template_brief)`.
- Thêm service gọi Template LLM với đúng payload ở mục 6.
- Parse phản hồi theo schema Checkpoint 2; chưa render UI, chưa thay đổi `present_visual`.
- Xác định model/config Template LLM qua cấu hình, không dùng chung trực tiếp session Gemini Live đang hội thoại.

**Hoàn thành khi:** Service nhận một request mẫu, trả Layout Spec hợp lệ hoặc lỗi validation có kiểm soát. Tool chưa xuất hiện trong function declarations của Gemini Live.

### Checkpoint 4 — Dynamic Grid Presentation ở backend

**Trạng thái: Hoàn tất**

**Mục tiêu:** Mở rộng tầng Presentation dùng chung để chấp nhận Layout Spec mà không cần `template.html` hay Jinja.

- Thêm contract `DynamicGridPresentation` hoặc tương đương, giữ nguyên `PresentationRequest`/nhánh Jinja hiện có cho Weather và lesson Education cũ.
- Mở rộng Presentation Pipeline để nhận Layout Spec đã validate, không tìm `templates/<template_id>/template.html`.
- Tạo payload panel trung lập: `ui_type: "grid_layout"`, Layout Spec và asset đã resolve an toàn.
- Chưa đăng ký tool cho Gemini Live và chưa thay đổi frontend.

**Hoàn thành khi:** Backend tạo được Dynamic Grid Presentation hợp lệ từ spec mẫu; nhánh HTML/Jinja cũ vẫn pass test và không bị thay đổi hành vi.

### Checkpoint 5 — Frontend grid renderer

**Trạng thái: Hoàn tất**

**Mục tiêu:** Render Layout Spec đã validate thành panel responsive, đẹp và nhất quán với theme Education.

- Tạo renderer CSS Grid 12 × 10 dùng chung cho PoC.
- Render hai block `text`, `image` từ dữ liệu đã validate.
- Áp dụng Design System Education cố định; LLM không cung cấp HTML/CSS hoặc style tự do.
- Bảo đảm màn hẹp reflow theo CSS đã định nghĩa.

**Hoàn thành khi:** Spec chó–mèo được render đúng với title, hướng dẫn, hai vùng ảnh và không lộ ô grid kỹ thuật.

### Checkpoint 6 — Anchor map, ASCII stage map và effect

**Trạng thái: Hoàn tất**

**Mục tiêu:** Panel PoC trở thành một panel tương tác được như template hiện tại.

- Sinh anchor ổn định từ các block hợp lệ.
- Sinh `panel_anchor_map` và ASCII `VISUAL STAGE MAP` từ chính Layout Spec/render data.
- Nối vào luồng `present_visual` hiện có để backend vẫn validate anchor/effect và frontend vẫn chạy animation hiện tại.

**Hoàn thành khi:** ASCII map phản ánh đúng panel chó–mèo, và `present_visual` cho từng vùng ảnh chạy đúng vùng đó.

### Checkpoint 7 — Handler Education và expose tool

**Trạng thái: Hoàn tất**

**Ghi chú kiểm thử:** Test trực tiếp của Dynamic Grid/handler đều pass. Toàn bộ suite còn một test Education cũ mong mục tiêu lượt bằng tiếng Anh, trong khi prompt hiện dùng tiếng Việt; lỗi này có từ contract prompt/test chưa đồng bộ và không được sửa trong checkpoint này.

**Mục tiêu:** Nối tool đã chuẩn bị ở Checkpoint 3 với Dynamic Grid Presentation đã render được.

- Thêm handler Education chỉ cho `request_template_layout`.
- Handler gọi Template LLM service, validate kết quả và tạo Dynamic Grid Presentation cho session hiện tại.
- Chỉ sau đó mới thêm declaration vào function declarations của Gemini Live.
- Lỗi model/spec trả DomainResult có kiểm soát, không làm treo session và không tạo panel dang dở.

**Hoàn thành khi:** Gemini Live gọi được tool chó–mèo, backend trả panel Dynamic Grid cùng stage map/effect catalog hợp lệ.

### Checkpoint 8 — Kiểm thử end-to-end và giới hạn PoC

**Trạng thái: Đang kiểm thử — contract Template LLM đã được rút gọn còn `blocks` gồm `text` và `image`; backend tự gắn canvas 12 × 10.**

**Mục tiêu:** Xác minh toàn bộ luồng voice → Gemini Live → Template LLM → panel → presentation.

- Test câu: “Hãy dạy bé về chó và mèo”.
- Kiểm tra Gemini Live tạo `template_brief`, Template LLM chọn đúng asset, panel render đúng và Gemini gọi effect đúng anchor trước lời nói tương ứng.
- Kiểm tra lỗi Template LLM/spec không làm treo session hay ảnh hưởng Weather/template Education cũ.
- Ghi nhận các điểm cần cho giai đoạn sau: chọn template có sẵn, lưu Layout Spec, thêm widget chuyên biệt và thêm domain.

**Hoàn thành khi:** Các tiêu chí PoC ở mục 13 được đạt và đã có quyết định rõ ràng trước khi mở rộng phạm vi.

## 13. Tiêu chí đạt PoC

- Gemini Live tự sinh đúng `domain_id=education` và brief ngắn từ voice request.
- Template LLM chọn đúng asset chó và mèo bằng caption/ID.
- Renderer dựng panel đẹp, responsive, không cần HTML/CSS do LLM viết.
- ASCII map mô tả đúng panel đang render và có anchor hợp lệ.
- Gemini Live gọi `present_visual` đúng anchor trước khi nói về vùng đó.
- Không thay đổi runtime của Weather hay các template Education hiện tại.
- Layout Spec chỉ tồn tại trong session hiện tại, chưa lưu vào kho template.
