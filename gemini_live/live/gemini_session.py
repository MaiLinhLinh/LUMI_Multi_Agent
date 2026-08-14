"""Concrete Gemini Live transport for the independent multi-domain application."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types

from gemini_live.domains import LiveDomainRegistry
from gemini_live.settings import Settings
from gemini_live.trace import begin_turn, trace, turn_id, warning

from .orchestrator import LiveSessionOrchestrator
from .persistent_transport import PersistentLiveTransport
from .session_protocol import LiveSessionState
from .visual_presentation import PRESENT_VISUAL_TOOL


logger = logging.getLogger("lumi.gemini_live")
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
# ``marker`` is emitted immediately before its PCM packet.  The browser uses
# that packet's AudioContext start time as the visual cue's clock.
AudioCallback = Callable[[bytes, int, dict[str, Any] | None, str], Awaitable[None]]
_RATE = re.compile(r"rate=(\d+)")


def _ui_trace_timestamp() -> str:
    """Return the local server time in the format shown by the trace log."""

    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

_CORE_INSTRUCTION = """
Bạn là Lumi, trợ lý giọng nói tiếng Việt.
Chỉ dùng dữ liệu thật do tool đã đăng ký trả về; không tự tạo dữ kiện, số liệu, kết quả, vùng giao diện hoặc hiệu ứng.
Khi một tool trả về presentation_instruction, facts, VISUAL STAGE MAP hoặc visual_effects, đó là chỉ dẫn trình bày có hiệu lực cho lượt hiện tại và được ưu tiên hơn hướng dẫn chung. Thực hiện đúng presentation_instruction đó.
Khi presentation_instruction yêu cầu minh hoạ một fact, BẮT BUỘC gọi present_visual với đúng anchor_id và effect_id hợp lệ trước khi nói về fact đó.
Không đọc, nhắc hoặc diễn giải tên tool, ID, JSON, template hay dữ liệu kỹ thuật cho người dùng. Giữ câu hỏi làm rõ ngắn gọn.
"""


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
            for item in [*self._registry.tool_declarations(), PRESENT_VISUAL_TOOL]
        ]
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            tools=[types.Tool(function_declarations=declarations)],
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    prefix_padding_ms=200,
                    silence_duration_ms=200,
                ),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            ),
            #thinking_config=types.ThinkingConfig(thinking_level="low"),
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
            settings=self._settings,
            transport=transport,
            orchestrator=self._orchestrator,
            on_event=on_event,
            on_audio=on_audio,
        )

class PersistentGeminiLiveConversation:
    """Consume one persistent transport using verified fact-driven presentation."""

    def __init__(
        self,
        *,
        session_id: str,
        settings: Settings,
        transport: PersistentLiveTransport,
        orchestrator: LiveSessionOrchestrator,
        on_event: EventCallback,
        on_audio: AudioCallback,
    ) -> None:
        self._session_id = session_id
        self._settings = settings
        self._transport = transport
        self._orchestrator = orchestrator
        self._on_event = on_event
        self._on_audio = on_audio
        self._active_query = ""
        self._transcript: list[str] = []
        self._tool_calls = 0
        self._animation_calls = 0
        self._audio_chunks = 0
        self._audio_bytes = 0
        self._sample_rate: int | None = None
        self._audio_started = False
        self._ui_pending_text_trace: list[str] = []
        self._ui_pending_text_trace_timestamp: str | None = None
        self._ui_pending_text_trace_turn_id: str | None = None
        self._pending_visual_marker: dict[str, Any] | None = None
        self._interrupted_turn_pending = False
        self._output_turn_id: str | None = None

    @property
    def turn_id(self) -> str:
        return turn_id()

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
        if self.state in {LiveSessionState.IDLE, LiveSessionState.ERROR}:
            raise GeminiLiveSessionError("Microphone audio is accepted only while the Live session is active.")
        await self._transport.send_audio(pcm)

    async def end_audio_stream(self) -> None:
        """Close the physical microphone stream, never an individual VAD turn."""

        await self._transport.end_audio()
        trace("GEMINI_AUDIO_STREAM_END_SENT")

    async def consume_audio_stream(self) -> None:
        """Continuously consume Gemini events while the browser microphone is enabled.

        Gemini's automatic VAD, rather than ``audio_stream_end``, decides each
        user turn.  This coroutine intentionally remains alive across model
        turn completions until the persistent transport closes or its caller
        cancels it.
        """

        async for message in self._transport.receive():
            server = getattr(message, "server_content", None)
            await self._handle_input_transcript(server)
            if await self._handle_interruption(server):
                continue
            self._resume_after_interruption_if_output(message, server)
            await self._handle_tool_calls(message)
            await self._handle_model_turn(server)
            if server and bool(getattr(server, "turn_complete", False)):
                if self._interrupted_turn_pending:
                    trace("GEMINI_INTERRUPTED_TURN_COMPLETE_IGNORED")
                    self._interrupted_turn_pending = False
                    self._reset_voice_turn_tracking()
                    continue
                if await self._handle_turn_complete():
                    summary = self._finish_turn()
                    await self._on_event({
                        "type": "live:turn_complete",
                        "session_id": self._session_id,
                        "turn_id": self._output_turn_id,
                        "summary": summary,
                    })
                    self._reset_voice_turn_tracking()
        raise GeminiLiveSessionError("Gemini Live connection closed while microphone streaming.")

    async def close(self) -> None:
        await self._transport.close()
        self._orchestrator.reset_session_state(self._session_id)
        await self._on_event({"type": "live:state", "state": LiveSessionState.IDLE})

    def _begin_turn(self, user_text: str) -> None:
        begin_turn()
        self._active_query = user_text
        self._transcript = []
        self._tool_calls = self._animation_calls = self._audio_chunks = self._audio_bytes = 0
        self._sample_rate = None
        self._audio_started = False
        self._ui_pending_text_trace = []
        self._ui_pending_text_trace_timestamp = None
        self._ui_pending_text_trace_turn_id = None
        self._pending_visual_marker = None
        self._interrupted_turn_pending = False
        self._output_turn_id = None
        if user_text == "<voice>":
            trace("MIC_BEGIN")
        else:
            trace("TEXT_BEGIN chars=%s", len(user_text))

    def _reset_voice_turn_tracking(self) -> None:
        """Prepare for Gemini VAD's next voice turn without ending the PCM stream."""

        self._active_query = "<voice>"
        self._transcript = []
        self._tool_calls = self._animation_calls = self._audio_chunks = self._audio_bytes = 0
        self._sample_rate = None
        self._audio_started = False
        self._ui_pending_text_trace = []
        self._ui_pending_text_trace_timestamp = None
        self._ui_pending_text_trace_turn_id = None
        self._pending_visual_marker = None
        self._interrupted_turn_pending = False
        self._output_turn_id = None

    async def _handle_interruption(self, server: Any) -> bool:
        """Discard the cancelled model output and notify the browser promptly."""

        if not server or not bool(getattr(server, "interrupted", False)):
            return False
        await self._flush_ui_text_trace()
        if self._pending_visual_marker is not None:
            warning(
                "VISUAL_MARKER_DISCARDED_ON_INTERRUPT anchor=%s",
                self._pending_visual_marker["cue"].get("anchor_id"),
            )
            self._pending_visual_marker = None
        self._interrupted_turn_pending = True
        trace("GEMINI_INTERRUPTED")
        await self._set_state(LiveSessionState.LISTENING)
        interrupted_turn_id = self._output_turn_id
        await self._on_event({"type": "live:interrupted", "turn_id": interrupted_turn_id})
        # The next model output is a new VAD turn, not a continuation of the
        # generation that Gemini cancelled.
        self._output_turn_id = None
        return True

    def _ensure_output_turn_id(self) -> str:
        """Return the local identifier for this model-output turn."""

        if self._output_turn_id is None:
            self._output_turn_id = uuid.uuid4().hex[:8]
        return self._output_turn_id

    def _resume_after_interruption_if_output(self, message: Any, server: Any) -> None:
        """A fresh model output belongs to the user's new VAD-managed turn."""

        if not self._interrupted_turn_pending:
            return
        has_tool_call = bool(getattr(getattr(message, "tool_call", None), "function_calls", None))
        has_model_turn = bool(getattr(getattr(server, "model_turn", None), "parts", None)) if server else False
        if has_tool_call or has_model_turn:
            self._interrupted_turn_pending = False

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
            if await self._handle_interruption(server):
                continue
            self._resume_after_interruption_if_output(message, server)
            await self._handle_tool_calls(message)
            await self._handle_model_turn(server)
            if server and bool(getattr(server, "turn_complete", False)):
                if self._interrupted_turn_pending:
                    trace("GEMINI_INTERRUPTED_TURN_COMPLETE_IGNORED")
                    self._interrupted_turn_pending = False
                    self._reset_voice_turn_tracking()
                    continue
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
        responses: list[types.FunctionResponse] = []
        response_tool_names: list[str] = []
        fact_count = 0
        effect_count = 0
        for call in calls:
            await self._flush_ui_text_trace()
            name, call_id = str(call.name), str(call.id)
            args = dict(call.args) if isinstance(call.args, dict) else {}
            output_turn_id = self._ensure_output_turn_id()
            self._tool_calls += 1
            trace("GEMINI_TOOL_CALL_RECEIVED name=%s args=%s", name, json.dumps(args, ensure_ascii=False))
            await self._on_event({
                "type": "live:debug_trace",
                "timestamp": _ui_trace_timestamp(),
                "event": "toolcall",
                "turn_id": output_turn_id,
                "content": f"{name}({json.dumps(args, ensure_ascii=False, separators=(',', ': '))})",
            })
            if name == "present_visual":
                # A visual marker is allowed while Gemini is already speaking.
                # Do not move to WAITING_FOR_TOOL: that transition used to reject
                # speaking -> waiting_for_tool and discarded the animation call.
                response = await self._handle_present_visual(args)
                responses.append(types.FunctionResponse(id=call_id, name=name, response={"result": response}))
                response_tool_names.append(name)
                await self._on_event({"type": "tool_result", "name": name, "response": response})
                continue
            await self._set_state(LiveSessionState.WAITING_FOR_TOOL)
            result = await self._orchestrator.execute_tool_call_result(
                session_id=self._session_id,
                query=self._active_query or "Yêu cầu hiện tại.",
                tool_name=name,
                arguments=args,
            )
            response = result.response
            if isinstance(response, dict):
                fact_count = len(response.get("facts", []))
                effect_count = len(response.get("visual_effects", []))
            presentation = result.presentation
            panel = getattr(presentation, "panel", None)
            if isinstance(panel, dict):
                await self._on_event({"type": "panel", "panel": panel})
            responses.append(types.FunctionResponse(id=call_id, name=name, response={"result": response}))
            response_tool_names.append(name)
            await self._on_event({"type": "tool_result", "name": name, "response": response})
        await self._transport.send_tool_responses(responses)
        await self._on_event({
            "type": "live:debug_trace",
            "timestamp": _ui_trace_timestamp(),
            "event": "tool_response",
            "turn_id": self._ensure_output_turn_id(),
            "content": ", ".join(response_tool_names),
        })
        trace(
            "TOOL_RESPONSE_SENT_TO_GEMINI facts=%s effects=%s",
            fact_count,
            effect_count,
        )

    async def _handle_present_visual(self, args: dict[str, Any]) -> dict[str, Any]:
        anchor_id = args.get("anchor_id")
        effect_id = args.get("effect_id")
        if not isinstance(anchor_id, str) or not isinstance(effect_id, str):
            return {"status": "rejected", "message": "anchor_id and effect_id must be strings"}
        try:
            cue = self._orchestrator.present_visual(
                session_id=self._session_id,
                anchor_id=anchor_id,
                effect_id=effect_id,
            )
        except ValueError as exc:
            warning("PRESENT_VISUAL_REJECTED reason=%s", exc)
            return {"status": "rejected", "message": str(exc)}
        self._animation_calls += 1
        trace(
            "PRESENT_VISUAL_ACCEPTED anchor=%s effect=%s target=%s",
            anchor_id,
            effect_id,
            cue["target_id"],
        )
        if self._pending_visual_marker is not None:
            warning(
                "VISUAL_MARKER_REPLACED previous_anchor=%s next_anchor=%s",
                self._pending_visual_marker["cue"].get("anchor_id"),
                anchor_id,
            )
        self._pending_visual_marker = {
            "cue": cue,
            "animation_delay_ms": self._settings.presentation_animation_delay_ms,
            "turn_id": self._ensure_output_turn_id(),
        }
        trace("VISUAL_MARKER_PENDING anchor=%s effect=%s", anchor_id, effect_id)
        return {"status": "completed", "anchor_id": anchor_id, "effect_id": effect_id}

    async def _handle_model_turn(self, server: Any) -> None:
        model_turn = getattr(server, "model_turn", None) if server else None
        for part in getattr(model_turn, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            pcm = getattr(inline, "data", None) if inline else None
            if not pcm:
                continue
            if self.state == LiveSessionState.WAITING_FOR_TOOL:
                await self._set_state(LiveSessionState.SPEAKING)
            output_turn_id = self._ensure_output_turn_id()
            rate = _sample_rate(getattr(inline, "mime_type", None))
            if self._sample_rate is None:
                self._sample_rate = rate
                await self._on_event({"type": "audio_format", "sample_rate_hz": rate})
            self._audio_chunks += 1
            self._audio_bytes += len(pcm)
            if not self._audio_started:
                self._audio_started = True
                trace("GEMINI_AUDIO_FIRST_PCM")
            marker = self._pending_visual_marker
            self._pending_visual_marker = None
            if marker is not None:
                cue = marker["cue"]
                trace(
                    "VISUAL_MARKER_ATTACHED_TO_PCM anchor=%s effect=%s chunk=%s",
                    cue.get("anchor_id"),
                    cue.get("effect"),
                    self._audio_chunks,
                )
            await self._on_audio(pcm, rate, marker, output_turn_id)

        output = getattr(server, "output_transcription", None) if server else None
        if output and getattr(output, "text", None):
            text = str(output.text)
            self._transcript.append(text)
            if self._ui_pending_text_trace_timestamp is None:
                self._ui_pending_text_trace_timestamp = _ui_trace_timestamp()
                self._ui_pending_text_trace_turn_id = self._ensure_output_turn_id()
            self._ui_pending_text_trace.append(text)
            await self._on_event({
                "type": "text",
                "text": text,
                "turn_id": self._ensure_output_turn_id(),
                "presentation_approved": True,
            })

    async def _flush_ui_text_trace(self) -> None:
        """Emit one readable text segment between successive tool calls."""

        text = "".join(self._ui_pending_text_trace).strip()
        timestamp = self._ui_pending_text_trace_timestamp
        output_turn_id = self._ui_pending_text_trace_turn_id
        self._ui_pending_text_trace = []
        self._ui_pending_text_trace_timestamp = None
        self._ui_pending_text_trace_turn_id = None
        if not text or timestamp is None:
            return
        await self._on_event({
            "type": "live:debug_trace",
            "timestamp": timestamp,
            "event": "text",
            "turn_id": output_turn_id,
            "content": text,
        })

    async def _handle_turn_complete(self) -> bool:
        await self._flush_ui_text_trace()
        if self._pending_visual_marker is not None:
            warning(
                "VISUAL_MARKER_UNATTACHED anchor=%s",
                self._pending_visual_marker["cue"].get("anchor_id"),
            )
            self._pending_visual_marker = None
        if self._tool_calls == 0:
            warning("GEMINI_TURN_COMPLETE_WITHOUT_TOOL")
        trace("GEMINI_TURN_COMPLETE")
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
            "turn_id": self._output_turn_id,
            "tool_calls": self._tool_calls,
            "animation_calls": self._animation_calls,
            "audio_chunks": self._audio_chunks,
            "audio_bytes": self._audio_bytes,
            "audio_sample_rate_hz": self._sample_rate,
            "transcript": final_text,
        }
        trace("PRESENTATION_COMPLETE visual_calls=%s", self._animation_calls)
        return summary
