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
                "Create or replace the visible panel for a new visual request. "
                "Do not call this for a follow-up that can be answered from the current panel; "
                "use the current VISUAL STAGE MAP and present_visual instead."
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
        return (
            """Chỉ giữ panel hiện tại nếu có thể trả lời đầy đủ mà không thay đổi bất kỳ nội dung,
            số lượng, asset, ngôn ngữ, vị trí hoặc bố cục nào trên panel.

            BẮT BUỘC gọi route_request khi người dùng yêu cầu tạo, thêm, bớt, thay, xếp,
            di chuyển, so sánh, minh hoạ lại hoặc học nội dung khiến panel hiện tại cần đổi.
            Nếu số lượng, đối tượng, nhãn, ngôn ngữ hoặc bố cục người dùng yêu cầu khác
            VISUAL STAGE MAP hiện tại, đó luôn là panel mới — không được trả lời bằng lời
            hay gọi present_visual thay thế.

            Ví dụ: panel có 1 con mèo; “xếp 3 con mèo thành hình tam giác” phải gọi
            route_request(domain_id="education", intent="Xếp ba hình mèo thành bố cục tam giác").
            """
            f"Domain hiện có: {domains}."
        )
