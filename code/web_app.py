"""Friendly web interface for the RAG Manager workflow."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from main import (
    _has_required_gemini_config,
    _load_workflow,
    _print_llm_usage,
    _response_from_result,
    _session_context_from_result,
)
from rag_manager.config import load_settings


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
MAX_QUERY_CHARS = 8_000


@dataclass
class ChatSession:
    """Per-browser conversation and visualization state."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    workflow_context: dict[str, Any] = field(default_factory=dict)
    active_visualization_html: str = ""
    updated_at: float = field(default_factory=time.monotonic)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class SessionStore:
    """Small bounded in-memory store for local web sessions."""

    def __init__(self, *, max_sessions: int = 128) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.RLock()
        self._max_sessions = max_sessions

    def get(self, session_id: str) -> ChatSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                if len(self._sessions) >= self._max_sessions:
                    oldest_id = min(
                        self._sessions,
                        key=lambda key: self._sessions[key].updated_at,
                    )
                    self._sessions.pop(oldest_id, None)
                session = ChatSession()
                self._sessions[session_id] = session
            session.updated_at = time.monotonic()
            return session

    def clear(self, session_id: str) -> ChatSession:
        with self._lock:
            session = ChatSession()
            self._sessions[session_id] = session
            return session


class WebChatService:
    """Connect the web API to the existing workflow without changing it."""

    def __init__(self, *, settings: Any, workflow: Any, store: SessionStore | None = None) -> None:
        self.settings = settings
        self.workflow = workflow
        self.store = store or SessionStore()

    def ensure_ready(self) -> None:
        if not _has_required_gemini_config(self.settings):
            raise RuntimeError("Thiếu GEMINI_API_KEY trong cấu hình.")
        if self.workflow is None:
            raise RuntimeError("Workflow LangGraph chưa sẵn sàng.")

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return _session_payload(session_id, self.store.get(session_id))

    def clear(self, session_id: str) -> dict[str, Any]:
        return _session_payload(session_id, self.store.clear(session_id))

    def chat(self, session_id: str, query: str) -> dict[str, Any]:
        self.ensure_ready()
        session = self.store.get(session_id)
        with session.lock:
            user_message = {
                "role": "user",
                "content": query,
                "include_in_history": True,
            }
            session.messages.append(user_message)
            state = {
                "query": query,
                "history": _workflow_history(session.messages),
                "settings": self.settings,
                **session.workflow_context,
            }

            try:
                result = self.workflow.invoke(state)
                if not isinstance(result, dict):
                    raise TypeError("Workflow trả về kết quả không hợp lệ.")

                _print_terminal_metrics(result)
                session.workflow_context.update(_session_context_from_result(result))
                new_html_path = _visualization_path(result)
                if new_html_path:
                    session.active_visualization_html = _read_rendered_template(
                        new_html_path
                    )

                assistant_message = {
                    "role": "assistant",
                    "content": _assistant_content(result, has_new_html=bool(new_html_path)),
                    # The deterministic dashboard notice is UI-only. Excluding it keeps
                    # Weather LLM1 focused on the user's actual wording in later turns.
                    "include_in_history": not bool(new_html_path),
                }
                session.messages.append(assistant_message)
                session.updated_at = time.monotonic()
                return _session_payload(session_id, session)
            except Exception as exc:  # noqa: BLE001 - web application boundary
                print(f"\n[WEB][WORKFLOW_ERROR] {type(exc).__name__}: {exc}", flush=True)
                session.messages.append(
                    {
                        "role": "assistant",
                        "content": "Xin lỗi, tôi chưa thể xử lý yêu cầu này lúc này.",
                        "include_in_history": False,
                    }
                )
                session.updated_at = time.monotonic()
                payload = _session_payload(session_id, session)
                payload["ok"] = False
                return payload


def _visualization_path(result: dict[str, Any]) -> str:
    output = result.get("visualization_output")
    if isinstance(output, dict) and output.get("ok") is True:
        output_path = output.get("html_path")
        if isinstance(output_path, str) and output_path.strip():
            return output_path.strip()

    if result.get("weather_status") == "completed":
        direct_path = result.get("visualization_html_path")
        if isinstance(direct_path, str) and direct_path.strip():
            return direct_path.strip()
    return ""


def _read_rendered_template(html_path: str) -> str:
    path = Path(html_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file visualization: {path}")
    return path.read_text(encoding="utf-8")


def _assistant_content(result: dict[str, Any], *, has_new_html: bool) -> str:
    if has_new_html:
        return "Đã cập nhật kết quả thời tiết ở bảng bên trái."
    return _response_from_result(result)


def _workflow_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if (
            message.get("include_in_history", True)
            and role in {"user", "assistant"}
            and isinstance(content, str)
            and content.strip()
        ):
            history.append({"role": role, "content": content})
    return history


def _print_terminal_metrics(result: dict[str, Any]) -> None:
    print("\n[WEB][WORKFLOW_METRICS]", flush=True)
    print(f"  - Topics: {result.get('selected_agents', [])}", flush=True)
    print(f"  - Timings: {result.get('timings', {})}", flush=True)
    _print_llm_usage(result.get("llm_usage", {}))


def _session_payload(session_id: str, session: ChatSession) -> dict[str, Any]:
    public_messages = [
        {
            "role": str(message.get("role", "assistant")),
            "content": str(message.get("content", "")),
        }
        for message in session.messages
    ]
    return {
        "ok": True,
        "session_id": session_id,
        "messages": public_messages,
        "has_visualization": bool(session.active_visualization_html),
        "visualization_html": session.active_visualization_html,
    }


def _valid_session_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("session_id không hợp lệ.")
    session_id = value.strip()
    if not session_id or len(session_id) > 128:
        raise ValueError("session_id không hợp lệ.")
    return session_id


def _valid_query(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("query không hợp lệ.")
    query = value.strip()
    if not query:
        raise ValueError("Vui lòng nhập câu hỏi.")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"Câu hỏi không được vượt quá {MAX_QUERY_CHARS} ký tự.")
    return query


_service: WebChatService | None = None
_service_lock = threading.Lock()


def _get_service() -> WebChatService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = WebChatService(
                    settings=load_settings(),
                    workflow=_load_workflow(),
                )
    return _service


async def homepage(_: Request) -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


async def health(_: Request) -> JSONResponse:
    try:
        _get_service().ensure_ready()
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)
    return JSONResponse({"ok": True})


async def get_session(request: Request) -> JSONResponse:
    try:
        session_id = _valid_session_id(request.path_params.get("session_id"))
        return JSONResponse(_get_service().snapshot(session_id))
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)


async def clear_session(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        session_id = _valid_session_id(payload.get("session_id"))
        return JSONResponse(_get_service().clear(session_id))
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)


async def chat(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        session_id = _valid_session_id(payload.get("session_id"))
        query = _valid_query(payload.get("query"))
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

    try:
        response = await run_in_threadpool(_get_service().chat, session_id, query)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=503)
    return JSONResponse(response)


routes = [
    Route("/", homepage),
    Route("/api/health", health),
    Route("/api/session/clear", clear_session, methods=["POST"]),
    Route("/api/session/{session_id}", get_session),
    Route("/api/chat", chat, methods=["POST"]),
    Mount("/assets", app=StaticFiles(directory=WEB_DIR), name="assets"),
]

app = Starlette(debug=False, routes=routes)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("WEB_PORT", "8501")),
    )
