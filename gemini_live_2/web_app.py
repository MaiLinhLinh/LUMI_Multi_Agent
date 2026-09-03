"""Standalone web entrypoint for the SurfaceDocument application."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gemini_live_2.live.gemini_session import GeminiLiveSession
from gemini_live_2.live.orchestrator import LiveSessionOrchestrator
from gemini_live_2.live.persistent_transport import PersistentLiveTransportStore
from gemini_live_2.live.registry import LiveToolRegistry
from gemini_live_2.catalogs.domains import DomainRegistry, ManifestError
from gemini_live_2.gateway import DomainGateway
from gemini_live_2.panel import PanelCompiler
from gemini_live_2.plan_agent import PlanAgent
from gemini_live_2.settings import load_settings
from gemini_live_2.trace import TRACE_LEVEL, trace
from gemini_live_2.widgets import build_default_widget_registry

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
logger = logging.getLogger("lumi.gemini_live.web")
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()
_cleanup_tasks: dict[str, asyncio.Task[None]] = {}


def _trace_timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def configure_logging() -> None:
    for name in ("lumi.trace", "lumi.gemini_live", "lumi.gemini_live.web", "lumi.plan_agent"):
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
domain_registry = DomainRegistry(ROOT / "domains")
domain_gateway = DomainGateway(domain_registry)
widget_registry = build_default_widget_registry()
panel_compiler = PanelCompiler(widget_registry)
plan_agent = PlanAgent(
    settings,
    domain_registry=domain_registry,
    domain_gateway=domain_gateway,
    widget_registry=widget_registry,
)
registry = LiveToolRegistry(domain_registry.available_domain_ids())
orchestrator = LiveSessionOrchestrator(
    domain_registry=domain_registry,
    plan_agent=plan_agent,
    panel_compiler=panel_compiler,
)
live_session = GeminiLiveSession(settings=settings, registry=registry, orchestrator=orchestrator)
persistent_transports = PersistentLiveTransportStore()


async def _session_lock(session_id: str) -> asyncio.Lock:
    async with _locks_guard:
        return _locks.setdefault(session_id, asyncio.Lock())


async def _cancel_scheduled_cleanup(session_id: str) -> None:
    task = _cleanup_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _close_after_grace(session_id: str) -> None:
    try:
        await asyncio.sleep(settings.live_reconnect_grace_seconds)
        await persistent_transports.close(session_id)
        orchestrator.reset_session_state(session_id)
        logger.info("[WEB:PERSISTENT_CLEANUP] session=%s", session_id)
    except asyncio.CancelledError:
        raise
    finally:
        _cleanup_tasks.pop(session_id, None)


def _schedule_cleanup(session_id: str) -> None:
    if session_id and session_id not in _cleanup_tasks:
        _cleanup_tasks[session_id] = asyncio.create_task(_close_after_grace(session_id))


async def home(_: Any) -> FileResponse:
    return FileResponse(WEB / "index.html")


async def app_js(_: Any) -> FileResponse:
    return FileResponse(WEB / "app.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})


async def health(_: Any) -> JSONResponse:
    return JSONResponse({
        "ok": bool(settings.gemini_live_api_key),
        "mode": "panel-ir-cp6",
        "domains": list(domain_registry.available_domain_ids()),
    })


async def domain_asset(request: Any) -> FileResponse | PlainTextResponse:
    """Serve only assets declared by the selected domain's trusted catalog."""

    try:
        resources = domain_registry.load(str(request.path_params["domain_id"]))
        asset = resources.assets.get(str(request.path_params["asset_id"]))
    except (ManifestError, ValueError):
        return PlainTextResponse("Asset not found.", status_code=404)
    return FileResponse(asset.path, media_type=asset.mime_type, headers={"Cache-Control": "public, max-age=3600"})


async def client_debug(request: Any) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {"message": "invalid client diagnostic"}
    if not isinstance(payload, dict):
        payload = {"message": str(payload)}
    logger.info("[WEB:CLIENT_DIAGNOSTIC] phase=%s message=%s source=%s line=%s", str(payload.get("phase") or "unknown")[:80], str(payload.get("message") or "")[:500], str(payload.get("source") or "")[:300], payload.get("line") or "")
    return JSONResponse({"ok": True})


async def live_socket(websocket: WebSocket) -> None:
    """Keep the browser socket and one Gemini Live connection persistent."""
    await websocket.accept()
    conversation: Any | None = None
    text_task: asyncio.Task[Any] | None = None
    stream_task: asyncio.Task[Any] | None = None
    session_id = ""
    microphone_enabled = False
    try:
        start = await websocket.receive_json()
        if not isinstance(start, dict) or start.get("type") != "live:connect":
            raise ValueError("Sự kiện đầu tiên phải là live:connect.")
        session_id = str(start.get("session_id") or uuid.uuid4())[:200]
        lock = await _session_lock(session_id)
        async with lock:
            await _cancel_scheduled_cleanup(session_id)
            send_lock = asyncio.Lock()

            async def event(payload: dict[str, Any]) -> None:
                async with send_lock:
                    await websocket.send_json(payload)

            async def audio(pcm: bytes, _: int, marker: dict[str, Any] | None = None, turn_id: str = "") -> None:
                async with send_lock:
                    await websocket.send_json({"type": "audio_chunk", "turn_id": turn_id})
                    if marker:
                        await websocket.send_json({"type": "audio_marker", **marker, "turn_id": turn_id})
                    await websocket.send_bytes(pcm)

            async def connect(reason: str | None = None) -> None:
                nonlocal conversation
                if reason:
                    await event({"type": "live:reconnecting", "reason": reason})
                    await persistent_transports.close(session_id)
                    orchestrator.reset_session_state(session_id)
                conversation = await live_session.open_persistent_conversation(
                    session_id=session_id,
                    transport=persistent_transports.get(session_id),
                    on_event=event,
                    on_audio=audio,
                )
                if reason:
                    await event({"type": "live:reconnected", "session_id": session_id})

            await connect()
            orchestrator.reset_session_state(session_id)
            await event({"type": "live:session_ready", "session_id": session_id})
            await event({"type": "live:state", "state": orchestrator.session_state(session_id)})
            logger.info("[WEB:PERSISTENT_CONNECTED] session=%s reused=%s", session_id, persistent_transports.get(session_id).connected)

            async def consume_stream() -> None:
                nonlocal microphone_enabled
                while microphone_enabled:
                    try:
                        await conversation.consume_audio_stream()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.exception("[WEB:PERSISTENT_AUDIO_STREAM_ERROR] session=%s", session_id)
                        await event({"type": "live:error", "message": str(exc)})
                        return

            chunks = total_bytes = 0
            while True:
                packet = await websocket.receive()
                if packet["type"] == "websocket.disconnect":
                    break
                if packet.get("bytes") is not None:
                    if microphone_enabled:
                        chunk = packet["bytes"]
                        chunks += 1
                        total_bytes += len(chunk)
                        await conversation.send_audio(chunk)
                        if chunks == 1:
                            trace("PCM_SENT first_chunk bytes=%s", len(chunk))
                        elif chunks % 25 == 0:
                            trace("PCM_SENT chunks=%s total_bytes=%s", chunks, total_bytes)
                    continue
                raw = packet.get("text")
                if not raw:
                    continue
                command = json.loads(raw)
                kind = str(command.get("type") or "")
                if kind == "live:text":
                    query = str(command.get("query") or "").strip()
                    if not query:
                        await event({"type": "live:error", "message": "Câu hỏi văn bản không được để trống."})
                    elif stream_task and not stream_task.done():
                        # The continuous microphone stream owns Gemini's sole
                        # receive loop. Typed input joins that same session as
                        # a barge-in instead of waiting for turn_complete.
                        await conversation.interrupt_with_text(query)
                    elif text_task and not text_task.done():
                        await event({"type": "live:error", "message": "Lumi đang xử lý lượt trước."})
                    else:
                        text_task = asyncio.create_task(conversation.submit_text(query))
                elif kind == "panel:interaction":
                    try:
                        interaction_result = orchestrator.apply_panel_interaction(
                            session_id=session_id,
                            surface_id=str(command.get("surface_id") or ""),
                            revision=command.get("revision"),
                            anchor_id=str(command.get("anchor_id") or ""),
                            action=str(command.get("action") or ""),
                        )
                    except ValueError as exc:
                        trace("PANEL_INTERACTION_REJECTED reason=%s", exc)
                        await event({"type": "panel:interaction_rejected", "message": str(exc)})
                        continue
                    interaction = interaction_result.interaction
                    if interaction_result.panel_update is not None:
                        await event({"type": "panel_update", "panel": interaction_result.panel_update})
                    await event({
                        "type": "live:debug_trace",
                        "timestamp": _trace_timestamp(),
                        "event": "panel_interaction",
                        "content": json.dumps(interaction, ensure_ascii=False, separators=(",", ":")),
                    })
                    if (stream_task and not stream_task.done()) or (text_task and not text_task.done()):
                        await conversation.interrupt_with_panel_interaction(interaction)
                    else:
                        text_task = asyncio.create_task(conversation.submit_panel_interaction(interaction))
                elif kind in {"live:audio_begin", "live:mic_enabled"}:
                    chunks = total_bytes = 0
                    microphone_enabled = True
                    await conversation.begin_audio()
                    if stream_task is None or stream_task.done():
                        stream_task = asyncio.create_task(consume_stream())
                elif kind in {"live:audio_end", "live:mic_disabled"}:
                    microphone_enabled = False
                    trace("MIC_END chunks=%s total_bytes=%s", chunks, total_bytes)
                    await conversation.end_audio_stream()
                elif kind == "live:close":
                    break
                else:
                    await event({"type": "live:error", "message": f"Sự kiện không hỗ trợ: {kind}"})
    except WebSocketDisconnect:
        logger.info("[WEB:DISCONNECT] session=%s", session_id or "unknown")
    except Exception as exc:
        logger.exception("[WEB:ERROR] session=%s", session_id or "unknown")
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "live:error", "message": str(exc)})
    finally:
        for task in (text_task, stream_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        _schedule_cleanup(session_id)


app = Starlette(routes=[
    Route("/", home),
    Route("/assets/app.js", app_js),
    Route("/assets/domains/{domain_id}/{asset_id}", domain_asset),
    Route("/api/health", health),
    Route("/api/client-debug", client_debug, methods=["POST"]),
    WebSocketRoute("/ws/live", live_socket),
])
app.mount("/assets", StaticFiles(directory=WEB), name="assets")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8002)
