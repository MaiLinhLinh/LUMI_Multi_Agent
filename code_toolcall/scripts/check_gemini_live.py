"""Small live checks for the configured Gemini speech-to-text and text-to-speech models."""
from __future__ import annotations

import asyncio
from array import array
import re
import sys

from google import genai
from google.genai import types

from rag_manager.config import load_settings
from rag_manager.voice_gateway import GeminiLiveSpeaker, GeminiLiveTranscriber


def pcm_24khz_to_16khz(audio: bytes) -> bytes:
    """Downsample mono signed-16-bit PCM with nearest-neighbour samples for the smoke test."""
    samples = array("h")
    samples.frombytes(audio)
    if sys.byteorder != "little":
        samples.byteswap()
    output = array(
        "h",
        (samples[(index * 3) // 2] for index in range((len(samples) * 2) // 3)),
    )
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


async def transcribe_vietnamese_smoke_test(settings) -> None:
    expected = "Xin chào"
    audio_chunks: list[bytes] = []
    audio_rate = 24000
    speech_complete = asyncio.Event()

    async def on_audio(chunk: bytes, mime_type: str) -> None:
        nonlocal audio_rate
        match = re.search(r"rate=(\\d+)", mime_type)
        if match:
            audio_rate = int(match.group(1))
        audio_chunks.append(chunk)

    async def on_speech_complete() -> None:
        speech_complete.set()

    speaker = GeminiLiveSpeaker(settings, on_audio, on_speech_complete)
    try:
        await speaker.connect()
        await speaker.speak(expected)
        await asyncio.wait_for(speech_complete.wait(), timeout=30)
    finally:
        await speaker.close()
    if audio_rate != 24000 or not audio_chunks:
        raise RuntimeError("Speech smoke test did not return 24 kHz PCM audio.")

    transcripts: list[str] = []
    final_transcript = asyncio.Event()

    async def on_transcript(text: str, finished: bool) -> None:
        transcripts.append(text)
        if finished:
            final_transcript.set()

    transcriber = GeminiLiveTranscriber(settings, on_transcript)
    try:
        await transcriber.connect()
        pcm_16khz = pcm_24khz_to_16khz(b"".join(audio_chunks))
        # Send 100 ms frames at realtime pace, matching the browser protocol.
        for offset in range(0, len(pcm_16khz), 3200):
            await transcriber.send_audio(pcm_16khz[offset : offset + 3200])
            await asyncio.sleep(0.1)
        await transcriber.finish_audio()
        try:
            await asyncio.wait_for(final_transcript.wait(), timeout=30)
        except TimeoutError as exc:
            task = transcriber._receive_task
            if task and task.done() and not task.cancelled():
                raise RuntimeError("Gemini Live receive task stopped unexpectedly") from task.exception()
            raise RuntimeError("Gemini Live returned no completed Vietnamese transcript within 30 seconds") from exc
    finally:
        await transcriber.close()

    transcript = transcripts[-1].strip()
    if not transcript:
        raise RuntimeError("Gemini Live returned an empty transcript.")
    print(f"Gemini Live audio-to-transcript round trip: ok ({transcript})")


async def main() -> None:
    settings = load_settings()
    model = settings.gemini_live_speech_model if "--speak" in sys.argv else settings.gemini_live_transcribe_model
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]
    if "--transcribe-test" in sys.argv:
        await transcribe_vietnamese_smoke_test(settings)
        return
    if "--speak" in sys.argv:
        audio_bytes = 0
        completed = asyncio.Event()

        async def on_audio(chunk: bytes, _: str) -> None:
            nonlocal audio_bytes
            audio_bytes += len(chunk)

        async def on_complete() -> None:
            completed.set()

        speaker = GeminiLiveSpeaker(settings, on_audio, on_complete)
        try:
            await speaker.connect()
            await speaker.speak("Xin chào.")
            await asyncio.wait_for(completed.wait(), timeout=30)
        finally:
            await speaker.close()
        if not audio_bytes:
            raise RuntimeError("Gemini Live returned no PCM audio.")
        print(f"Gemini Live speech: ok ({audio_bytes} PCM bytes)")
        return

    client = genai.Client(api_key=settings.gemini_live_api_key)
    connection = client.aio.live.connect(
        model=model,
        config=types.LiveConnectConfig(
            response_modalities=["TEXT" if "--text" in sys.argv else "AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
        ),
    )
    await connection.__aenter__()
    await connection.__aexit__(None, None, None)
    print(f"Gemini Live handshake: ok ({model})")


if __name__ == "__main__":
    asyncio.run(main())
