"""Offline MMS CTC alignment trial for a Gemini Live WAV.

Run this file in Google Colab after uploading the WAV and scene manifest::

    !python ctc_mms_fa_colab.py --audio gemini_live_trial.wav \
        --scenes ctc_trial_scenes.json --output ctc_alignment_result.json

The script uses no Gemini credentials.  It reads the known, normalized
``alignment_text`` and returns measured scene timestamps from the actual WAV.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Align Gemini Live WAV scenes with MMS CTC.")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--scenes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def validate_scenes(scenes: Any) -> tuple[list[dict[str, Any]], str]:
    """Validate the shared scene-manifest subset used by file and WS modes."""
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


def _load_manifest(path: Path) -> tuple[list[dict[str, Any]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return validate_scenes(payload.get("scenes"))


def _frame_to_ms(frame: int, *, waveform_samples: int, emission_frames: int, sample_rate: int) -> int:
    return round(frame * waveform_samples * 1000 / emission_frames / sample_rate)


def align(audio_path: Path, scenes_path: Path) -> dict[str, Any]:
    import torch
    import torchaudio
    import torchaudio.functional as F

    if not hasattr(torchaudio.pipelines, "MMS_FA"):
        raise RuntimeError(
            "torchaudio.pipelines.MMS_FA is unavailable. Use a Colab PyTorch runtime "
            "with a torchaudio release that includes MMS_FA (the setup instructions explain this)."
        )
    scenes, transcript = _load_manifest(scenes_path)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=False).to(device).eval()
    tokenizer = bundle.get_tokenizer()
    aligner = bundle.get_aligner()

    waveform, input_rate = torchaudio.load(audio_path)
    if waveform.size(0) > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if input_rate != bundle.sample_rate:
        waveform = F.resample(waveform, input_rate, bundle.sample_rate)

    # MMS FA tokenizer receives one string per word; it does not include a
    # dictionary token for a literal space. The aligner combines these word
    # token lists into one target sequence internally.
    tokens = tokenizer(transcript.split())
    with torch.inference_mode():
        emission, _ = model(waveform.to(device))
    # torchaudio 2.11 returns one list of TokenSpan objects per input word.
    # This is already the word boundary structure needed for scene assembly.
    word_token_spans = aligner(emission[0], tokens)

    words = transcript.split()
    if len(word_token_spans) != len(words):
        raise RuntimeError(
            "MMS word count does not match normalized transcript: "
            f"got={len(word_token_spans)}, expected={len(words)}."
        )

    word_spans: list[dict[str, Any]] = []
    for word, characters in zip(words, word_token_spans, strict=True):
        if len(characters) != len(word):
            raise RuntimeError(
                f"MMS character count does not match word {word!r}: "
                f"got={len(characters)}, expected={len(word)}."
            )
        word_spans.append({
            "word": word,
            "start_ms": _frame_to_ms(
                characters[0].start,
                waveform_samples=waveform.size(1),
                emission_frames=emission.size(1),
                sample_rate=bundle.sample_rate,
            ),
            "end_ms": _frame_to_ms(
                characters[-1].end,
                waveform_samples=waveform.size(1),
                emission_frames=emission.size(1),
                sample_rate=bundle.sample_rate,
            ),
            "confidence": round(sum(float(item.score) for item in characters) / len(characters), 4),
        })

    results: list[dict[str, Any]] = []
    word_cursor = 0
    for scene in scenes:
        count = len(scene["alignment_text"].split())
        scene_words = word_spans[word_cursor:word_cursor + count]
        word_cursor += count
        results.append({
            "scene_id": scene["scene_id"],
            "start_ms": scene_words[0]["start_ms"],
            "end_ms": scene_words[-1]["end_ms"],
            "confidence": round(
                sum(item["confidence"] for item in scene_words) / len(scene_words), 4
            ),
            "words": scene_words,
        })
    if word_cursor != len(word_spans):
        raise RuntimeError("Scene word boundaries did not consume the transcript.")

    return {
        "schema_version": "lumi.ctc_alignment_result.v1",
        "audio_file": audio_path.name,
        "input_sample_rate_hz": input_rate,
        "alignment_sample_rate_hz": bundle.sample_rate,
        "device": str(device),
        "torchaudio_version": torchaudio.__version__,
        "scenes": results,
    }


def main() -> None:
    args = _arguments()
    result = align(args.audio, args.scenes)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
