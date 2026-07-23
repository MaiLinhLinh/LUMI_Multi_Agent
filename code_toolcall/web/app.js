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
const sendButton = document.querySelector("#sendButton");
const clearButton = document.querySelector("#clearButton");
const connectionStatus = document.querySelector("#connectionStatus");
const firstTextLog = document.querySelector("#firstTextLog");
const messageTemplate = document.querySelector("#messageTemplate");

const SESSION_KEY = "lumi_web_session_id";
const YOUTUBE_NOCOOKIE_ORIGIN = "https://www.youtube-nocookie.com";
const YOUTUBE_VIDEO_ID_PATTERN = /^[A-Za-z0-9_-]{11}$/;
const STREAM_CHARACTER_DELAY_MS = 18;
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

function render() {
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

  messagesElement.replaceChildren();
  for (const message of state.messages || []) {
    messagesElement.appendChild(createMessage(message.role, message.content));
  }
  if (streamingDraft !== null) {
    messagesElement.appendChild(createMessage("assistant", streamingDraft, true));
  } else if (busy) {
    messagesElement.appendChild(createTypingMessage());
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
  window.requestAnimationFrame(() => {
    messagesElement.scrollTop = messagesElement.scrollHeight;
  });
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

async function submitQuery(query) {
  const cleanQuery = query.trim();
  if (!cleanQuery || busy) return;

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
    render();
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
queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
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
