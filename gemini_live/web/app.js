import { AnimationController } from "/assets/presentation/animation_controller.js";

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
let presentationFitObserver = null;
let audioContext = null, sampleRate = null, nextAudioAt = 0, pendingAudioMarker = null;
let pendingAudioChunkTurnId = null, activeAudioTurnId = null;
let sources = new Set(), inputTranscriptBubble = null, traceBubble = null;
let microphoneStream = null, microphoneContext = null, microphoneSource = null, microphoneProcessor = null, muteGain = null;
let recording = false, openingMicrophone = false;

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
  const start = Math.max(audioContext.currentTime + 0.010, nextAudioAt);
  source.start(start); nextAudioAt = start + buffer.duration;
  if (pendingAudioMarker) {
    if (pendingAudioMarker.turn_id === pcmTurnId) {
      animationController.queue(pendingAudioMarker);
      reportVisualDiagnostic("marker_attached_to_pcm", {
        anchor_id: pendingAudioMarker.anchor_id,
        effect: pendingAudioMarker.effect,
        turn_id: pcmTurnId,
        audio_start_at: start,
      });
    } else {
      console.warn("[GEMINI_LIVE:MARKER_DROPPED]", {
        reason: "turn_id_mismatch",
        markerTurnId: pendingAudioMarker.turn_id,
        pcmTurnId,
      });
    }
    pendingAudioMarker = null;
  }
  animationController.armAtAudioStart(start, audioContext);
  sources.add(source);
  source.addEventListener("ended", () => sources.delete(source), { once: true });
}

function fitPresentationToHost(content) {
  const fit = () => {
    content.style.transform = "scale(1)";
    const availableWidth = templateHost.clientWidth;
    const availableHeight = templateHost.clientHeight;
    const contentWidth = Math.max(content.scrollWidth, content.offsetWidth);
    const contentHeight = content.scrollHeight;
    if (!availableWidth || !availableHeight || !contentWidth || !contentHeight) return;
    const scale = Math.min(1, availableWidth / contentWidth, availableHeight / contentHeight);
    content.style.transform = `scale(${scale})`;
  };

  const scheduleFit = () => requestAnimationFrame(() => requestAnimationFrame(fit));
  presentationFitObserver?.disconnect();
  presentationFitObserver = new ResizeObserver(scheduleFit);
  presentationFitObserver.observe(templateHost);
  scheduleFit();
}

function renderPanel(panel) {
  if (!panel?.html) return;
  contentPanel.hidden = false; weatherView.hidden = false; welcome.hidden = true;
  workspace.classList.remove("no-dashboard"); workspace.classList.add("has-dashboard");
  contentTitle.textContent = panel.ui_type === "weather" ? "Thông tin thời tiết" : "Nội dung trực quan";
  const root = templateHost.shadowRoot || templateHost.attachShadow({ mode: "open" });
  root.replaceChildren();
  const style = document.createElement("style");
  style.textContent = `[data-present-id]{transition:outline .18s,box-shadow .18s,transform .18s}.lumi-highlight{outline:3px solid #0ea5e9!important;outline-offset:4px;box-shadow:0 0 0 8px #0ea5e922!important}.lumi-pulse{animation:lumi-pulse 720ms cubic-bezier(.2,.8,.3,1) 2}@keyframes lumi-pulse{0%,100%{transform:scale(1);filter:none}50%{transform:scale(1.035);filter:drop-shadow(0 0 8px rgba(14,165,233,.7))}}`;
  const content = document.createElement("div");
  content.style.cssText = "width:100%; transform-origin:top center; will-change:transform;";
  content.innerHTML = panel.html;
  root.append(style, content);
  fitPresentationToHost(content);
  animationController.clear();
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
function canStartUserTurn() {
  return socketReady && (liveState === "idle" || liveState === "listening") && !recording && !sources.size;
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
  if (payload.type === "scene") animationController.queue({
    target_id: payload.scene.target_id,
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
function startCapture() {
  if (!recording || !microphoneStream || microphoneContext) return;
  microphoneContext = new AudioContext();
  microphoneSource = microphoneContext.createMediaStreamSource(microphoneStream);
  microphoneProcessor = microphoneContext.createScriptProcessor(4096, 1, 1);
  muteGain = microphoneContext.createGain(); muteGain.gain.value = 0;
  microphoneProcessor.addEventListener("audioprocess", event => {
    if (!recording || socket?.readyState !== WebSocket.OPEN) return;
    const samples = event.inputBuffer.getChannelData(0);
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
    if (!canStartUserTurn()) { voiceStatus.textContent = "Lumi đang xử lý lượt trước."; return; }
    await armAudio(); resetAudio(); animationController.clear(); inputTranscriptBubble = null; traceBubble = null;
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
