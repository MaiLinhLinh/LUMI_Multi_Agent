from __future__ import annotations

import asyncio
import wave
from io import BytesIO

import pytest

from types import SimpleNamespace

from rag_manager.presentation.gemini_live_wav import LiveWavCapture, _event_details, _sample_rate_from_mime
from rag_manager.presentation import gemini_live_wav
from rag_manager.config import Settings


def test_live_wav_capture_writes_pcm16_wav_with_actual_duration() -> None:
    capture = LiveWavCapture()
    capture.append(b"\x00\x00" * 24_000, "audio/pcm;rate=24000")

    assert capture.sample_rate == 24_000
    assert capture.duration_ms == 1_000
    with wave.open(BytesIO(capture.wav_bytes())) as output:
        assert output.getframerate() == 24_000
        assert output.getsampwidth() == 2
        assert output.getnchannels() == 1
        assert output.getnframes() == 24_000


def test_live_wav_capture_rejects_mixed_sample_rates() -> None:
    capture = LiveWavCapture()
    capture.append(b"\x00\x00", "audio/pcm;rate=24000")

    with pytest.raises(RuntimeError, match="changed audio sample rate"):
        capture.append(b"\x00\x00", "audio/pcm;rate=16000")


@pytest.mark.parametrize(
    ("mime_type", "expected"),
    [(None, 24_000), ("audio/pcm;rate=24000", 24_000), ("audio/pcm; rate=16000", 16_000)],
)
def test_sample_rate_parser(mime_type: str | None, expected: int) -> None:
    assert _sample_rate_from_mime(mime_type) == expected


def test_event_details_exposes_error_without_audio_payload() -> None:
    event = SimpleNamespace(error=SimpleNamespace(message="quota"), go_away=None, server_content=None)

    assert _event_details(event) == {
        "has_server_content": False,
        "audio_parts": 0,
        "text_parts": 0,
        "generation_complete": False,
        "turn_complete": False,
        "interrupted": False,
        "error": "namespace(message='quota')",
        "go_away": None,
    }


def test_live_wav_sends_final_script_as_client_content(monkeypatch) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.sent = None

        async def send_client_content(self, **kwargs) -> None:
            self.sent = kwargs

        async def receive(self):
            yield SimpleNamespace(
                error=None,
                go_away=None,
                server_content=SimpleNamespace(
                    model_turn=SimpleNamespace(parts=[
                        SimpleNamespace(inline_data=SimpleNamespace(data=b"\x00\x00", mime_type="audio/pcm;rate=24000"))
                    ]),
                    generation_complete=True,
                    turn_complete=True,
                    interrupted=False,
                ),
            )

    class FakeConnection:
        async def __aenter__(self) -> FakeSession:
            return session

        async def __aexit__(self, *_args) -> None:
            return None

    class FakeLive:
        def connect(self, **_kwargs) -> FakeConnection:
            return FakeConnection()

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            self.aio = SimpleNamespace(live=FakeLive())

    session = FakeSession()
    monkeypatch.setattr(gemini_live_wav.genai, "Client", FakeClient)
    settings = Settings(gemini_api_key="agent", gemini_model="model", gemini_live_api_key="live")

    capture = asyncio.run(gemini_live_wav.capture_gemini_live_wav(settings, "Xin chào."))

    assert capture.duration_ms == 0  # One PCM sample rounds to zero milliseconds.
    assert session.sent["turn_complete"] is True
    content = session.sent["turns"]
    assert content.role == "user"
    assert content.parts[0].text.endswith("Xin chào.")


def test_live_wav_does_not_stop_before_later_audio_event(monkeypatch) -> None:
    class FakeSession:
        async def send_client_content(self, **_kwargs) -> None:
            return None

        async def receive(self):
            yield SimpleNamespace(
                error=None,
                go_away=None,
                server_content=SimpleNamespace(
                    model_turn=None, generation_complete=False, turn_complete=False, interrupted=False,
                ),
            )
            yield SimpleNamespace(
                error=None,
                go_away=None,
                server_content=SimpleNamespace(
                    model_turn=SimpleNamespace(parts=[
                        SimpleNamespace(inline_data=SimpleNamespace(data=b"\x00\x00", mime_type="audio/pcm;rate=24000"))
                    ]),
                    generation_complete=True,
                    turn_complete=True,
                    interrupted=False,
                ),
            )

    class FakeConnection:
        async def __aenter__(self) -> FakeSession:
            return FakeSession()

        async def __aexit__(self, *_args) -> None:
            return None

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            self.aio = SimpleNamespace(live=SimpleNamespace(connect=lambda **_kwargs: FakeConnection()))

    monkeypatch.setattr(gemini_live_wav.genai, "Client", FakeClient)
    settings = Settings(gemini_api_key="agent", gemini_model="model", gemini_live_api_key="live")

    capture = asyncio.run(gemini_live_wav.capture_gemini_live_wav(settings, "Xin chào."))

    assert capture.chunks == [b"\x00\x00"]
