"""Streamlit chat interface for the RAG Manager workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from main import (
    _has_required_gemini_config,
    _load_workflow,
    _response_from_result,
    _session_context_from_result,
)
from rag_manager.config import load_settings

_APP_STYLES = """
<style>
    .stApp {
        background:
            radial-gradient(circle at top left, rgba(14, 165, 233, 0.10), transparent 28rem),
            #f5f7fb;
        color: #0f172a;
    }

    [data-testid="stHeader"] {
        background: transparent;
    }

    [data-testid="stToolbar"],
    [data-testid="stSidebarCollapsedControl"] {
        display: none;
    }

    [data-testid="stMainBlockContainer"] {
        width: 100%;
        max-width: 1120px;
        padding-top: 1.75rem;
        padding-bottom: 7rem;
    }

    .lumi-header {
        padding: 0.25rem 0 1.25rem;
    }

    .lumi-header h1 {
        color: #0f172a;
        font-size: clamp(1.65rem, 3vw, 2.25rem);
        line-height: 1.15;
        margin: 0;
    }

    .lumi-header p {
        color: #64748b;
        margin: 0.45rem 0 0;
    }

    [data-testid="stChatMessage"] {
        background: rgba(255, 255, 255, 0.96);
        border: 1px solid #dce4ef;
        border-radius: 18px;
        box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
        margin-bottom: 0.85rem;
        padding: 1rem 1.1rem;
    }

    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        background: #eaf4ff;
        border-color: #c9e2fb;
        flex-direction: row-reverse;
        margin-left: auto;
        margin-right: 0;
        max-width: 76%;
        width: fit-content;
    }

    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
        margin-left: 0;
        margin-right: auto;
    }

    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]):not(:has(iframe)) {
        max-width: 76%;
        width: fit-content;
    }

    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]):has(iframe) {
        max-width: 100%;
        width: 100%;
    }

    [data-testid="stChatMessageContent"] {
        min-width: 0;
        overflow: hidden;
        width: 100%;
    }

    [data-testid="stChatMessage"] iframe {
        background: #ffffff;
        border: 1px solid #dce4ef;
        border-radius: 14px;
        box-shadow: none;
        display: block;
        width: 100%;
    }

    [data-testid="stChatInput"] {
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 16px;
        box-shadow: 0 12px 35px rgba(15, 23, 42, 0.12);
    }

    .stButton > button {
        border: 1px solid #cbd5e1;
        border-radius: 12px;
        color: #334155;
        min-height: 2.6rem;
    }

    @media (max-width: 720px) {
        [data-testid="stMainBlockContainer"] {
            padding-left: 0.75rem;
            padding-right: 0.75rem;
        }

        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]),
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
            margin-left: 0;
            margin-right: 0;
        }
    }
</style>
"""


def _visualization_path(result: dict[str, Any]) -> str:
    direct_path = result.get("visualization_html_path")
    if isinstance(direct_path, str) and direct_path.strip():
        return direct_path.strip()

    output = result.get("visualization_output")
    if isinstance(output, dict):
        output_path = output.get("html_path")
        if isinstance(output_path, str) and output_path.strip():
            return output_path.strip()
    return ""


def _read_rendered_template(html_path: str) -> str:
    path = Path(html_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file visualization: {path}")
    return path.read_text(encoding="utf-8")


def _assistant_message(result: dict[str, Any]) -> dict[str, str]:
    if result.get("weather_status") in {
        "needs_clarification",
        "unavailable",
        "error",
    }:
        return {
            "role": "assistant",
            "content": _response_from_result(result),
            "html_path": "",
        }

    html_path = _visualization_path(result)
    if html_path:
        return {"role": "assistant", "content": "", "html_path": html_path}
    return {
        "role": "assistant",
        "content": _response_from_result(result),
        "html_path": "",
    }


def _workflow_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if (
            role in {"user", "assistant"}
            and isinstance(content, str)
            and content.strip()
        ):
            history.append({"role": role, "content": content})
    return history


def run_app() -> None:
    import streamlit as st
    import streamlit.components.v1 as components

    st.set_page_config(
        page_title="Trợ lí ảo chatbot",
        page_icon="🌤️",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    st.markdown(_APP_STYLES, unsafe_allow_html=True)

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "workflow_context" not in st.session_state:
        st.session_state.workflow_context = {}
    if "settings" not in st.session_state:
        st.session_state.settings = load_settings()
    if "workflow" not in st.session_state:
        st.session_state.workflow = _load_workflow()

    header_column, action_column = st.columns([5, 1])
    with header_column:
        st.markdown(
            """
            <div class="lumi-header">
                <h1>Trợ lí ảo chatbot</h1>
                <p>Trợ lý thời tiết, tin tức và tri thức trong một cuộc hội thoại.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with action_column:
        st.write("")
        clear_conversation = st.button(
            "Xóa",
            help="Xóa lịch sử hội thoại hiện tại",
            use_container_width=True,
        )

    if clear_conversation:
        st.session_state.messages = []
        st.session_state.workflow_context = {}
        st.rerun()

    settings = st.session_state.settings
    workflow = st.session_state.workflow
    if not _has_required_gemini_config(settings):
        st.error(
            "Thiếu GEMINI_API_KEY. Hãy cấu hình biến môi trường rồi chạy lại "
            "ứng dụng."
        )
        return
    if workflow is None:
        st.error("Workflow LangGraph chưa sẵn sàng.")
        return

    for message in st.session_state.messages:
        _render_chat_message(st, components, message)

    query = st.chat_input("Nhập câu hỏi của bạn")
    if not query:
        return

    user_message = {"role": "user", "content": query, "html_path": ""}
    st.session_state.messages.append(user_message)
    _render_chat_message(st, components, user_message)

    try:
        with st.spinner("Đang xử lý..."):
            result = workflow.invoke(
                {
                    "query": query,
                    "history": _workflow_history(st.session_state.messages),
                    "settings": settings,
                    **st.session_state.workflow_context,
                }
            )
        if not isinstance(result, dict):
            raise TypeError("Workflow trả về kết quả không hợp lệ.")
        st.session_state.workflow_context.update(
            _session_context_from_result(result)
        )
        assistant_message = _assistant_message(result)
    except Exception as exc:  # noqa: BLE001 - UI boundary
        assistant_message = {
            "role": "assistant",
            "content": f"Không thể xử lý yêu cầu lúc này: {exc}",
            "html_path": "",
        }

    st.session_state.messages.append(assistant_message)
    _render_chat_message(st, components, assistant_message)


def _render_chat_message(st: Any, components: Any, message: dict[str, str]) -> None:
    role = message.get("role", "assistant")
    with st.chat_message(role):
        content = message.get("content", "")
        if content:
            st.markdown(content)

        html_path = message.get("html_path", "")
        if html_path:
            try:
                rendered_template = _read_rendered_template(html_path)
            except (OSError, UnicodeError) as exc:
                st.error(str(exc))
            else:
                components.html(rendered_template, height=600, scrolling=True)


if __name__ == "__main__":
    run_app()
