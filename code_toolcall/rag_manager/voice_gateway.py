"""Protocol helpers for the browser-to-voice gateway WebSocket.

This module deliberately contains no agent/tool logic.  The future Gemini Live
bridge will consume this small, validated protocol and forward only final text
transcripts to the existing chat endpoint.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from rag_manager.config import Settings


class VoiceProtocolError(ValueError):
    """Raised when a browser sends an invalid voice gateway event."""


@dataclass
class VoiceSocketState:
    session_id: str | None = None
    cancelled_turns: int = 0
    transcriber: Any = None
    speaker: Any = None


def read_event(raw: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise VoiceProtocolError("Sự kiện voice phải là JSON object.")
    event_type = raw.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        raise VoiceProtocolError("Sự kiện voice thiếu trường type.")
    return event_type.strip(), raw


def start_event(raw: dict[str, Any], settings: Settings, state: VoiceSocketState) -> dict[str, Any]:
    session_id = raw.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise VoiceProtocolError("Sự kiện voice:start thiếu session_id.")
    if len(session_id) > 200:
        raise VoiceProtocolError("session_id voice quá dài.")
    if not settings.gemini_live_api_key:
        raise VoiceProtocolError("Thiếu GEMINI_LIVE_API_KEY trong code_toolcall/.env.")
    if not settings.gemini_live_transcribe_model:
        raise VoiceProtocolError("Thiếu GEMINI_LIVE_TRANSCRIBE_MODEL trong code_toolcall/.env.")
    if not settings.gemini_live_speech_model:
        raise VoiceProtocolError("Thiếu GEMINI_LIVE_SPEECH_MODEL trong code_toolcall/.env.")

    state.session_id = session_id.strip()
    return {
        "type": "voice_ready",
        "session_id": state.session_id,
        "transcribe_model": settings.gemini_live_transcribe_model,
        "speech_model": settings.gemini_live_speech_model,
        "phase": "gateway_ready",
        "voice": settings.gemini_live_voice or None,
    }


def cancel_event(state: VoiceSocketState) -> dict[str, Any]:
    state.cancelled_turns += 1
    return {
        "type": "voice_cancelled",
        "session_id": state.session_id,
        "cancelled_turns": state.cancelled_turns,
    }


class GeminiLiveTranscriber:
    """Forwards PCM audio to Gemini Live and exposes its text-only transcript."""

    def __init__(self, settings: Settings, on_transcript: Any) -> None:
        self._settings = settings
        self._on_transcript = on_transcript
        self._connection: Any = None
        self._session: Any = None
        self._receive_task: asyncio.Task[None] | None = None
        self._latest_input_transcript = ""
        self._audio_finished = False
        self._final_transcript_sent = False

    async def connect(self) -> None:
        client = genai.Client(api_key=self._settings.gemini_live_api_key)
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_hints=types.LanguageHints(language_codes=["vi-VN"]),
                adaptation_phrases=["Lumi", "Hà Nội", "Sơn Tùng M-TP"],
            ),
            system_instruction=(
                "You are the speech-to-text layer of a Vietnamese assistant. "
                "Do not call tools and do not answer the user. Produce only an accurate input transcript."
            ),
        )
        self._connection = client.aio.live.connect(
            model=self._settings.gemini_live_transcribe_model,
            config=config,
        )
        self._session = await self._connection.__aenter__()
        self._receive_task = asyncio.create_task(self._receive(), name="gemini-live-transcript")

    async def send_audio(self, pcm_16khz: bytes) -> None:
        if not self._session:
            raise RuntimeError("Gemini Live transcription session is not connected.")
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm_16khz, mime_type="audio/pcm;rate=16000")
        )

    async def finish_audio(self) -> None:
        if self._session:
            self._audio_finished = True
            await self._session.send_realtime_input(audio_stream_end=True)
            await self._emit_final_transcript_if_available()

    async def _emit_final_transcript_if_available(self) -> None:
        if self._final_transcript_sent or not self._latest_input_transcript:
            return
        self._final_transcript_sent = True
        await self._on_transcript(self._latest_input_transcript, True)

    async def _receive(self) -> None:
        assert self._session is not None
        async for message in self._session.receive():
            server_content = message.server_content
            if not server_content:
                continue
            interim = server_content.interim_input_transcription
            if interim and interim.text:
                await self._on_transcript(interim.text, False)
            transcript = server_content.input_transcription
            if transcript and transcript.text:
                self._latest_input_transcript = transcript.text
                if transcript.finished or self._audio_finished:
                    await self._emit_final_transcript_if_available()
                else:
                    await self._on_transcript(transcript.text, False)
                continue


    async def close(self) -> None:
        if self._receive_task:
            self._receive_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._receive_task
            self._receive_task = None
        if self._connection:
            with suppress(Exception):
                await self._connection.__aexit__(None, None, None)
            self._connection = None
            self._session = None


class GeminiLiveSpeaker:
    """Reads a Gemma-authored answer as PCM audio; it has no tools or agent access."""

    def __init__(self, settings: Settings, on_audio: Any, on_complete: Any) -> None:
        self._settings = settings
        self._on_audio = on_audio
        self._on_complete = on_complete
        self._connection: Any = None
        self._session: Any = None
        self._receive_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        client = genai.Client(api_key=self._settings.gemini_live_api_key)
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self._settings.gemini_live_voice,
                    ),
                ),
                language_code="vi-VN",
            ),
            system_instruction=(
                "You are the speech output layer of a Vietnamese assistant. "
                "Read aloud, naturally and faithfully, only the text supplied by the backend. "
                "Do not add facts, answer questions, call tools, or make decisions."
            ),
        )
        self._connection = client.aio.live.connect(
            model=self._settings.gemini_live_speech_model,
            config=config,
        )
        self._session = await self._connection.__aenter__()
        self._receive_task = asyncio.create_task(self._receive(), name="gemini-live-speaker")

    async def speak(self, text: str) -> None:
        if not self._session:
            raise RuntimeError("Gemini Live speech session is not connected.")
        instruction = "Read the following assistant answer aloud in Vietnamese, faithfully and without additions:\n\n" + text
        await self._session.send_client_content(turns=[{"role": "user", "parts": [{"text": instruction}]}])

    async def _receive(self) -> None:
        assert self._session is not None
        async for message in self._session.receive():
            server_content = message.server_content
            if not server_content:
                continue
            model_turn = server_content.model_turn
            for part in model_turn.parts if model_turn and model_turn.parts else []:
                inline_data = part.inline_data
                if inline_data and inline_data.data:
                    await self._on_audio(inline_data.data, inline_data.mime_type or "audio/pcm;rate=24000")
            if server_content.turn_complete:
                await self._on_complete()

    async def close(self) -> None:
        if self._receive_task:
            self._receive_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._receive_task
            self._receive_task = None
        if self._connection:
            with suppress(Exception):
                await self._connection.__aexit__(None, None, None)
            self._connection = None
            self._session = None
