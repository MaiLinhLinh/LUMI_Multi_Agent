"""High-confidence local router before the LLM Manager."""
from __future__ import annotations

import re

from rag_manager.state import GraphState


def router_node(state: GraphState) -> dict[str, str]:
    text = state["query"].casefold()
    # Bypass the Manager only for explicit, unambiguous domain markers.
    # Words such as "mưa", "bài", or a short follow-up need history and are
    # deliberately delegated to the LLM Manager.
    if re.search(r"thời tiết|thoi tiet|dự báo thời tiết|du bao thoi tiet", text):
        return {"route": "domain", "selected_agent": "weather"}
    if re.search(r"\b(bật|mở|phát|nghe|tìm)\b.*\b(bài hát|bai hat|nhạc|nhac|playlist)\b", text):
        return {"route": "domain", "selected_agent": "music"}
    if re.search(r"biểu đồ|bieu do|tương tác.*(kết quả|biểu đồ)|tuong tac.*(ket qua|bieu do)", text):
        return {"route": "visual", "selected_agent": "visual"}
    return {"route": "manager"}
