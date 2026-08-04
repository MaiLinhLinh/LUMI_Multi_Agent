"""Standalone UI for the Gemini Live tool-call baseline.

Run this file separately from ``web_app.py``.  It intentionally has no CTC
imports, routes, or feature flags.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from rag_manager.config import load_settings
from rag_manager.live_toolcall_experiment.session import GeminiLiveToolCallExperiment


BASE = Path(__file__).resolve().parent
WEB = BASE / "web_live_toolcall_experiment"
logger = logging.getLogger("lumi.live_toolcall_experiment.web")


def configure_experiment_logging() -> None:
    """Make the isolated Live experiment state-machine visible in its terminal."""
    experiment_logger = logging.getLogger("lumi.live_toolcall_experiment")
    experiment_logger.setLevel(logging.INFO)
    experiment_logger.propagate = False
    if experiment_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    experiment_logger.addHandler(handler)


configure_experiment_logging()


@dataclass
class ExperimentSession:
    """Server-side memory that survives one short Live connection."""

    history: list[dict[str, str]] = field(default_factory=list)
    domain_contexts: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


sessions: dict[str, ExperimentSession] = {}
sessions_lock = asyncio.Lock()
_HISTORY_LIMIT = 6


async def get_session(session_id: str) -> ExperimentSession:
    async with sessions_lock:
        return sessions.setdefault(session_id, ExperimentSession())


def recent_history(session: ExperimentSession) -> list[dict[str, str]]:
    return list(session.history[-_HISTORY_LIMIT:])


def append_history(session: ExperimentSession, role: str, content: Any) -> None:
    text = str(content or "").strip()
    if not text or role not in {"user", "assistant"}:
        return
    session.history.append({"role": role, "content": text[:4000]})
    del session.history[:-_HISTORY_LIMIT]


async def home(_: Any) -> FileResponse:
    return FileResponse(WEB / "index.html")


async def app_js(_: Any) -> FileResponse:
    return FileResponse(WEB / "app.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})


async def health(_: Any) -> JSONResponse:
    settings = load_settings()
    return JSONResponse({"ok": bool(settings.gemini_live_api_key), "mode": "live-toolcall-experiment"})


async def live_toolcall_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        packet = await websocket.receive_json()
        if not isinstance(packet, dict) or packet.get("type") != "live:start":
            raise ValueError("First event must be live:start.")
        input_mode = str(packet.get("input_mode") or "text").strip().lower()
        if input_mode not in {"text", "audio"}:
            raise ValueError("input_mode must be text or audio.")
        query = packet.get("query")
        if input_mode == "text" and (not isinstance(query, str) or not query.strip() or len(query) > 8000):
            raise ValueError("Text live:start requires a non-empty query up to 8000 characters.")
        session_id = packet.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 200:
            session_id = str(uuid.uuid4())

        session_state = await get_session(session_id)
        async with session_state.lock:
            settings = load_settings()
            bridge = GeminiLiveToolCallExperiment(settings)
            outbound_lock = asyncio.Lock()
            history = recent_history(session_state)
            domain_contexts = {
                domain_id: dict(context)
                for domain_id, context in session_state.domain_contexts.items()
                if isinstance(domain_id, str) and isinstance(context, dict)
            }
            logger.info(
                "[LIVE_EXPERIMENT:SESSION_MEMORY_LOADED] session=%s history=%s domains=%s",
                session_id,
                len(history),
                sorted(domain_contexts),
            )

            async def publish_event(event: dict[str, Any]) -> None:
                async with outbound_lock:
                    await websocket.send_json(event)

            async def publish_audio(pcm: bytes, _: int) -> None:
                async with outbound_lock:
                    await websocket.send_bytes(pcm)

            await websocket.send_json({"type": "live:ready", "session_id": session_id, "input_mode": input_mode})
            if input_mode == "audio":
                audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

                async def receive_audio() -> None:
                    chunk_count = 0
                    byte_count = 0
                    try:
                        while True:
                            incoming = await websocket.receive()
                            if incoming["type"] == "websocket.disconnect":
                                logger.info("[LIVE_EXPERIMENT:MIC_DISCONNECT] session=%s chunks=%s bytes=%s", session_id, chunk_count, byte_count)
                                break
                            if incoming.get("bytes") is not None:
                                chunk = incoming["bytes"]
                                chunk_count += 1
                                byte_count += len(chunk)
                                if chunk_count == 1 or chunk_count % 25 == 0:
                                    logger.info("[LIVE_EXPERIMENT:MIC_RECEIVED] session=%s chunks=%s bytes=%s", session_id, chunk_count, byte_count)
                                    await publish_event({
                                        "type": "live:server_audio_received",
                                        "chunks": chunk_count,
                                        "bytes": byte_count,
                                    })
                                await audio_queue.put(chunk)
                                continue
                            text = incoming.get("text")
                            if not text:
                                continue
                            event = json.loads(text)
                            if isinstance(event, dict) and event.get("type") == "live:audio_end":
                                logger.info("[LIVE_EXPERIMENT:MIC_END] session=%s chunks=%s bytes=%s", session_id, chunk_count, byte_count)
                                await publish_event({
                                    "type": "live:server_audio_closed",
                                    "chunks": chunk_count,
                                    "bytes": byte_count,
                                })
                                break
                    finally:
                        await audio_queue.put(None)

                receiver = asyncio.create_task(receive_audio(), name="live-toolcall-browser-audio")
                try:
                    outcome = await bridge.run_audio_turn(
                        audio_chunks=audio_queue,
                        history=history,
                        domain_contexts=domain_contexts,
                        on_event=publish_event,
                        on_audio=publish_audio,
                    )
                finally:
                    if not receiver.done():
                        receiver.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await receiver
            else:
                outcome = await bridge.run_turn(
                    query=query,
                    history=history,
                    domain_contexts=domain_contexts,
                    on_event=publish_event,
                    on_audio=publish_audio,
                )
            updated_contexts = outcome.get("domain_contexts")
            if isinstance(updated_contexts, dict):
                session_state.domain_contexts = {
                    domain_id: dict(context)
                    for domain_id, context in updated_contexts.items()
                    if isinstance(domain_id, str) and isinstance(context, dict)
                }
            append_history(session_state, "user", outcome.get("input_text") or query)
            append_history(session_state, "assistant", outcome.get("transcript"))
            logger.info(
                "[LIVE_EXPERIMENT:SESSION_MEMORY_SAVED] session=%s history=%s domains=%s",
                session_id,
                len(session_state.history),
                sorted(session_state.domain_contexts),
            )
            await websocket.send_json({"type": "live:complete", "session_id": session_id, "summary": outcome})
    except WebSocketDisconnect:
        logger.info("[LIVE_EXPERIMENT:DISCONNECT]")
    except Exception as exc:
        logger.exception("[LIVE_EXPERIMENT:ERROR]")
        try:
            await websocket.send_json({"type": "live:error", "message": str(exc)})
        except Exception:
            pass


app = Starlette(
    debug=False,
    routes=[
        Route("/", home),
        Route("/assets/app.js", app_js),
        Route("/api/health", health),
        WebSocketRoute("/ws/live-toolcall-experiment", live_toolcall_socket),
        Mount("/assets", app=StaticFiles(directory=WEB), name="assets"),
    ],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
