"""Tests for the shared persistent-session protocol, without transport I/O."""

from __future__ import annotations

import unittest

from gemini_live.live.session_protocol import LiveSessionState, can_transition


class LiveSessionProtocolTests(unittest.TestCase):
    def test_expected_happy_path_is_allowed(self) -> None:
        self.assertTrue(can_transition(LiveSessionState.IDLE, LiveSessionState.LISTENING))
        self.assertTrue(can_transition(LiveSessionState.LISTENING, LiveSessionState.WAITING_FOR_TOOL))
        self.assertTrue(can_transition(LiveSessionState.WAITING_FOR_TOOL, LiveSessionState.SPEAKING))
        self.assertTrue(can_transition(LiveSessionState.SPEAKING, LiveSessionState.LISTENING))

    def test_microphone_cannot_jump_from_speaking_to_waiting_for_tool(self) -> None:
        self.assertFalse(can_transition(LiveSessionState.SPEAKING, LiveSessionState.WAITING_FOR_TOOL))

    def test_error_must_recover_through_idle(self) -> None:
        self.assertFalse(can_transition(LiveSessionState.ERROR, LiveSessionState.LISTENING))
        self.assertTrue(can_transition(LiveSessionState.ERROR, LiveSessionState.IDLE))


if __name__ == "__main__":
    unittest.main()
