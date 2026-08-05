# Persistent Gemini Live session — checkpoints

Scope: refactor only the shared Live session layer. Weather, Education and
future domains retain ownership of their tools, business context, view model,
adapter and prompt guidance.

## CP-01 — Shared state machine and event contract

- [x] Define shared technical states: `idle`, `listening`,
  `waiting_for_tool`, `speaking`, `error`.
- [x] Define permitted state transitions in `live/session_protocol.py`.
- [x] Separate technical session state from domain business state, such as
  Education's `awaiting_answer`.
- [x] Reserve neutral browser/backend control events for session state,
  microphone lifecycle, timeout, reconnect and close.
- [x] Confirm the first persistent version locks the microphone while
  `waiting_for_tool` or `speaking`; no barge-in yet.

### Operating rules

1. Browser may send microphone PCM only in `listening`.
2. `audio_end` moves the session to `waiting_for_tool` until Gemini either
   asks a clarification, starts an approved scene, or fails.
3. During a presentation, audio and transcript are forwarded only after the
   expected `trigger_scene(scene_id)` has been accepted.
4. The backend sends scene `n + 1` only after Gemini marks scene `n` complete.
   After the final scene, the session returns to `listening`; it does not close.
5. Domain code never opens WebSockets, receives PCM, sends scenes or owns
   transport timeouts.
6. Settings for timeouts and cleanup will be introduced in CP-05, not silently
   embedded in a domain.

### Event contract to implement in later checkpoints

| Direction | Event | Meaning |
| --- | --- | --- |
| Browser → backend | `live:audio_begin` | Request transition to `listening`. |
| Browser → backend | PCM binary frames | Microphone data; accepted only while listening. |
| Browser → backend | `live:audio_end` | User has stopped speaking. |
| Backend → browser | `live:state` | Authoritative technical state. |
| Backend → browser | `panel`, `scene`, PCM, `text` | Existing presentation payloads. |
| Backend → browser | `live:timeout` / `live:error` | Current turn ended safely; UI may enable mic. |
| Backend → browser | `live:reconnecting` / `live:reconnected` | Gemini transport recovery status. |

Next checkpoint: **CP-02 — persistent Gemini transport**. Introduce a
per-`session_id` connection owner with explicit `connect`, `send_audio`,
`send_text` and `close`, while retaining a single receive task per Gemini
connection. No frontend change yet.

## CP-02 — Persistent Gemini transport

- [x] Add `PersistentLiveTransport`: a domain-neutral owner of one Gemini Live
  async context and exactly one background receive task.
- [x] Add explicit `connect`, `send_text`, `send_audio`, `end_audio`,
  `send_tool_responses`, `receive` and `close` operations.
- [x] Add `PersistentLiveTransportStore`, mapping application `session_id` to a
  reusable connection owner.
- [x] The former one-turn implementation was removed after CP-04; the only
  supported production path is persistent transport plus shared orchestration.
- [x] Keep all domain code unchanged.

Next checkpoint: **CP-03 — persistent orchestration**. Move the existing raw
Gemini receive-loop logic to one long-running orchestrated turn processor:
it will consume the persistent transport's messages, execute tools, send
approved scene instructions one at a time, and return to `listening` after the
last scene instead of closing the Gemini connection.

## CP-03 — Persistent orchestration

- [x] Add shared technical state storage to `LiveSessionOrchestrator`, keyed
  by `session_id`; domain context and lesson state remain untouched.
- [x] Add `PersistentGeminiLiveConversation`, which consumes only the message
  queue of `PersistentLiveTransport` and never creates a second receiver.
- [x] Preserve the current tool path: Gemini tool call → dispatcher → domain →
  shared Presentation Pipeline → validated panel + active scenes.
- [x] Preserve backend-owned scene order. A scene is forwarded to the browser
  only after the expected `trigger_scene(scene_id)` is accepted.
- [x] Suppress PCM produced before an active presentation scene is accepted.
  Keep its output transcript visible to the browser with
  `presentation_approved=false`, so prompt/token waste remains observable.
- [x] After the last scene or a direct clarification finishes, return to
  `listening`, remember the completed turn, and keep Gemini connected.

Next checkpoint: **CP-04 — persistent browser/backend socket**. Replace the
one-turn `/ws/live` protocol with a single browser socket that owns one
persistent conversation. It will toggle only microphone capture and will no
longer close after `live:complete`.

## CP-04 — Persistent browser/backend socket

- [x] Replace the browser's one-turn `live:start` handshake with one
  `live:connect` handshake when the page opens.
- [x] Keep one browser WebSocket and one `PersistentGeminiLiveConversation`
  while that browser socket remains open.
- [x] Add browser commands `live:text`, `live:audio_begin`, `live:audio_end`
  and `live:close`; microphone PCM is only sent between begin/end.
- [x] Keep UI state driven by authoritative backend `live:state` events.
- [x] Stop closing the browser socket after a turn; use `live:turn_complete`
  only as a turn boundary.
- [x] Keep microphone locked while a turn is processing or audio is playing.
- [x] Release the Live connection when the browser disconnects. A reconnect
  grace period and idle TTL remain CP-05 work.

Verification completed: `node --check web/app.js`, Python `compileall`, and
the 22 existing unit tests all pass. Manual end-to-end verification requires a
running Gemini API configuration and is intentionally the first task of CP-05.

Next checkpoint: **CP-05 — timeout, reconnect and cleanup policy**. Add
settings-driven deadlines for a silent/unfinished turn, reconnect-safe memory
rehydration, idle cleanup and consistent browser recovery events.

## CP-05 — Timeout, reconnect and cleanup policy

- [x] Add settings with safe defaults:
  `LIVE_TURN_TIMEOUT_SECONDS=45`, `LIVE_IDLE_TIMEOUT_SECONDS=900`, and
  `LIVE_RECONNECT_GRACE_SECONDS=30`.
- [x] Bound each active text/audio turn with the configured turn timeout.
  On expiry, cancel that turn, emit `live:timeout`, create a fresh Gemini Live
  connection, and retain server-owned history/domain context.
- [x] Add browser-visible `live:reconnecting` and `live:reconnected` events.
- [x] Add an idle watcher for a connected page. It reports `idle_timeout` and
  closes the browser socket; the next user action opens a fresh browser socket.
- [x] On browser disconnect, cancel active browser work but keep Gemini Live
  for the configured reconnect grace period. A reconnect drops stale raw
  Gemini messages, resets only technical state, and preserves verified memory.
- [x] Close the Live transport and reset technical state after grace expires.

Verification completed: `node --check web/app.js`, Python `compileall`, and
the 22 existing unit tests all pass. A live manual test is required for the
Gemini API timing paths: two consecutive turns, browser refresh within 30
seconds, intentional mic silence, and an idle timeout.

Next checkpoint: **manual persistent-session verification**. Start the app,
exercise the four scenarios above, inspect the structured logs, then address
only observed behavior before changing Education prompt guidance.
