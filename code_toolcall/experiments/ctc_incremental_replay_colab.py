"""Replay a PCM WAV into Lumi's incremental CTC worker in real time.

This is a Colab-only verification client. It deliberately does not import Lumi
application code and does not call Gemini. It proves whether the worker emits
``scene_confirmed`` while audio is still arriving, using a previously captured
Gemini Live WAV.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import websockets


def load_pcm_wav(path: Path) -> tuple[bytes, int]:
    """Return mono PCM16-LE frames and their sample rate."""
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1:
            raise ValueError("Replay WAV must be mono.")
        if wav.getsampwidth() != 2:
            raise ValueError("Replay WAV must be 16-bit PCM.")
        if wav.getcomptype() != "NONE":
            raise ValueError("Replay WAV must be uncompressed PCM.")
        return wav.readframes(wav.getnframes()), wav.getframerate()


def load_scenes(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Scenes JSON requires a non-empty scenes list.")
    return [
        {"scene_id": scene["scene_id"], "alignment_text": scene["alignment_text"]}
        for scene in scenes
    ]


def load_expected_boundaries(path: Path | None) -> dict[str, dict[str, int]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("Expected alignment JSON requires a scenes list.")
    return {
        scene["scene_id"]: {"start_ms": int(scene["start_ms"]), "end_ms": int(scene["end_ms"])}
        for scene in scenes
    }


async def replay(
    *,
    url: str,
    audio_path: Path,
    scenes_path: Path,
    expected_path: Path | None,
    chunk_ms: int,
    speed: float,
    prebuffer_ms: int,
) -> None:
    if not 50 <= chunk_ms <= 2_000:
        raise ValueError("--chunk-ms must be between 50 and 2000.")
    if speed <= 0:
        raise ValueError("--speed must be greater than zero.")
    if not 0 <= prebuffer_ms <= 30_000:
        raise ValueError("--prebuffer-ms must be between 0 and 30000.")

    pcm, sample_rate = load_pcm_wav(audio_path)
    scenes = load_scenes(scenes_path)
    expected = load_expected_boundaries(expected_path)
    bytes_per_chunk = max(2, round(sample_rate * 2 * chunk_ms / 1000 / 2) * 2)
    audio_duration_ms = round(len(pcm) * 1000 / (sample_rate * 2))
    presentation_id = f"replay-{uuid.uuid4()}"
    started_at = time.monotonic()
    ready = asyncio.Event()
    complete = asyncio.Event()
    receiver_error: Exception | None = None
    confirmed: list[dict[str, Any]] = []

    print(
        "[CTC_REPLAY:START]"
        f" url={url} audio_ms={audio_duration_ms} chunk_ms={chunk_ms}"
        f" speed={speed} scenes={len(scenes)} prebuffer_ms={prebuffer_ms}"
    )
    print(
        "[CTC_REPLAY:PLAYBACK_PLAN]"
        f" audio_playback_starts_at_ms={prebuffer_ms}"
        " (simulated; replay itself does not send audio to speakers)"
    )
    async with websockets.connect(url, max_size=None) as socket:
        async def receive_events() -> None:
            nonlocal receiver_error
            try:
                async for raw in socket:
                    if not isinstance(raw, str):
                        continue
                    event = json.loads(raw)
                    elapsed_ms = round((time.monotonic() - started_at) * 1000)
                    kind = event.get("type")
                    if kind == "ctc_ready":
                        print(f"[CTC_REPLAY:READY] at_ms={elapsed_ms}")
                        ready.set()
                    elif kind == "scene_confirmed":
                        scheduled_at_ms = prebuffer_ms + int(event["start_ms"])
                        lead_ms = scheduled_at_ms - elapsed_ms
                        confirmed.append({
                            **event,
                            "received_at_ms": elapsed_ms,
                            "scheduled_at_ms": scheduled_at_ms,
                            "lead_ms": lead_ms,
                        })
                        print(
                            "[CTC_REPLAY:SCENE_CONFIRMED]"
                            f" scene={event.get('scene_id')} start_ms={event.get('start_ms')}"
                            f" end_ms={event.get('end_ms')} confidence={event.get('confidence')}"
                            f" received_at_ms={elapsed_ms} scheduled_at_ms={scheduled_at_ms}"
                            f" lead_ms={lead_ms} status={'ready' if lead_ms >= 0 else 'late'}"
                        )
                    elif kind == "ctc_complete":
                        print(f"[CTC_REPLAY:COMPLETE] at_ms={elapsed_ms} {event}")
                        complete.set()
                        return
                    elif kind == "ctc_error":
                        raise RuntimeError(f"Worker error: {event.get('message')}")
            except Exception as error:
                receiver_error = error
                ready.set()
                complete.set()

        receiver = asyncio.create_task(receive_events())
        await socket.send(json.dumps({
            "type": "ctc_start",
            "presentation_id": presentation_id,
            "input_sample_rate_hz": sample_rate,
            "scenes": scenes,
        }))
        await asyncio.wait_for(ready.wait(), timeout=15)
        if receiver_error:
            raise receiver_error

        for offset in range(0, len(pcm), bytes_per_chunk):
            await socket.send(pcm[offset:offset + bytes_per_chunk])
            await asyncio.sleep((chunk_ms / 1000) / speed)
            if receiver_error:
                raise receiver_error

        print(f"[CTC_REPLAY:FINALIZE] audio_sent_ms={audio_duration_ms}")
        await socket.send(json.dumps({"type": "ctc_finalize", "presentation_id": presentation_id}))
        await asyncio.wait_for(complete.wait(), timeout=120)
        await receiver
        if receiver_error:
            raise receiver_error

    early = [event for event in confirmed if event["received_at_ms"] < audio_duration_ms]
    print(
        "[CTC_REPLAY:SUMMARY]"
        f" confirmed={len(confirmed)}/{len(scenes)}"
        f" before_full_audio={len(early)} audio_ms={audio_duration_ms}"
    )
    if not early:
        print("[CTC_REPLAY:NOTE] No scene was confirmed before the full WAV arrived.")
    late = [event for event in confirmed if event["lead_ms"] < 0]
    min_lead_ms = min((event["lead_ms"] for event in confirmed), default=None)
    print(
        "[CTC_REPLAY:PREBUFFER_SUMMARY]"
        f" prebuffer_ms={prebuffer_ms} late={len(late)}/{len(confirmed)}"
        f" min_lead_ms={min_lead_ms}"
    )
    if expected:
        print("[CTC_REPLAY:BOUNDARY_COMPARISON] incremental versus offline")
        for event in confirmed:
            reference = expected.get(event["scene_id"])
            if reference is None:
                continue
            start_error = event["start_ms"] - reference["start_ms"]
            end_error = event["end_ms"] - reference["end_ms"]
            print(
                "[CTC_REPLAY:BOUNDARY]"
                f" scene={event['scene_id']} start_error_ms={start_error}"
                f" end_error_ms={end_error}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a Gemini WAV into the Lumi incremental CTC worker.")
    parser.add_argument("--url", default="ws://127.0.0.1:8765/ws/ctc")
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--scenes", required=True, type=Path)
    parser.add_argument(
        "--expected",
        type=Path,
        help="Optional offline ctc_alignment_result.json to compare boundaries.",
    )
    parser.add_argument("--chunk-ms", type=int, default=250)
    parser.add_argument("--speed", type=float, default=1.0, help="1.0 means real-time replay.")
    parser.add_argument(
        "--prebuffer-ms",
        type=int,
        default=8_000,
        help="Simulated initial frontend audio buffer before playback starts (default: 8000).",
    )
    args = parser.parse_args()
    asyncio.run(
        replay(
            url=args.url,
            audio_path=args.audio,
            scenes_path=args.scenes,
            expected_path=args.expected,
            chunk_ms=args.chunk_ms,
            speed=args.speed,
            prebuffer_ms=args.prebuffer_ms,
        )
    )


if __name__ == "__main__":
    main()
