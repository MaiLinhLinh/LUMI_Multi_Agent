const form = document.querySelector("#form");
const queryInput = document.querySelector("#query");
const messages = document.querySelector("#messages");
const messageTemplate = document.querySelector("#message");
const weatherView = document.querySelector("#weatherView");
const weatherHost = document.querySelector("#weatherTemplateHost");
const overlay = document.querySelector("#overlay");
const status = document.querySelector("#status");
const eventLog = document.querySelector("#events");
const microphoneButton = document.querySelector("#microphone");
const voiceStatus = document.querySelector("#voiceStatus");

const sessionKey = "lumi.live-toolcall-experiment.session";
const sessionId = localStorage.getItem(sessionKey) || crypto.randomUUID();
localStorage.setItem(sessionKey, sessionId);

let socket = null;
let audioContext = null;
let sampleRate = null;
let nextAudioAt = 0;
let sources = new Set();
let assistantBubble = null;
let activeTarget = null;
let clearAnimationTimer = null;
let pendingAnimationCommand = null;
let animationStartTimer = null;
let microphoneStream = null;
let microphoneContext = null;
let microphoneSource = null;
let microphoneProcessor = null;
let microphoneMuteGain = null;
let voiceCapturing = false;
let microphoneChunksSent = 0;
let microphoneBytesSent = 0;

function appendMessage(role, text) {
  const node = messageTemplate.content.firstElementChild.cloneNode(true);
  node.classList.toggle("user", role === "user");
  node.querySelector("b").textContent = role === "user" ? "B" : "L";
  node.querySelector("p").textContent = text;
  messages.append(node);
  messages.scrollTop = messages.scrollHeight;
  return node.querySelector("p");
}

function log(event) {
  const safe = { ...event };
  if (safe.panel) safe.panel = { ui_type: safe.panel.ui_type, template_id: safe.panel.template_id };
  eventLog.textContent += `${JSON.stringify(safe, null, 2)}\n`;
  eventLog.scrollTop = eventLog.scrollHeight;
}

function armAudio() {
  if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
  return audioContext.resume();
}

function resetAudio() {
  for (const source of sources) { try { source.stop(); } catch (_) {} }
  sources = new Set();
  sampleRate = null;
  nextAudioAt = 0;
}

function schedulePendingAnimation(audioStartAt) {
  if (!pendingAnimationCommand || !audioContext) return;
  const command = pendingAnimationCommand;
  pendingAnimationCommand = null;
  if (animationStartTimer) clearTimeout(animationStartTimer);
  const sceneDelayMs = 180;
  const waitMs = Math.max(0, (audioStartAt - audioContext.currentTime) * 1000) + sceneDelayMs;
  animationStartTimer = window.setTimeout(() => {
    animationStartTimer = null;
    applyAnimation(command);
    const event = {
      type: "frontend_animation_started_with_audio",
      command,
      scene_delay_ms: sceneDelayMs,
      client_at_ms: Math.round(performance.now()),
    };
    log(event);
    console.info("[LIVE_EXPERIMENT:FRONTEND_ANIMATION_STARTED]", event);
  }, waitMs);
  const event = {
    type: "frontend_animation_armed",
    command,
    scene_delay_ms: sceneDelayMs,
    audio_start_at: Number(audioStartAt.toFixed(3)),
    audio_current_time: Number(audioContext.currentTime.toFixed(3)),
    scheduled_wait_ms: Math.round(waitMs),
    client_at_ms: Math.round(performance.now()),
  };
  log(event);
  console.info("[LIVE_EXPERIMENT:FRONTEND_ANIMATION_ARMED]", event);
}

function playPcm(bytes) {
  if (!audioContext || !sampleRate || !bytes.byteLength) return;
  const pcm = new Int16Array(bytes);
  const buffer = audioContext.createBuffer(1, pcm.length, sampleRate);
  const channel = buffer.getChannelData(0);
  for (let index = 0; index < pcm.length; index += 1) channel[index] = pcm[index] / 32768;
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  const startAt = Math.max(audioContext.currentTime + .025, nextAudioAt);
  source.start(startAt);
  nextAudioAt = startAt + buffer.duration;
  schedulePendingAnimation(startAt);
  sources.add(source);
  source.addEventListener("ended", () => sources.delete(source), { once: true });
}

function renderPanel(panel) {
  if (!panel?.html) return;
  weatherView.hidden = false;
  const root = weatherHost.shadowRoot || weatherHost.attachShadow({ mode: "open" });
  root.replaceChildren();
  const style = document.createElement("style");
  style.textContent = `
    :host { display: block; }
    [data-present-id] { transition: outline-color .18s ease, box-shadow .18s ease, transform .18s ease; }
    .lumi-highlight {
      outline: 3px solid #0ea5e9 !important;
      outline-offset: 4px;
      box-shadow: 0 0 0 8px rgba(14, 165, 233, .14) !important;
    }
    .lumi-pulse { animation: lumi-pulse 900ms ease-out 2; }
    @keyframes lumi-pulse {
      0% { box-shadow: 0 0 0 0 rgba(14, 165, 233, .52); transform: scale(1); }
      70% { box-shadow: 0 0 0 12px rgba(14, 165, 233, 0); transform: scale(1.012); }
      100% { box-shadow: 0 0 0 0 rgba(14, 165, 233, 0); transform: scale(1); }
    }
  `;
  const container = document.createElement("div");
  container.innerHTML = panel.html;
  root.append(style, container);
  clearAnimation();
  status.textContent = `Đã render template ${panel.template_id}. Gemini Live đang điều phối.`;
}

function clearAnimation() {
  if (clearAnimationTimer) clearTimeout(clearAnimationTimer);
  clearAnimationTimer = null;
  if (animationStartTimer) clearTimeout(animationStartTimer);
  animationStartTimer = null;
  pendingAnimationCommand = null;
  activeTarget?.classList.remove("lumi-highlight", "lumi-pulse");
  activeTarget = null;
  overlay.replaceChildren();
}

function applyAnimation(command) {
  const root = weatherHost.shadowRoot;
  if (!root) return;
  const target = root.querySelector(`[data-present-id="${CSS.escape(command.target_id)}"]`);
  if (!target) {
    log({ type: "frontend_rejected_animation", reason: "target_missing", command });
    return;
  }
  clearAnimation();
  activeTarget = target;
  if (command.effect === "highlight") target.classList.add("lumi-highlight");
  if (command.effect === "pulse") target.classList.add("lumi-pulse");
  if (command.effect === "draw_circle") drawCircle(target);
  if (command.effect === "draw_arrow") drawArrow(target);
  log({ type: "frontend_animation", command });
}

function targetRect(target) {
  const hostRect = weatherView.getBoundingClientRect();
  const rect = target.getBoundingClientRect();
  return {
    x: rect.left - hostRect.left,
    y: rect.top - hostRect.top,
    width: rect.width,
    height: rect.height,
  };
}

function svgNode(name, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function drawCircle(target) {
  const rect = targetRect(target);
  const ellipse = svgNode("ellipse", {
    class: "ring", cx: rect.x + rect.width / 2, cy: rect.y + rect.height / 2,
    rx: Math.max(12, rect.width / 2 + 7), ry: Math.max(12, rect.height / 2 + 7), pathLength: 100,
  });
  overlay.append(ellipse);
}

function drawArrow(target) {
  const rect = targetRect(target);
  const endX = rect.x + rect.width / 2;
  const endY = rect.y + rect.height / 2;
  const startX = Math.max(10, endX - 95);
  const startY = Math.max(12, endY - 70);
  const line = svgNode("path", { class: "arrow", d: `M ${startX} ${startY} L ${endX} ${endY}`, pathLength: 100 });
  const head = svgNode("path", { class: "arrow", d: `M ${endX - 13} ${endY - 5} L ${endX} ${endY} L ${endX - 5} ${endY - 13}`, pathLength: 100 });
  overlay.append(line, head);
}

function showText(text) {
  if (!text) return;
  if (!assistantBubble) assistantBubble = appendMessage("assistant", "");
  assistantBubble.textContent += text;
  messages.scrollTop = messages.scrollHeight;
}

function handleSocketMessage(event) {
  if (event.data instanceof ArrayBuffer) {
    playPcm(event.data);
    return;
  }
  const payload = JSON.parse(event.data);
  log(payload);
  if (payload.type === "live:ready") status.textContent = "Gemini Live đã sẵn sàng.";
  if (payload.type === "live:input_ready") {
    voiceStatus.textContent = "Đang nghe… Nhấn Dừng nói khi kết thúc câu hỏi.";
    startPcmCapture();
  }
  if (payload.type === "live:server_audio_received") {
    voiceStatus.textContent = `Server đã nhận audio: ${payload.chunks} chunk.`;
  }
  if (payload.type === "live:gemini_audio_sent") {
    voiceStatus.textContent = `Đã chuyển audio tới Gemini Live: ${payload.chunks} chunk.`;
  }
  if (payload.type === "live:server_audio_closed") {
    voiceStatus.textContent = `Server đã nhận đủ audio (${payload.chunks} chunk), đang chờ Gemini Live.`;
  }
  if (payload.type === "live:gemini_audio_closed") {
    voiceStatus.textContent = `Đã kết thúc input tới Gemini Live (${payload.chunks} chunk).`;
  }
  if (payload.type === "input_transcript") {
    voiceStatus.textContent = payload.final ? `Đã nghe: ${payload.text}` : `Đang nghe: ${payload.text}`;
  }
  if (payload.type === "panel") renderPanel(payload.panel);
  if (payload.type === "scene") {
    pendingAnimationCommand = { target_id: payload.scene.target_id, effect: payload.scene.effect };
    const event = {
      type: "frontend_animation_waiting_for_audio",
      scene_id: payload.scene.scene_id,
      command: pendingAnimationCommand,
      client_at_ms: Math.round(performance.now()),
    };
    log(event);
    console.info("[LIVE_EXPERIMENT:FRONTEND_SCENE_RECEIVED]", event);
  }
  if (payload.type === "animation") applyAnimation(payload.command);
  if (payload.type === "audio_format") sampleRate = Number(payload.sample_rate_hz);
  if (payload.type === "text") showText(payload.text);
  if (payload.type === "live:complete") {
    status.textContent = `Hoàn tất: ${payload.summary.tool_calls} tool call, ${payload.summary.animation_calls} animation marker.`;
    stopMicrophoneCapture();
    socket?.close();
  }
  if (payload.type === "live:error") {
    status.textContent = `Lỗi thử nghiệm: ${payload.message}`;
    stopMicrophoneCapture();
    socket?.close();
  }
}

function openLiveSocket({ inputMode, query = "" }) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${protocol}//${location.host}/ws/live-toolcall-experiment`);
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => socket.send(JSON.stringify({
    type: "live:start", session_id: sessionId, input_mode: inputMode, query,
  })));
  socket.addEventListener("message", handleSocketMessage);
  socket.addEventListener("error", () => {
    status.textContent = "Không thể kết nối WebSocket thử nghiệm.";
    stopMicrophoneCapture();
  });
  socket.addEventListener("close", () => { socket = null; });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query || socket?.readyState === WebSocket.OPEN) return;
  await armAudio();
  resetAudio();
  clearAnimation();
  assistantBubble = null;
  eventLog.textContent = "";
  appendMessage("user", query);
  queryInput.value = "";
  status.textContent = "Đang kết nối Gemini Live…";
  openLiveSocket({ inputMode: "text", query });
});

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
  if (!microphoneStream || microphoneContext) return;
  microphoneContext = new AudioContext();
  microphoneSource = microphoneContext.createMediaStreamSource(microphoneStream);
  microphoneProcessor = microphoneContext.createScriptProcessor(4096, 1, 1);
  microphoneMuteGain = microphoneContext.createGain();
  microphoneMuteGain.gain.value = 0;
  microphoneChunksSent = 0;
  microphoneBytesSent = 0;
  log({ type: "frontend_mic_capture_started", sample_rate_hz: microphoneContext.sampleRate });
  microphoneProcessor.addEventListener("audioprocess", (event) => {
    if (!voiceCapturing || socket?.readyState !== WebSocket.OPEN) return;
    const pcm = pcm16k(event.inputBuffer.getChannelData(0), event.inputBuffer.sampleRate);
    socket.send(pcm);
    microphoneChunksSent += 1;
    microphoneBytesSent += pcm.byteLength;
    if (microphoneChunksSent === 1 || microphoneChunksSent % 25 === 0) {
      log({
        type: "frontend_mic_audio_sent",
        chunks: microphoneChunksSent,
        bytes: microphoneBytesSent,
      });
    }
  });
  microphoneSource.connect(microphoneProcessor);
  microphoneProcessor.connect(microphoneMuteGain);
  microphoneMuteGain.connect(microphoneContext.destination);
}

function stopMicrophoneCapture() {
  voiceCapturing = false;
  microphoneProcessor?.disconnect();
  microphoneSource?.disconnect();
  microphoneMuteGain?.disconnect();
  microphoneContext?.close();
  microphoneStream?.getTracks().forEach((track) => track.stop());
  microphoneContext = microphoneSource = microphoneProcessor = microphoneMuteGain = microphoneStream = null;
  microphoneButton.classList.remove("listening");
  microphoneButton.textContent = "Bắt đầu nói";
}

microphoneButton.addEventListener("click", async () => {
  if (voiceCapturing) {
    voiceCapturing = false;
    microphoneButton.classList.remove("listening");
    microphoneButton.textContent = "Đang xử lý…";
    microphoneProcessor?.disconnect();
    microphoneSource?.disconnect();
    microphoneMuteGain?.disconnect();
    microphoneContext?.close();
    microphoneContext = microphoneSource = microphoneProcessor = microphoneMuteGain = null;
    microphoneStream?.getTracks().forEach((track) => track.stop());
    microphoneStream = null;
    log({
      type: "frontend_mic_capture_stopped",
      chunks: microphoneChunksSent,
      bytes: microphoneBytesSent,
    });
    socket?.send(JSON.stringify({ type: "live:audio_end" }));
    voiceStatus.textContent = "Đã gửi audio; Gemini Live đang gọi tool và trả lời…";
    return;
  }
  if (socket?.readyState === WebSocket.OPEN) return;
  if (!navigator.mediaDevices?.getUserMedia) {
    voiceStatus.textContent = "Trình duyệt không hỗ trợ microphone.";
    return;
  }
  try {
    await armAudio();
    resetAudio();
    clearAnimation();
    assistantBubble = null;
    eventLog.textContent = "";
    microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    voiceCapturing = true;
    microphoneButton.classList.add("listening");
    microphoneButton.textContent = "Dừng nói";
    voiceStatus.textContent = "Đang kết nối microphone tới Gemini Live…";
    status.textContent = "Đang chờ voice input…";
    openLiveSocket({ inputMode: "audio" });
  } catch (error) {
    stopMicrophoneCapture();
    voiceStatus.textContent = `Không thể mở microphone: ${error.message || "unknown error"}`;
  }
});
