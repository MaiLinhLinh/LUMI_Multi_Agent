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
const messageTemplate = document.querySelector("#messageTemplate");

const SESSION_KEY = "lumi_web_session_id";
const YOUTUBE_NOCOOKIE_ORIGIN = "https://www.youtube-nocookie.com";
const YOUTUBE_VIDEO_ID_PATTERN = /^[A-Za-z0-9_-]{11}$/;
const sessionId = getOrCreateSessionId();
let state = {
  messages: [],
  active_panel: {},
  active_panel_revision: 0,
  has_active_panel: false,
};
let busy = false;
let renderedPanelRevision = null;

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
  if (busy) messagesElement.appendChild(createTypingMessage());

  suggestions.classList.toggle("hidden", hasMessages || busy);
  connectionStatus.textContent = busy ? "Đang xử lý..." : "Sẵn sàng";
  connectionStatus.classList.toggle("busy", busy);
  queryInput.disabled = busy;
  sendButton.disabled = busy;
  window.requestAnimationFrame(() => {
    messagesElement.scrollTop = messagesElement.scrollHeight;
  });
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
  musicView.hidden = true;
  weatherView.hidden = false;
  contentEyebrow.textContent = "Kết quả trực quan";
  contentTitle.textContent = "Thông tin thời tiết";
  contentBadge.textContent = "Dữ liệu từ Redis";
  if (weatherFrame.srcdoc !== panel.html) weatherFrame.srcdoc = panel.html;
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
    musicFrame.hidden = true;
    musicStopped.hidden = false;
    return;
  }

  musicStopped.hidden = true;
  musicFrame.hidden = false;
  musicFrame.src = youtubeEmbedUrl(music.video_id);
}

function youtubeEmbedUrl(videoId) {
  if (!YOUTUBE_VIDEO_ID_PATTERN.test(videoId)) return "";
  return `${YOUTUBE_NOCOOKIE_ORIGIN}/embed/${encodeURIComponent(videoId)}?autoplay=1&rel=0`;
}

function clearActivePanel() {
  weatherFrame.srcdoc = "";
  musicFrame.removeAttribute("src");
  weatherView.hidden = true;
  musicView.hidden = true;
}

function createMessage(role, content) {
  const fragment = messageTemplate.content.cloneNode(true);
  const article = fragment.querySelector(".message");
  const avatar = fragment.querySelector(".avatar");
  const bubble = fragment.querySelector(".bubble");
  const isUser = role === "user";
  article.classList.add(isUser ? "user" : "assistant");
  avatar.textContent = isUser ? "B" : "L";
  bubble.textContent = content || "";
  return fragment;
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

  busy = true;
  state.messages = [...(state.messages || []), { role: "user", content: cleanQuery }];
  queryInput.value = "";
  resizeInput();
  render();

  try {
    state = await requestJson("/api/chat", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, query: cleanQuery }),
    });
  } catch (error) {
    state.messages = [
      ...(state.messages || []),
      { role: "assistant", content: error.message || "Đã xảy ra lỗi kết nối." },
    ];
  } finally {
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
