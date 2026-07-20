const workspace = document.querySelector("#workspace");
const welcome = document.querySelector("#welcome");
const dashboardPanel = document.querySelector("#dashboardPanel");
const dashboardFrame = document.querySelector("#dashboardFrame");
const messagesElement = document.querySelector("#messages");
const suggestions = document.querySelector("#suggestions");
const chatForm = document.querySelector("#chatForm");
const queryInput = document.querySelector("#queryInput");
const sendButton = document.querySelector("#sendButton");
const clearButton = document.querySelector("#clearButton");
const connectionStatus = document.querySelector("#connectionStatus");
const messageTemplate = document.querySelector("#messageTemplate");

const SESSION_KEY = "lumi_web_session_id";
const sessionId = getOrCreateSessionId();
let state = { messages: [], visualization_html: "", has_visualization: false };
let busy = false;

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
  const hasDashboard = Boolean(state.has_visualization && state.visualization_html);
  const hasMessages = Boolean(state.messages?.length);
  workspace.classList.toggle("has-dashboard", hasDashboard);
  workspace.classList.toggle("no-dashboard", !hasDashboard);
  workspace.classList.toggle("has-messages", hasMessages);
  dashboardPanel.hidden = !hasDashboard;
  welcome.hidden = hasDashboard || hasMessages;

  if (hasDashboard && dashboardFrame.srcdoc !== state.visualization_html) {
    dashboardFrame.srcdoc = state.visualization_html;
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
    state = { messages: [{ role: "assistant", content: error.message }], visualization_html: "", has_visualization: false };
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
