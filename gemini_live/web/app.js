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
const status = connectionStatus;
const voiceStatus = document.querySelector("#voiceStatus");
const mic = document.querySelector("#microphoneButton");
const avatar = document.querySelector("#presentationAvatar");

const SESSION_KEY = "lumi.gemini-live.session";
const sessionId = localStorage.getItem(SESSION_KEY) || crypto.randomUUID();
localStorage.setItem(SESSION_KEY, sessionId);

let socket = null, audioContext = null, sampleRate = null, nextAudioAt = 0;
let sources = new Set(), assistantBubble = null, pendingScene = null, animationTimer = null, activeTarget = null;
let microphoneStream = null, microphoneContext = null, microphoneSource = null, microphoneProcessor = null, muteGain = null, recording = false;

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
  sources = new Set(); sampleRate = null; nextAudioAt = 0;
}
function clearAnimation() {
  if (animationTimer) clearTimeout(animationTimer); animationTimer = null;
  pendingScene = null; activeTarget?.classList.remove("lumi-highlight"); activeTarget = null; overlay.replaceChildren();
}
function svg(tag, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}
function rectFor(target) {
  const root = weatherView.getBoundingClientRect(), box = target.getBoundingClientRect();
  return { x: box.left - root.left, y: box.top - root.top, width: box.width, height: box.height };
}
function runAnimation(command) {
  const root = templateHost.shadowRoot;
  const target = root?.querySelector(`[data-present-id="${CSS.escape(command.target_id)}"]`);
  if (!target) { console.warn("[GEMINI_LIVE:UI_TARGET_MISSING]", command); return; }
  clearAnimation(); activeTarget = target;
  if (command.effect === "highlight" || command.effect === "reveal") target.classList.add("lumi-highlight");
  const rect = rectFor(target);
  if (command.effect === "draw_circle") overlay.append(svg("ellipse", { class: "lumi-overlay-shape lumi-overlay-draw-circle", pathLength: "100", cx: rect.x + rect.width / 2, cy: rect.y + rect.height / 2, rx: Math.max(14, rect.width / 2 + 8), ry: Math.max(14, rect.height / 2 + 8) }));
  if (command.effect === "draw_arrow") {
    const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    overlay.append(svg("path", { class: "lumi-overlay-shape lumi-overlay-draw-arrow", pathLength: "100", d: `M ${Math.max(8, x - 100)} ${Math.max(10, y - 68)} L ${x} ${y}` }));
  }
}
function armSceneAt(audioStartAt) {
  if (!pendingScene || !audioContext) return;
  const scene = pendingScene; pendingScene = null;
  const delay = Math.max(0, (audioStartAt - audioContext.currentTime) * 1000) + 180;
  animationTimer = setTimeout(() => { avatar.dataset.avatarState = "speaking"; runAnimation(scene); }, delay);
  console.info("[GEMINI_LIVE:SCENE_ARMED]", { scene, delay_ms: Math.round(delay) });
}
function playPcm(bytes) {
  if (!audioContext || !sampleRate || !bytes.byteLength) return;
  const input = new Int16Array(bytes), buffer = audioContext.createBuffer(1, input.length, sampleRate), output = buffer.getChannelData(0);
  for (let i = 0; i < input.length; i += 1) output[i] = input[i] / 32768;
  const source = audioContext.createBufferSource(); source.buffer = buffer; source.connect(audioContext.destination);
  const start = Math.max(audioContext.currentTime + .025, nextAudioAt); source.start(start); nextAudioAt = start + buffer.duration;
  armSceneAt(start); sources.add(source); source.addEventListener("ended", () => sources.delete(source), { once: true });
}
function renderPanel(panel) {
  if (!panel?.html) return;
  contentPanel.hidden = false;
  weatherView.hidden = false;
  welcome.hidden = true;
  // These are the original web_app layout states: only has-dashboard makes
  // the workspace split into the visual panel and the narrow right chat.
  workspace.classList.remove("no-dashboard");
  workspace.classList.add("has-dashboard");
  contentTitle.textContent = panel.ui_type === "weather" ? "Thông tin thời tiết" : "Nội dung trực quan";
  const root = templateHost.shadowRoot || templateHost.attachShadow({ mode: "open" }); root.replaceChildren();
  const style = document.createElement("style");
  style.textContent = `[data-present-id]{transition:outline .18s,box-shadow .18s}.lumi-highlight{outline:3px solid #0ea5e9!important;outline-offset:4px;box-shadow:0 0 0 8px #0ea5e922!important}`;
  const content = document.createElement("div"); content.innerHTML = panel.html; root.append(style, content); clearAnimation();
}
function showText(text) {
  if (!text) return; if (!assistantBubble) assistantBubble = addMessage("assistant", "");
  assistantBubble.textContent += text; messages.scrollTop = messages.scrollHeight;
}
function handleMessage(event) {
  if (event.data instanceof ArrayBuffer) { playPcm(event.data); return; }
  const payload = JSON.parse(event.data); console.info("[GEMINI_LIVE:EVENT]", payload);
  if (payload.type === "live:ready") { status.textContent = "Gemini Live đang lắng nghe."; connectionStatus.textContent = "Đã kết nối"; }
  if (payload.type === "live:input_ready") { voiceStatus.textContent = "Đang nghe… nhấn micro lần nữa để kết thúc."; startCapture(); }
  if (payload.type === "live:server_audio_received") voiceStatus.textContent = `Đã gửi ${payload.chunks} đoạn audio.`;
  if (payload.type === "input_transcript" && payload.text) voiceStatus.textContent = `Đã nghe: ${payload.text}`;
  if (payload.type === "panel") renderPanel(payload.panel);
  if (payload.type === "scene") pendingScene = { target_id: payload.scene.target_id, effect: payload.scene.effect };
  if (payload.type === "audio_format") sampleRate = Number(payload.sample_rate_hz);
  if (payload.type === "text") showText(payload.text);
  if (payload.type === "live:complete") { status.textContent = "Hoàn tất trình bày."; avatar.dataset.avatarState = "idle"; stopCapture(); socket?.close(); }
  if (payload.type === "live:error") { status.textContent = `Lỗi: ${payload.message}`; avatar.dataset.avatarState = "idle"; stopCapture(); socket?.close(); }
}
function openSocket(input_mode, query = "") {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${protocol}//${location.host}/ws/live`); socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => socket.send(JSON.stringify({ type: "live:start", session_id: sessionId, input_mode, query })));
  socket.addEventListener("message", handleMessage);
  socket.addEventListener("error", () => { status.textContent = "Không thể kết nối Gemini Live."; stopCapture(); });
  socket.addEventListener("close", () => { socket = null; connectionStatus.textContent = "Sẵn sàng"; });
}
function pcm16k(input, sourceRate) {
  const frames = Math.max(1, Math.round(input.length * 16000 / sourceRate)), data = new Int16Array(frames), ratio = input.length / frames;
  for (let i = 0; i < frames; i += 1) { const begin = Math.floor(i * ratio), end = Math.min(input.length, Math.max(begin + 1, Math.floor((i + 1) * ratio))); let sum = 0; for (let j = begin; j < end; j += 1) sum += input[j]; const v = Math.max(-1, Math.min(1, sum / (end - begin))); data[i] = v < 0 ? v * 0x8000 : v * 0x7fff; }
  return data.buffer;
}
function startCapture() {
  if (!microphoneStream || microphoneContext) return;
  microphoneContext = new AudioContext(); microphoneSource = microphoneContext.createMediaStreamSource(microphoneStream); microphoneProcessor = microphoneContext.createScriptProcessor(4096, 1, 1); muteGain = microphoneContext.createGain(); muteGain.gain.value = 0;
  microphoneProcessor.addEventListener("audioprocess", event => { if (recording && socket?.readyState === WebSocket.OPEN) socket.send(pcm16k(event.inputBuffer.getChannelData(0), event.inputBuffer.sampleRate)); });
  microphoneSource.connect(microphoneProcessor); microphoneProcessor.connect(muteGain); muteGain.connect(microphoneContext.destination);
}
function stopCapture() {
  recording = false; microphoneProcessor?.disconnect(); microphoneSource?.disconnect(); muteGain?.disconnect(); microphoneContext?.close(); microphoneStream?.getTracks().forEach(track => track.stop());
  microphoneStream = microphoneContext = microphoneSource = microphoneProcessor = muteGain = null; mic.classList.remove("listening"); mic.setAttribute("aria-pressed", "false");
}
form.addEventListener("submit", async event => { event.preventDefault(); const query = queryInput.value.trim(); if (!query || socket) return; await armAudio(); resetAudio(); clearAnimation(); assistantBubble = null; addMessage("user", query); queryInput.value = ""; status.textContent = "Đang xử lý…"; openSocket("text", query); });
document.querySelectorAll("[data-query]").forEach(button => button.addEventListener("click", () => { queryInput.value = button.dataset.query; form.requestSubmit(); }));
mic.addEventListener("click", async () => {
  if (recording) { recording = false; mic.setAttribute("aria-pressed", "false"); socket?.send(JSON.stringify({ type: "live:audio_end" })); voiceStatus.textContent = "Đang xử lý yêu cầu giọng nói…"; return; }
  if (socket || !navigator.mediaDevices?.getUserMedia) return;
  try { await armAudio(); resetAudio(); clearAnimation(); assistantBubble = null; microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount:1, echoCancellation:true, noiseSuppression:true, autoGainControl:true } }); recording = true; mic.classList.add("listening"); mic.setAttribute("aria-pressed", "true"); status.textContent = "Đang nghe…"; openSocket("audio"); } catch (error) { voiceStatus.textContent = `Không thể mở micro: ${error.message}`; stopCapture(); }
});
