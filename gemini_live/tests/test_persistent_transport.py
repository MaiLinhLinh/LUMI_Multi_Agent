"""Unit tests for persistent transport ownership without a Gemini network call."""

from __future__ import annotations

import unittest

from gemini_live.live.persistent_transport import PersistentLiveTransportStore


class PersistentLiveTransportStoreTests(unittest.TestCase):
    def test_same_application_session_reuses_transport_owner(self) -> None:
        store = PersistentLiveTransportStore()
        first = store.get("session-a")
        second = store.get("session-a")
        other = store.get("session-b")

        self.assertIs(first, second)
        self.assertIsNot(first, other)
        self.assertFalse(first.connected)


if __name__ == "__main__":
    unittest.main()
