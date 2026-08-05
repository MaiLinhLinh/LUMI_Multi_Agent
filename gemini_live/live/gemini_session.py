"""Concrete Gemini Live transport for the independent multi-domain application."""

from __future__ import annotations

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
from .persistent_transport import PersistentLiveTransport
from .scene_state import ActivePresentationScenes
from .session_protocol import LiveSessionState


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

    def _connection_config(self, session_id: str) -> types.LiveConnectConfig:
        """Build configuration once per Gemini connection, including safe memory."""

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
        return types.LiveConnectConfig(
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

    async def open_persistent_conversation(
        self,
        *,
        session_id: str,
        transport: PersistentLiveTransport,
        on_event: EventCallback,
        on_audio: AudioCallback,
    ) -> "PersistentGeminiLiveConversation":
        """Open or reuse one Gemini Live connection for a persistent browser session."""

        if not self._settings.gemini_live_api_key:
            raise GeminiLiveSessionError("GEMINI_LIVE_API_KEY chưa được cấu hình.")
        client = genai.Client(api_key=self._settings.gemini_live_api_key)
        if not transport.connected:
            await transport.connect(client.aio.live.connect(
                model=self._settings.gemini_live_model,
                config=self._connection_config(session_id),
            ))
        logger.info("[LIVE:PERSISTENT_CONNECTED] session=%s model=%s", session_id, self._settings.gemini_live_model)
        return PersistentGeminiLiveConversation(
            session_id=session_id,
            transport=transport,
            orchestrator=self._orchestrator,
            on_event=on_event,
            on_audio=on_audio,
        )

class PersistentGeminiLiveConversation:
    """Consume one persistent transport while preserving existing tool/scene rules.

    Tool dispatch and presentation sequencing remain shared concerns rather
    than Weather/Education responsibilities.
    """

    def __init__(
        self,
        *,
        session_id: str,
        transport: PersistentLiveTransport,
        orchestrator: LiveSessionOrchestrator,
        on_event: EventCallback,
        on_audio: AudioCallback,
    ) -> None:
        self._session_id = session_id
        self._transport = transport
        self._orchestrator = orchestrator
        self._on_event = on_event
        self._on_audio = on_audio
        self._active_scenes: ActivePresentationScenes | None = None
        self._scene_ids: list[str] = []
        self._next_scene = 0
        self._active_scene: str | None = None
        self._active_query = ""
        self._transcript: list[str] = []
        self._tool_calls = 0
        self._animation_calls = 0
        self._audio_chunks = 0
        self._audio_bytes = 0
        self._sample_rate: int | None = None

    @property
    def state(self) -> LiveSessionState:
        return self._orchestrator.session_state(self._session_id)

    async def submit_text(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise GeminiLiveSessionError("Câu hỏi không được để trống.")
        await self._set_state(LiveSessionState.LISTENING)
        self._begin_turn(query)
        await self._transport.send_text(query)
        await self._set_state(LiveSessionState.WAITING_FOR_TOOL)
        return await self._consume_until_settled()

    async def begin_audio(self) -> None:
        """Enter listening when the persistent browser microphone begins."""

        await self._set_state(LiveSessionState.LISTENING)
        self._begin_turn("<voice>")
        await self._on_event({"type": "live:input_ready", "sample_rate_hz": 16_000})

    async def send_audio(self, pcm: bytes) -> None:
        if self.state != LiveSessionState.LISTENING:
            raise GeminiLiveSessionError("Microphone audio is accepted only while listening.")
        await self._transport.send_audio(pcm)

    async def end_audio(self) -> dict[str, Any]:
        if self.state != LiveSessionState.LISTENING:
            raise GeminiLiveSessionError("No active microphone turn to end.")
        await self._transport.end_audio()
        await self._set_state(LiveSessionState.WAITING_FOR_TOOL)
        return await self._consume_until_settled()

    async def close(self) -> None:
        await self._transport.close()
        self._orchestrator.reset_session_state(self._session_id)
        await self._on_event({"type": "live:state", "state": LiveSessionState.IDLE})

    def _begin_turn(self, user_text: str) -> None:
        self._active_query = user_text
        self._transcript = []
        self._tool_calls = self._animation_calls = self._audio_chunks = self._audio_bytes = 0
        self._sample_rate = None
        self._active_scenes = None
        self._scene_ids = []
        self._next_scene = 0
        self._active_scene = None

    async def _set_state(self, target: LiveSessionState) -> None:
        current = self.state
        if current != target:
            self._orchestrator.transition_session(session_id=self._session_id, target=target)
        await self._on_event({"type": "live:state", "state": target})

    async def _consume_until_settled(self) -> dict[str, Any]:
        """Handle messages until this turn safely returns to listening."""

        async for message in self._transport.receive():
            server = getattr(message, "server_content", None)
            await self._handle_input_transcript(server)
            await self._handle_tool_calls(message)
            await self._handle_model_turn(server)
            if server and bool(getattr(server, "turn_complete", False)):
                if await self._handle_turn_complete():
                    return self._finish_turn()
        raise GeminiLiveSessionError("Gemini Live connection closed before the turn completed.")

    async def _handle_input_transcript(self, server: Any) -> None:
        input_transcript = getattr(server, "input_transcription", None) if server else None
        if input_transcript and getattr(input_transcript, "text", None):
            self._active_query = str(input_transcript.text)
            await self._on_event({
                "type": "input_transcript",
                "text": self._active_query,
                "final": bool(getattr(input_transcript, "finished", False)),
            })

    async def _handle_tool_calls(self, message: Any) -> None:
        calls = getattr(getattr(message, "tool_call", None), "function_calls", None) or []
        if not calls:
            return
        if any(str(call.name) != "trigger_scene" for call in calls):
            await self._set_state(LiveSessionState.WAITING_FOR_TOOL)
        responses: list[types.FunctionResponse] = []
        for call in calls:
            name, call_id = str(call.name), str(call.id)
            args = dict(call.args) if isinstance(call.args, dict) else {}
            self._tool_calls += 1
            logger.info("[LIVE:PERSISTENT_TOOL_CALL] session=%s name=%s args=%s", self._session_id, name, json.dumps(args, ensure_ascii=False))
            if name == "trigger_scene":
                response = await self._trigger_scene(str(args.get("scene_id") or "").strip())
            else:
                result = await self._orchestrator.execute_tool_call_result(
                    session_id=self._session_id,
                    query=self._active_query or "Yêu cầu hiện tại.",
                    tool_name=name,
                    arguments=args,
                )
                response = result.tool_response
                presentation = result.presentation
                panel = getattr(presentation, "panel", None)
                scenes = getattr(presentation, "scenes", None)
                if isinstance(panel, dict):
                    await self._on_event({"type": "panel", "panel": panel})
                if isinstance(scenes, ActivePresentationScenes):
                    self._active_scenes = scenes
                    self._scene_ids = list(scenes.scenes)
                    self._next_scene = 0
                    self._active_scene = None
                    logger.info("[LIVE:PERSISTENT_PLAN_ACTIVE] session=%s scenes=%s", self._session_id, len(self._scene_ids))
            responses.append(types.FunctionResponse(id=call_id, name=name, response={"result": response}))
            await self._on_event({"type": "tool_result", "name": name, "response": response})
        await self._transport.send_tool_responses(responses)

    async def _trigger_scene(self, requested: str) -> dict[str, Any]:
        expected = self._scene_ids[self._next_scene] if self._active_scene is None and self._next_scene < len(self._scene_ids) else ""
        scene = self._active_scenes.resolve(requested) if self._active_scenes else None
        if scene is None or requested != expected:
            return {"status": "rejected", "reason": "scene_not_next"}
        self._active_scene = requested
        self._animation_calls += 1
        await self._set_state(LiveSessionState.SPEAKING)
        await self._on_event({"type": "scene", "scene": scene})
        logger.info("[LIVE:PERSISTENT_SCENE_ACCEPTED] session=%s scene=%s", self._session_id, requested)
        return {"status": "completed", "scene_id": requested}

    async def _handle_model_turn(self, server: Any) -> None:
        model_turn = getattr(server, "model_turn", None) if server else None
        for part in getattr(model_turn, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            pcm = getattr(inline, "data", None) if inline else None
            if not pcm:
                continue
            if self._active_scenes is not None and self._active_scene is None:
                logger.warning("[LIVE:PERSISTENT_AUDIO_DROPPED_NO_SCENE] session=%s bytes=%s", self._session_id, len(pcm))
                continue
            if self.state == LiveSessionState.WAITING_FOR_TOOL:
                await self._set_state(LiveSessionState.SPEAKING)
            rate = _sample_rate(getattr(inline, "mime_type", None))
            if self._sample_rate is None:
                self._sample_rate = rate
                await self._on_event({"type": "audio_format", "sample_rate_hz": rate})
            self._audio_chunks += 1
            self._audio_bytes += len(pcm)
            await self._on_audio(pcm, rate)

        output = getattr(server, "output_transcription", None) if server else None
        if output and getattr(output, "text", None):
            # Keep transcript visible for diagnosis even when pre-scene PCM is
            # intentionally muted.  This exposes unwanted Gemini generation
            # and its token cost without letting it disrupt narrated scenes.
            text = str(output.text)
            self._transcript.append(text)
            await self._on_event({
                "type": "text",
                "text": text,
                "presentation_approved": self._active_scenes is None or self._active_scene is not None,
            })

    async def _handle_turn_complete(self) -> bool:
        if self._active_scene:
            logger.info("[LIVE:PERSISTENT_SCENE_COMPLETE] session=%s scene=%s", self._session_id, self._active_scene)
            self._next_scene += 1
            self._active_scene = None
        if self._active_scenes is not None and self._next_scene < len(self._scene_ids):
            scene = self._active_scenes.resolve(self._scene_ids[self._next_scene])
            if scene is None:
                raise GeminiLiveSessionError("Compiled presentation scene is unavailable.")
            await self._transport.send_text(json.dumps({
                "BACKEND_PRESENTATION_SCENE": {
                    "scene_id": scene["scene_id"],
                    "narration": scene.get("spoken_text") or scene["narration"],
                }
            }, ensure_ascii=False))
            return False
        self._active_scenes = None
        self._scene_ids = []
        self._next_scene = 0
        self._active_scene = None
        await self._set_state(LiveSessionState.LISTENING)
        return True

    def _finish_turn(self) -> dict[str, Any]:
        final_text = "".join(self._transcript).strip()
        self._orchestrator.remember_turn(
            session_id=self._session_id,
            user_text=self._active_query,
            assistant_text=final_text,
        )
        summary = {
            "tool_calls": self._tool_calls,
            "animation_calls": self._animation_calls,
            "audio_chunks": self._audio_chunks,
            "audio_bytes": self._audio_bytes,
            "audio_sample_rate_hz": self._sample_rate,
            "transcript": final_text,
        }
        logger.info("[LIVE:PERSISTENT_TURN_DONE] session=%s tools=%s scenes=%s", self._session_id, self._tool_calls, self._animation_calls)
        return summary
