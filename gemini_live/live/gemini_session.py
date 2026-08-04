"""Concrete Gemini Live transport for the independent multi-domain application."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from google import genai
from google.genai import types

from gemini_live.domains import LiveDomainRegistry
from gemini_live.settings import Settings

from .orchestrator import LiveSessionOrchestrator
from .scene_state import ActivePresentationScenes


logger = logging.getLogger("lumi.gemini_live")
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
AudioCallback = Callable[[bytes, int], Awaitable[None]]
_RATE = re.compile(r"rate=(\d+)")

_CORE_INSTRUCTION = """You are Lumi, a Vietnamese voice assistant. Use registered tools for real-world facts; never invent tool data. After a completed data tool response supplies a presentation plan, present one supplied scene at a time. For each scene, call trigger_scene with its exact scene_id before speaking its exact narration. Wait for the backend to provide the next scene. Keep clarification questions concise."""


class GeminiLiveSessionError(RuntimeError):
    pass


def _sample_rate(mime_type: str | None) -> int:
    match = _RATE.search(mime_type or "")
    return int(match.group(1)) if match else 24_000


class GeminiLiveSession:
    """Transport only: domains own tools, facts, templates and planning."""

    def __init__(
        self,
        *,
        settings: Settings,
        registry: LiveDomainRegistry,
        orchestrator: LiveSessionOrchestrator,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._orchestrator = orchestrator

    async def run_text_turn(
        self, *, session_id: str, query: str, on_event: EventCallback, on_audio: AudioCallback
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise GeminiLiveSessionError("Câu hỏi không được để trống.")

        async def send_input(session: Any) -> None:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=query)]), turn_complete=True
            )

        return await self._run(
            session_id=session_id, label=query, send_input=send_input, on_event=on_event, on_audio=on_audio
        )

    async def run_audio_turn(
        self,
        *,
        session_id: str,
        audio_chunks: "asyncio.Queue[bytes | None]",
        on_event: EventCallback,
        on_audio: AudioCallback,
    ) -> dict[str, Any]:
        async def send_input(session: Any) -> None:
            count = size = 0
            await on_event({"type": "live:input_ready", "sample_rate_hz": 16_000})
            while True:
                chunk = await audio_chunks.get()
                if chunk is None:
                    logger.info("[LIVE:MIC_INPUT_END] chunks=%s bytes=%s", count, size)
                    await session.send_realtime_input(audio_stream_end=True)
                    await on_event({"type": "live:gemini_audio_closed", "chunks": count, "bytes": size})
                    return
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
                count += 1
                size += len(chunk)
                if count == 1 or count % 25 == 0:
                    logger.info("[LIVE:MIC_INPUT_SENT] chunks=%s bytes=%s", count, size)
                    await on_event({"type": "live:gemini_audio_sent", "chunks": count, "bytes": size})

        return await self._run(
            session_id=session_id, label="<voice>", send_input=send_input, on_event=on_event, on_audio=on_audio
        )

    def _instruction(self, session_id: str) -> str:
        memory = self._orchestrator.session_memory(session_id)
        history = [
            f"{item['role']}: {item['content'][:700]}"
            for item in memory.history[-6:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        contexts: dict[str, dict[str, Any]] = {}
        for domain_id, context in memory.domain_contexts.items():
            safe = {
                key: value for key, value in context.items()
                if isinstance(value, (str, int, float, bool)) or value is None
            }
            if safe:
                contexts[domain_id] = safe
        sections = [_CORE_INSTRUCTION, self._registry.prompt_guidance()]
        if history:
            sections.append("Recent conversation (context only):\n" + "\n".join(history))
        if contexts:
            sections.append("Confirmed server context:\n" + json.dumps(contexts, ensure_ascii=False))
        return "\n\n".join(section for section in sections if section)

    async def _run(
        self,
        *,
        session_id: str,
        label: str,
        send_input: Callable[[Any], Awaitable[None]],
        on_event: EventCallback,
        on_audio: AudioCallback,
    ) -> dict[str, Any]:
        if not self._settings.gemini_live_api_key:
            raise GeminiLiveSessionError("GEMINI_LIVE_API_KEY chưa được cấu hình.")
        declarations = [
            types.FunctionDeclaration(
                name=item["name"], description=item["description"], parameters_json_schema=item["parameters"]
            )
            for item in self._registry.tool_declarations()
        ]
        declarations.append(types.FunctionDeclaration(
            name="trigger_scene",
            description="Trigger the exact next backend presentation scene immediately before speaking it.",
            parameters_json_schema={
                "type": "object", "properties": {"scene_id": {"type": "string"}}, "required": ["scene_id"]
            },
        ))
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            tools=[types.Tool(function_declarations=declarations)],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_hints=types.LanguageHints(language_codes=["vi-VN"])
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._settings.gemini_live_voice)
                ),
                language_code="vi-VN",
            ),
            system_instruction=self._instruction(session_id),
        )
        active_scenes: ActivePresentationScenes | None = None
        scene_ids: list[str] = []
        next_scene = 0
        active_scene: str | None = None
        active_query = "" if label == "<voice>" else label
        transcript: list[str] = []
        audio_chunks = 0
        audio_bytes = 0
        tool_calls = animation_calls = 0
        sample_rate: int | None = None

        client = genai.Client(api_key=self._settings.gemini_live_api_key)
        async with client.aio.live.connect(model=self._settings.gemini_live_model, config=config) as session:
            logger.info("[LIVE:START] session=%s input=%s model=%s", session_id, label, self._settings.gemini_live_model)
            input_task = asyncio.create_task(send_input(session), name="gemini-live-input")
            continue_receive = False
            try:
                while True:
                    async for message in session.receive():
                        server = getattr(message, "server_content", None)
                        input_transcript = getattr(server, "input_transcription", None) if server else None
                        if input_transcript and getattr(input_transcript, "text", None):
                            active_query = str(input_transcript.text)
                            await on_event({"type": "input_transcript", "text": active_query, "final": bool(getattr(input_transcript, "finished", False))})

                        calls = getattr(getattr(message, "tool_call", None), "function_calls", None) or []
                        if calls:
                            responses: list[types.FunctionResponse] = []
                            for call in calls:
                                name, call_id = str(call.name), str(call.id)
                                args = dict(call.args) if isinstance(call.args, dict) else {}
                                tool_calls += 1
                                logger.info("[LIVE:TOOL_CALL] name=%s args=%s", name, json.dumps(args, ensure_ascii=False))
                                if name == "trigger_scene":
                                    requested = str(args.get("scene_id") or "").strip()
                                    expected = scene_ids[next_scene] if active_scene is None and next_scene < len(scene_ids) else ""
                                    scene = active_scenes.resolve(requested) if active_scenes else None
                                    if scene is None or requested != expected:
                                        response = {"status": "rejected", "reason": "scene_not_next"}
                                    else:
                                        active_scene = requested
                                        animation_calls += 1
                                        response = {"status": "completed", "scene_id": requested}
                                        await on_event({"type": "scene", "scene": scene})
                                        logger.info("[LIVE:SCENE_ACCEPTED] scene=%s target=%s", requested, scene["target_id"])
                                else:
                                    result = await self._orchestrator.execute_tool_call_result(
                                        session_id=session_id,
                                        query=active_query or "Yêu cầu hiện tại.",
                                        tool_name=name,
                                        arguments=args,
                                    )
                                    response = result.tool_response
                                    presentation = result.presentation
                                    panel = getattr(presentation, "panel", None)
                                    scenes = getattr(presentation, "scenes", None)
                                    if isinstance(panel, dict):
                                        await on_event({"type": "panel", "panel": panel})
                                    if isinstance(scenes, ActivePresentationScenes):
                                        active_scenes, scene_ids, next_scene, active_scene = scenes, list(scenes.scenes), 0, None
                                        logger.info("[LIVE:PLAN_ACTIVE] scenes=%s ids=%s", len(scene_ids), scene_ids)
                                responses.append(types.FunctionResponse(id=call_id, name=name, response={"result": response}))
                                await on_event({"type": "tool_result", "name": name, "response": response})
                            await session.send_tool_response(function_responses=responses)

                        model_turn = getattr(server, "model_turn", None) if server else None
                        for part in getattr(model_turn, "parts", None) or []:
                            inline = getattr(part, "inline_data", None)
                            pcm = getattr(inline, "data", None) if inline else None
                            if not pcm:
                                continue
                            if active_scenes is not None and active_scene is None:
                                logger.warning("[LIVE:AUDIO_DROPPED_NO_SCENE] bytes=%s", len(pcm))
                                continue
                            rate = _sample_rate(getattr(inline, "mime_type", None))
                            if sample_rate is None:
                                sample_rate = rate
                                await on_event({"type": "audio_format", "sample_rate_hz": rate})
                            audio_chunks += 1
                            audio_bytes += len(pcm)
                            await on_audio(pcm, rate)
                        output = getattr(server, "output_transcription", None) if server else None
                        if output and getattr(output, "text", None):
                            text = str(output.text)
                            transcript.append(text)
                            await on_event({"type": "text", "text": text})
                        if server and bool(getattr(server, "turn_complete", False)):
                            if active_scene:
                                logger.info("[LIVE:SCENE_COMPLETE] scene=%s", active_scene)
                                next_scene += 1
                                active_scene = None
                            if active_scenes is not None and next_scene < len(scene_ids):
                                scene = active_scenes.resolve(scene_ids[next_scene])
                                if scene is None:
                                    raise GeminiLiveSessionError("Compiled presentation scene is unavailable.")
                                await session.send_client_content(
                                    turns=types.Content(role="user", parts=[types.Part(text=json.dumps({
                                        "BACKEND_PRESENTATION_SCENE": {
                                            "scene_id": scene["scene_id"],
                                            "narration": scene.get("spoken_text") or scene["narration"],
                                        }
                                    }, ensure_ascii=False))]), turn_complete=True
                                )
                                continue_receive = True
                            else:
                                continue_receive = False
                                break
                    if continue_receive:
                        continue_receive = False
                        continue
                    break
            finally:
                if not input_task.done():
                    input_task.cancel()
                try:
                    await input_task
                except asyncio.CancelledError:
                    pass
        final_text = "".join(transcript).strip()
        self._orchestrator.remember_turn(session_id=session_id, user_text=active_query or label, assistant_text=final_text)
        summary = {"tool_calls": tool_calls, "animation_calls": animation_calls, "audio_chunks": audio_chunks, "audio_bytes": audio_bytes, "audio_sample_rate_hz": sample_rate, "transcript": final_text}
        logger.info("[LIVE:DONE] session=%s tools=%s scenes=%s audio_chunks=%s", session_id, tool_calls, animation_calls, audio_chunks)
        return summary
