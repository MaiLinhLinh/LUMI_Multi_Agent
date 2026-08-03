"""One-shot Gemini Live WAV capture for offline CTC experiments.

This is deliberately outside the browser voice gateway and the active
presentation pipeline.  It turns one already-finalized Vietnamese script into
one WAV file so alignment can be evaluated before any runtime provider is
replaced.

Run from ``code_toolcall``::

    conda run -n LumiMultiAgent python -m rag_manager.presentation.gemini_live_wav \
        --text-file samples/script.txt --output tmp/gemini_live_trial.wav
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types

from rag_manager.config import Settings, load_settings


logger = logging.getLogger("lumi.presentation.gemini_live_wav")
_PCM_RATE_PATTERN = re.compile(r"(?:^|;)\s*rate\s*=\s*(\d+)", re.IGNORECASE)


@dataclass
class LiveWavCapture:
    """Validated 16-bit PCM chunks returned by one Gemini Live turn."""

    sample_rate: int | None = None
    chunks: list[bytes] = field(default_factory=list)

    def append(self, pcm_bytes: bytes, mime_type: str | None) -> None:
        if not pcm_bytes:
            return
        rate = _sample_rate_from_mime(mime_type)
        if self.sample_rate is None:
            self.sample_rate = rate
        elif rate != self.sample_rate:
            raise RuntimeError(
                f"Gemini Live changed audio sample rate from {self.sample_rate} to {rate}."
            )
        if len(pcm_bytes) % 2:
            raise RuntimeError("Gemini Live returned an odd number of PCM16 bytes.")
        self.chunks.append(pcm_bytes)

    @property
    def pcm_bytes(self) -> bytes:
        return b"".join(self.chunks)

    @property
    def duration_ms(self) -> int:
        if not self.sample_rate:
            return 0
        return round((len(self.pcm_bytes) // 2) * 1000 / self.sample_rate)

    def wav_bytes(self) -> bytes:
        if not self.sample_rate or not self.chunks:
            raise RuntimeError("Gemini Live returned no PCM audio.")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)  # Gemini Live output is signed PCM16 LE.
            output.setframerate(self.sample_rate)
            output.writeframes(self.pcm_bytes)
        return buffer.getvalue()


def _sample_rate_from_mime(mime_type: str | None) -> int:
    if not mime_type:
        return 24_000
    match = _PCM_RATE_PATTERN.search(mime_type)
    if not match:
        raise RuntimeError(f"Unsupported Gemini Live audio MIME type: {mime_type!r}")
    rate = int(match.group(1))
    if rate <= 0:
        raise RuntimeError(f"Invalid Gemini Live audio sample rate: {rate}")
    return rate


def _event_details(message: object) -> dict[str, object]:
    """Keep enough Live diagnostics to debug a failed trial without audio logs."""
    server_content = getattr(message, "server_content", None)
    model_turn = getattr(server_content, "model_turn", None) if server_content else None
    parts = getattr(model_turn, "parts", None) or []
    return {
        "has_server_content": bool(server_content),
        "audio_parts": sum(
            1
            for part in parts
            if getattr(getattr(part, "inline_data", None), "data", None)
        ),
        "text_parts": sum(1 for part in parts if getattr(part, "text", None)),
        "generation_complete": bool(getattr(server_content, "generation_complete", False)),
        "turn_complete": bool(getattr(server_content, "turn_complete", False)),
        "interrupted": bool(getattr(server_content, "interrupted", False)),
        "error": str(getattr(message, "error", None) or "") or None,
        "go_away": str(getattr(message, "go_away", None) or "") or None,
    }


async def capture_gemini_live_wav(
    settings: Settings,
    script: str,
    *,
    timeout_seconds: float = 90.0,
) -> LiveWavCapture:
    """Read *script* once through Gemini Live and collect its raw PCM output."""
    if not settings.gemini_live_api_key:
        raise RuntimeError("GEMINI_LIVE_API_KEY is missing in code_toolcall/.env.")
    if not script.strip():
        raise ValueError("The script to synthesize must not be blank.")

    client = genai.Client(api_key=settings.gemini_live_api_key)
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=settings.gemini_live_voice,
                ),
            ),
        ),
        system_instruction=(
            "You are a Vietnamese speech renderer. Read only the supplied script, "
            "faithfully in Vietnamese. Do not add, omit, paraphrase, answer, or "
            "comment on any text."
        ),
    )
    capture = LiveWavCapture()
    started = time.perf_counter()
    async with client.aio.live.connect(
        model=settings.gemini_live_speech_model,
        config=config,
    ) as session:
        # A finalized script is ordered, turn-based content.  Sending it as
        # realtime text relies on VAD and may receive no response at all.
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text=f"Read this script verbatim:\n\n{script.strip()}")],
            ),
            turn_complete=True,
        )
        logger.info(
            "[CTC_TRIAL:LIVE_SEND] model=%s chars=%s",
            settings.gemini_live_speech_model,
            len(script),
        )
        async with asyncio.timeout(timeout_seconds):
            async for message in session.receive():
                details = _event_details(message)
                logger.info("[CTC_TRIAL:LIVE_EVENT] details=%s", details)
                if details["error"]:
                    raise RuntimeError(f"Gemini Live returned an error: {details['error']}")
                if details["go_away"]:
                    raise RuntimeError(f"Gemini Live closed the session: {details['go_away']}")
                server_content = getattr(message, "server_content", None)
                model_turn = getattr(server_content, "model_turn", None) if server_content else None
                chunks_before = len(capture.chunks)
                for part in getattr(model_turn, "parts", None) or []:
                    inline_data = getattr(part, "inline_data", None)
                    data = getattr(inline_data, "data", None) if inline_data else None
                    if data:
                        capture.append(data, getattr(inline_data, "mime_type", None))
                if server_content:
                    generation_complete = bool(getattr(server_content, "generation_complete", False))
                    turn_complete = bool(getattr(server_content, "turn_complete", False))
                    if generation_complete or turn_complete:
                        logger.info(
                            "[CTC_TRIAL:LIVE_TERMINAL] details=%s new_audio_chunks=%s total_chunks=%s",
                            details,
                            len(capture.chunks) - chunks_before,
                            len(capture.chunks),
                        )
                    # A full-script capture does not need a conversational
                    # boundary. generation_complete is sufficient and avoids
                    # waiting indefinitely on preview models that omit the
                    # later turn_complete event.
                    if generation_complete or turn_complete:
                        break

    logger.info(
        "[CTC_TRIAL:LIVE_DONE] chunks=%s pcm_bytes=%s rate=%s duration_ms=%s elapsed_ms=%.1f",
        len(capture.chunks),
        len(capture.pcm_bytes),
        capture.sample_rate,
        capture.duration_ms,
        (time.perf_counter() - started) * 1000,
    )
    if not capture.chunks:
        raise RuntimeError("Gemini Live completed without audio PCM.")
    return capture


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture one Gemini Live TTS response as WAV.")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--text", help="Final script to read verbatim.")
    input_group.add_argument("--text-file", type=Path, help="UTF-8 file containing the final script.")
    parser.add_argument("--output", type=Path, required=True, help="Destination WAV path.")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    args = _parse_args()
    script = args.text if args.text is not None else args.text_file.read_text(encoding="utf-8")
    capture = asyncio.run(
        capture_gemini_live_wav(load_settings(), script, timeout_seconds=args.timeout_seconds)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(capture.wav_bytes())
    print(
        f"Wrote {args.output} | {capture.sample_rate} Hz | "
        f"{capture.duration_ms} ms | {len(capture.chunks)} PCM chunks"
    )


if __name__ == "__main__":
    main()
