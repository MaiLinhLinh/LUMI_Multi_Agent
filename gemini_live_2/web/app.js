import { AnimationController } from "/assets/presentation/animation_controller.js?v=circle-effect-20260822";
import { renderSurfaceDocument } from "/assets/panel_renderer.js?v=surface-document-sd8";

const form = document.querySelector("#chatForm");
const queryInput = document.querySelector("#queryInput");
const messages = document.querySelector("#messages");
const messageTemplate = document.querySelector("#messageTemplate");
const workspace = document.querySelector("#workspace");
const welcome = document.querySelector("#welcome");
const contentPanel = document.querySelector("#contentPanel");
const contentTitle = document.querySelector("#contentTitle");
const weatherView = document.querySelector("#weatherView");
const templateHost = document.querySelector("#weatherTemplateHost");
const overlay = document.querySelector("#presentationOverlay");
const connectionStatus = document.querySelector("#connectionStatus");
const voiceStatus = document.querySelector("#voiceStatus");
const mic = document.querySelector("#microphoneButton");
const avatar = document.querySelector("#presentationAvatar");

const SESSION_KEY = "lumi.gemini-live.session";
const sessionId = localStorage.getItem(SESSION_KEY) || crypto.randomUUID();
localStorage.setItem(SESSION_KEY, sessionId);

const animationController = new AnimationController({
  templateHost,
  viewport: weatherView,
  overlay,
  avatar,
  onDiagnostic: reportVisualDiagnostic,
});

let socket = null;
let socketReady = false;
let liveState = "idle";
let readyWaiters = [];
let panelInteractionRoot = null;
let audioContext = null, sampleRate = null, nextAudioAt = 0, pendingAudioMarker = null;
let pendingAudioChunkTurnId = null, activeAudioTurnId = null;
let activePanelRevision = null;
let sources = new Set(), inputTranscriptBubble = null, traceBubble = null;
let microphoneStream = null, microphoneContext = null, microphoneSource = null, microphoneProcessor = null, muteGain = null;
let recording = false, openingMicrophone = false;
// Gemini remains the only turn detector.  This gate runs only while Gemini is
// busy, so it avoids streaming endless silence yet immediately passes a real
// barge-in back to Gemini for normal VAD handling.
const BARGE_IN_RMS_THRESHOLD = 0.008;
const BARGE_IN_TRAILING_AUDIO_MS = 300;
let sendBusyAudioUntil = 0;

function reportVisualDiagnostic(phase, details = {}) {
  const message = JSON.stringify(details);
  console.info(`[GEMINI_LIVE:VISUAL_${phase}]`, details);
  fetch("/api/client-debug", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phase: `visual_${phase}`, message }),
  }).catch(() => {});
}

function addMessage(role, text) {
  const node = messageTemplate.content.firstElementChild.cloneNode(true);
  node.classList.toggle("user", role === "user");
  node.querySelector(".avatar").textContent = role === "user" ? "B" : "L";
  node.querySelector(".bubble").textContent = text;
  messages.append(node); messages.scrollTop = messages.scrollHeight;
  return node.querySelector(".bubble");
}

async function armAudio() {
  if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
  await audioContext.resume();
}
function resetAudio() {
  for (const item of sources) { try { item.stop(); } catch (_) {} }
  sources = new Set(); sampleRate = null; nextAudioAt = 0; pendingAudioMarker = null;
  pendingAudioChunkTurnId = null; activeAudioTurnId = null;
}
function interruptOutput(turnId) {
  for (const source of [...sources]) {
    if (turnId && source.lumiTurnId !== turnId) continue;
    try { source.stop(); } catch (_) {}
    sources.delete(source);
  }
  if (!turnId || activeAudioTurnId === turnId) {
    activeAudioTurnId = null;
    nextAudioAt = audioContext?.currentTime || 0;
  }
  if (!turnId || pendingAudioChunkTurnId === turnId) pendingAudioChunkTurnId = null;
  if (!turnId || pendingAudioMarker?.turn_id === turnId) pendingAudioMarker = null;
  if (turnId) animationController.cancelTurn(turnId);
  else animationController.clear();
}
function playPcm(bytes) {
  if (!audioContext || !sampleRate || !bytes.byteLength) return;
  const pcmTurnId = pendingAudioChunkTurnId;
  pendingAudioChunkTurnId = null;
  if (!pcmTurnId) {
    console.warn("[GEMINI_LIVE:AUDIO_DROPPED]", { reason: "missing_turn_id" });
    return;
  }
  if (activeAudioTurnId !== pcmTurnId) {
    activeAudioTurnId = pcmTurnId;
  }
  const input = new Int16Array(bytes);
  const buffer = audioContext.createBuffer(1, input.length, sampleRate);
  const output = buffer.getChannelData(0);
  for (let i = 0; i < input.length; i += 1) output[i] = input[i] / 32768;
  const source = audioContext.createBufferSource();
  source.buffer = buffer; source.connect(audioContext.destination);
  source.lumiTurnId = pcmTurnId;
  const start = Math.max(audioContext.currentTime + 0.040, nextAudioAt);
  source.start(start); nextAudioAt = start + buffer.duration;
  if (pendingAudioMarker) {
    const markerMatchesPanel = Number.isInteger(pendingAudioMarker.panel_revision)
      && pendingAudioMarker.panel_revision === activePanelRevision;
    if (pendingAudioMarker.turn_id === pcmTurnId && markerMatchesPanel) {
      animationController.queue(pendingAudioMarker);
      reportVisualDiagnostic("marker_attached_to_pcm", {
        anchor_id: pendingAudioMarker.anchor_id,
        effect: pendingAudioMarker.effect,
        turn_id: pcmTurnId,
        audio_start_at: start,
      });
    } else {
      console.warn("[GEMINI_LIVE:MARKER_DROPPED]", {
        reason: pendingAudioMarker.turn_id !== pcmTurnId ? "turn_id_mismatch" : "panel_revision_mismatch",
        markerTurnId: pendingAudioMarker.turn_id,
        pcmTurnId,
        markerPanelRevision: pendingAudioMarker.panel_revision,
        activePanelRevision,
      });
    }
    pendingAudioMarker = null;
  }
  animationController.armAtAudioStart(start, audioContext);
  sources.add(source);
  source.addEventListener("ended", () => sources.delete(source), { once: true });
}

function fitPresentationToHost(content) {
  // SurfaceDocument owns the available panel rectangle directly. Scaling a whole grid
  // to fit its height makes a 16-column panel look narrow and centred.
  content.style.transform = "none";
  content.style.width = "100%";
  content.style.height = "100%";
}

function hiddenComponentIdsInCurrentSurface() {
  const root = templateHost.shadowRoot;
  if (!root) return new Set();
  return new Set(
    [...root.querySelectorAll('[data-component-id][data-visibility="hidden"]')]
      .map((node) => node.dataset.componentId)
      .filter(Boolean),
  );
}

function sendPanelInteraction(event) {
  const detail = event?.detail;
  if (!detail || socket?.readyState !== WebSocket.OPEN) {
    reportVisualDiagnostic("panel_interaction_ignored", { reason: "socket_not_open" });
    return;
  }
  const surfaceId = typeof detail.surface_id === "string" ? detail.surface_id.trim() : "";
  const anchorId = typeof detail.anchor_id === "string" ? detail.anchor_id.trim() : "";
  const action = typeof detail.action === "string" ? detail.action.trim() : "";
  if (!surfaceId || !anchorId || !action || !Number.isInteger(activePanelRevision)) {
    reportVisualDiagnostic("panel_interaction_ignored", { reason: "invalid_event", detail });
    return;
  }
  socket.send(JSON.stringify({
    type: "panel:interaction",
    surface_id: surfaceId,
    revision: activePanelRevision,
    anchor_id: anchorId,
    action,
  }));
  reportVisualDiagnostic("panel_interaction_sent", {
    surface_id: surfaceId,
    revision: activePanelRevision,
    anchor_id: anchorId,
    action,
  });
}

function renderPanel(panel, { isUpdate = false } = {}) {
  const isSurfaceDocument = panel?.ui_type === "surface_document" && panel?.surface
    && Array.isArray(panel.surface.components);
  if (!isSurfaceDocument) return;
  const revealedComponentIds = isUpdate ? hiddenComponentIdsInCurrentSurface() : new Set();
  const revision = Number(panel.surface?.revision);
  activePanelRevision = Number.isInteger(revision) && revision > 0 ? revision : null;
  contentPanel.hidden = false; weatherView.hidden = false; welcome.hidden = true;
  workspace.classList.remove("no-dashboard"); workspace.classList.add("has-dashboard");
  contentTitle.textContent = panel.ui_type === "weather" ? "Thông tin thời tiết" : "Nội dung trực quan";
  const root = templateHost.shadowRoot || templateHost.attachShadow({ mode: "open" });
  if (panelInteractionRoot !== root) {
    panelInteractionRoot?.removeEventListener("panel:interaction", sendPanelInteraction);
    root.addEventListener("panel:interaction", sendPanelInteraction);
    panelInteractionRoot = root;
  }
  root.replaceChildren();
  const widgetStyles = document.createElement("link");
  widgetStyles.rel = "stylesheet";
  widgetStyles.href = "/assets/widgets/styles.css?v=text-fit-20260903";
  const style = document.createElement("style");
  style.textContent = `[data-anchor-id]{transition:outline .18s,box-shadow .18s,transform .18s}.lumi-highlight{outline:3px solid #0ea5e9!important;outline-offset:4px;box-shadow:0 0 0 8px #0ea5e922!important}.lumi-pulse{animation:lumi-pulse 720ms cubic-bezier(.2,.8,.3,1) 2}@keyframes lumi-pulse{0%,100%{transform:scale(1);filter:none}50%{transform:scale(1.035);filter:drop-shadow(0 0 8px rgba(14,165,233,.7))}}`;
  const content = document.createElement("div");
  content.style.cssText = "width:100%; height:100%;";
  content.append(renderSurfaceDocument(
    panel.surface,
    Array.isArray(panel.assets) ? panel.assets : [],
    { revealedComponentIds },
  ));
  root.append(widgetStyles, style, content);
  // The first layout pass can happen before the shadow stylesheet arrives.
  // Refit once it has supplied the real grid and widget dimensions.
  widgetStyles.addEventListener("load", () => fitPresentationToHost(content), { once: true });
  fitPresentationToHost(content);
  animationController.clear();
}
function clearPanel({ surface_id: surfaceId, revision } = {}) {
  const nextRevision = Number(revision);
  if (!Number.isInteger(nextRevision) || nextRevision <= 0) return;
  templateHost.shadowRoot?.replaceChildren();
  activePanelRevision = null;
  contentPanel.hidden = true;
  weatherView.hidden = true;
  welcome.hidden = false;
  workspace.classList.remove("has-dashboard");
  workspace.classList.add("no-dashboard");
  animationController.clear();
  reportVisualDiagnostic("panel_cleared", { surface_id: surfaceId || null, revision: nextRevision });
}
function showText(text) {
  // The trace bubble is the single visible assistant output for this mode.
  // It receives the same transcript in grouped `text:` entries.
}
function addTraceBubble() {
  const node = messageTemplate.content.firstElementChild.cloneNode(true);
  node.classList.add("debug-trace");
  node.querySelector(".avatar").remove();
  const bubble = node.querySelector(".bubble");
  messages.append(node); messages.scrollTop = messages.scrollHeight;
  return bubble;
}
function showLiveTrace(payload) {
  const timestamp = String(payload.timestamp || "").trim();
  const eventType = String(payload.event || "").trim();
  const content = String(payload.content || "").trim();
  if (!timestamp || !eventType || !content) return;
  if (!traceBubble) traceBubble = addTraceBubble();
  traceBubble.textContent += `${timestamp} | ${eventType}: ${content}\n`;
  messages.scrollTop = messages.scrollHeight;
}
function showInputTranscript(text, final = false) {
  if (!text) return;
  if (!inputTranscriptBubble) inputTranscriptBubble = addMessage("user", "");
  inputTranscriptBubble.textContent = text;
  inputTranscriptBubble.dataset.final = final ? "true" : "false";
  messages.scrollTop = messages.scrollHeight;
}

function connectSocket() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  socketReady = false;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${protocol}//${location.host}/ws/live`);
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => socket.send(JSON.stringify({ type: "live:connect", session_id: sessionId })));
  socket.addEventListener("message", handleMessage);
  socket.addEventListener("error", () => {
    connectionStatus.textContent = "Lỗi kết nối";
    voiceStatus.textContent = "Không thể kết nối Gemini Live.";
    releaseReadyWaiters(new Error("Gemini Live socket error"));
  });
  socket.addEventListener("close", () => {
    socket = null; socketReady = false; liveState = "idle";
    connectionStatus.textContent = "Sẵn sàng";
    releaseReadyWaiters(new Error("Gemini Live socket closed before becoming ready"));
    stopCapture();
  });
}
function releaseReadyWaiters(error = null) {
  const waiters = readyWaiters; readyWaiters = [];
  for (const waiter of waiters) error ? waiter.reject(error) : waiter.resolve();
}
async function ensureSocket() {
  connectSocket();
  if (socketReady) return;
  await new Promise((resolve, reject) => readyWaiters.push({ resolve, reject }));
}
function handleMessage(event) {
  if (event.data instanceof ArrayBuffer) {
    playPcm(event.data);
    avatar.dataset.avatarState = "speaking";
    voiceStatus.textContent = "Lumi đang nói…";
    return;
  }
  const payload = JSON.parse(event.data);
  console.info("[GEMINI_LIVE:EVENT]", payload);
  if (payload.type === "live:session_ready") {
    socketReady = true; connectionStatus.textContent = "Đã kết nối";
    voiceStatus.textContent = "Đang mở micro…"; releaseReadyWaiters();
    void enableMicrophone();
  }
  if (payload.type === "live:state") {
    liveState = String(payload.state || "idle");
    const labels = { idle: "Sẵn sàng", listening: recording ? "Đang nghe…" : "Sẵn sàng nói", waiting_for_tool: "Đang xử lý…", speaking: "Lumi đang trình bày…", error: "Có lỗi" };
    connectionStatus.textContent = labels[liveState] || liveState;
    if (liveState === "listening") voiceStatus.textContent = recording ? "Đang nghe…" : "Đã tắt micro";
    if (liveState === "waiting_for_tool") voiceStatus.textContent = "Lumi đang xử lý…";
    if (liveState === "speaking") voiceStatus.textContent = "Lumi đang nói…";
    if (liveState === "speaking" || liveState === "waiting_for_tool") avatar.dataset.avatarState = "speaking";
    if (liveState === "idle" || (liveState === "listening" && !recording)) avatar.dataset.avatarState = "idle";
  }
  if (payload.type === "live:input_ready") {
    // The backend can acknowledge mic_enabled before getUserMedia() resolves.
    // startCapture() is also called after recording is set below, so this
    // acknowledgement is harmless in either ordering.
    voiceStatus.textContent = "Đang nghe…";
    startCapture();
  }
  if (payload.type === "input_transcript" && payload.text) {
    showInputTranscript(payload.text, Boolean(payload.final));
  }
  if (payload.type === "panel") renderPanel(payload.panel);
  if (payload.type === "panel_update") {
    const revision = Number(payload.panel?.surface?.revision);
    if (!Number.isInteger(revision) || revision <= 0 || (
      Number.isInteger(activePanelRevision) && revision <= activePanelRevision
    )) {
      reportVisualDiagnostic("panel_update_ignored", {
        reason: "missing_or_stale_revision",
        received_revision: payload.panel?.surface?.revision,
        active_revision: activePanelRevision,
      });
    } else {
      renderPanel(payload.panel, { isUpdate: true });
      reportVisualDiagnostic("panel_update_rendered", { revision });
    }
  }
  if (payload.type === "panel_clear") clearPanel(payload);
  if (payload.type === "scene") animationController.queue({
    anchor_id: payload.scene.anchor_id,
    effect: payload.scene.effect,
    actions: payload.scene.actions || [],
    animation_delay_ms: Number(payload.animation_delay_ms) || 0,
  });
  if (payload.type === "audio_chunk") {
    pendingAudioChunkTurnId = typeof payload.turn_id === "string" && payload.turn_id ? payload.turn_id : null;
  }
  if (payload.type === "audio_marker") {
    pendingAudioMarker = {
      ...payload.cue,
      animation_delay_ms: Number(payload.animation_delay_ms) || 0,
      turn_id: typeof payload.turn_id === "string" ? payload.turn_id : null,
    };
    reportVisualDiagnostic("marker_received", {
      anchor_id: pendingAudioMarker.anchor_id,
      effect: pendingAudioMarker.effect,
      turn_id: pendingAudioMarker.turn_id,
    });
  }
  if (payload.type === "audio_format") sampleRate = Number(payload.sample_rate_hz);
  if (payload.type === "live:debug_trace") showLiveTrace(payload);
  if (payload.type === "panel:interaction_rejected") {
    reportVisualDiagnostic("panel_interaction_rejected", { message: payload.message || "rejected" });
  }
  if (payload.type === "text") showText(payload.text);
  if (payload.type === "live:turn_complete") {
    avatar.dataset.avatarState = "idle";
    voiceStatus.textContent = recording ? "Đang nghe…" : "Đã tắt micro";
  }
  if (payload.type === "live:interrupted") {
    interruptOutput(typeof payload.turn_id === "string" ? payload.turn_id : null);
    avatar.dataset.avatarState = "idle";
    voiceStatus.textContent = recording ? "Đang nghe…" : "Đã tắt micro";
  }
  if (payload.type === "live:timeout") {
    stopCapture();
    voiceStatus.textContent = payload.reason === "idle_timeout"
      ? "Phiên đã tạm đóng vì không hoạt động. Đang kết nối lại khi bạn nói tiếp."
      : "Lumi chưa nhận được phản hồi kịp thời. Bạn có thể nói lại.";
  }
  if (payload.type === "live:reconnecting") {
    connectionStatus.textContent = "Đang kết nối lại…";
    voiceStatus.textContent = "Đang khôi phục phiên hội thoại…";
  }
  if (payload.type === "live:reconnected") {
    connectionStatus.textContent = "Đã kết nối lại";
    voiceStatus.textContent = "Đã khôi phục ngữ cảnh. Bạn có thể nói tiếp.";
  }
  if (payload.type === "live:error") {
    if (!socketReady) releaseReadyWaiters(new Error(payload.message || "Gemini Live startup failed"));
    voiceStatus.textContent = `Lỗi: ${payload.message}`;
    avatar.dataset.avatarState = "idle"; stopCapture();
  }
}

function pcm16k(input, sourceRate) {
  const frames = Math.max(1, Math.round(input.length * 16000 / sourceRate));
  const data = new Int16Array(frames), ratio = input.length / frames;
  for (let i = 0; i < frames; i += 1) {
    const begin = Math.floor(i * ratio), end = Math.min(input.length, Math.max(begin + 1, Math.floor((i + 1) * ratio)));
    let sum = 0; for (let j = begin; j < end; j += 1) sum += input[j];
    const v = Math.max(-1, Math.min(1, sum / (end - begin)));
    data[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return data.buffer;
}
function rmsLevel(samples) {
  let sumSquares = 0;
  for (let index = 0; index < samples.length; index += 1) sumSquares += samples[index] * samples[index];
  return Math.sqrt(sumSquares / Math.max(samples.length, 1));
}
function shouldSendMicrophoneSamples(samples) {
  if (liveState !== "waiting_for_tool" && liveState !== "speaking") return true;
  const now = performance.now();
  if (rmsLevel(samples) >= BARGE_IN_RMS_THRESHOLD) {
    sendBusyAudioUntil = now + BARGE_IN_TRAILING_AUDIO_MS;
  }
  return now <= sendBusyAudioUntil;
}
function startCapture() {
  if (!recording || !microphoneStream || microphoneContext) return;
  microphoneContext = new AudioContext();
  microphoneSource = microphoneContext.createMediaStreamSource(microphoneStream);
  microphoneProcessor = microphoneContext.createScriptProcessor(4096, 1, 1);
  muteGain = microphoneContext.createGain(); muteGain.gain.value = 0;
  microphoneProcessor.addEventListener("audioprocess", event => {
    if (!recording || socket?.readyState !== WebSocket.OPEN) return;
    const samples = event.inputBuffer.getChannelData(0);
    if (!shouldSendMicrophoneSamples(samples)) return;
    socket.send(pcm16k(samples, event.inputBuffer.sampleRate));
  });
  microphoneSource.connect(microphoneProcessor); microphoneProcessor.connect(muteGain); muteGain.connect(microphoneContext.destination);
}
function disableMicrophone(reason) {
  if (!recording && !microphoneStream) return;
  recording = false;
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "live:mic_disabled" }));
  stopCapture();
  voiceStatus.textContent = "Đã tắt micro";
  console.info("[GEMINI_LIVE:MIC_STOPPED]", { reason });
}
function stopCapture() {
  recording = false;
  sendBusyAudioUntil = 0;
  microphoneProcessor?.disconnect(); microphoneSource?.disconnect(); muteGain?.disconnect();
  microphoneContext?.close(); microphoneStream?.getTracks().forEach(track => track.stop());
  microphoneStream = microphoneContext = microphoneSource = microphoneProcessor = muteGain = null;
  mic.classList.remove("listening"); mic.setAttribute("aria-pressed", "false");
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;
  try {
    await ensureSocket();
    // Typed input is a barge-in source. It may replace a speaking or stalled
    // turn, so stop only browser output and let the backend inject the text
    // into the same Gemini Live session.
    await armAudio(); interruptOutput(null); inputTranscriptBubble = null; traceBubble = null;
    addMessage("user", query); queryInput.value = ""; voiceStatus.textContent = "Đang xử lý…";
    socket.send(JSON.stringify({ type: "live:text", query }));
  } catch (error) { voiceStatus.textContent = `Không thể kết nối: ${error.message}`; }
});
document.querySelectorAll("[data-query]").forEach(button => button.addEventListener("click", () => {
  queryInput.value = button.dataset.query; form.requestSubmit();
}));

async function enableMicrophone() {
  if (openingMicrophone || !navigator.mediaDevices?.getUserMedia) return;
  openingMicrophone = true;
  try {
    if (!socketReady) voiceStatus.textContent = "Đang kết nối Gemini Live…";
    await ensureSocket();
    if (recording || microphoneStream) return;
    await armAudio();
    microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    recording = true;
    mic.classList.add("listening"); mic.setAttribute("aria-pressed", "true");
    socket.send(JSON.stringify({ type: "live:mic_enabled" }));
    // Do not rely solely on live:input_ready: it may have reached the browser
    // while getUserMedia() was still pending, before recording became true.
    // WebSocket preserves the mic_enabled → PCM ordering on this connection.
    startCapture();
    console.info("[GEMINI_LIVE:MIC_CAPTURE_STARTED]", { sampleRate: microphoneContext?.sampleRate });
  } catch (error) {
    voiceStatus.textContent = `Không thể mở micro: ${error.message}`; stopCapture();
  } finally { openingMicrophone = false; }
}
mic.addEventListener("click", event => {
  event.preventDefault();
  if (recording || microphoneStream) disableMicrophone("button_toggle");
  else void enableMicrophone();
});
mic.addEventListener("keydown", event => {
  if (event.repeat || (event.key !== "Enter" && event.key !== " ")) return;
  event.preventDefault();
  if (recording || microphoneStream) disableMicrophone("key_toggle");
  else void enableMicrophone();
});

fetch("/api/client-debug", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ phase: "app_bootstrap", message: "app.js reached connectSocket" }),
}).catch(() => {});

window.addEventListener("pagehide", () => {
  if (socket?.readyState === WebSocket.OPEN) {
    if (recording) socket.send(JSON.stringify({ type: "live:mic_disabled" }));
    socket.send(JSON.stringify({ type: "live:close" }));
  }
  socket?.close();
});
connectSocket();
