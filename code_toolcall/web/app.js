const workspace = document.querySelector("#workspace");
const welcome = document.querySelector("#welcome");
const contentPanel = document.querySelector("#contentPanel");
const contentEyebrow = document.querySelector("#contentEyebrow");
const contentTitle = document.querySelector("#contentTitle");
const contentBadge = document.querySelector("#contentBadge span");
const presentationAvatar = document.querySelector("#presentationAvatar");
const weatherView = document.querySelector("#weatherView");
const weatherTemplateHost = document.querySelector("#weatherTemplateHost");
const presentationOverlay = document.querySelector("#presentationOverlay");
const musicView = document.querySelector("#musicView");
const musicFrame = document.querySelector("#musicFrame");
const musicStopped = document.querySelector("#musicStopped");
const musicTitle = document.querySelector("#musicTitle");
const musicArtist = document.querySelector("#musicArtist");
const musicVersion = document.querySelector("#musicVersion");
const messagesElement = document.querySelector("#messages");
const suggestions = document.querySelector("#suggestions");
const chatForm = document.querySelector("#chatForm");
const queryInput = document.querySelector("#queryInput");
const microphoneButton = document.querySelector("#microphoneButton");
const sendButton = document.querySelector("#sendButton");
const voiceStatus = document.querySelector("#voiceStatus");
const clearButton = document.querySelector("#clearButton");
const connectionStatus = document.querySelector("#connectionStatus");
const firstTextLog = document.querySelector("#firstTextLog");
const messageTemplate = document.querySelector("#messageTemplate");

const SESSION_KEY = "lumi_web_session_id";
const YOUTUBE_NOCOOKIE_ORIGIN = "https://www.youtube-nocookie.com";
const YOUTUBE_VIDEO_ID_PATTERN = /^[A-Za-z0-9_-]{11}$/;
const STREAM_CHARACTER_DELAY_MS = 18;
const SPEECH_READY_TIMEOUT_MS = 5000;
const SPEECH_STEP_TIMEOUT_MIN_MS = 15000;
const SPEECH_STEP_TIMEOUT_MAX_MS = 30000;
const TIMING_MARKER_LABELS = {
  server_request_received: "Server nhận request",
  manager_started: "Manager bắt đầu",
  manager_finished: "Manager kết thúc",
  music_started: "Music bắt đầu",
  music_finished: "Music kết thúc",
  first_text_delta_sent: "Server bắt đầu gửi text_delta",
  first_text_delta_received: "Frontend nhận text_delta",
  first_text_rendered: "Text đầu tiên được render (TTFT)",
};
const sessionId = getOrCreateSessionId();
let state = {
  messages: [],
  active_panel: {},
  active_panel_revision: 0,
  has_active_panel: false,
};
let busy = false;
let renderedPanelRevision = null;
let renderedPanelSignature = null;
let streamingDraft = null;
const latencyMarkers = [];
let microphoneStream = null;
let microphoneContext = null;
let microphoneSource = null;
let microphoneProcessor = null;
let microphoneMuteGain = null;
let voiceSocket = null;
let speechSocket = null;
let voiceAwaitingTranscript = false;
let voiceSpeaking = false;
let speakerContext = null;
let speakerNextStartTime = 0;
const speakerSources = new Set();
let streamedSpeechReady = false;
let streamedSpeechInFlight = false;
const pendingSpeechTexts = [];
let streamedSpeechFinishTimer = null;
let speechReadyTimer = null;
let speechStepTimer = null;
let activeSpeechTurnId = null;
let speechUnavailable = false;
let weatherTemplateRoot = null;
let speechAudioChunkCount = 0;
let speechAudioByteCount = 0;

const AVATAR_STATES = new Set([
  "idle", "thinking", "speaking", "explain", "point_left", "point_right", "concerned",
]);

class AvatarController {
  constructor(element) {
    this.element = element;
  }

  setState(state) {
    const safeState = AVATAR_STATES.has(state) ? state : "idle";
    this.element.dataset.avatarState = safeState;
    this.element.setAttribute("aria-label", `Lumi ${this.describeState(safeState)}`);
  }

  describeState(state) {
    return {
      idle: "đang chờ",
      thinking: "đang chuẩn bị trả lời",
      speaking: "đang nói",
      explain: "đang giải thích",
      point_left: "đang chỉ sang trái",
      point_right: "đang chỉ sang phải",
      concerned: "đang nhấn mạnh lưu ý",
    }[state];
  }
}

const avatarController = new AvatarController(presentationAvatar);

/**
 * Reads a non-presentation response through the Gemini Live voice gateway.
 * Presentation narration itself always uses Live CTC below.
 */
class GeminiLiveResponseSpeaker {
  constructor() {
    this.socket = null;
    this.turnId = null;
    this.requestId = 0;
    this.started = false;
  }

  speak({ text, onStart, onEnd, onError }) {
    if (!text?.trim() || typeof WebSocket === "undefined") {
      onError?.(new Error("Gemini Live TTS is unavailable."));
      return false;
    }
    this.cancel();
    const requestId = ++this.requestId;
    const turnId = newSpeechTurnId();
    this.turnId = turnId;
    this.started = false;
    const socket = new WebSocket(voiceSocketUrl());
    this.socket = socket;
    socket.binaryType = "arraybuffer";
    socket.addEventListener("open", () => {
      if (requestId !== this.requestId) return;
      socket.send(JSON.stringify({ type: "voice:speech_start", session_id: sessionId, turn_id: turnId }));
    });
    socket.addEventListener("message", (event) => {
      if (requestId !== this.requestId) return;
      if (event.data instanceof ArrayBuffer) {
        if (!this.started) {
          this.started = true;
          voiceSpeaking = true;
          avatarController.setState("speaking");
          onStart?.();
        }
        playPcmChunk(event.data);
        return;
      }
      const message = JSON.parse(event.data);
      if (message.type === "voice_speech_ready" && message.turn_id === turnId) {
        socket.send(JSON.stringify({ type: "voice:speak", turn_id: turnId, text: text.trim() }));
      } else if (message.type === "voice_speech_end" && message.turn_id === turnId) {
        const waitMs = Math.max(0, (speakerNextStartTime - speakerContext?.currentTime || 0) * 1000);
        window.setTimeout(() => {
          if (requestId !== this.requestId) return;
          this.cancel();
          onEnd?.();
        }, waitMs + 30);
      } else if (message.type === "voice_error") {
        this.cancel();
        onError?.(new Error(message.message || "Gemini Live TTS failed."));
      }
    });
    socket.addEventListener("error", () => {
      if (requestId !== this.requestId) return;
      this.cancel();
      onError?.(new Error("Không thể kết nối Gemini Live TTS."));
    });
    return true;
  }

  cancel() {
    this.requestId += 1;
    if (this.socket && this.socket.readyState < WebSocket.CLOSING) this.socket.close();
    this.socket = null;
    this.turnId = null;
    this.started = false;
    stopSpeakerAudio();
  }
}

const responseSpeaker = new GeminiLiveResponseSpeaker();

function reportPresentationDebug(event, detail = {}) {
  if (!['localhost', '127.0.0.1', '::1'].includes(window.location.hostname)) return;
  const payload = JSON.stringify({ session_id: sessionId, event, detail });
  fetch('/api/presentation/debug', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload,
    keepalive: true,
  }).catch(() => {});
}

class PresentationOverlayController {
  constructor({ host, overlay }) {
    this.host = host;
    this.overlay = overlay;
    this.templateRoot = null;
    this.activeStep = null;
    this.activeTarget = null;
    this.dimmedTargets = [];
    this.overlayAnimationFrames = new Set();
    this.overlayTimers = new Set();
    this.tracedPaths = [];
    this.resizeObserver = new ResizeObserver(() => this.redraw());
    window.addEventListener("resize", () => this.redraw());
  }

  setTemplateRoot(templateRoot) {
    this.clear();
    this.resizeObserver.disconnect();
    this.templateRoot = templateRoot;
    if (templateRoot) {
      this.installPresentationStyles(templateRoot);
      this.resizeObserver.observe(this.host);
    }
  }

  apply(step) {
    this.clear();
    if (!this.templateRoot || !step || typeof step.target_id !== "string") {
      reportPresentationDebug('overlay_skipped', { reason: 'template_or_step_missing' });
      return false;
    }
    const actions = Array.isArray(step.actions) && step.actions.length
      ? step.actions
      : [{ target_ids: [step.target_id], effect: step.effect, start_ms: 0, duration_ms: 900, payload: {} }];
    let accepted = false;
    actions.forEach((action) => {
      const delay = Math.max(0, Number(action.start_ms) || 0);
      const applyAction = () => { accepted = this.applyAction(action, step) || accepted; };
      if (delay) {
        const timer = window.setTimeout(() => {
          this.overlayTimers.delete(timer);
          applyAction();
        }, delay);
        this.overlayTimers.add(timer);
      } else applyAction();
    });
    return accepted || actions.some((action) => (Number(action.start_ms) || 0) > 0);
  }

  applyAction(action, step) {
    const targetIds = Array.isArray(action?.target_ids) ? action.target_ids : [];
    const targets = targetIds.map((targetId) => this.templateRoot.querySelector(
      `[data-present-id="${CSS.escape(targetId)}"]`,
    )).filter(Boolean);
    if (!targets.length) {
      reportPresentationDebug('overlay_skipped', { reason: 'action_targets_missing', target_ids: targetIds });
      return false;
    }
    if (action.effect === "draw_group_bracket") {
      this.drawGroupBracket(targets, action.payload || {});
      return true;
    }
    if (action.effect === "trace_chart_segment") {
      this.traceTemperatureLine(targets[0], action.payload?.point_indices);
      return true;
    }
    if (action.effect === "draw_temperature_range") {
      this.drawTemperatureRange(targets[0]);
      return true;
    }
    const target = targets[0];

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const effect = reducedMotion && ["pulse", "draw_circle", "draw_arrow", "trace_line"].includes(action.effect)
      ? "highlight"
      : action.effect;
    this.activeStep = { ...step, target_id: targetIds[0], effect };
    this.activeTarget = target;
    target.classList.add(`lumi-present-${effect}`);

    if (effect === "dim_others") {
      this.dimmedTargets = [...this.templateRoot.querySelectorAll("[data-present-id]")]
        .filter((element) => element !== target && !target.contains(element));
      this.dimmedTargets.forEach((element) => element.classList.add("lumi-present-dim"));
    }
    if (effect === "trace_line") this.traceTemperatureLine(target);
    this.redraw();
    reportPresentationDebug('overlay_applied', { target_id: targetIds[0], effect, gesture: step.gesture });
    return true;
  }

  drawGroupBracket(targets) {
    const containerRect = this.host.getBoundingClientRect();
    const rects = targets.map((target) => target.getBoundingClientRect()).filter((rect) => rect.width && rect.height);
    if (!rects.length) return;
    const left = Math.min(...rects.map((rect) => rect.left)) - containerRect.left - 5;
    const top = Math.min(...rects.map((rect) => rect.top)) - containerRect.top - 5;
    const right = Math.max(...rects.map((rect) => rect.right)) - containerRect.left + 5;
    const bottom = Math.max(...rects.map((rect) => rect.bottom)) - containerRect.top + 5;
    this.overlay.setAttribute("viewBox", `0 0 ${containerRect.width} ${containerRect.height}`);
    const bracket = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    bracket.setAttribute("class", "lumi-overlay-shape lumi-overlay-group-bracket");
    bracket.setAttribute("x", String(left)); bracket.setAttribute("y", String(top));
    bracket.setAttribute("width", String(right - left)); bracket.setAttribute("height", String(bottom - top));
    bracket.setAttribute("rx", "14"); bracket.setAttribute("pathLength", "100");
    this.overlay.appendChild(bracket);
  }

  drawTemperatureRange(target) {
    const points = [...target.querySelectorAll('[data-present-id^="weather.temperature_trend.point."]')];
    const containerRect = this.host.getBoundingClientRect();
    const rects = points.map((point) => point.getBoundingClientRect()).filter((rect) => rect.width && rect.height);
    if (rects.length < 2) return;
    const left = Math.min(...rects.map((rect) => rect.left)) - containerRect.left - 10;
    const right = Math.max(...rects.map((rect) => rect.right)) - containerRect.left + 10;
    const top = Math.min(...rects.map((rect) => rect.top)) - containerRect.top - 12;
    const bottom = Math.max(...rects.map((rect) => rect.bottom)) - containerRect.top + 12;
    this.overlay.setAttribute("viewBox", `0 0 ${containerRect.width} ${containerRect.height}`);
    const range = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    range.setAttribute("class", "lumi-overlay-temperature-range");
    range.setAttribute("x", String(left)); range.setAttribute("y", String(top));
    range.setAttribute("width", String(right - left)); range.setAttribute("height", String(bottom - top));
    range.setAttribute("rx", "10");
    this.overlay.appendChild(range);
  }

  clear() {
    this.cancelOverlayAnimations();
    this.restoreTracedPaths();
    this.overlay.replaceChildren();
    this.activeTarget?.classList.remove(
      "lumi-present-reveal",
      "lumi-present-highlight",
      "lumi-present-pulse",
      "lumi-present-dim_others",
      "lumi-present-draw_circle",
      "lumi-present-draw_arrow",
      "lumi-present-trace_line",
    );
    this.dimmedTargets.forEach((element) => element.classList.remove("lumi-present-dim"));
    this.activeStep = null;
    this.activeTarget = null;
    this.dimmedTargets = [];
  }

  redraw() {
    if (!this.activeTarget || !this.activeStep) return;
    this.cancelOverlayAnimations();
    this.overlay.replaceChildren();
    if (!["draw_circle", "draw_arrow"].includes(this.activeStep.effect)) return;

    const containerRect = this.host.getBoundingClientRect();
    const targetRect = this.activeTarget.getBoundingClientRect();
    if (!containerRect.width || !containerRect.height || !targetRect.width || !targetRect.height) return;
    this.overlay.setAttribute("viewBox", `0 0 ${containerRect.width} ${containerRect.height}`);
    const x = targetRect.left - containerRect.left;
    const y = targetRect.top - containerRect.top;
    const namespace = "http://www.w3.org/2000/svg";

    if (this.activeStep.effect === "draw_circle") {
      const circle = document.createElementNS(namespace, "ellipse");
      circle.setAttribute("class", "lumi-overlay-shape lumi-overlay-draw-circle");
      circle.setAttribute("cx", String(x + targetRect.width / 2));
      circle.setAttribute("cy", String(y + targetRect.height / 2));
      circle.setAttribute("rx", String(Math.max(12, targetRect.width / 2 + 8)));
      circle.setAttribute("ry", String(Math.max(12, targetRect.height / 2 + 8)));
      circle.setAttribute("pathLength", "100");
      this.overlay.appendChild(circle);
      return;
    }

    const startX = Math.max(14, x - Math.min(90, Math.max(38, targetRect.width * 0.35)));
    const startY = Math.max(18, y + targetRect.height * 0.2);
    const endX = Math.max(14, x + 6);
    const endY = Math.max(18, y + targetRect.height * 0.5);
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("class", "lumi-overlay-shape lumi-overlay-draw-arrow");
    path.setAttribute("pathLength", "100");
    path.setAttribute("d", `M ${startX} ${startY} Q ${(startX + endX) / 2} ${startY - 24} ${endX} ${endY}`);
    const arrowHead = document.createElementNS(namespace, "path");
    arrowHead.setAttribute("class", "lumi-overlay-arrow-head");
    arrowHead.setAttribute("d", `M ${endX} ${endY} l -10 -5 l 2 10 Z`);
    const pointer = document.createElementNS(namespace, "g");
    pointer.setAttribute("class", "lumi-overlay-pointer");
    pointer.setAttribute("aria-hidden", "true");
    const pen = document.createElementNS(namespace, "path");
    pen.setAttribute("d", "M -20 -7 L -5 -7 L 4 0 L -5 7 L -20 7 Z");
    const penTip = document.createElementNS(namespace, "path");
    penTip.setAttribute("d", "M 4 0 L -5 -7 L -1 0 L -5 7 Z");
    pointer.append(pen, penTip);
    this.overlay.append(path, arrowHead, pointer);
    this.animatePointer(pointer, startX, startY, endX, endY);
  }

  cancelOverlayAnimations() {
    this.overlayAnimationFrames.forEach((frame) => window.cancelAnimationFrame(frame));
    this.overlayAnimationFrames.clear();
    this.overlayTimers.forEach((timer) => window.clearTimeout(timer));
    this.overlayTimers.clear();
  }

  traceTemperatureLine(target, pointIndices = null) {
    const path = target.querySelector('[data-present-id$=".line"]');
    if (!path || typeof path.getTotalLength !== "function") return;
    const length = path.getTotalLength();
    if (!Number.isFinite(length) || length <= 0) return;
    this.tracedPaths.push({
      path,
      strokeDasharray: path.style.strokeDasharray,
      strokeDashoffset: path.style.strokeDashoffset,
      transition: path.style.transition,
    });
    path.style.strokeDasharray = String(length);
    path.style.strokeDashoffset = String(length);
    path.style.transition = "none";
    void path.getBoundingClientRect();
    const frame = window.requestAnimationFrame(() => {
      this.overlayAnimationFrames.delete(frame);
      path.style.transition = "stroke-dashoffset 1000ms cubic-bezier(.22, .8, .3, 1)";
      path.style.strokeDashoffset = "0";
    });
    this.overlayAnimationFrames.add(frame);
    const timer = window.setTimeout(() => {
      this.overlayTimers.delete(timer);
      this.drawTemperatureExtremes(target, pointIndices);
    }, 680);
    this.overlayTimers.add(timer);
  }

  drawTemperatureExtremes(target, pointIndices = null) {
    const markers = Array.isArray(pointIndices) && pointIndices.length
      ? pointIndices.map((index) => target.querySelector(`[data-present-id="weather.temperature_trend.point.${index}"]`)).filter(Boolean)
      : [...target.querySelectorAll('[data-temperature-extreme="true"]')];
    if (!markers.length) return;
    const containerRect = this.host.getBoundingClientRect();
    const namespace = "http://www.w3.org/2000/svg";
    for (const marker of markers) {
      const rect = marker.getBoundingClientRect();
      if (!rect.width || !rect.height) continue;
      const ellipse = document.createElementNS(namespace, "ellipse");
      ellipse.setAttribute("class", "lumi-overlay-shape lumi-overlay-draw-circle");
      ellipse.setAttribute("cx", String(rect.left - containerRect.left + rect.width / 2));
      ellipse.setAttribute("cy", String(rect.top - containerRect.top + rect.height / 2));
      ellipse.setAttribute("rx", String(Math.max(10, rect.width / 2 + 7)));
      ellipse.setAttribute("ry", String(Math.max(10, rect.height / 2 + 7)));
      ellipse.setAttribute("pathLength", "100");
      this.overlay.appendChild(ellipse);
    }
  }

  restoreTracedPaths() {
    for (const item of this.tracedPaths) {
      item.path.style.strokeDasharray = item.strokeDasharray;
      item.path.style.strokeDashoffset = item.strokeDashoffset;
      item.path.style.transition = item.transition;
    }
    this.tracedPaths = [];
  }

  animatePointer(pointer, startX, startY, endX, endY) {
    const durationMs = 760;
    const angle = Math.atan2(endY - startY, endX - startX) * (180 / Math.PI);
    const startedAt = performance.now();
    let scheduledFrame = null;
    const tick = (now) => {
      if (scheduledFrame !== null) this.overlayAnimationFrames.delete(scheduledFrame);
      const progress = Math.min(1, (now - startedAt) / durationMs);
      const eased = 1 - ((1 - progress) ** 3);
      const x = startX + (endX - startX) * eased;
      const y = startY + (endY - startY) * eased;
      pointer.setAttribute("transform", `translate(${x} ${y}) rotate(${angle})`);
      pointer.style.opacity = String(Math.min(1, progress * 5));
      if (progress < 1) {
        scheduledFrame = window.requestAnimationFrame(tick);
        this.overlayAnimationFrames.add(scheduledFrame);
      } else {
        window.setTimeout(() => pointer.remove(), 180);
      }
    };
    scheduledFrame = window.requestAnimationFrame(tick);
    this.overlayAnimationFrames.add(scheduledFrame);
  }

  installPresentationStyles(templateRoot) {
    if (templateRoot.querySelector("#lumi-presentation-styles")) return;
    const styles = document.createElement("style");
    styles.id = "lumi-presentation-styles";
    styles.textContent = `
      [data-present-id] { transition: opacity 180ms ease, box-shadow 180ms ease, outline-color 180ms ease, transform 180ms ease; }
      .lumi-present-reveal, .lumi-present-highlight, .lumi-present-dim_others { outline: 3px solid rgba(14, 165, 233, .95); outline-offset: 4px; box-shadow: 0 0 0 7px rgba(14, 165, 233, .16); }
      .lumi-present-pulse { outline: 3px solid rgba(14, 165, 233, .95); outline-offset: 4px; animation: lumi-present-pulse 1.1s ease-in-out 2; }
      .lumi-present-dim { opacity: .32; }
      @keyframes lumi-present-pulse { 50% { box-shadow: 0 0 0 13px rgba(14, 165, 233, 0); transform: translateY(-1px); } }
      @media (prefers-reduced-motion: reduce) { [data-present-id] { transition: none; animation: none !important; } }
    `;
    templateRoot.appendChild(styles);
  }
}

const presentationController = new PresentationOverlayController({
  host: weatherView,
  overlay: presentationOverlay,
});


/**
 * Receives one PCM stream for a completed presentation. Audio is kept ahead
 * of playback by an initial buffer; CTC's measured scene timestamps then
 * schedule DOM effects against the same AudioContext clock.
 */
class LiveCtcPresentationPlayer {
  constructor() {
    this.requestId = 0;
    this.socket = null;
    this.context = null;
    this.sources = new Set();
    this.timers = new Set();
    this.pendingAudio = [];
    this.audioStartAt = null;
    this.audioQueuedSeconds = 0;
    this.audioScheduledSeconds = 0;
    this.sampleRate = null;
    this.prebufferSeconds = 8;
    this.scenes = new Map();
    this.pendingEvents = [];
    this.confirmedScenes = new Map();
    this.startedScenes = new Set();
    this.completed = false;
    this.onSceneStart = null;
    this.onComplete = null;
  }

  reset() {
    this.requestId += 1;
    if (this.socket && this.socket.readyState < WebSocket.CLOSING) this.socket.close();
    this.socket = null;
    this.sources.forEach((source) => { try { source.stop(); } catch (_) {} });
    this.sources.clear();
    this.timers.forEach((timer) => window.clearTimeout(timer));
    this.timers.clear();
    this.pendingAudio = [];
    this.audioStartAt = null;
    this.audioQueuedSeconds = 0;
    this.audioScheduledSeconds = 0;
    this.sampleRate = null;
    this.scenes.clear();
    this.pendingEvents = [];
    this.confirmedScenes.clear();
    this.startedScenes.clear();
    this.completed = false;
    presentationController.clear();
  }

  arm() {
    if (!this.context) this.context = new (window.AudioContext || window.webkitAudioContext)();
    this.context.resume().catch((error) => reportPresentationDebug("live_ctc_audio_resume_failed", { message: error.message || "unknown" }));
  }

  start(contract, { onSceneStart, onComplete } = {}) {
    if (!contract || !Array.isArray(contract.scenes) || !contract.scenes.length) return false;
    this.reset();
    this.arm();
    const requestId = ++this.requestId;
    this.onSceneStart = typeof onSceneStart === "function" ? onSceneStart : null;
    this.onComplete = typeof onComplete === "function" ? onComplete : null;
    this.prebufferSeconds = Math.max(0, Number(contract.prebuffer_ms || 8000) / 1000);
    contract.scenes.forEach((scene, index) => this.scenes.set(`scene-${index}`, scene));
    const presentationId = window.crypto?.randomUUID?.() || `presentation-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const socket = new WebSocket(presentationSocketUrl());
    this.socket = socket;
    socket.binaryType = "arraybuffer";
    socket.addEventListener("open", () => {
      if (requestId !== this.requestId) return;
      socket.send(JSON.stringify({ type: "presentation:start", presentation_id: presentationId, scenes: contract.scenes }));
    });
    socket.addEventListener("message", (event) => {
      if (requestId !== this.requestId) return;
      if (event.data instanceof ArrayBuffer) {
        this.receivePcm(event.data);
        return;
      }
      const message = JSON.parse(event.data);
      if (message.type === "presentation_audio_format") {
        this.sampleRate = Number(message.sample_rate_hz);
        reportPresentationDebug("live_ctc_audio_format", { sample_rate_hz: this.sampleRate, prebuffer_ms: Math.round(this.prebufferSeconds * 1000) });
      } else if (message.type === "scene_confirmed") {
        this.receiveSceneConfirmed(message);
      } else if (message.type === "presentation_complete") {
        this.completed = true;
        this.tryStartPlayback();
        this.maybeFinish();
      } else if (message.type === "presentation_error") {
        reportPresentationDebug("live_ctc_error", { message: message.message || "unknown" });
        this.reset();
        setVoiceStatus(message.message || "KhÃ´ng thá»ƒ Ä‘á»“ng bá»™ giÃ´ng nÃ³i vÃ  hoáº¡t há»a.", "error");
      }
    });
    socket.addEventListener("error", () => {
      if (requestId === this.requestId) reportPresentationDebug("live_ctc_socket_error");
    });
    reportPresentationDebug("live_ctc_started", { scenes: contract.scenes.length, prebuffer_ms: Math.round(this.prebufferSeconds * 1000) });
    return true;
  }

  receivePcm(bytes) {
    if (!this.sampleRate || !bytes.byteLength) return;
    this.pendingAudio.push(bytes);
    const seconds = bytes.byteLength / 2 / this.sampleRate;
    this.audioQueuedSeconds += seconds;
    if (this.audioStartAt === null) {
      this.tryStartPlayback();
      return;
    }
    this.schedulePcm(bytes);
  }

  schedulePcm(bytes) {
    const pcm = new Int16Array(bytes);
    const buffer = this.context.createBuffer(1, pcm.length, this.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < pcm.length; index += 1) channel[index] = pcm[index] / 0x8000;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    const startAt = this.audioStartAt + (this.audioScheduledSeconds || 0);
    this.audioScheduledSeconds = (this.audioScheduledSeconds || 0) + buffer.duration;
    source.start(startAt);
    this.sources.add(source);
    source.addEventListener("ended", () => this.sources.delete(source), { once: true });
  }

  receiveSceneConfirmed(event) {
    this.confirmedScenes.set(event.scene_id, event);
    this.pendingEvents.push(event);
    this.tryStartPlayback();
    this.flushSceneEvents();
  }

  // Do not start audio until CTC has measured scene 0.  Otherwise its visual
  // cue can arrive after the corresponding speech has already begun. A short,
  // completed presentation may start below the normal prebuffer threshold.
  tryStartPlayback() {
    if (this.audioStartAt !== null || !this.context || !this.confirmedScenes.has("scene-0")) return;
    const enoughAudio = this.audioQueuedSeconds >= this.prebufferSeconds;
    if (!enoughAudio && !this.completed) {
      reportPresentationDebug("live_ctc_waiting_for_prebuffer", {
        buffered_ms: Math.round(this.audioQueuedSeconds * 1000),
        prebuffer_ms: Math.round(this.prebufferSeconds * 1000),
        scene_0_confirmed: true,
      });
      return;
    }

    this.audioStartAt = this.context.currentTime + 0.05;
    voiceSpeaking = true;
    setVoiceStatus("Dang doc phan trinh bay...");
    reportPresentationDebug("live_ctc_prebuffer_ready", {
      buffered_ms: Math.round(this.audioQueuedSeconds * 1000),
      prebuffer_ms: Math.round(this.prebufferSeconds * 1000),
      short_presentation: !enoughAudio,
      scene_0_confirmed: true,
    });
    for (const chunk of this.pendingAudio) this.schedulePcm(chunk);
    this.pendingAudio = [];
    this.flushSceneEvents();
  }

  flushSceneEvents() {
    if (this.audioStartAt === null) return;
    const events = this.pendingEvents.splice(0);
    events.forEach((event) => this.scheduleScene(event));
  }

  scheduleScene(event) {
    const scene = this.scenes.get(event.scene_id);
    if (!scene || this.startedScenes.has(event.scene_id)) return;
    const audioStartMs = Number(event.start_ms);
    const audioEndMs = Number(event.end_ms);
    if (!Number.isFinite(audioStartMs) || !Number.isFinite(audioEndMs)) return;
    const startsAt = this.audioStartAt + audioStartMs / 1000;
    const delayMs = Math.max(0, (startsAt - this.context.currentTime) * 1000);
    const timer = window.setTimeout(() => {
      this.timers.delete(timer);
      this.startedScenes.add(event.scene_id);
      this.onSceneStart?.(scene);
      presentationController.apply(scene);
      avatarController.setState(scene.gesture);
      reportPresentationDebug("live_ctc_scene_started", { scene_id: event.scene_id, start_ms: audioStartMs, end_ms: audioEndMs });
      this.scheduleSceneEnd(event, scene);
    }, delayMs);
    this.timers.add(timer);
    reportPresentationDebug("live_ctc_scene_scheduled", { scene_id: event.scene_id, lead_ms: Math.round(delayMs) });
  }

  scheduleSceneEnd(event, scene) {
    const endsAt = this.audioStartAt + Number(event.end_ms) / 1000;
    const delayMs = Math.max(0, (endsAt - this.context.currentTime) * 1000);
    const timer = window.setTimeout(() => {
      this.timers.delete(timer);
      if (this.startedScenes.has(event.scene_id)) presentationController.clear();
      if (scene.gesture) avatarController.setState("idle");
      this.maybeFinish();
    }, delayMs + 80);
    this.timers.add(timer);
  }

  maybeFinish() {
    if (!this.completed || this.startedScenes.size < this.scenes.size) return;
    const timer = window.setTimeout(() => {
      this.timers.delete(timer);
      voiceSpeaking = false;
      avatarController.setState("idle");
      this.onComplete?.();
    }, 120);
    this.timers.add(timer);
  }
}

const liveCtcPresentation = new LiveCtcPresentationPlayer();

if (["localhost", "127.0.0.1", "::1"].includes(window.location.hostname)) {
  window.__lumiPresentationDebug = {
    apply: (step) => presentationController.apply(step),
    clear: () => presentationController.clear(),
    setAvatarState: (state) => avatarController.setState(state),
  };
}

function getOrCreateSessionId() {
  const existing = window.localStorage.getItem(SESSION_KEY);
  if (existing) return existing;
  const value = window.crypto?.randomUUID?.() || `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  window.localStorage.setItem(SESSION_KEY, value);
  return value;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "Không thể kết nối tới ứng dụng.");
  return payload;
}

async function requestNdjson(url, options = {}, onEvent = () => {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.message || "Không thể kết nối tới ứng dụng.");
  }
  if (!response.body) throw new Error("Trình duyệt không hỗ trợ luồng trả lời.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line));
    }
    if (done) break;
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}

function render({ preserveMessages = false } = {}) {
  const panel = getActivePanel();
  const hasDashboard = isValidPanel(panel);
  const hasMessages = Boolean(state.messages?.length);
  workspace.classList.toggle("has-dashboard", hasDashboard);
  workspace.classList.toggle("no-dashboard", !hasDashboard);
  workspace.classList.toggle("has-messages", hasMessages);
  contentPanel.hidden = !hasDashboard;
  welcome.hidden = hasDashboard || hasMessages;

  const revision = Number.isInteger(state.active_panel_revision)
    ? state.active_panel_revision
    : 0;
  const panelSignature = hasDashboard ? JSON.stringify(panel) : null;
  if (hasDashboard && panelSignature !== renderedPanelSignature) {
    renderActivePanel(panel);
    renderedPanelRevision = revision;
    renderedPanelSignature = panelSignature;
  } else if (hasDashboard) {
    renderedPanelRevision = revision;
  } else if (!hasDashboard && renderedPanelRevision !== null) {
    clearActivePanel();
    renderedPanelRevision = null;
    renderedPanelSignature = null;
  }

  if (!preserveMessages) {
    messagesElement.replaceChildren();
    for (const message of state.messages || []) {
      messagesElement.appendChild(createMessage(message.role, message.content));
    }
    if (streamingDraft !== null) {
      messagesElement.appendChild(createMessage("assistant", streamingDraft, true));
    } else if (busy) {
      messagesElement.appendChild(createTypingMessage());
    }
  }

  suggestions.classList.toggle("hidden", hasMessages || busy);
  connectionStatus.textContent = streamingDraft !== null
    ? "Đang trả lời..."
    : busy
      ? "Đang xử lý..."
      : "Sẵn sàng";
  connectionStatus.classList.toggle("busy", busy);
  renderFirstTextLog();
  queryInput.disabled = busy;
  sendButton.disabled = busy;
  microphoneButton.disabled = busy || voiceAwaitingTranscript;
  window.requestAnimationFrame(() => {
    messagesElement.scrollTop = messagesElement.scrollHeight;
  });
}

function setVoiceStatus(text, mode = "idle") {
  voiceStatus.textContent = text;
  voiceStatus.classList.toggle("listening", mode === "listening");
  voiceStatus.classList.toggle("error", mode === "error");
  microphoneButton.classList.toggle("listening", mode === "listening");
  microphoneButton.setAttribute("aria-pressed", String(mode === "listening"));
  microphoneButton.setAttribute("aria-label", mode === "listening" ? "Dừng ghi âm" : "Bắt đầu nói");
  microphoneButton.title = mode === "listening" ? "Dừng ghi âm" : "Nói với Lumi";
}

function releaseMicrophone() {
  for (const track of microphoneStream?.getTracks?.() || []) track.stop();
  microphoneProcessor?.disconnect();
  microphoneSource?.disconnect();
  microphoneMuteGain?.disconnect();
  microphoneContext?.close?.();
  microphoneStream = null;
  microphoneContext = null;
  microphoneSource = null;
  microphoneProcessor = null;
  microphoneMuteGain = null;
}

function closeVoiceSocket() {
  if (voiceSocket && voiceSocket.readyState < WebSocket.CLOSING) voiceSocket.close();
  voiceSocket = null;
}

function stopSpeakerAudio() {
  for (const source of speakerSources) source.stop();
  speakerSources.clear();
  speakerContext?.close?.();
  speakerContext = null;
  speakerNextStartTime = 0;
  voiceSpeaking = false;
  avatarController.setState("idle");
}

function pcmSampleRate(mimeType) {
  const match = /rate=(\d+)/i.exec(mimeType || "");
  return match ? Number(match[1]) : 24000;
}

function playPcmChunk(arrayBuffer, mimeType = "audio/pcm;rate=24000") {
  if (!speakerContext) speakerContext = new AudioContext();
  const pcm = new Int16Array(arrayBuffer);
  if (!pcm.length) return;
  speechAudioChunkCount += 1;
  speechAudioByteCount += arrayBuffer.byteLength;
  avatarController.setState("speaking");
  const sampleRate = pcmSampleRate(mimeType);
  const buffer = speakerContext.createBuffer(1, pcm.length, sampleRate);
  const channel = buffer.getChannelData(0);
  for (let index = 0; index < pcm.length; index += 1) channel[index] = pcm[index] / 0x8000;
  const source = speakerContext.createBufferSource();
  source.buffer = buffer;
  source.connect(speakerContext.destination);
  const startAt = Math.max(speakerContext.currentTime, speakerNextStartTime);
  source.start(startAt);
  speakerNextStartTime = startAt + buffer.duration;
  speakerSources.add(source);
  source.addEventListener("ended", () => speakerSources.delete(source), { once: true });
}

function newSpeechTurnId() {
  return window.crypto?.randomUUID?.() || `speech-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function startSpeechReadyTimer(turnId) {
  if (speechReadyTimer !== null) window.clearTimeout(speechReadyTimer);
  speechReadyTimer = window.setTimeout(() => {
    if (streamedSpeechReady || activeSpeechTurnId !== turnId) return;
    speechUnavailable = true;
    stopSpeakerAudio();
    closeSpeechSocket();
    microphoneButton.disabled = busy || voiceAwaitingTranscript;
    setVoiceStatus("Không thể khởi tạo giọng nói; câu trả lời vẫn hiển thị bằng văn bản.", "error");
  }, SPEECH_READY_TIMEOUT_MS);
}

function resetStreamedSpeechState() {
  if (speechReadyTimer !== null) window.clearTimeout(speechReadyTimer);
  if (speechStepTimer !== null) window.clearTimeout(speechStepTimer);
  if (streamedSpeechFinishTimer !== null) window.clearTimeout(streamedSpeechFinishTimer);
  speechReadyTimer = null;
  speechStepTimer = null;
  streamedSpeechFinishTimer = null;
  streamedSpeechReady = false;
  streamedSpeechInFlight = false;
  activeSpeechTurnId = null;
  pendingSpeechTexts.length = 0;
}

function finishStreamedSpeechAfterPlayback() {
  if (streamedSpeechFinishTimer !== null) return;
  const remainingMs = Math.max(0, (speakerNextStartTime - speakerContext?.currentTime || 0) * 1000);
  streamedSpeechFinishTimer = window.setTimeout(() => {
    stopSpeakerAudio();
    closeSpeechSocket();
    resetStreamedSpeechState();
    setVoiceStatus("Nhấn micro để nói");
    microphoneButton.disabled = busy || voiceAwaitingTranscript;
  }, remainingMs + 50);
}

function completeGeminiLiveSpeechAfterPlayback() {
  if (streamedSpeechFinishTimer !== null) return;
  const remainingMs = Math.max(0, (speakerNextStartTime - speakerContext?.currentTime || 0) * 1000);
  streamedSpeechFinishTimer = window.setTimeout(() => {
    streamedSpeechFinishTimer = null;
    if (!voiceSpeaking) return;
    if (pendingSpeechTexts.length) {
      sendCompletedSpeech();
    } else {
      stopSpeakerAudio();
      closeSpeechSocket();
      resetStreamedSpeechState();
      setVoiceStatus("Nhấn micro để nói");
      microphoneButton.disabled = busy || voiceAwaitingTranscript;
    }
  }, remainingMs + 30);
}

function speechStepTimeoutMs(text) {
  return Math.min(
    SPEECH_STEP_TIMEOUT_MAX_MS,
    Math.max(SPEECH_STEP_TIMEOUT_MIN_MS, 7000 + text.length * 180),
  );
}

function startSpeechStepTimer(text) {
  if (speechStepTimer !== null) window.clearTimeout(speechStepTimer);
  const timeoutMs = speechStepTimeoutMs(text);
  speechStepTimer = window.setTimeout(() => {
    speechStepTimer = null;
    if (!streamedSpeechInFlight) return;
    reportPresentationDebug('tts_step_timeout', {
      chars: text.length,
      audio_chunks: speechAudioChunkCount,
      audio_bytes: speechAudioByteCount,
      timeout_ms: timeoutMs,
    });
    // Do not send another request into a Live session whose prior turn is stuck.
    speechUnavailable = true;
    pendingSpeechTexts.length = 0;
    streamedSpeechInFlight = false;
    stopSpeakerAudio();
    closeSpeechSocket();
    resetStreamedSpeechState();
    setVoiceStatus("Giọng nói không phản hồi kịp; phần trình bày vẫn tiếp tục trên màn hình.", "error");
    microphoneButton.disabled = busy || voiceAwaitingTranscript;
  }, timeoutMs);
}

function sendCompletedSpeech() {
  if (!streamedSpeechReady || streamedSpeechInFlight || !pendingSpeechTexts.length || !speechSocket || speechSocket.readyState !== WebSocket.OPEN) return;
  const text = pendingSpeechTexts.shift();
  streamedSpeechInFlight = true;
  speechAudioChunkCount = 0;
  speechAudioByteCount = 0;
  reportPresentationDebug('tts_requested', { chars: text.length, queued_speech: pendingSpeechTexts.length });
  speechSocket.send(JSON.stringify({ type: "voice:speak", turn_id: activeSpeechTurnId, text }));
  startSpeechStepTimer(text);
}

function startStreamedSpeech({ startReadyTimeout = true } = {}) {
  if (voiceSpeaking) return;
  resetStreamedSpeechState();
  stopSpeakerAudio();
  closeSpeechSocket();
  voiceSpeaking = true;
  avatarController.setState("thinking");
  speechUnavailable = false;
  activeSpeechTurnId = newSpeechTurnId();
  if (startReadyTimeout) startSpeechReadyTimer(activeSpeechTurnId);
  setVoiceStatus("Đang chuẩn bị giọng nói…");
  const socket = new WebSocket(voiceSocketUrl());
  speechSocket = socket;
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => {
    if (speechSocket !== socket) return;
    socket.send(JSON.stringify({ type: "voice:speech_start", session_id: sessionId, turn_id: activeSpeechTurnId }));
  });
  socket.addEventListener("message", (event) => {
    if (event.data instanceof ArrayBuffer) {
      playPcmChunk(event.data);
      setVoiceStatus("Đang đọc câu trả lời…");
      return;
    }
    const message = JSON.parse(event.data);
    if (message.type === "voice_speech_ready") {
      if (message.turn_id !== activeSpeechTurnId) return;
      streamedSpeechReady = true;
      if (speechReadyTimer !== null) window.clearTimeout(speechReadyTimer);
      speechReadyTimer = null;
      reportPresentationDebug('tts_ready');
      sendCompletedSpeech();
    } else if (message.type === "voice_speech_end" && message.turn_id === activeSpeechTurnId) {
      if (speechStepTimer !== null) window.clearTimeout(speechStepTimer);
      speechStepTimer = null;
      streamedSpeechInFlight = false;
      reportPresentationDebug('tts_end_received', { audio_chunks: speechAudioChunkCount, audio_bytes: speechAudioByteCount });
      completeGeminiLiveSpeechAfterPlayback();
    } else if (message.type === "voice_error") {
      reportPresentationDebug('tts_error', { message: message.message || 'unknown' });
      speechUnavailable = true;
      stopSpeakerAudio();
      closeSpeechSocket();
      resetStreamedSpeechState();
      setVoiceStatus(message.message || "Không thể đọc câu trả lời.", "error");
      microphoneButton.disabled = busy || voiceAwaitingTranscript;
    }
  });
  socket.addEventListener("error", () => {
    if (voiceSpeaking) {
      stopSpeakerAudio();
      closeSpeechSocket();
      resetStreamedSpeechState();
      setVoiceStatus("Không thể kết nối giọng nói.", "error");
      microphoneButton.disabled = busy || voiceAwaitingTranscript;
    }
  });
  socket.addEventListener("close", () => {
    if (speechSocket !== socket) return;
    speechSocket = null;
    if (!voiceSpeaking) return;
    reportPresentationDebug('tts_socket_closed', { in_flight: streamedSpeechInFlight, queued_speech: pendingSpeechTexts.length });
    stopSpeakerAudio();
    resetStreamedSpeechState();
    microphoneButton.disabled = busy || voiceAwaitingTranscript;
  });
}

function speakCompletedResponse(text) {
  if (!text?.trim()) return;
  if (speechUnavailable) return;
  responseSpeaker.speak({
    text,
    onStart: () => setVoiceStatus("Đang đọc câu trả lời…"),
    onEnd: () => {
      avatarController.setState("idle");
      microphoneButton.disabled = busy || voiceAwaitingTranscript;
    },
    onError: (error) => {
      speechUnavailable = true;
      setVoiceStatus(error.message || "Không thể đọc câu trả lời.", "error");
      microphoneButton.disabled = busy || voiceAwaitingTranscript;
    },
  });
}

function cancelStreamedSpeech() {
  responseSpeaker.cancel();
  stopSpeakerAudio();
  setVoiceStatus("Nhấn micro để nói");
}

function pcm16k(input, inputSampleRate) {
  const targetRate = 16000;
  const frameCount = Math.max(1, Math.round(input.length * targetRate / inputSampleRate));
  const output = new Int16Array(frameCount);
  const ratio = input.length / frameCount;
  for (let index = 0; index < frameCount; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(input.length, Math.max(start + 1, Math.floor((index + 1) * ratio)));
    let total = 0;
    for (let sample = start; sample < end; sample += 1) total += input[sample];
    const value = Math.max(-1, Math.min(1, total / (end - start)));
    output[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  return output.buffer;
}

function startPcmCapture() {
  microphoneContext = new AudioContext();
  microphoneSource = microphoneContext.createMediaStreamSource(microphoneStream);
  microphoneProcessor = microphoneContext.createScriptProcessor(4096, 1, 1);
  microphoneMuteGain = microphoneContext.createGain();
  microphoneMuteGain.gain.value = 0;
  microphoneProcessor.addEventListener("audioprocess", (event) => {
    if (voiceSocket?.readyState !== WebSocket.OPEN || voiceAwaitingTranscript) return;
    const input = event.inputBuffer.getChannelData(0);
    voiceSocket.send(pcm16k(input, event.inputBuffer.sampleRate));
  });
  microphoneSource.connect(microphoneProcessor);
  microphoneProcessor.connect(microphoneMuteGain);
  microphoneMuteGain.connect(microphoneContext.destination);
  setVoiceStatus("Đang nghe… Nhấn micro lần nữa để kết thúc.", "listening");
}

function endVoiceCapture() {
  if (!voiceSocket || voiceSocket.readyState !== WebSocket.OPEN || !microphoneStream) return;
  voiceAwaitingTranscript = true;
  releaseMicrophone();
  voiceSocket.send(JSON.stringify({ type: "voice:audio_end" }));
  setVoiceStatus("Đang nhận diện lời nói…");
  render();
}

function voiceSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/voice`;
}

function presentationSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/presentation`;
}

async function startVoiceTurn() {
  if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === "undefined" || typeof WebSocket === "undefined") {
    setVoiceStatus("Trình duyệt này không hỗ trợ voice realtime.", "error");
    return;
  }
  try {
    cancelStreamedSpeech();
    microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    voiceSocket = new WebSocket(voiceSocketUrl());
    voiceSocket.binaryType = "arraybuffer";
    voiceSocket.addEventListener("open", () => {
      voiceSocket.send(JSON.stringify({ type: "voice:start", session_id: sessionId }));
      setVoiceStatus("Đang kết nối nhận diện giọng nói…");
    });
    voiceSocket.addEventListener("message", async (event) => {
      if (event.data instanceof ArrayBuffer) {
        playPcmChunk(event.data);
        setVoiceStatus("Đang đọc câu trả lời…");
        return;
      }
      const message = JSON.parse(event.data);
      if (message.type === "voice_ready") {
        startPcmCapture();
      } else if (message.type === "voice_transcript") {
        setVoiceStatus(message.final ? "Đã nhận diện. Đang gửi yêu cầu…" : `Đang nghe: ${message.text}`, message.final ? "idle" : "listening");
        if (message.final && message.text) {
          voiceAwaitingTranscript = false;
          releaseMicrophone();
          closeVoiceSocket();
          microphoneButton.disabled = true;
          setVoiceStatus("Đang chuẩn bị giọng nói…");
          await submitQuery(message.text, { speakResponse: true });
        }
      } else if (message.type === "voice_error") {
        voiceAwaitingTranscript = false;
        releaseMicrophone();
        closeVoiceSocket();
        setVoiceStatus(message.message || "Không thể nhận diện giọng nói.", "error");
        render();
      }
    });
    voiceSocket.addEventListener("close", () => {
      if (!voiceAwaitingTranscript && microphoneStream) releaseMicrophone();
    });
    voiceSocket.addEventListener("error", () => {
      if (!voiceAwaitingTranscript) setVoiceStatus("Không thể kết nối Voice Gateway.", "error");
    });
  } catch (error) {
    releaseMicrophone();
    const denied = error?.name === "NotAllowedError" || error?.name === "SecurityError";
    setVoiceStatus(
      denied ? "Bạn chưa cấp quyền sử dụng micro." : "Không thể khởi tạo micro. Hãy thử lại.",
      "error",
    );
  }
}

function renderFirstTextLog() {
  if (!latencyMarkers.length) {
    firstTextLog.hidden = true;
    firstTextLog.textContent = "";
    return;
  }
  firstTextLog.textContent = latencyMarkers
    .map(({ label, elapsedMs, source }) => (
      `[${source}] ${label}: ${(elapsedMs / 1000).toFixed(2)} giây`
    ))
    .join("\n");
  firstTextLog.hidden = false;
}

function recordLatencyMarker(marker, elapsedMs, source) {
  if (!Number.isFinite(elapsedMs) || latencyMarkers.some((item) => item.marker === marker)) {
    return;
  }
  latencyMarkers.push({
    marker,
    elapsedMs,
    source,
    label: TIMING_MARKER_LABELS[marker] || marker,
  });
  renderFirstTextLog();
}

function getActivePanel() {
  if (state.active_panel && typeof state.active_panel === "object") {
    return state.active_panel;
  }
  if (state.visualization_html) {
    return { ui_type: "weather", html: state.visualization_html };
  }
  return {};
}

function isValidPanel(panel) {
  if (!panel || typeof panel !== "object") return false;
  if (panel.ui_type === "weather") return typeof panel.html === "string" && Boolean(panel.html);
  if (panel.ui_type !== "youtube_player") return false;
  const music = panel.music;
  return Boolean(
    music
    && typeof music === "object"
    && typeof music.video_id === "string"
    && YOUTUBE_VIDEO_ID_PATTERN.test(music.video_id)
    && ["play", "replay", "stop"].includes(panel.player_action)
  );
}

function renderActivePanel(panel) {
  if (panel.ui_type === "weather") {
    renderWeatherPanel(panel);
    return;
  }
  renderMusicPanel(panel);
}

function renderStreamedPanel(panel) {
  if (!isValidPanel(panel)) return;
  state = {
    ...state,
    active_panel: panel,
    has_active_panel: true,
  };
  render();
}

function renderWeatherPanel(panel) {
  musicFrame.removeAttribute("src");
  delete musicFrame.dataset.videoId;
  musicView.hidden = true;
  weatherView.hidden = false;
  contentEyebrow.textContent = "Kết quả trực quan";
  contentTitle.textContent = "Thông tin thời tiết";
  contentBadge.textContent = "Dữ liệu từ Redis";
  weatherTemplateRoot = renderWeatherTemplate(panel.html);
  presentationController.setTemplateRoot(weatherTemplateRoot);
}

function renderMusicPanel(panel) {
  const music = panel.music;
  clearWeatherTemplate();
  weatherView.hidden = true;
  musicView.hidden = false;
  contentEyebrow.textContent = panel.player_action === "stop" ? "Đã dừng" : "Đang phát";
  contentTitle.textContent = "Trình phát âm nhạc";
  contentBadge.textContent = "YouTube";
  musicTitle.textContent = typeof music.title === "string" ? music.title : "Bài hát";
  musicArtist.textContent = typeof music.artist === "string" ? music.artist : "";
  musicVersion.textContent = typeof music.version === "string" && music.version
    ? music.version
    : "YouTube";

  if (panel.player_action === "stop") {
    musicFrame.removeAttribute("src");
    delete musicFrame.dataset.videoId;
    musicFrame.hidden = true;
    musicStopped.hidden = false;
    return;
  }

  musicStopped.hidden = true;
  musicFrame.hidden = false;
  if (musicFrame.dataset.videoId !== music.video_id) {
    musicFrame.src = youtubeEmbedUrl(music.video_id);
    musicFrame.dataset.videoId = music.video_id;
  }
}

function youtubeEmbedUrl(videoId) {
  if (!YOUTUBE_VIDEO_ID_PATTERN.test(videoId)) return "";
  return `${YOUTUBE_NOCOOKIE_ORIGIN}/embed/${encodeURIComponent(videoId)}?autoplay=1&rel=0`;
}

function clearActivePanel() {
  clearWeatherTemplate();
  musicFrame.removeAttribute("src");
  delete musicFrame.dataset.videoId;
  weatherView.hidden = true;
  musicView.hidden = true;
}

function renderWeatherTemplate(html) {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  parsed.querySelectorAll("script, iframe, object, embed, base").forEach((element) => element.remove());
  parsed.querySelectorAll("*").forEach((element) => {
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim().toLowerCase();
      if (name.startsWith("on") || ((name === "href" || name === "src") && value.startsWith("javascript:"))) {
        element.removeAttribute(attribute.name);
      }
    }
  });

  const templateRoot = weatherTemplateHost.shadowRoot
    || weatherTemplateHost.attachShadow({ mode: "open" });
  const documentRoot = document.createElement("div");
  documentRoot.className = "weather-template-document";
  documentRoot.innerHTML = parsed.body.innerHTML;
  const templateStyles = [...parsed.head.querySelectorAll("style")].map((style) => {
    const scoped = document.createElement("style");
    scoped.textContent = style.textContent.replace(/\b(html|body)\b/g, ".weather-template-document");
    return scoped;
  });
  const hostStyles = document.createElement("style");
  hostStyles.textContent = `
    :host { display: block; width: 100%; height: 100%; min-height: 0; overflow: auto; background: #fff; }
    .weather-template-document { width: 100%; min-height: 100%; }
  `;
  templateRoot.replaceChildren(hostStyles, ...templateStyles, documentRoot);
  return templateRoot;
}

function clearWeatherTemplate() {
  presentationController.setTemplateRoot(null);
  weatherTemplateRoot?.replaceChildren();
  weatherTemplateRoot = null;
}

function createMessage(role, content, isStreaming = false) {
  const fragment = messageTemplate.content.cloneNode(true);
  const article = fragment.querySelector(".message");
  const avatar = fragment.querySelector(".avatar");
  const bubble = fragment.querySelector(".bubble");
  const isUser = role === "user";
  article.classList.add(isUser ? "user" : "assistant");
  if (isStreaming) article.classList.add("streaming");
  avatar.textContent = isUser ? "B" : "L";
  bubble.textContent = content || "";
  return fragment;
}

function createCharacterStreamer(onFirstText = () => {}) {
  const characters = [];
  let timer = null;
  let drainResolvers = [];
  let hasRenderedFirstText = false;

  function resolveDrain() {
    if (characters.length || timer !== null) return;
    for (const resolve of drainResolvers) resolve();
    drainResolvers = [];
  }

  function paint() {
    const bubble = messagesElement.querySelector(".message.streaming .bubble");
    if (bubble) bubble.textContent = streamingDraft || "";
    messagesElement.scrollTop = messagesElement.scrollHeight;
  }

  function pump() {
    timer = null;
    const character = characters.shift();
    if (character === undefined) {
      resolveDrain();
      return;
    }
    streamingDraft = `${streamingDraft || ""}${character}`;
    paint();
    if (!hasRenderedFirstText) {
      hasRenderedFirstText = true;
      onFirstText();
    }
    const delay = characters.length > 120
      ? Math.max(8, STREAM_CHARACTER_DELAY_MS / 2)
      : STREAM_CHARACTER_DELAY_MS;
    timer = window.setTimeout(pump, delay);
  }

  return {
    push(text) {
      if (typeof text !== "string" || !text) return;
      characters.push(...Array.from(text));
      if (streamingDraft === null) {
        streamingDraft = "";
        render();
      }
      if (timer === null) pump();
    },
    drain() {
      if (!characters.length && timer === null) return Promise.resolve();
      return new Promise((resolve) => drainResolvers.push(resolve));
    },
    abort() {
      characters.length = 0;
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      resolveDrain();
    },
  };
}

function createTypingMessage() {
  const fragment = messageTemplate.content.cloneNode(true);
  const article = fragment.querySelector(".message");
  const avatar = fragment.querySelector(".avatar");
  const bubble = fragment.querySelector(".bubble");
  article.classList.add("assistant", "typing");
  avatar.textContent = "L";
  bubble.setAttribute("aria-label", "Trợ lí đang xử lý");
  for (let index = 0; index < 3; index += 1) bubble.appendChild(document.createElement("i"));
  return fragment;
}

async function submitQuery(query, { speakResponse = false } = {}) {
  const cleanQuery = query.trim();
  if (!cleanQuery || busy) return;

  if (!speakResponse) cancelStreamedSpeech();
  speechUnavailable = false;
  liveCtcPresentation.reset();

  const requestStartedAt = performance.now();
  latencyMarkers.length = 0;
  busy = true;
  state.messages = [...(state.messages || []), { role: "user", content: cleanQuery }];
  queryInput.value = "";
  resizeInput();
  render();

  const characterStreamer = createCharacterStreamer(() => {
    recordLatencyMarker(
      "first_text_rendered",
      performance.now() - requestStartedAt,
      "Frontend",
    );
  });
  let finalState = null;
  let hasReceivedStreamedNarration = false;
  let hasPresentationSteps = false;
  let presentationKeepsDraft = false;
  const onPresentationSceneStart = (scene) => {
    if (!hasReceivedStreamedNarration) {
      hasReceivedStreamedNarration = true;
      recordLatencyMarker(
        "first_text_delta_received",
        performance.now() - requestStartedAt,
        "Frontend",
      );
    }
    characterStreamer.push(`${scene.narration}\n\n`);
  };
  const onPresentationComplete = () => {
    const finishBubble = () => {
      if (busy) {
        window.setTimeout(finishBubble, 30);
        return;
      }
      characterStreamer.drain().then(() => {
        if (!presentationKeepsDraft) return;
        presentationKeepsDraft = false;
        streamingDraft = null;
        render();
      });
    };
    finishBubble();
  };
  try {
    await requestNdjson("/api/chat/stream", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, query: cleanQuery }),
    }, (event) => {
      if (event?.type === "timing") {
        recordLatencyMarker(event.marker, Number(event.elapsed_ms), "Server");
      } else if (event?.type === "text_delta") {
        if (!hasReceivedStreamedNarration) {
          hasReceivedStreamedNarration = true;
          recordLatencyMarker(
            "first_text_delta_received",
            performance.now() - requestStartedAt,
            "Frontend",
          );
        }
        characterStreamer.push(event.delta);
      } else if (event?.type === "panel_ready") {
        renderStreamedPanel(event.panel);
      } else if (event?.type === "presentation_contract") {
        reportPresentationDebug('presentation_contract_received', {
          scenes: Array.isArray(event.contract?.scenes) ? event.contract.scenes.length : 0,
          prebuffer_ms: event.contract?.prebuffer_ms,
        });
        const accepted = liveCtcPresentation.start(event.contract, {
          onSceneStart: onPresentationSceneStart,
          onComplete: onPresentationComplete,
        });
        hasPresentationSteps ||= accepted;
        presentationKeepsDraft ||= accepted;
      } else if (event?.type === "final") {
        finalState = event.payload;
      } else if (event?.type === "error") {
        throw new Error(event.message || "Luồng trả lời đã bị gián đoạn.");
      }
    });
    await characterStreamer.drain();
    if (!finalState || typeof finalState !== "object") {
      throw new Error("Máy chủ chưa gửi kết quả cuối cùng.");
    }
    const finalAssistantAnswer = [...(finalState.messages || [])]
      .reverse()
      .find((message) => message?.role === "assistant" && typeof message.content === "string")
      ?.content
      ?.trim();
    if (speakResponse && !hasPresentationSteps && finalAssistantAnswer) speakCompletedResponse(finalAssistantAnswer);
    state = finalState;
  } catch (error) {
    characterStreamer.abort();
    state.messages = [
      ...(state.messages || []),
      { role: "assistant", content: error.message || "Đã xảy ra lỗi kết nối." },
    ];
  } finally {
    if (!presentationKeepsDraft) streamingDraft = null;
    busy = false;
    render({
      preserveMessages: (speakResponse || hasPresentationSteps) && hasReceivedStreamedNarration && Boolean(finalState),
    });
    queryInput.focus();
  }
}

function resizeInput() {
  queryInput.style.height = "auto";
  queryInput.style.height = `${Math.min(queryInput.scrollHeight, 130)}px`;
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitQuery(queryInput.value);
});

queryInput.addEventListener("input", resizeInput);
microphoneButton.addEventListener("click", () => {
  if (busy) return;
  if (microphoneStream && !voiceAwaitingTranscript) endVoiceCapture();
  else if (!voiceAwaitingTranscript) startVoiceTurn();
});
queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

window.addEventListener("pagehide", () => {
  if (voiceSocket?.readyState === WebSocket.OPEN) voiceSocket.send(JSON.stringify({ type: "voice:cancel" }));
  releaseMicrophone();
  cancelStreamedSpeech();
  closeVoiceSocket();
});

suggestions.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-query]");
  if (button) submitQuery(button.dataset.query || "");
});

clearButton.addEventListener("click", async () => {
  if (busy) return;
  latencyMarkers.length = 0;
  busy = true;
  render();
  try {
    state = await requestJson("/api/session/clear", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch (error) {
    state = {
      messages: [{ role: "assistant", content: error.message }],
      active_panel: {},
      active_panel_revision: 0,
      has_active_panel: false,
    };
  } finally {
    busy = false;
    render();
    queryInput.focus();
  }
});

async function initialize() {
  try {
    state = await requestJson(`/api/session/${encodeURIComponent(sessionId)}`);
  } catch (error) {
    state.messages = [{ role: "assistant", content: error.message }];
  }
  render();
  queryInput.focus();
}

initialize();
