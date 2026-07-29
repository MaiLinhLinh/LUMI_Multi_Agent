const workspace = document.querySelector("#workspace");
const welcome = document.querySelector("#welcome");
const contentPanel = document.querySelector("#contentPanel");
const contentEyebrow = document.querySelector("#contentEyebrow");
const contentTitle = document.querySelector("#contentTitle");
const contentBadge = document.querySelector("#contentBadge span");
const weatherView = document.querySelector("#weatherView");
const weatherFrame = document.querySelector("#weatherFrame");
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
let pendingSpeechText = "";
let streamedSpeechFinishTimer = null;
let speechReadyTimer = null;
let activeSpeechTurnId = null;
let speechUnavailable = false;

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
  if (hasDashboard && revision !== renderedPanelRevision) {
    renderActivePanel(panel);
    renderedPanelRevision = revision;
  } else if (!hasDashboard && renderedPanelRevision !== null) {
    clearActivePanel();
    renderedPanelRevision = null;
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

function closeSpeechSocket() {
  if (speechSocket && speechSocket.readyState < WebSocket.CLOSING) speechSocket.close();
  speechSocket = null;
}

function stopSpeakerAudio() {
  for (const source of speakerSources) source.stop();
  speakerSources.clear();
  speakerContext?.close?.();
  speakerContext = null;
  speakerNextStartTime = 0;
  voiceSpeaking = false;
}

function pcmSampleRate(mimeType) {
  const match = /rate=(\d+)/i.exec(mimeType || "");
  return match ? Number(match[1]) : 24000;
}

function playPcmChunk(arrayBuffer, mimeType = "audio/pcm;rate=24000") {
  if (!speakerContext) speakerContext = new AudioContext();
  const pcm = new Int16Array(arrayBuffer);
  if (!pcm.length) return;
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

function resetStreamedSpeechState() {
  streamedSpeechReady = false;
  streamedSpeechInFlight = false;
  pendingSpeechText = "";
  if (streamedSpeechFinishTimer !== null) window.clearTimeout(streamedSpeechFinishTimer);
  streamedSpeechFinishTimer = null;
  if (speechReadyTimer !== null) window.clearTimeout(speechReadyTimer);
  speechReadyTimer = null;
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

function sendCompletedSpeech() {
  if (!streamedSpeechReady || streamedSpeechInFlight || !pendingSpeechText || !speechSocket || speechSocket.readyState !== WebSocket.OPEN) return;
  const text = pendingSpeechText;
  pendingSpeechText = "";
  streamedSpeechInFlight = true;
  speechSocket.send(JSON.stringify({ type: "voice:speak", turn_id: activeSpeechTurnId, text }));
}

function startStreamedSpeech({ startReadyTimeout = true } = {}) {
  if (voiceSpeaking) return;
  resetStreamedSpeechState();
  stopSpeakerAudio();
  closeSpeechSocket();
  voiceSpeaking = true;
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
      sendCompletedSpeech();
    } else if (message.type === "voice_speech_end" && message.turn_id === activeSpeechTurnId) {
      streamedSpeechInFlight = false;
      finishStreamedSpeechAfterPlayback();
    } else if (message.type === "voice_error") {
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
    stopSpeakerAudio();
    resetStreamedSpeechState();
    microphoneButton.disabled = busy || voiceAwaitingTranscript;
  });
}

function speakCompletedResponse(text) {
  if (!text?.trim()) return;
  if (speechUnavailable) return;
  if (!voiceSpeaking) startStreamedSpeech();
  pendingSpeechText = text.trim();
  sendCompletedSpeech();
}

function cancelStreamedSpeech() {
  if (!voiceSpeaking) return;
  stopSpeakerAudio();
  closeSpeechSocket();
  resetStreamedSpeechState();
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
    startStreamedSpeech({ startReadyTimeout: false });
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
          if (!streamedSpeechReady) startSpeechReadyTimer(activeSpeechTurnId);
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

function renderWeatherPanel(panel) {
  musicFrame.removeAttribute("src");
  delete musicFrame.dataset.videoId;
  musicView.hidden = true;
  weatherView.hidden = false;
  contentEyebrow.textContent = "Kết quả trực quan";
  contentTitle.textContent = "Thông tin thời tiết";
  contentBadge.textContent = "Dữ liệu từ Redis";
  weatherFrame.srcdoc = panel.html;
}

function renderMusicPanel(panel) {
  const music = panel.music;
  weatherFrame.srcdoc = "";
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
  weatherFrame.srcdoc = "";
  musicFrame.removeAttribute("src");
  delete musicFrame.dataset.videoId;
  weatherView.hidden = true;
  musicView.hidden = true;
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
  let hasReceivedFirstTextDelta = false;
  try {
    await requestNdjson("/api/chat/stream", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, query: cleanQuery }),
    }, (event) => {
      if (event?.type === "timing") {
        recordLatencyMarker(event.marker, Number(event.elapsed_ms), "Server");
      } else if (event?.type === "text_delta") {
        if (!hasReceivedFirstTextDelta) {
          hasReceivedFirstTextDelta = true;
          recordLatencyMarker(
            "first_text_delta_received",
            performance.now() - requestStartedAt,
            "Frontend",
          );
        }
        characterStreamer.push(event.delta);
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
    if (speakResponse && finalAssistantAnswer) speakCompletedResponse(finalAssistantAnswer);
    state = finalState;
  } catch (error) {
    characterStreamer.abort();
    state.messages = [
      ...(state.messages || []),
      { role: "assistant", content: error.message || "Đã xảy ra lỗi kết nối." },
    ];
  } finally {
    streamingDraft = null;
    busy = false;
    render({
      preserveMessages: speakResponse && hasReceivedFirstTextDelta && Boolean(finalState),
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
  stopSpeakerAudio();
  closeVoiceSocket();
  closeSpeechSocket();
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
