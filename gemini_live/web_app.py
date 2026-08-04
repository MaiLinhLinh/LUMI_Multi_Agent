"""Independent production-style web application for Gemini Live Lumi."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

# Support both ``python -m gemini_live.web_app`` from the project root and
# ``python web_app.py`` while the current directory is ``gemini_live``.
if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from gemini_live.bootstrap import create_domain_registry, create_presentation_pipeline
from gemini_live.live import GeminiLiveSession, LiveSessionOrchestrator, LiveToolDispatcher
from gemini_live.settings import load_settings


BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
logger = logging.getLogger("lumi.gemini_live.web")
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


def configure_logging() -> None:
    for name in ("lumi.gemini_live", "lumi.gemini_live.web"):
        current = logging.getLogger(name)
        current.setLevel(logging.INFO)
        current.propagate = False
        if not current.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%H:%M:%S"))
            current.addHandler(handler)


configure_logging()
settings = load_settings()
registry = create_domain_registry(settings)
presentation_pipeline = create_presentation_pipeline(settings)
orchestrator = LiveSessionOrchestrator(
    LiveToolDispatcher(registry),
    presentation_pipeline=presentation_pipeline,
)
live_session = GeminiLiveSession(settings=settings, registry=registry, orchestrator=orchestrator)


async def _session_lock(session_id: str) -> asyncio.Lock:
    async with _locks_guard:
        return _locks.setdefault(session_id, asyncio.Lock())


async def home(_: Any) -> FileResponse:
    return FileResponse(WEB / "index.html")


async def app_js(_: Any) -> FileResponse:
    return FileResponse(WEB / "app.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})


async def health(_: Any) -> JSONResponse:
    return JSONResponse({
        "ok": bool(settings.gemini_live_api_key),
        "mode": "gemini-live",
        "domains": registry.domain_ids,
    })


async def live_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        start = await websocket.receive_json()
        if not isinstance(start, dict) or start.get("type") != "live:start":
            raise ValueError("Sự kiện đầu tiên phải là live:start.")
        input_mode = str(start.get("input_mode") or "text").lower()
        if input_mode not in {"text", "audio"}:
            raise ValueError("input_mode phải là text hoặc audio.")
        query = str(start.get("query") or "").strip()
        if input_mode == "text" and not query:
            raise ValueError("Câu hỏi văn bản không được để trống.")
        session_id = str(start.get("session_id") or uuid.uuid4())[:200]
        lock = await _session_lock(session_id)
        async with lock:
            async def event(payload: dict[str, Any]) -> None:
                await websocket.send_json(payload)

            async def audio(pcm: bytes, _: int) -> None:
                await websocket.send_bytes(pcm)

            await event({"type": "live:ready", "session_id": session_id, "input_mode": input_mode})
            logger.info("[WEB:REQUEST] session=%s mode=%s", session_id, input_mode)
            if input_mode == "text":
                summary = await live_session.run_text_turn(
                    session_id=session_id, query=query, on_event=event, on_audio=audio
                )
            else:
                queue: asyncio.Queue[bytes | None] = asyncio.Queue()

                async def receive_audio() -> None:
                    chunks = size = 0
                    try:
                        while True:
                            packet = await websocket.receive()
                            if packet["type"] == "websocket.disconnect":
                                break
                            if packet.get("bytes") is not None:
                                chunk = packet["bytes"]
                                chunks += 1
                                size += len(chunk)
                                await queue.put(chunk)
                                if chunks == 1 or chunks % 25 == 0:
                                    logger.info("[WEB:MIC_RECEIVED] session=%s chunks=%s bytes=%s", session_id, chunks, size)
                                    await event({"type": "live:server_audio_received", "chunks": chunks, "bytes": size})
                                continue
                            text = packet.get("text")
                            if text and json.loads(text).get("type") == "live:audio_end":
                                break
                    finally:
                        await queue.put(None)
                        await event({"type": "live:server_audio_closed", "chunks": chunks, "bytes": size})

                receiver = asyncio.create_task(receive_audio(), name="browser-mic-input")
                try:
                    summary = await live_session.run_audio_turn(
                        session_id=session_id, audio_chunks=queue, on_event=event, on_audio=audio
                    )
                finally:
                    if not receiver.done():
                        receiver.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await receiver
            await event({"type": "live:complete", "session_id": session_id, "summary": summary})
    except WebSocketDisconnect:
        logger.info("[WEB:DISCONNECT]")
    except Exception as exc:
        logger.exception("[WEB:ERROR]")
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "live:error", "message": str(exc)})


app = Starlette(routes=[
    Route("/", home),
    Route("/assets/app.js", app_js),
    Route("/api/health", health),
    WebSocketRoute("/ws/live", live_socket),
    Mount("/assets", app=StaticFiles(directory=WEB), name="assets"),
])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
