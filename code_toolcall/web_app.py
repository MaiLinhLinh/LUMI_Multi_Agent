"""Standalone Starlette web application for the native tool-calling graph."""
from __future__ import annotations
import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect
from rag_manager.config import load_settings
from rag_manager.graph import build_workflow
from rag_manager.presentation.gemini_live_ctc import PresentationBridgeError, stream_gemini_live_ctc, validate_presentation_scenes
from rag_manager.voice_gateway import GeminiLiveSpeaker, GeminiLiveTranscriber, VoiceProtocolError, VoiceSocketState, cancel_event, read_event, speech_start_event, start_event

BASE=Path(__file__).resolve().parent; WEB=BASE/"web"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
# Individual Google/Ollama HTTP request lines are noise beside the structured
# LLM and tool timings below. Errors still remain visible.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("lumi.web")


def _format_usage(usage: list[dict[str, Any]]) -> str:
    if not usage:
        return "  (không có lượt LLM)"
    rows = []
    for item in usage:
        label = item.get("stage") or f"turn {item.get('turn', '?')}"
        mode = "retry" if item.get("retry_after_empty_stream") else ("stream" if item.get("streaming") else "normal")
        rows.append(
            "  - %-9s %-6s %7.1f ms | in=%s out=%s total=%s thought=%s"
            % (
                label,
                mode,
                float(item.get("inference_ms") or 0),
                item.get("input_tokens"),
                item.get("output_tokens"),
                item.get("total_tokens"),
                item.get("thought_tokens"),
            )
        )
    return "\n".join(rows)


def _format_tools(trace: list[dict[str, Any]]) -> str:
    if not trace:
        return "  (không gọi tool)"
    return "\n".join(
        "  - %-22s status=%-18s %7.1f ms | args=%s"
        % (
            item.get("tool", "?"),
            item.get("status", "?"),
            float(item.get("latency_ms") or 0),
            json.dumps(item.get("arguments", {}), ensure_ascii=False),
        )
        for item in trace
    )
@dataclass
class Session: messages:list[dict[str,Any]]=field(default_factory=list); panel:dict[str,Any]=field(default_factory=dict); weather_context:dict[str,Any]=field(default_factory=dict); music_session:dict[str,Any]=field(default_factory=dict); lock:threading.RLock=field(default_factory=threading.RLock)
sessions:dict[str,Session]={}; sessions_lock=threading.Lock(); workflow=None; workflow_lock=threading.Lock()
def get_session(key:str)->Session:
    with sessions_lock: return sessions.setdefault(key,Session())
def get_workflow():
    global workflow
    with workflow_lock:
        if workflow is None:
            settings=load_settings()
            if not settings.gemini_api_key: raise RuntimeError("Thiếu GEMINI_API_KEY trong code_toolcall/.env")
            workflow=build_workflow(settings)
        return workflow
def payload(key:str,s:Session)->dict[str,Any]:
    return {"ok":True,"session_id":key,"messages":s.messages,"has_active_panel":bool(s.panel),"active_panel":s.panel,"active_panel_revision":len(s.messages),"has_visualization":s.panel.get("ui_type")=="weather","visualization_html":s.panel.get("html","")}
def execute(
    key: str,
    query: str,
    response_stream_callback: Any = None,
    presentation_stream_callback: Any = None,
) -> dict[str, Any]:
    s=get_session(key)
    with s.lock:
        started=time.perf_counter()
        logger.info("\n========== REQUEST START ==========" "\nsession : %s\nquery   : %s\n===================================", key, query)
        s.messages.append({"role":"user","content":query})
        try:
            result=get_workflow().invoke({"query":query,"history":s.messages[:-1],"weather_context":s.weather_context,"music_session":s.music_session,"session_id":key,"tool_trace":[],"response_stream_callback":response_stream_callback,"presentation_stream_callback":presentation_stream_callback})
        except Exception:
            logger.exception("[REQUEST][ERROR] session=%s workflow failed", key)
            raise
        answer=result.get("final_answer") or "Tôi chưa thể xử lý yêu cầu này."
        visual=result.get("visualization_payload") or {}
        if visual.get("ui_type") == "weather" and visual.get("html"):
            s.panel=visual
        elif visual.get("ui_type") == "youtube_player":
            s.panel=visual
        weather_context=result.get("weather_context")
        if isinstance(weather_context, dict) and weather_context.get("last_location_id"):
            s.weather_context=weather_context
        music_session=result.get("music_session")
        if isinstance(music_session, dict):
            s.music_session=music_session
        s.messages.append({"role":"assistant","content":answer,"domain":result.get("selected_agent", "")})
        timings = result.get("timings", {})
        logger.info(
            "\n========== REQUEST DONE ==========="
            "\nagent   : %s"
            "\nstatus  : %s"
            "\ntotal   : %.1f ms"
            "\nvisible : first=%s ms | end=%s ms"
            "\n\nLLM usage:"
            "\n%s"
            "\n\nTools:"
            "\n%s"
            "\n===================================",
            result.get("selected_agent"),
            result.get("agent_result", {}).get("status"),
            (time.perf_counter() - started) * 1000,
            timings.get("time_to_first_visible_ms"),
            timings.get("time_to_end_visible_ms"),
            _format_usage(result.get("llm_usage", [])),
            _format_tools(result.get("tool_trace", [])),
        )
        return payload(key,s)
async def home(_:Request): return FileResponse(WEB/"index.html")
async def app_js(_:Request): return FileResponse(WEB/"app.js", media_type="application/javascript", headers={"Cache-Control":"no-store"})
async def health(_:Request):
    try: get_workflow(); return JSONResponse({"ok":True})
    except Exception as exc: return JSONResponse({"ok":False,"message":str(exc)},status_code=503)
async def chat(request:Request):
    try:
        raw=await request.json(); query=str(raw.get("query","")).strip(); key=str(raw.get("session_id") or uuid.uuid4())
        if not query: raise ValueError("Vui lòng nhập câu hỏi.")
        return JSONResponse(await run_in_threadpool(execute,key,query))
    except ValueError as exc: return JSONResponse({"ok":False,"message":str(exc)},status_code=400)
    except Exception as exc: return JSONResponse({"ok":False,"message":f"Lỗi workflow: {exc}"},status_code=500)
async def chat_stream(request:Request):
    try:
        raw=await request.json(); query=str(raw.get("query","")).strip(); key=str(raw.get("session_id") or uuid.uuid4())
        if not query: raise ValueError("Vui lòng nhập câu hỏi.")
    except ValueError as exc: return JSONResponse({"ok":False,"message":str(exc)},status_code=400)
    async def events():
        loop=asyncio.get_running_loop()
        event_queue:asyncio.Queue[dict[str,Any]]=asyncio.Queue()
        stream_started=time.perf_counter()
        first_delta_sent=False
        first_delta_lock=threading.Lock()
        def publish(domain:str,text:str)->None:
            nonlocal first_delta_sent
            if text:
                with first_delta_lock:
                    if not first_delta_sent:
                        first_delta_sent=True
                        loop.call_soon_threadsafe(event_queue.put_nowait,{"type":"timing","marker":"first_text_delta_sent","elapsed_ms":round((time.perf_counter()-stream_started)*1000,2)})
                loop.call_soon_threadsafe(event_queue.put_nowait,{"type":"text_delta","domain":domain,"delta":text})
        def publish_presentation(event_type: str, value: dict[str, Any]) -> None:
            nonlocal first_delta_sent
            if event_type == "panel_ready":
                event = {"type": "panel_ready", "panel": value}
            elif event_type == "presentation_contract":
                event = {"type": "presentation_contract", "contract": value}
                with first_delta_lock:
                    if not first_delta_sent:
                        first_delta_sent = True
                        loop.call_soon_threadsafe(event_queue.put_nowait, {
                            "type": "timing",
                            "marker": "first_text_delta_sent",
                            "elapsed_ms": round((time.perf_counter() - stream_started) * 1000, 2),
                        })
            else:
                logger.warning("[STREAM] ignored unknown presentation event: %s", event_type)
                return
            loop.call_soon_threadsafe(event_queue.put_nowait, event)
        async def worker()->None:
            try:
                result=await run_in_threadpool(execute,key,query,publish,publish_presentation)
                await event_queue.put({"type":"final","payload":result})
            except Exception as exc:
                logger.exception("[STREAM][ERROR] session=%s",key)
                await event_queue.put({"type":"error","message":str(exc)})
        task=asyncio.create_task(worker())
        yield (json.dumps({"type":"timing","marker":"server_request_received","elapsed_ms":0})+"\n").encode()
        try:
            while True:
                event=await event_queue.get()
                yield (json.dumps(event,ensure_ascii=False)+"\n").encode()
                if event["type"] in {"final","error"}: break
        finally:
            if not task.done(): task.cancel()
    return StreamingResponse(events(),media_type="application/x-ndjson; charset=utf-8")

async def presentation_debug(request: Request):
    """Receive small client-side presentation diagnostics for local tracing."""
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "message": "Invalid diagnostic payload."}, status_code=400)
    event = raw.get("event")
    session_id = raw.get("session_id")
    detail = raw.get("detail", {})
    if not isinstance(event, str) or not event.strip() or len(event) > 80:
        return JSONResponse({"ok": False, "message": "Invalid diagnostic event."}, status_code=400)
    if not isinstance(session_id, str) or len(session_id) > 200:
        session_id = "unknown"
    if not isinstance(detail, dict):
        detail = {}
    safe_detail = {
        str(key)[:80]: value
        for key, value in detail.items()
        if isinstance(key, str) and isinstance(value, (str, int, float, bool, type(None)))
    }
    logger.info("[PRESENTATION:CLIENT] session=%s event=%s detail=%s", session_id, event.strip(), safe_detail)
    return JSONResponse({"ok": True})


async def get_session_route(request:Request): return JSONResponse(payload(request.path_params["session_id"],get_session(request.path_params["session_id"])))
async def clear(request:Request):
    raw=await request.json(); key=str(raw.get("session_id","") or uuid.uuid4())
    with sessions_lock: sessions[key]=Session()
    return JSONResponse(payload(key,sessions[key]))

async def voice_socket(websocket: WebSocket) -> None:
    """Voice -> Gemini Live transcript -> browser, without agent/tool access."""
    await websocket.accept()
    state = VoiceSocketState()
    settings = load_settings()

    async def publish_transcript(text: str, is_final: bool) -> None:
        clean_text = text.strip()
        if clean_text:
            await websocket.send_json({
                "type": "voice_transcript",
                "session_id": state.session_id,
                "text": clean_text,
                "final": is_final,
            })

    async def close_transcriber() -> None:
        if state.transcriber is not None:
            await state.transcriber.close()
            state.transcriber = None

    async def publish_speech_audio(audio: bytes, _: str) -> None:
        state.speech_audio_chunks += 1
        state.speech_audio_bytes += len(audio)
        await websocket.send_bytes(audio)

    async def publish_speech_complete() -> None:
        logger.info(
            "[TTS:END] turn=%s chunks=%s bytes=%s",
            state.speech_turn_id,
            state.speech_audio_chunks,
            state.speech_audio_bytes,
        )
        await websocket.send_json({"type": "voice_speech_end", "session_id": state.session_id, "turn_id": state.speech_turn_id})

    async def close_speaker() -> None:
        if state.speaker is not None:
            await state.speaker.close()
            state.speaker = None

    try:
        while True:
            try:
                packet = await websocket.receive()
                if packet["type"] == "websocket.disconnect":
                    break
                if packet.get("bytes") is not None:
                    if state.transcriber is None:
                        raise VoiceProtocolError("Voice chưa sẵn sàng nhận audio.")
                    await state.transcriber.send_audio(packet["bytes"])
                    continue
                if packet.get("text") is None:
                    continue
                event_type, raw = read_event(json.loads(packet["text"]))
                if event_type == "voice:start":
                    ready = start_event(raw, settings, state)
                    get_session(ready["session_id"])
                    await close_transcriber()
                    state.transcriber = GeminiLiveTranscriber(settings, publish_transcript)
                    await state.transcriber.connect()
                    ready["phase"] = "transcription_ready"
                    await websocket.send_json(ready)
                elif event_type == "voice:speech_start":
                    ready = speech_start_event(raw, settings, state)
                    get_session(ready["session_id"])
                    if state.speaker is None:
                        state.speaker = GeminiLiveSpeaker(settings, publish_speech_audio, publish_speech_complete)
                        await state.speaker.connect()
                    await websocket.send_json(ready)
                elif event_type == "voice:speak":
                    text = raw.get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise VoiceProtocolError("Sự kiện voice:speak thiếu text.")
                    if state.session_id is None:
                        raise VoiceProtocolError("Voice chưa có session_id.")
                    if raw.get("turn_id") != state.speech_turn_id:
                        raise VoiceProtocolError("Voice TTS thuộc lượt đã hết hiệu lực.")
                    if state.speaker is None:
                        raise VoiceProtocolError("Voice TTS chưa sẵn sàng.")
                    state.speech_audio_chunks = 0
                    state.speech_audio_bytes = 0
                    logger.info("[TTS:SPEAK] turn=%s chars=%s text=%r", state.speech_turn_id, len(text.strip()), text.strip())
                    await state.speaker.speak(text.strip())
                elif event_type == "voice:audio_end":
                    if state.transcriber is None:
                        raise VoiceProtocolError("Voice chưa sẵn sàng nhận audio.")
                    await state.transcriber.finish_audio()
                elif event_type == "voice:cancel":
                    await close_transcriber()
                    await close_speaker()
                    await websocket.send_json(cancel_event(state))
                elif event_type == "voice:ping":
                    await websocket.send_json({"type": "voice:pong", "session_id": state.session_id})
                else:
                    raise VoiceProtocolError(f"Sự kiện voice không hỗ trợ: {event_type}.")
            except VoiceProtocolError as exc:
                await websocket.send_json({"type": "voice_error", "message": str(exc)})
            except Exception:
                logger.exception("[VOICE][ERROR] session=%s", state.session_id)
                await websocket.send_json({"type": "voice_error", "message": "Không thể xử lý voice. Hãy thử lại."})
    except WebSocketDisconnect:
        logger.info("[VOICE][DISCONNECT] session=%s", state.session_id)
    finally:
        await close_transcriber()
        await close_speaker()


async def presentation_socket(websocket: WebSocket) -> None:
    """Experimental one-turn Gemini Live PCM + CTC presentation channel."""
    await websocket.accept()
    settings = load_settings()
    try:
        packet = await websocket.receive_json()
        if not isinstance(packet, dict) or packet.get("type") != "presentation:start":
            raise PresentationBridgeError("First presentation event must be presentation:start.")
        presentation_id = packet.get("presentation_id")
        if not isinstance(presentation_id, str) or not presentation_id.strip() or len(presentation_id) > 200:
            raise PresentationBridgeError("presentation:start requires a valid presentation_id.")
        scenes = validate_presentation_scenes(packet.get("scenes"))

        async def publish_audio(pcm: bytes, _: int) -> None:
            await websocket.send_bytes(pcm)

        async def publish_ctc(event: dict[str, Any]) -> None:
            await websocket.send_json(event)

        await websocket.send_json({
            "type": "presentation_ready",
            "presentation_id": presentation_id,
            "prebuffer_ms": settings.presentation_ctc_prebuffer_ms,
        })
        await stream_gemini_live_ctc(
            settings,
            presentation_id=presentation_id,
            scenes=scenes,
            on_audio=publish_audio,
            on_ctc_event=publish_ctc,
        )
        await websocket.send_json({"type": "presentation_complete", "presentation_id": presentation_id})
    except (PresentationBridgeError, ValueError) as exc:
        logger.exception("[PRESENTATION:LIVE_CTC_ERROR] %s", exc)
        await websocket.send_json({"type": "presentation_error", "message": str(exc)})
    except WebSocketDisconnect:
        logger.info("[PRESENTATION:LIVE_CTC_DISCONNECT]")
    except Exception:
        logger.exception("[PRESENTATION:LIVE_CTC_ERROR]")
        await websocket.send_json({"type": "presentation_error", "message": "Live CTC presentation failed."})

routes=[Route("/",home),Route("/assets/app.js",app_js),Route("/api/health",health),Route("/api/chat",chat,methods=["POST"]),Route("/api/chat/stream",chat_stream,methods=["POST"]),Route("/api/presentation/debug",presentation_debug,methods=["POST"]),Route("/api/session/clear",clear,methods=["POST"]),Route("/api/session/{session_id}",get_session_route),WebSocketRoute("/ws/voice",voice_socket),WebSocketRoute("/ws/presentation",presentation_socket),Mount("/assets",app=StaticFiles(directory=WEB),name="assets")]
app=Starlette(debug=False,routes=routes)
if __name__=="__main__":
 import uvicorn; uvicorn.run(app,host="127.0.0.1",port=8000)
