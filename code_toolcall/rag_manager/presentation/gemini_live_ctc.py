"""One-turn Gemini Live PCM bridge with MMS-CTC scene events.

This module intentionally does not decide presentation content or DOM effects.
It receives compiler-validated scenes, has Gemini Live read their complete
``spoken_text`` once, and forwards the same PCM stream to the browser and the
external CTC worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from google import genai
from google.genai import types

from rag_manager.config import Settings
from rag_manager.presentation.gemini_live_wav import _sample_rate_from_mime
from rag_manager.presentation.schemas import CompiledPresentationStep

try:  # websockets 14+; keep the fallback for the current Conda environment.
    from websockets.asyncio.client import connect as websocket_connect
except ImportError:  # pragma: no cover - version dependent
    from websockets import connect as websocket_connect


logger = logging.getLogger("lumi.presentation.live_ctc")
AudioCallback = Callable[[bytes, int], Awaitable[None]]
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class PresentationBridgeError(RuntimeError):
    """A live presentation or CTC worker error."""


def validate_presentation_scenes(raw_scenes: Any) -> list[CompiledPresentationStep]:
    if not isinstance(raw_scenes, list) or not raw_scenes or len(raw_scenes) > 6:
        raise PresentationBridgeError("Presentation requires between 1 and 6 compiled scenes.")
    try:
        return [CompiledPresentationStep.model_validate(scene) for scene in raw_scenes]
    except Exception as error:
        raise PresentationBridgeError(f"Invalid compiled presentation scenes: {error}") from error


async def stream_gemini_live_ctc(
    settings: Settings,
    *,
    presentation_id: str,
    scenes: list[CompiledPresentationStep],
    on_audio: AudioCallback,
    on_ctc_event: EventCallback,
) -> None:
    """Read all scenes once and emit actual CTC scene timestamps as they arrive."""
    if not settings.gemini_live_api_key:
        raise PresentationBridgeError("GEMINI_LIVE_API_KEY is missing.")
    if not settings.presentation_ctc_worker_url:
        raise PresentationBridgeError("PRESENTATION_CTC_WORKER_URL is missing.")

    script = "\n\n".join(scene.spoken_text for scene in scenes)
    ctc_scenes = [
        {"scene_id": f"scene-{index}", "alignment_text": scene.alignment_text}
        for index, scene in enumerate(scenes)
    ]
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=settings.gemini_live_voice,
                ),
            ),
            language_code="vi-VN",
        ),
        system_instruction=(
            "You are a Vietnamese speech renderer. Read only the supplied script, "
            "faithfully in Vietnamese. Do not add, omit, paraphrase, answer, or comment."
        ),
    )
    ctc_complete = asyncio.Event()
    ctc_error: str | None = None

    async with websocket_connect(settings.presentation_ctc_worker_url, max_size=None) as ctc_socket:
        async def receive_ctc() -> None:
            nonlocal ctc_error
            async for raw_event in ctc_socket:
                if not isinstance(raw_event, str):
                    continue
                event = json.loads(raw_event)
                event_type = event.get("type")
                if event_type == "ctc_error":
                    ctc_error = str(event.get("message") or "Unknown CTC worker error.")
                    ctc_complete.set()
                    return
                await on_ctc_event(event)
                if event_type == "ctc_complete":
                    ctc_complete.set()
                    return

        receiver = asyncio.create_task(receive_ctc(), name="presentation-ctc-receiver")
        try:
            sample_rate: int | None = None
            ctc_started = False
            client = genai.Client(api_key=settings.gemini_live_api_key)
            async with client.aio.live.connect(
                model=settings.gemini_live_speech_model,
                config=config,
            ) as live_session:
                await live_session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part(text=f"Read this script verbatim:\n\n{script}")],
                    ),
                    turn_complete=True,
                )
                logger.info(
                    "[PRESENTATION:LIVE_CTC_SEND] presentation=%s scenes=%s chars=%s",
                    presentation_id, len(scenes), len(script),
                )
                async for message in live_session.receive():
                    server_content = getattr(message, "server_content", None)
                    model_turn = getattr(server_content, "model_turn", None) if server_content else None
                    for part in getattr(model_turn, "parts", None) or []:
                        inline_data = getattr(part, "inline_data", None)
                        pcm = getattr(inline_data, "data", None) if inline_data else None
                        if not pcm:
                            continue
                        rate = _sample_rate_from_mime(getattr(inline_data, "mime_type", None))
                        if sample_rate is None:
                            sample_rate = rate
                            await ctc_socket.send(json.dumps({
                                "type": "ctc_start",
                                "presentation_id": presentation_id,
                                "input_sample_rate_hz": sample_rate,
                                "scenes": ctc_scenes,
                            }))
                            ctc_started = True
                            await on_ctc_event({
                                "type": "presentation_audio_format",
                                "presentation_id": presentation_id,
                                "sample_rate_hz": sample_rate,
                            })
                        elif rate != sample_rate:
                            raise PresentationBridgeError(
                                f"Gemini Live changed PCM sample rate from {sample_rate} to {rate}."
                            )
                        await ctc_socket.send(pcm)
                        await on_audio(pcm, sample_rate)
                    if ctc_error:
                        raise PresentationBridgeError(ctc_error)
                    if server_content and (
                        bool(getattr(server_content, "generation_complete", False))
                        or bool(getattr(server_content, "turn_complete", False))
                    ):
                        break
            if not ctc_started:
                raise PresentationBridgeError("Gemini Live completed without PCM audio.")
            await ctc_socket.send(json.dumps({
                "type": "ctc_finalize", "presentation_id": presentation_id,
            }))
            await asyncio.wait_for(ctc_complete.wait(), timeout=90)
            if ctc_error:
                raise PresentationBridgeError(ctc_error)
        finally:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receiver
