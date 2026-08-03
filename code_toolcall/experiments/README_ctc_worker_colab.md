# Lumi CTC Worker on Colab

This is the presentation speech-timing service used by the current Gemini Live
presentation TTS path and it does not call Gemini itself.

## Start it in Colab

Upload `ctc_mms_fa_worker_colab.py`, then run:

```python
!pip install -q fastapi "uvicorn[standard]"
!python ctc_mms_fa_worker_colab.py --host 0.0.0.0 --port 8765
```

The model loads once at startup.  A GPU runtime is recommended.

## Verify incremental confirmation before integrating Lumi

Upload `ctc_incremental_replay_colab.py`, one Gemini Live WAV such as
`ctc_weather_evaluation.wav`, and its matching scene manifest such as
`ctc_evaluation_scenes.json`. Keep the worker running, then open a **second
Colab cell** and run:

```python
!pip install -q websockets
!python ctc_incremental_replay_colab.py --audio ctc_weather_evaluation.wav --scenes ctc_evaluation_scenes.json --expected ctc_weather_evaluation_result.json
```

The client sends 250-ms PCM chunks at real-time speed. It aligns each current
scene together with the next scene as a look-ahead anchor, then confirms only
the current one. A successful incremental run has at least one
`[CTC_REPLAY:SCENE_CONFIRMED]` before
`[CTC_REPLAY:FINALIZE]`; its summary reports `before_full_audio` above zero.
The final scene is intentionally confirmed only during finalization. When
`--expected` is supplied, the client also prints each incremental boundary's
difference from the earlier offline alignment.

## WebSocket contract

Connect to `ws://HOST:8765/ws/ctc`.  One connection represents one completed
presentation.  Send this JSON first:

```json
{
  "type": "ctc_start",
  "presentation_id": "uuid-for-one-answer",
  "input_sample_rate_hz": 24000,
  "scenes": [
    {"scene_id": "rain-risk", "alignment_text": "kha nang mua len toi chin muoi sau phan tram"}
  ]
}
```

Then send raw Gemini Live PCM16 little-endian chunks as binary WebSocket
messages, in playback order.  Every roughly 750 ms the worker aligns the next
unconfirmed scene.  It emits `scene_confirmed` only after that end boundary is
stable across two passes and at least 500 ms of later audio has arrived.

End the stream with:

```json
{"type": "ctc_finalize", "presentation_id": "uuid-for-one-answer"}
```

The worker returns one event per validated scene:

```json
{
  "type": "scene_confirmed",
  "presentation_id": "uuid-for-one-answer",
  "scene_id": "rain-risk",
  "start_ms": 350,
  "end_ms": 2850,
  "confidence": 0.71
}
```

It then sends `ctc_complete`; any final unconfirmed scenes are emitted before
that event.  Scene confidence is telemetry, not a hard rejection threshold:
MMS scores Vietnamese number words conservatively even when their scene
boundary is stable.
