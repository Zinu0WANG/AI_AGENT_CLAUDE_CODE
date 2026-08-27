const state = {
  userId: "demo-user",
  sessionId: localStorage.getItem("memory-agent-session"),
  busy: false,
};

const elements = {
  form: document.querySelector("#chatForm"),
  input: document.querySelector("#messageInput"),
  send: document.querySelector("#sendButton"),
  messages: document.querySelector("#messages"),
  session: document.querySelector("#sessionLabel"),
  summary: document.querySelector("#summaryText"),
  memories: document.querySelector("#memoryList"),
  count: document.querySelector("#memoryCount"),
  search: document.querySelector("#memorySearch"),
  status: document.querySelector("#statusFilter"),
  recall: document.querySelector("#recallTrace"),
  newSession: document.querySelector("#newSessionButton"),
  mode: document.querySelector("#modeText"),
  dot: document.querySelector("#statusDot"),
  toast: document.querySelector("#toast"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `请求失败：${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  setTimeout(() => elements.toast.classList.remove("show"), 2200);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderMessages(messages) {
  elements.messages.innerHTML = messages.length
    ? messages.map((item) => `<div class="message ${item.role}">${escapeHtml(item.content)}</div>`).join("")
    : `<div class="empty-state"><strong>从一次自我介绍开始</strong><span>试试：“我叫小林，我喜欢简洁的中文回答。”</span></div>`;
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

async function loadSession() {
  if (!state.sessionId) return renderMessages([]);
  try {
    const session = await api(`/api/sessions/${state.sessionId}`);
    elements.session.textContent = `SESSION / ${session.id.slice(0, 12)}`;
    elements.summary.textContent = session.summary || "会话尚未触发压缩。";
    renderMessages(session.messages);
  } catch {
    state.sessionId = null;
    localStorage.removeItem("memory-agent-session");
    renderMessages([]);
  }
}

function memoryActions(memory) {
  if (memory.status === "active") {
    return `<button data-action="archive" data-id="${memory.id}">归档</button>
            <button data-action="delete" data-id="${memory.id}" class="danger">移到回收站</button>`;
  }
  if (memory.status === "archived" || memory.status === "deleted") {
    return `<button data-action="restore" data-id="${memory.id}">恢复</button>
            <button data-action="permanent" data-id="${memory.id}" class="danger">永久删除</button>`;
  }
  return "";
}

function renderMemories(memories) {
  elements.count.textContent = `${memories.length} 条`;
  elements.memories.innerHTML = memories.length
    ? memories.map((memory) => `
      <article class="memory-card">
        <div class="memory-top">
          <span class="type-badge">${escapeHtml(memory.type)}</span>
          <span class="caption">${new Date(memory.updated_at).toLocaleDateString()}</span>
        </div>
        <p>${escapeHtml(memory.content)}</p>
        <div class="memory-meta">
          <span>置信度 ${Math.round(memory.confidence * 100)}%</span>
          <span>重要度 ${Math.round(memory.importance * 100)}%</span>
          <span>召回 ${memory.access_count} 次</span>
        </div>
        <div class="memory-actions">${memoryActions(memory)}</div>
      </article>`).join("")
    : `<div class="empty-state compact">当前分类下还没有记忆。</div>`;
}

async function loadMemories() {
  const query = elements.search.value.trim();
  if (query && elements.status.value === "active") {
    const results = await api("/api/memories/search", {
      method: "POST",
      body: JSON.stringify({ query, user_id: state.userId, limit: 20 }),
    });
    renderMemories(results);
    return;
  }
  const memories = await api(`/api/memories?user_id=${state.userId}&status=${elements.status.value}`);
  const filtered = query
    ? memories.filter((item) => item.content.toLowerCase().includes(query.toLowerCase()))
    : memories;
  renderMemories(filtered);
}

function scoreBar(label, value) {
  const percentage = Math.round((value || 0) * 100);
  return `<div class="score-row">
    <span>${label}</span>
    <span class="track"><i style="width:${percentage}%"></i></span>
    <span>${percentage}</span>
  </div>`;
}

function renderRecall(memories) {
  elements.recall.innerHTML = memories.length
    ? memories.map((memory) => `
      <article class="recall-card">
        <h3>${escapeHtml(memory.reason)} · ${Math.round(memory.score * 100)} 分</h3>
        <p>${escapeHtml(memory.content)}</p>
        ${scoreBar("语义", memory.score_breakdown.semantic)}
        ${scoreBar("关键词", memory.score_breakdown.lexical)}
        ${scoreBar("新鲜度", memory.score_breakdown.recency)}
      </article>`).join("")
    : `<div class="empty-state compact">本轮没有召回长期记忆。</div>`;
}

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.input.value.trim();
  if (!message || state.busy) return;
  state.busy = true;
  elements.send.disabled = true;
  const previous = [...elements.messages.querySelectorAll(".message")].map((node) => ({
    role: node.classList.contains("user") ? "user" : "assistant",
    content: node.textContent,
  }));
  renderMessages([...previous, { role: "user", content: message }, { role: "assistant pending", content: "正在思考与检索记忆…" }]);
  elements.input.value = "";
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        session_id: state.sessionId,
        user_id: state.userId,
      }),
    });
    state.sessionId = result.session_id;
    localStorage.setItem("memory-agent-session", state.sessionId);
    elements.session.textContent = `SESSION / ${state.sessionId.slice(0, 12)}`;
    elements.summary.textContent = result.short_term.summary || "会话尚未触发压缩。";
    renderMessages(result.short_term.recent_messages);
    renderRecall(result.recalled_memories);
    await loadMemories();
    if (result.written_memories.length) toast(`写入 ${result.written_memories.length} 条长期记忆`);
  } catch (error) {
    toast(error.message);
    await loadSession();
  } finally {
    state.busy = false;
    elements.send.disabled = false;
    elements.input.focus();
  }
});

elements.newSession.addEventListener("click", async () => {
  const session = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ user_id: state.userId }),
  });
  state.sessionId = session.id;
  localStorage.setItem("memory-agent-session", session.id);
  elements.session.textContent = `SESSION / ${session.id.slice(0, 12)}`;
  elements.summary.textContent = "会话尚未触发压缩。";
  renderMessages([]);
  renderRecall([]);
  toast("已创建新会话，长期记忆仍然保留");
});

elements.memories.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { action, id } = button.dataset;
  try {
    if (action === "permanent") {
      if (!confirm("永久删除后无法恢复，确认继续？")) return;
      await api(`/api/memories/${id}/permanent`, { method: "DELETE" });
    } else {
      const status = { archive: "archived", delete: "deleted", restore: "active" }[action];
      await api(`/api/memories/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
    }
    await loadMemories();
  } catch (error) {
    toast(error.message);
  }
});

let searchTimer;
elements.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadMemories().catch((error) => toast(error.message)), 280);
});
elements.status.addEventListener("change", () => loadMemories().catch((error) => toast(error.message)));

async function boot() {
  try {
    const health = await api("/api/health");
    elements.mode.textContent = `${health.chat_mode} / ${health.retrieval_mode}`;
    elements.dot.classList.add("online");
    await Promise.all([loadSession(), loadMemories()]);
  } catch (error) {
    elements.mode.textContent = "连接失败";
    toast(error.message);
  }
}

boot();

