from rag_manager.config import Settings
from rag_manager.voice_gateway import VoiceProtocolError, VoiceSocketState, cancel_event, read_event, start_event


def settings() -> Settings:
    return Settings(gemini_api_key="agent-key", gemini_model="gemma-agent", gemini_live_api_key="voice-key")


def test_start_keeps_voice_session_separate_from_agent_model() -> None:
    state = VoiceSocketState()
    event = start_event({"type": "voice:start", "session_id": "session-1"}, settings(), state)

    assert event == {
        "type": "voice_ready",
        "session_id": "session-1",
        "transcribe_model": "gemini-3.1-flash-live-preview",
        "speech_model": "gemini-3.1-flash-live-preview",
        "phase": "gateway_ready",
        "voice": "kore",
    }
    assert state.session_id == "session-1"


def test_gateway_rejects_missing_voice_key() -> None:
    state = VoiceSocketState()
    missing_key = Settings(gemini_api_key="agent-key", gemini_model="gemma-agent")

    try:
        start_event({"type": "voice:start", "session_id": "session-1"}, missing_key, state)
    except VoiceProtocolError as exc:
        assert "GEMINI_LIVE_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected VoiceProtocolError")


def test_cancel_tracks_turns_and_event_requires_type() -> None:
    state = VoiceSocketState(session_id="session-1")
    assert cancel_event(state)["cancelled_turns"] == 1
    try:
        read_event({})
    except VoiceProtocolError:
        pass
    else:
        raise AssertionError("Expected VoiceProtocolError")
