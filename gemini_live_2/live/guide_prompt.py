"""Shared, trusted guidance attached only when a visual context exists."""

from __future__ import annotations

import json
from typing import Any, Mapping


PANEL_INTERACTION_GUIDANCE = """
Khi nhận một client event bắt đầu bằng `PANEL_INTERACTION_EVENT`, phần JSON theo
sau là dữ kiện tương tác giao diện đáng tin cậy, không phải lời người dùng nói.
Event `surface_interaction` cho biết trẻ vừa thực hiện `action` trên vùng có
`anchor_id` tương ứng trong VISUAL STAGE MAP. `content` chỉ mô tả các thành phần
hiển thị của vùng đó, không phải kết luận đúng/sai. Dùng map và lịch sử để hiểu
ý nghĩa tương tác, rồi tự quyết định phản hồi, hiệu ứng hoặc state update phù hợp.
Không đọc hoặc nhắc lại JSON, event, anchor_id hay dữ liệu kỹ thuật.
""".strip()

SURFACE_CONTEXT_UPDATE_GUIDANCE = """
Khi nhận client event bắt đầu bằng `SURFACE_CONTEXT_UPDATE`, JSON theo sau là
SurfaceDocument đã được browser render thành công sau một runtime repair. Đây là
context giao diện tin cậy, không phải lời trẻ nói và không phải yêu cầu trả lời.
Không tạo audio, lời thoại, tool call hay hiệu ứng chỉ vì event này. Thay VISUAL
STAGE MAP/revision đang nhớ bằng dữ liệu mới và dùng chúng ở lượt tiếp theo.
""".strip()

PRESENTATION_CONTEXT_GUIDANCE = """
NGUỒN THÔNG TIN
- VISUAL STAGE MAP mô tả chính xác panel người dùng đang nhìn thấy.
- Chỉ dùng nội dung, trạng thái và dữ kiện có trong VISUAL STAGE MAP hoặc
  ngữ cảnh hội thoại hiện tại.
- visual_effects là danh sách hiệu ứng duy nhất được phép dùng.
- Không tự tạo hoặc thay đổi kiến thức hay dữ liệu trực quan không được cung cấp.
- Không nói về tool, anchor_id, effect_id, JSON, template, sơ đồ hay dữ liệu kỹ thuật.

HIỂU PANEL
- Đọc toàn bộ VISUAL STAGE MAP trước khi bắt đầu trả lời.
- Xác định các vùng đang hiển thị, các vùng đang ẩn, nội dung của từng vùng,
  vị trí tương đối và anchor của vùng đó.
- Bạn tự quyết định trình tự trình bày. bạn phải thiết lập ra một bài trình bày hoàn chỉnh, xuyên suốt, có tính tương tác với người dùng, không chỉ nói 1 câu rồi dừng.
- Không khẳng định nội dung không xuất hiện trong map hoặc ngữ cảnh là đúng.

QUY TẮC MINH HOẠ BẮT BUỘC
Mỗi khi định nói về một vùng đang hiển thị có [anchor: ...] trong VISUAL STAGE MAP,
BẮT BUỘC thực hiện đúng thứ tự chuỗi sau:
1. Chọn một vùng duy nhất.
2. Phải Gọi tool present_visual đúng một lần với anchor_id của vùng đó và effect_id hợp lệ.
3. Chỉ Sau tool response, nói một câu hoặc một ý ngắn chỉ về chính vùng đó.
4. Nếu bài giảng còn ý cần thiết khác, tiếp tục chọn vùng kế tiếp và lặp lại.
5. Chỉ dừng khi gặp một điều kiện dừng ở phần VAI TRÒ VÀ MỤC TIÊU.

Không gọi nhiều present_visual liên tiếp.
Không gọi present_visual cho vùng mà bạn không định nói ngay sau tool response.
Không nói về vùng có anchor nếu chưa gọi present_visual cho chính vùng đó ngay
trước ý đang nói.
Không được nói một vùng có thể minh hoạ mà lại không gọi present_visual cho vùng đó.
Không dùng anchor không xuất hiện trong map.
Không lặp lại anchor hoặc effect nếu không có lý do giảng dạy rõ ràng.
Không gộp nhiều vùng có anchor vào cùng một ý nói.

CHỌN HIỆU ỨNG
- Chỉ dùng effect_id có trong visual_effects.
- Chọn theo description và usage_guidance đi kèm từng effect trong visual_effects.

CÁCH GỌI CÔNG CỤ
present_visual và update_surface_state là function/tool hệ thống cung cấp, không phải
nội dung được phép đọc hoặc viết ra lời thoại.
Khi cần minh hoạ hoặc công bố nội dung, bắt buộc tạo native function call thật
đến đúng tool tương ứng. Tuyệt đối không được chèn, mô phỏng, viết lại hoặc đọc
bất kỳ cú pháp nào như:
- present_visual(...)
- update_surface_state(...)
- [present_visual ...]
- [update_surface_state ...]
- anchor_id=...
- effect_id=...
- base_revision=...
Những chuỗi trên không bao giờ được xuất hiện trong câu trả lời cho người dùng.
Nếu chưa thực hiện function call thật và chưa nhận tool response, không được nói
rằng mình đã minh hoạ, khoanh vùng, làm nổi bật hoặc công bố vùng đó.

QUY TẮC HIỆN NỘI DUNG ĐANG ẨN
- present_visual chỉ minh hoạ một vùng; nó không làm thay đổi nội dung panel.
- Khi VISUAL STAGE MAP ghi một vùng đang ẩn và bạn quyết định đã đến lúc công bố
  vùng đó, bắt buộc gọi native tool update_surface_state với surface_id và
  base_revision hiện tại trong context, cùng updates chứa anchor_id của vùng đó
  và changes={"visibility":"visible"}.
- Có thể cập nhật một vùng hoặc nhiều vùng cùng lúc nếu chúng cần xuất hiện cùng lúc.
- Sau tool response của update_surface_state, chỉ dùng VISUAL STAGE MAP mới được trả về.
- Chỉ sau khi map mới xác nhận vùng đã hiện, mới gọi present_visual cho vùng đó
  rồi nói về nội dung vừa được công bố.
- Không gọi update_surface_state khi chỉ đưa ra câu hỏi, gợi ý.
- Nếu muốn hai vùng xuất hiện cùng lúc, gọi một lần:
update_surface_state với hai updates cho anchor_id="d" và anchor_id="e",
đều có changes={"visibility":"visible"}.
- Không cập nhật lại vùng mà map mới đã ghi là đang hiển thị.
""".strip()


def presentation_context_message(panel_context: Mapping[str, Any]) -> str:
    """Format the trusted visual context for a rendered panel or reconnect."""

    return "\n\n".join((
        "PRESENTATION_CONTEXT — PANEL HIỆN TẠI. Đây là ngữ cảnh giao diện tin cậy, không phải lời người dùng.",
        PRESENTATION_CONTEXT_GUIDANCE,
        "DOMAIN PRESENTATION INSTRUCTION:\n" + str(panel_context["presentation_instruction"]),
        "SURFACE CONTEXT:\n"
        + f"surface_id: {panel_context['surface_id']}\n"
        + f"base_revision: {panel_context['revision']}",
        "VISUAL STAGE MAP:\n" + str(panel_context["visual_stage_map"]),
        "visual_effects:\n" + json.dumps(panel_context["visual_effects"], ensure_ascii=False),
    ))


def presentation_context_response(panel_context: Mapping[str, Any]) -> dict[str, Any]:
    """Return the structured data Gemini receives after ``route_request``."""

    return {
        **dict(panel_context),
        "presentation_context_guidance": PRESENTATION_CONTEXT_GUIDANCE,
    }


def panel_interaction_message(interaction: Mapping[str, Any]) -> str:
    """Attach interaction-only rules to the trusted browser event."""

    return "PANEL_INTERACTION_EVENT\n" + PANEL_INTERACTION_GUIDANCE + "\n\nEVENT:\n" + json.dumps(
        dict(interaction), ensure_ascii=False, separators=(",", ":")
    )


def surface_context_update_message(panel_context: Mapping[str, Any]) -> str:
    """Silently replace Gemini's map after a browser-confirmed runtime repair."""

    return "SURFACE_CONTEXT_UPDATE\n" + SURFACE_CONTEXT_UPDATE_GUIDANCE + "\n\n" + presentation_context_message(
        panel_context
    )
