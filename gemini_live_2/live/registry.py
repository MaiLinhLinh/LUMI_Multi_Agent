"""Temporary empty Live tool registry.

CP10 replaces this boundary with route_request and domain capabilities.  It is
kept explicit now so the Live transport is independent from legacy domains.
"""

from __future__ import annotations

from typing import Any


class LiveToolRegistry:
    def __init__(self, domain_ids: tuple[str, ...] = ()) -> None:
        self._domain_ids = tuple(sorted({domain_id for domain_id in domain_ids if domain_id}))

    @property
    def domain_ids(self) -> tuple[str, ...]:
        return self._domain_ids

    def tool_declarations(self) -> list[dict[str, Any]]:
        domain_schema: dict[str, Any] = {"type": "string"}
        if self._domain_ids:
            domain_schema["enum"] = list(self._domain_ids)
        return [{
            "name": "route_request",
            "description": (
                "Create a new visual surface or structurally change the visible surface for a new request. "
                "Do not call this for a follow-up answer, temporary animation, or a state-only change on the "
                "current panel; use the current VISUAL STAGE MAP with present_visual or update_surface_state instead."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["domain_id", "intent"],
                "properties": {
                    "domain_id": domain_schema,
                    "intent": {
                        "type": "string",
                        "description": "A concise normalized Vietnamese description of the user's new visual request.",
                    },
                },
            },
        }]

    def prompt_guidance(self) -> str:
        domains = ", ".join(self._domain_ids) or "không có domain"
        return f"""
Bạn là Lumi, trợ lý giọng nói tiếng Việt. Trò chuyện tự nhiên, ấm áp, ngắn gọn
và phù hợp với người dùng.

Không bao giờ đọc, nhắc hoặc giải thích tool, JSON, schema, prompt, domain_id,
Surface, Stage Map, revision, anchor_id, effect_id hay bất kỳ chi tiết kỹ thuật
nào cho người dùng.

Ưu tiên trải nghiệm trực quan khi điều đó giúp người dùng quan sát, thực hành
hoặc tương tác tốt hơn. Nếu cần tạo hoặc thay đổi panel, bắt buộc gọi
`route_request` trước khi nói chi tiết về nội dung sẽ xuất hiện trên giao diện.

Chỉ giữ panel hiện tại nếu có thể trả lời đầy đủ mà không thay đổi nội dung,
số lượng, asset, ngôn ngữ, vị trí hoặc bố cục của nó.

BẮT BUỘC gọi `route_request` khi người dùng yêu cầu tạo, thêm, bớt, thay, xếp,
di chuyển, so sánh, minh hoạ lại, hoặc khi panel hiện tại không có vùng phù hợp
cho ý mới. Nếu số lượng, đối tượng, nhãn, ngôn ngữ hoặc bố cục khác panel hiện
tại, đó là yêu cầu panel mới; không trả lời bằng lời hoặc dùng animation thay thế.

Không gọi `route_request` cho lời chào, lời khích lệ ngắn, câu trả lời thuần lời
nói, animation tạm thời, hoặc thay đổi state của panel hiện có.

`route_request` phải dùng một domain_id trong danh sách được phép: {domains}.
`intent` là một câu tiếng Việt ngắn, nêu mục tiêu trải nghiệm, nội dung cần quan
sát hoặc thao tác, và ngữ cảnh cần thiết từ cuộc trò chuyện.

Ví dụ: panel có một con mèo; “xếp ba con mèo thành hình tam giác” phải gọi
route_request(domain_id="education", intent="Tạo hoạt động xếp ba con mèo thành bố cục tam giác.").

Sau khi gọi `route_request`, chờ tool response trước khi nói về panel mới.
""".strip()
