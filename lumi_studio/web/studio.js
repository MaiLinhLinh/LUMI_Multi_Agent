const state = { prompts: [], selected: null };
const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.detail || `HTTP ${response.status}`);
  return body;
}
function setText(selector, value) { $(selector).textContent = value || ""; }
function result(target, text, ok = false) { target.innerHTML = `<span class="${ok ? "ok" : "error"}">${text}</span>`; }

async function loadOverview() {
  const data = await api("/api/overview");
  setText("#projectStatus", `Draft: ${data.draft_name} · Backend gốc: chỉ đọc`);
}
async function loadPrompts() {
  const data = await api("/api/prompts"); state.prompts = data.prompts;
  const list = $("#promptList"); list.replaceChildren();
  for (const prompt of data.prompts) {
    const node = $("#promptTemplate").content.firstElementChild.cloneNode(true);
    node.dataset.id = prompt.prompt_id;
    node.querySelector(".prompt-item-owner").textContent = prompt.owner;
    node.querySelector(".prompt-item-title").textContent = prompt.title;
    node.querySelector(".prompt-item-trigger").textContent = prompt.trigger;
    node.addEventListener("click", () => selectPrompt(prompt.prompt_id)); list.append(node);
  }
}
async function selectPrompt(id) {
  const prompt = await api(`/api/prompts/${encodeURIComponent(id)}`); state.selected = prompt;
  document.querySelectorAll(".prompt-item").forEach((node) => node.classList.toggle("active", node.dataset.id === id));
  $("#emptyPrompt").hidden = true; $("#promptEditor").hidden = false;
  setText("#promptOwner", prompt.owner); setText("#promptTitle", prompt.title); setText("#promptDescription", prompt.description);
  setText("#promptPath", prompt.relative_path + (prompt.constant ? ` :: ${prompt.constant}` : "")); setText("#promptTrigger", prompt.trigger);
  $("#promptContent").value = prompt.content; $("#originalPrompt").textContent = prompt.original_content;
  $("#draftBadge").textContent = prompt.drafted ? "Đang dùng draft" : "Đang xem bản gốc";
  $("#promptContent").readOnly = !prompt.editable; $("#savePromptButton").hidden = !prompt.editable;
  setText("#saveStatus", prompt.editable ? "Lưu chỉ vào workspace draft." : "Prompt động: Studio chỉ hiển thị source logic ở bản đầu.");
}
async function savePrompt() {
  if (!state.selected) return; const button = $("#savePromptButton"); button.disabled = true;
  try { const data = await api(`/api/prompts/${encodeURIComponent(state.selected.prompt_id)}`, { method:"PUT", body:JSON.stringify({ content: $("#promptContent").value }) }); await loadPrompts(); await selectPrompt(data.prompt_id); setText("#saveStatus", "Đã lưu vào draft; backend gốc chưa đổi."); }
  catch (error) { setText("#saveStatus", error.message); } finally { button.disabled = false; }
}
async function validate() {
  const target = $("#validationResult"); target.textContent = "Đang validate...";
  try { const data = await api("/api/validate", { method:"POST" }); if (data.ok) result(target, `Draft hợp lệ. Domain đã kiểm tra: ${data.checked_domain_ids.join(", ") || "không có domain mới"}.`, true); else target.innerHTML = `<span class="error">Draft chưa hợp lệ.</span><ul>${data.errors.map((item) => `<li><b>${item.scope}</b>: ${item.detail}</li>`).join("")}</ul>`; }
  catch (error) { result(target, error.message); }
}
async function loadDomains() {
  const data = await api("/api/domains"); $("#domainList").innerHTML = `<span class="muted">Domain gốc: ${data.source_domain_ids.join(", ") || "—"}<br>Domain draft: ${data.draft_domain_ids.join(", ") || "chưa có"}</span>`;
}
async function createDomain() {
  const status = $("#domainStatus"); status.textContent = "Đang tạo...";
  try { const data = await api("/api/domains", { method:"POST", body:JSON.stringify({ domain_id:$("#domainId").value, presentation_prompt:$("#presentationPrompt").value, plan_prompt:$("#planPrompt").value }) }); setText("#domainStatus", `Đã tạo ${data.domain_id} trong draft.`); await loadDomains(); await validate(); }
  catch (error) { setText("#domainStatus", error.message); }
}
function sandboxUrl(item) {
  const port = Number(item?.port);
  if (Number.isInteger(port) && port > 0 && port <= 65535) {
    const protocol = item?.scheme === "https" ? "https:" : window.location.protocol;
    return `${protocol}//${window.location.hostname}:${port}`;
  }
  return item?.url || null;
}
async function loadSandboxes() {
  const data = await api("/api/sandboxes"); const root = $("#sandboxList"); root.replaceChildren();
  for (const item of data.sandboxes) { const node = document.createElement("div"); node.className = "sandbox"; const url = sandboxUrl(item); const link = url ? `<a href="${url}" target="_blank" rel="noreferrer">Mở ${url}</a>` : item.id; node.innerHTML = `<b>${item.id}</b><br>${link}<br><span class="muted">${item.status}</span>`; root.append(node); }
}
async function startSandbox() {
  const button = $("#startSandboxButton"); button.disabled = true; setText("#sandboxList", "Đang làm mới sandbox bằng draft hiện tại...");
  try { const data = await api("/api/sandboxes", { method:"POST" }); const url = sandboxUrl(data); if (data.status === "running" && url) $("#sandboxList").innerHTML = `<div class="sandbox"><span class="ok">Sandbox ${data.mode === "updated" ? "đã cập nhật" : "đã tạo"}.</span><br><a href="${url}" target="_blank" rel="noreferrer">Mở web test: ${url}</a></div>`; else $("#sandboxList").innerHTML = `<div class="sandbox"><span class="error">Sandbox không khởi động được.</span><pre>${data.detail || "Xem server.log trong workspace."}</pre></div>`; await loadSandboxes(); }
  catch (error) { result($("#sandboxList"), error.message); } finally { button.disabled = false; }
}
$("#savePromptButton").addEventListener("click", savePrompt); $("#validateButton").addEventListener("click", validate); $("#createDomainButton").addEventListener("click", createDomain); $("#startSandboxButton").addEventListener("click", startSandbox);
Promise.all([loadOverview(), loadPrompts(), loadDomains(), loadSandboxes()]).catch((error) => { setText("#projectStatus", error.message); });
