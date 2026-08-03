"""WebSocket CTC worker for the Lumi Gemini Live alignment experiment.

This worker deliberately owns no Gemini connection.  It accepts raw PCM16-LE
audio emitted by Gemini Live, plus the already-validated scene manifest, then
returns audio-timeline events.  Keeping Gemini and CTC separate lets Lumi use
the same worker for offline WAV tests and a later Live bridge.

Run in Colab (after uploading this file and ``ctc_mms_fa_colab.py``)::

    !pip install -q fastapi "uvicorn[standard]"
    !python ctc_mms_fa_worker_colab.py --host 0.0.0.0 --port 8765

Protocol, one WebSocket per completed presentation:

1. JSON ``ctc_start``: presentation id, 24-kHz source rate, scenes.
2. Binary messages: PCM16-LE audio chunks in source-rate order.
3. While audio is arriving, stable scene boundaries are emitted as
   ``scene_confirmed`` events.
4. JSON ``ctc_finalize``: align any remaining scenes, then emit
   ``ctc_complete``.

The worker never makes content decisions; it only measures where the known
spoken scenes occur in real Gemini audio.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import torch
import torchaudio
import torchaudio.functional as F
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger("lumi.ctc_worker")
MAX_PCM_BYTES = 32 * 1024 * 1024
# Re-run alignment only after enough new source audio exists.  MMS is loaded
# once on the Colab GPU, but inference is still expensive.
ALIGN_INTERVAL_MS = 750
# Align the full current scene plus this short, known prefix of its successor.
# The prefix is an acoustic anchor: it proves the next scene has actually
# started, without waiting for the whole successor scene to be spoken.
LOOKAHEAD_ANCHOR_WORDS = 4
# A phrase forced-aligned before it has been fully spoken tends to end at the
# latest audio frame.  Require audio after the short successor anchor and two
# nearly identical measurements before publishing an irreversible scene event.
MIN_TAIL_MS = 500
STABILITY_TOLERANCE_MS = 140
STABLE_PASSES_REQUIRED = 2


class ProtocolError(ValueError):
    """An invalid CTC worker client message."""


class InsufficientAudioError(RuntimeError):
    """The next known scene cannot yet fit in the received CTC frames."""


def _frame_to_ms(frame: int, *, waveform_samples: int, emission_frames: int, sample_rate: int) -> int:
    """Convert an MMS emission-frame boundary to the source audio timeline."""
    return round(frame * waveform_samples * 1000 / emission_frames / sample_rate)


def validate_scenes(scenes: Any) -> tuple[list[dict[str, Any]], str]:
    """Validate only the scene-manifest fields the worker needs.

    Kept locally rather than imported from the offline experiment script so a
    Colab worker can be uploaded and started as one self-contained file.
    """
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Manifest requires a non-empty scenes list.")
    alignment_parts: list[str] = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict) or not isinstance(scene.get("scene_id"), str):
            raise ValueError(f"Scene {index} requires scene_id.")
        text = scene.get("alignment_text")
        if not isinstance(text, str) or not text.strip() or not text.isascii():
            raise ValueError(f"Scene {scene['scene_id']} requires non-empty ASCII alignment_text.")
        alignment_parts.append(text.strip())
    return scenes, " ".join(alignment_parts)


@dataclass
class PresentationAudio:
    presentation_id: str
    input_sample_rate_hz: int
    scenes: list[dict[str, Any]]
    transcript: str
    pcm: bytearray = field(default_factory=bytearray)
    confirmed_scene_count: int = 0
    confirmed_end_ms: int = 0
    last_alignment_audio_ms: int = 0
    candidate_end_ms: int | None = None
    candidate_anchor_end_ms: int | None = None
    stable_passes: int = 0


class MMSAligner:
    """Load MMS once and align PCM regions on the Colab GPU."""

    def __init__(self) -> None:
        if not hasattr(torchaudio.pipelines, "MMS_FA"):
            raise RuntimeError("torchaudio.pipelines.MMS_FA is unavailable in this runtime.")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.bundle = torchaudio.pipelines.MMS_FA
        self.model = self.bundle.get_model(with_star=False).to(self.device).eval()
        self.tokenizer = self.bundle.get_tokenizer()
        self.aligner = self.bundle.get_aligner()
        logger.info("[CTC_WORKER:MODEL_READY] device=%s sample_rate=%s", self.device, self.bundle.sample_rate)

    def _waveform(self, pcm: bytes | bytearray, input_sample_rate_hz: int) -> torch.Tensor:
        if not pcm:
            raise ProtocolError("No PCM audio was received before alignment.")
        if len(pcm) % 2:
            raise ProtocolError("PCM16 audio byte length must be even.")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        waveform = torch.from_numpy(samples).unsqueeze(0)
        if input_sample_rate_hz != self.bundle.sample_rate:
            waveform = F.resample(waveform, input_sample_rate_hz, self.bundle.sample_rate)
        return waveform

    def _align_words(self, waveform: torch.Tensor, transcript: str, *, offset_ms: int = 0) -> list[dict[str, Any]]:
        tokens = self.tokenizer(transcript.split())
        with torch.inference_mode():
            emission, _ = self.model(waveform.to(self.device))
        try:
            token_spans = self.aligner(emission[0], tokens)
        except RuntimeError as error:
            # MMS raises this on GPU when a partial PCM prefix has fewer CTC
            # frames than the characters in the next scene.  In an
            # incremental stream that means "wait for more audio", not a
            # malformed request or a fatal worker error.
            if "targets length is too long for CTC" in str(error):
                raise InsufficientAudioError(str(error)) from error
            raise
        words = transcript.split()
        if len(token_spans) != len(words):
            raise RuntimeError("MMS returned a different number of word spans.")

        word_spans: list[dict[str, Any]] = []
        for word, characters in zip(words, token_spans, strict=True):
            if len(characters) != len(word):
                raise RuntimeError(f"MMS character count does not match word {word!r}.")
            word_spans.append({
                "word": word,
                "start_ms": offset_ms + _frame_to_ms(characters[0].start, waveform_samples=waveform.size(1), emission_frames=emission.size(1), sample_rate=self.bundle.sample_rate),
                "end_ms": offset_ms + _frame_to_ms(characters[-1].end, waveform_samples=waveform.size(1), emission_frames=emission.size(1), sample_rate=self.bundle.sample_rate),
                "confidence": round(sum(float(item.score) for item in characters) / len(characters), 4),
            })
        return word_spans

    def align_next_scene_with_lookahead(self, state: PresentationAudio) -> dict[str, Any]:
        """Align the next scene plus a short successor anchor.

        A scene alone can be spuriously stretched into speech belonging to the
        next scene.  The first few known words of that successor prove the
        transition has happened, without waiting for the entire successor.
        The successor itself is re-aligned with its own anchor in a later pass.
        """
        if state.confirmed_scene_count >= len(state.scenes) - 1:
            raise RuntimeError("Look-ahead requires a successor scene.")
        source_offset_bytes = round(state.confirmed_end_ms * state.input_sample_rate_hz * 2 / 1000)
        waveform = self._waveform(state.pcm[source_offset_bytes:], state.input_sample_rate_hz)
        scene = state.scenes[state.confirmed_scene_count]
        successor = state.scenes[state.confirmed_scene_count + 1]
        successor_words = successor["alignment_text"].split()
        anchor_words_expected = successor_words[:LOOKAHEAD_ANCHOR_WORDS]
        anchor_text = " ".join(anchor_words_expected)
        window_text = f"{scene['alignment_text']} {anchor_text}"
        words = self._align_words(waveform, window_text, offset_ms=state.confirmed_end_ms)
        current_word_count = len(scene["alignment_text"].split())
        current_words = words[:current_word_count]
        anchor_words = words[current_word_count:]
        if len(anchor_words) != len(anchor_words_expected):
            raise RuntimeError("MMS did not return every look-ahead anchor word.")
        return {
            "scene_id": scene["scene_id"],
            "start_ms": current_words[0]["start_ms"],
            "end_ms": current_words[-1]["end_ms"],
            "confidence": round(sum(item["confidence"] for item in current_words) / len(current_words), 4),
            "lookahead_scene_id": successor["scene_id"],
            # Internal evidence for stabilization.  It is intentionally not
            # sent to the frontend as part of the public scene event.
            "anchor_end_ms": anchor_words[-1]["end_ms"],
            "anchor_word_count": len(anchor_words),
        }

    def align(self, state: PresentationAudio) -> dict[str, Any]:
        waveform = self._waveform(state.pcm, state.input_sample_rate_hz)
        word_spans = self._align_words(waveform, state.transcript)

        scenes: list[dict[str, Any]] = []
        cursor = 0
        for scene in state.scenes:
            count = len(scene["alignment_text"].split())
            scene_words = word_spans[cursor:cursor + count]
            cursor += count
            scenes.append({
                "scene_id": scene["scene_id"],
                "start_ms": scene_words[0]["start_ms"],
                "end_ms": scene_words[-1]["end_ms"],
                "confidence": round(sum(item["confidence"] for item in scene_words) / len(scene_words), 4),
            })
        if cursor != len(word_spans):
            raise RuntimeError("Scene words did not consume the alignment transcript.")

        duration_ms = round(len(state.pcm) * 1000 / 2 / state.input_sample_rate_hz)
        return {"scenes": scenes, "audio_duration_ms": duration_ms}


def _start_state(raw: dict[str, Any]) -> PresentationAudio:
    if raw.get("type") != "ctc_start":
        raise ProtocolError("First CTC message must have type=ctc_start.")
    presentation_id = raw.get("presentation_id")
    if not isinstance(presentation_id, str) or not presentation_id.strip() or len(presentation_id) > 200:
        raise ProtocolError("ctc_start requires a valid presentation_id.")
    sample_rate = raw.get("input_sample_rate_hz")
    if not isinstance(sample_rate, int) or not 8_000 <= sample_rate <= 48_000:
        raise ProtocolError("ctc_start requires input_sample_rate_hz between 8000 and 48000.")
    scenes, transcript = validate_scenes(raw.get("scenes"))
    return PresentationAudio(presentation_id.strip(), sample_rate, scenes, transcript)


def _audio_duration_ms(state: PresentationAudio) -> int:
    return round(len(state.pcm) * 1000 / 2 / state.input_sample_rate_hz)


app = FastAPI(title="Lumi MMS CTC worker")
_aligner: MMSAligner | None = None
_align_lock = asyncio.Lock()


@app.on_event("startup")
async def load_model() -> None:
    global _aligner
    _aligner = MMSAligner()


async def _try_confirm_next_scene(socket: WebSocket, state: PresentationAudio) -> None:
    """Publish one scene only after its CTC end boundary has stabilized."""
    # The final scene has no successor to anchor its end.  Keep it for the
    # complete-pass alignment after Gemini has finished speaking.
    if state.confirmed_scene_count >= len(state.scenes) - 1:
        return
    audio_duration_ms = _audio_duration_ms(state)
    if audio_duration_ms < ALIGN_INTERVAL_MS or (
        audio_duration_ms - state.last_alignment_audio_ms < ALIGN_INTERVAL_MS
    ):
        return
    state.last_alignment_audio_ms = audio_duration_ms
    assert _aligner is not None
    try:
        async with _align_lock:
            candidate = await asyncio.to_thread(_aligner.align_next_scene_with_lookahead, state)
    except InsufficientAudioError:
        logger.info(
            "[CTC_WORKER:PENDING_AUDIO] presentation=%s scene=%s audio_ms=%s",
            state.presentation_id,
            state.scenes[state.confirmed_scene_count]["scene_id"],
            audio_duration_ms,
        )
        return

    stable = (
        state.candidate_end_ms is not None
        and state.candidate_anchor_end_ms is not None
        and abs(candidate["end_ms"] - state.candidate_end_ms) <= STABILITY_TOLERANCE_MS
        and abs(candidate["anchor_end_ms"] - state.candidate_anchor_end_ms) <= STABILITY_TOLERANCE_MS
    )
    state.stable_passes = state.stable_passes + 1 if stable else 1
    state.candidate_end_ms = candidate["end_ms"]
    state.candidate_anchor_end_ms = candidate["anchor_end_ms"]
    tail_ms = audio_duration_ms - candidate["anchor_end_ms"]
    logger.info(
        "[CTC_WORKER:CANDIDATE] presentation=%s scene=%s end_ms=%s anchor_end_ms=%s anchor_words=%s tail_ms=%s passes=%s confidence=%.4f",
        state.presentation_id,
        candidate["scene_id"],
        candidate["end_ms"],
        candidate["anchor_end_ms"],
        candidate["anchor_word_count"],
        tail_ms,
        state.stable_passes,
        candidate["confidence"],
    )
    if tail_ms < MIN_TAIL_MS or state.stable_passes < STABLE_PASSES_REQUIRED:
        return

    state.confirmed_scene_count += 1
    state.confirmed_end_ms = candidate["end_ms"]
    state.candidate_end_ms = None
    state.candidate_anchor_end_ms = None
    state.stable_passes = 0
    logger.info(
        "[CTC_WORKER:SCENE_CONFIRMED] presentation=%s scene=%s lookahead=%s start_ms=%s end_ms=%s confidence=%.4f",
        state.presentation_id,
        candidate["scene_id"],
        candidate["lookahead_scene_id"],
        candidate["start_ms"],
        candidate["end_ms"],
        candidate["confidence"],
    )
    public_candidate = {
        key: value
        for key, value in candidate.items()
        if key not in {"anchor_end_ms", "anchor_word_count"}
    }
    await socket.send_json({"type": "scene_confirmed", "presentation_id": state.presentation_id, **public_candidate})


@app.websocket("/ws/ctc")
async def ctc_socket(socket: WebSocket) -> None:
    await socket.accept()
    state: PresentationAudio | None = None
    try:
        while True:
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            raw_bytes = message.get("bytes")
            if raw_bytes is not None:
                if state is None:
                    raise ProtocolError("Send ctc_start before PCM audio.")
                if len(state.pcm) + len(raw_bytes) > MAX_PCM_BYTES:
                    raise ProtocolError("PCM audio exceeds the 32 MiB worker limit.")
                state.pcm.extend(raw_bytes)
                await _try_confirm_next_scene(socket, state)
                continue
            raw_text = message.get("text")
            if raw_text is None:
                continue
            import json
            raw = json.loads(raw_text)
            if not isinstance(raw, dict):
                raise ProtocolError("CTC control message must be a JSON object.")
            if state is None:
                state = _start_state(raw)
                await socket.send_json({"type": "ctc_ready", "presentation_id": state.presentation_id})
                continue
            if raw.get("type") != "ctc_finalize":
                raise ProtocolError("Expected binary PCM or type=ctc_finalize.")
            if raw.get("presentation_id") != state.presentation_id:
                raise ProtocolError("ctc_finalize presentation_id does not match ctc_start.")
            assert _aligner is not None
            async with _align_lock:
                result = await asyncio.to_thread(_aligner.align, state)
            # Incremental events were already sent for the stable prefix.
            # Finalization supplies only the remaining suffix, guaranteeing no
            # duplicate scene_confirmed event reaches the frontend.
            for scene in result["scenes"][state.confirmed_scene_count:]:
                await socket.send_json({"type": "scene_confirmed", "presentation_id": state.presentation_id, **scene})
            await socket.send_json({
                "type": "ctc_complete",
                "presentation_id": state.presentation_id,
                "audio_duration_ms": result["audio_duration_ms"],
                "scene_count": len(result["scenes"]),
            })
            return
    except (ProtocolError, ValueError, RuntimeError) as error:
        logger.exception("[CTC_WORKER:ERROR] %s", error)
        await socket.send_json({"type": "ctc_error", "message": str(error)})
        await socket.close(code=1008)
    except WebSocketDisconnect:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Lumi's Colab MMS CTC WebSocket worker.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
