"""Persistent-browser WebSocket application for the multi-domain Gemini Live Lumi."""

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
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from gemini_live.bootstrap import create_domain_registry, create_presentation_pipeline
from gemini_live.live import (
    GeminiLiveSession,
    LiveSessionOrchestrator,
    LiveToolDispatcher,
    PersistentLiveTransportStore,
)
from gemini_live.settings import load_settings
from gemini_live.trace import TRACE_LEVEL, trace


BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
logger = logging.getLogger("lumi.gemini_live.web")
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()
_cleanup_tasks: dict[str, asyncio.Task[None]] = {}


def configure_logging() -> None:
    for name in ("lumi.trace", "lumi.gemini_live", "lumi.gemini_live.web", "lumi.presentation"):
        current = logging.getLogger(name)
        current.setLevel(TRACE_LEVEL if name == "lumi.trace" else logging.INFO)
        current.propagate = False
        if not current.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"
            ))
            current.addHandler(handler)


configure_logging()
settings = load_settings()
registry = create_domain_registry(settings)
presentation_pipeline = create_presentation_pipeline(settings)
orchestrator = LiveSessionOrchestrator(
    LiveToolDispatcher(registry), presentation_pipeline=presentation_pipeline
)
live_session = GeminiLiveSession(settings=settings, registry=registry, orchestrator=orchestrator)
persistent_transports = PersistentLiveTransportStore()


async def _session_lock(session_id: str) -> asyncio.Lock:
    async with _locks_guard:
        return _locks.setdefault(session_id, asyncio.Lock())


async def _cancel_scheduled_cleanup(session_id: str) -> None:
    task = _cleanup_tasks.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _close_after_grace(session_id: str) -> None:
    try:
        await asyncio.sleep(settings.live_reconnect_grace_seconds)
        await persistent_transports.close(session_id)
        orchestrator.reset_session_state(session_id)
        logger.info("[WEB:PERSISTENT_CLEANUP] session=%s grace_s=%s", session_id, settings.live_reconnect_grace_seconds)
    except asyncio.CancelledError:
        raise
    finally:
        _cleanup_tasks.pop(session_id, None)


def _schedule_cleanup(session_id: str) -> None:
    if not session_id or session_id in _cleanup_tasks:
        return
    _cleanup_tasks[session_id] = asyncio.create_task(
        _close_after_grace(session_id), name=f"live-cleanup:{session_id}"
    )


async def home(_: Any) -> FileResponse:
    return FileResponse(WEB / "index.html")


async def app_js(_: Any) -> FileResponse:
    return FileResponse(
        WEB / "app.js", media_type="application/javascript", headers={"Cache-Control": "no-store"}
    )


async def health(_: Any) -> JSONResponse:
    return JSONResponse({
        "ok": bool(settings.gemini_live_api_key),
        "mode": "gemini-live-persistent",
        "domains": registry.domain_ids,
    })


async def client_debug(request: Request) -> JSONResponse:
    """Receive short browser bootstrap diagnostics during local development."""

    try:
        payload = await request.json()
    except Exception:
        payload = {"message": "invalid client diagnostic"}
    if not isinstance(payload, dict):
        payload = {"message": str(payload)}
    logger.info(
        "[WEB:CLIENT_DIAGNOSTIC] phase=%s message=%s source=%s line=%s",
        str(payload.get("phase") or "unknown")[:80],
        str(payload.get("message") or "")[:500],
        str(payload.get("source") or "")[:300],
        payload.get("line") or "",
    )
    return JSONResponse({"ok": True})


async def live_socket(websocket: WebSocket) -> None:
    """Keep one browser socket and one Gemini connection across many turns."""

    await websocket.accept()
    conversation: Any | None = None
    turn_task: asyncio.Task[None] | None = None
    audio_stream_task: asyncio.Task[None] | None = None
    idle_task: asyncio.Task[None] | None = None
    session_id = ""
    try:
        start = await websocket.receive_json()
        if not isinstance(start, dict) or start.get("type") != "live:connect":
            raise ValueError("Sự kiện đầu tiên phải là live:connect.")
        session_id = str(start.get("session_id") or uuid.uuid4())[:200]
        lock = await _session_lock(session_id)

        async with lock:
            await _cancel_scheduled_cleanup(session_id)
            send_lock = asyncio.Lock()
            loop = asyncio.get_running_loop()
            last_activity = loop.time()

            def touch() -> None:
                nonlocal last_activity
                last_activity = loop.time()

            async def event(payload: dict[str, Any]) -> None:
                touch()
                async with send_lock:
                    await websocket.send_json(payload)

            async def audio(
                pcm: bytes,
                _: int,
                marker: dict[str, Any] | None = None,
                turn_id: str = "",
            ) -> None:
                touch()
                async with send_lock:
                    await websocket.send_json({"type": "audio_chunk", "turn_id": turn_id})
                    if marker is not None:
                        await websocket.send_json({"type": "audio_marker", **marker, "turn_id": turn_id})
                    await websocket.send_bytes(pcm)

            async def reconnect(reason: str) -> None:
                """Reconnect transport only; domain memory/context remains server-owned."""

                nonlocal conversation
                await event({"type": "live:reconnecting", "reason": reason})
                await persistent_transports.close(session_id)
                orchestrator.reset_session_state(session_id)
                conversation = await live_session.open_persistent_conversation(
                    session_id=session_id,
                    transport=persistent_transports.get(session_id),
                    on_event=event,
                    on_audio=audio,
                )
                await event({"type": "live:reconnected", "session_id": session_id})
                await event({"type": "live:state", "state": orchestrator.session_state(session_id)})
                logger.info("[WEB:PERSISTENT_RECONNECTED] session=%s reason=%s", session_id, reason)

            transport = persistent_transports.get(session_id)
            discarded = transport.discard_pending_messages() if transport.connected else 0
            if discarded:
                logger.info("[WEB:PERSISTENT_STALE_EVENTS_DROPPED] session=%s count=%s", session_id, discarded)
            conversation = await live_session.open_persistent_conversation(
                session_id=session_id, transport=transport, on_event=event, on_audio=audio
            )
            # A browser reconnect never resumes an unverified technical state.
            orchestrator.reset_session_state(session_id)
            await event({"type": "live:session_ready", "session_id": session_id})
            await event({"type": "live:state", "state": orchestrator.session_state(session_id)})
            logger.info("[WEB:PERSISTENT_CONNECTED] session=%s reused=%s", session_id, transport.connected)

            async def finish_turn(awaitable: Any) -> None:
                try:
                    summary = await asyncio.wait_for(awaitable, timeout=settings.live_turn_timeout_seconds)
                    await event({
                        "type": "live:turn_complete",
                        "session_id": session_id,
                        "turn_id": summary.get("turn_id"),
                        "summary": summary,
                    })
                except asyncio.TimeoutError:
                    logger.warning("[WEB:PERSISTENT_TURN_TIMEOUT] session=%s timeout_s=%s", session_id, settings.live_turn_timeout_seconds)
                    await event({"type": "live:timeout", "reason": "turn_timeout"})
                    await reconnect("turn_timeout")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("[WEB:PERSISTENT_TURN_ERROR] session=%s", session_id)
                    try:
                        await reconnect("transport_error")
                    except Exception:
                        await event({"type": "live:error", "message": str(exc)})

            async def idle_watch() -> None:
                interval = min(5.0, max(0.5, settings.live_idle_timeout_seconds / 10))
                while True:
                    await asyncio.sleep(interval)
                    if loop.time() - last_activity < settings.live_idle_timeout_seconds:
                        continue
                    await event({"type": "live:timeout", "reason": "idle_timeout"})
                    logger.info("[WEB:PERSISTENT_IDLE_TIMEOUT] session=%s", session_id)
                    await websocket.close(code=1000)
                    return

            idle_task = asyncio.create_task(idle_watch(), name=f"live-idle:{session_id}")
            microphone_enabled = False

            async def consume_audio_stream() -> None:
                """Consume the persistent microphone stream across Gemini VAD turns."""

                while microphone_enabled:
                    try:
                        await conversation.consume_audio_stream()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.exception("[WEB:PERSISTENT_AUDIO_STREAM_ERROR] session=%s", session_id)
                        try:
                            await reconnect("audio_stream_error")
                            if microphone_enabled:
                                await conversation.begin_audio()
                        except Exception:
                            await event({"type": "live:error", "message": str(exc)})
                            return

            chunks = size = 0
            while True:
                packet = await websocket.receive()
                touch()
                if packet["type"] == "websocket.disconnect":
                    break
                if packet.get("bytes") is not None:
                    if turn_task is not None and not turn_task.done():
                        logger.warning("[WEB:PERSISTENT_AUDIO_REJECTED] session=%s reason=text_turn_active", session_id)
                        continue
                    if not microphone_enabled:
                        logger.warning("[WEB:PERSISTENT_AUDIO_REJECTED] session=%s reason=microphone_disabled", session_id)
                        continue
                    chunk = packet["bytes"]
                    chunks += 1
                    size += len(chunk)
                    await conversation.send_audio(chunk)
                    if chunks == 1:
                        trace("PCM_SENT first_chunk bytes=%s", len(chunk))
                    elif chunks % 25 == 0:
                        trace("PCM_SENT chunks=%s total_bytes=%s", chunks, size)
                    if chunks == 1 or chunks % 25 == 0:
                        await event({"type": "live:server_audio_received", "chunks": chunks, "bytes": size})
                    continue

                text = packet.get("text")
                if not text:
                    continue
                command = json.loads(text)
                command_type = str(command.get("type") or "")
                if command_type == "live:text":
                    query = str(command.get("query") or "").strip()
                    if not query:
                        await event({"type": "live:error", "message": "Câu hỏi văn bản không được để trống."})
                    elif turn_task is not None and not turn_task.done():
                        await event({"type": "live:error", "message": "Lumi đang xử lý lượt trước."})
                    else:
                        turn_task = asyncio.create_task(finish_turn(conversation.submit_text(query)), name=f"live-text:{session_id}")
                elif command_type in {"live:audio_begin", "live:mic_enabled"}:
                    if turn_task is not None and not turn_task.done():
                        await event({"type": "live:error", "message": "Lumi đang xử lý lượt trước."})
                    else:
                        # Gemini may have closed its remote socket while the
                        # browser stayed open. Recreate it before accepting a
                        # continuous microphone stream so PCM is not sent to
                        # a dead session.
                        if not persistent_transports.get(session_id).connected:
                            await reconnect("transport_closed_before_microphone")
                        chunks = size = 0
                        microphone_enabled = True
                        await conversation.begin_audio()
                        if audio_stream_task is None or audio_stream_task.done():
                            audio_stream_task = asyncio.create_task(
                                consume_audio_stream(), name=f"live-audio-stream:{session_id}"
                            )
                elif command_type in {"live:audio_end", "live:mic_disabled"}:
                    if turn_task is not None and not turn_task.done():
                        await event({"type": "live:error", "message": "Không có lượt microphone đang chờ."})
                    else:
                        trace("MIC_END chunks=%s total_bytes=%s", chunks, size)
                        await event({"type": "live:server_audio_closed", "chunks": chunks, "bytes": size})
                        microphone_enabled = False
                        await conversation.end_audio_stream()
                elif command_type == "live:close":
                    break
                else:
                    await event({"type": "live:error", "message": f"Sự kiện không hỗ trợ: {command_type}"})
    except WebSocketDisconnect:
        logger.info("[WEB:DISCONNECT] session=%s", session_id or "unknown")
    except Exception as exc:
        logger.exception("[WEB:ERROR] session=%s", session_id or "unknown")
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "live:error", "message": str(exc)})
    finally:
        if idle_task is not None and not idle_task.done():
            idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await idle_task
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn_task
        if audio_stream_task is not None and not audio_stream_task.done():
            audio_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await audio_stream_task
        # Keep Gemini open briefly so a browser reconnect can retain its Live
        # context. Verified memory is preserved even if the grace period ends.
        _schedule_cleanup(session_id)


app = Starlette(routes=[
    Route("/", home),
    Route("/assets/app.js", app_js),
    Route("/api/health", health),
    Route("/api/client-debug", client_debug, methods=["POST"]),
    WebSocketRoute("/ws/live", live_socket),
    Mount("/assets", app=StaticFiles(directory=WEB), name="assets"),
])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8002)
