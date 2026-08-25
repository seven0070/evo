const byId = (id) => document.getElementById(id);

const state = {
  connected: false,
  profile: null,
  runtime: null,
  tasks: [],
};

async function invoke(command, args = {}) {
  const tauri = window.__TAURI__;
  if (!tauri?.core?.invoke) {
    throw new Error("Evo desktop bridge is unavailable. Run this UI through the Tauri application.");
  }
  return tauri.core.invoke(command, args);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;"}[char]));
}

function setHealth(status, label = status) {
  const pill = byId("health-pill");
  pill.textContent = label;
  pill.className = `pill ${status === "healthy" ? "healthy" : status === "degraded" ? "warn" : "neutral"}`;
}

function renderStatus() {
  const runtime = state.runtime || {};
  const record = runtime.runtime || {};
  byId("runtime-state").textContent = record.state || "—";
  byId("runtime-health").textContent = runtime.health || "—";
  byId("queue-depth").textContent = runtime.queue_depth ?? "—";
  byId("approval-count").textContent = runtime.pending_approvals ?? "—";
  byId("workspace-name").textContent = record.current_environment || "local workspace";
  byId("profile-summary").textContent = state.profile ? `${state.profile.model} · ${state.profile.max_tasks_per_cycle} task/cycle · external actions disabled` : "Personal profile loading…";
  setHealth(runtime.health || "neutral", runtime.health || "Connecting");
  byId("last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

function renderApprovals() {
  const approvals = (state.runtime?.pending_approvals_list || []).filter(Boolean);
  byId("approval-badge").textContent = `${approvals.length} pending`;
  if (!approvals.length) {
    byId("approval-list").className = "empty-state";
    byId("approval-list").textContent = "No approval requests.";
    return;
  }
  byId("approval-list").className = "list";
  byId("approval-list").innerHTML = approvals.map((item) => `<div class="list-item"><div class="row"><h3>${escapeHtml(item.task_id || "Approval request")}</h3><span class="status-waiting">Waiting</span></div><p>${escapeHtml(item.reason || "This task requires explicit human approval.")}</p><button class="button primary approve-button" data-task-id="${escapeHtml(item.task_id)}" data-scope="${escapeHtml(item.scope_hash || "")}">Approve once</button></div>`).join("");
  document.querySelectorAll(".approve-button").forEach((button) => button.addEventListener("click", async () => {
    try {
      await invoke("approve_task", { taskId: button.dataset.taskId, scopeHash: button.dataset.scope, reason: "Approved by desktop operator" });
      byId("goal-message").textContent = "Approval recorded. Evo may continue only through Runtime.";
      await refresh();
    } catch (error) { byId("goal-message").textContent = error.message; }
  }));
}

function renderHistory() {
  const tasks = state.tasks.filter((task) => task.status === "completed" || task.status === "failed" || task.status === "blocked").slice(0, 8);
  if (!tasks.length) { byId("history-list").className = "empty-state"; byId("history-list").textContent = "No completed goals yet."; return; }
  byId("history-list").className = "list";
  byId("history-list").innerHTML = tasks.map((task) => `<div class="list-item"><div class="row"><h3>${escapeHtml(task.goal)}</h3><span class="status-${escapeHtml(task.status)}">${escapeHtml(task.status)}</span></div><p>${task.metadata?.verified ? "Authoritatively verified" : "Verification evidence is not complete"} · ${escapeHtml(task.task_id)}</p></div>`).join("");
}

async function refresh() {
  try {
    state.profile = await invoke("get_profile");
    state.runtime = await invoke("get_status");
    state.tasks = await invoke("list_tasks", { limit: 30 });
    state.connected = true;
    renderStatus(); renderApprovals(); renderHistory();
  } catch (error) {
    state.connected = false;
    setHealth("neutral", "Offline");
    byId("goal-message").textContent = error.message;
  }
}

byId("run-button").addEventListener("click", async () => {
  const goal = byId("goal-input").value.trim();
  if (!goal) { byId("goal-message").textContent = "Enter a concrete goal first."; return; }
  const button = byId("run-button");
  button.disabled = true; button.textContent = "Planning…";
  try {
    const result = await invoke("submit_goal", { goal, approvalRequired: byId("approval-check").checked });
    byId("goal-message").textContent = result.message || `Goal queued: ${result.task_id}`;
    byId("goal-input").value = "";
    await invoke("run_cycle");
    await refresh();
  } catch (error) { byId("goal-message").textContent = error.message; }
  finally { button.disabled = false; button.textContent = "Run goal"; }
});

byId("refresh-button").addEventListener("click", refresh);
byId("safe-mode-button").addEventListener("click", async () => {
  try { const result = await invoke("set_safe_mode", { enabled: true, reason: "Enabled by desktop operator" }); byId("goal-message").textContent = result.message || "Safe mode enabled."; await refresh(); }
  catch (error) { byId("goal-message").textContent = error.message; }
});
byId("kill-switch-button").addEventListener("click", async () => {
  if (!window.confirm("Activate Evo’s emergency kill switch? Restart remains blocked until it is cleared through the existing operator path.")) return;
  try { const result = await invoke("kill_switch", { reason: "Activated by desktop operator" }); byId("goal-message").textContent = result.message || "Kill switch activated."; await refresh(); }
  catch (error) { byId("goal-message").textContent = error.message; }
});

refresh();
setInterval(refresh, 4000);
