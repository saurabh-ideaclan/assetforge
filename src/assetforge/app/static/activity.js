// The Activity centre (a drawer from the sidebar pill), task toasts and badges, and the shell's honesty banners:
// "server not reachable, reconnecting…" and "AssetForge was updated" (reloads itself when nothing is typed or
// playing). Everything here reads the store; nothing polls.
import { api, h, toast, copy } from "/static/lib.js";
import { onTasks, onStatus, refreshTasks, tasks as currentTasks } from "/static/store.js";
import { dirtyFields } from "/static/live.js";

const STATE_LABEL = { queued: "Queued", running: "Running", waiting: "Waiting for you", succeeded: "Done", failed: "Failed",
                      cancelled: "Cancelled", interrupted: "Interrupted" };
const WHO = { app: "the app", cli: "the CLI", script: "a script", agent: "an agent" };
const loadedAt = Date.now() / 1000;
let drawer = null;
let unseen = 0;
const toasted = new Set();

function elapsed(s) {
  if (s == null) return "";
  s = Math.max(0, Math.round(s));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
function liveElapsed(t) { return (t.ended_at || Date.now() / 1000) - (t.started_at || t.created_at); }

// ---------------------------------------------------------------------- pill, badge, toasts

export function initActivity() {
  const pill = document.getElementById("activity-pill");
  pill.hidden = false;
  pill.setAttribute("role", "button");
  pill.tabIndex = 0;
  pill.removeAttribute("href");
  pill.addEventListener("click", (e) => { e.preventDefault(); toggleDrawer(); });
  pill.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleDrawer(); } });
  onTasks((data, prev) => {
    renderPill(pill, data);
    notify(data, prev);
    if (drawer) renderDrawer();
  });
  initBanners();
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && drawer) closeDrawer(); });
}

function renderPill(pill, data) {
  const c = data.counts || {};
  const live = (c.running || 0) + (c.queued || 0);
  const parts = [];
  if (live) parts.push(h("span.spinner"), ` ${live} running`);
  if (c.waiting) parts.push(h("span.ap-wait", ` ${live ? "· " : ""}${c.waiting} waiting for you`));
  if (!parts.length) parts.push(h("span.ap-idle", "◷"), " Activity");
  if (unseen) parts.push(h("span.nav-badge", { title: "Ended since you last looked" }, String(unseen)));
  pill.replaceChildren(...parts);
  pill.classList.toggle("busy", !!live);
  pill.classList.toggle("waiting", !!c.waiting);
  pill.title = "Activity: running, waiting for you, finished and failed work, wherever it was started";
  const storyBadge = document.getElementById("story-badge");
  if (storyBadge) {
    const stories = new Set((data.tasks || []).filter((t) => t.story && ["running", "queued", "waiting"].includes(t.state)).map((t) => t.story));
    storyBadge.textContent = String(stories.size);
    storyBadge.hidden = !stories.size;
  }
}

function notify(data, prev) {
  const before = new Map((prev?.tasks || []).map((t) => [t.id, t.state]));
  for (const t of data.tasks || []) {
    const was = before.get(t.id);
    const ended = !["queued", "running"].includes(t.state);
    const fresh = ended && (t.ended_at || 0) >= loadedAt - 1 && (was === undefined ? !!prev : ["queued", "running"].includes(was));
    if (!fresh || toasted.has(t.id)) continue;
    toasted.add(t.id);
    unseen += 1;
    const actions = [];
    if (t.result_link) actions.push({ label: "Open", onclick: () => (location.hash = t.result_link) });
    actions.push({ label: "Details", onclick: () => openDrawer(t.id) });
    const kind = t.state === "succeeded" ? "good" : t.state === "waiting" ? "warn" : "bad";
    const text = t.state === "succeeded" ? `Done: ${t.title}` : t.state === "waiting" ? `Needs you: ${t.title}` : `${STATE_LABEL[t.state]}: ${t.title}`;
    toast(text, { actions, kind, timeout: t.state === "succeeded" ? 6000 : 12000 });
  }
  const pill = document.getElementById("activity-pill");
  if (pill) renderPill(pill, data);
}

// ---------------------------------------------------------------------- drawer

let focusId = null;
let clock = null;

export function openDrawer(id = null) { focusId = id; if (!drawer) toggleDrawer(); else renderDrawer(); }
function closeDrawer() { drawer?.remove(); drawer = null; clearInterval(clock); }

function toggleDrawer() {
  if (drawer) return closeDrawer();
  drawer = h("aside.ac-drawer", { role: "dialog", "aria-label": "Activity" });
  document.body.append(drawer);
  unseen = 0;
  const pill = document.getElementById("activity-pill");
  renderPill(pill, currentTasks());
  refreshTasks();
  renderDrawer();
  clock = setInterval(() => drawer?.querySelectorAll("[data-elapsed]").forEach((el) => {  // a ticking clock, not a poll
    const t = (currentTasks().tasks || []).find((x) => x.id === el.dataset.elapsed);
    if (t && ["running", "queued"].includes(t.state)) el.textContent = elapsed(liveElapsed(t));
  }), 1000);
}

function renderDrawer() {
  if (!drawer) return;
  const all = currentTasks().tasks || [];
  const groups = [
    ["Running", all.filter((t) => t.state === "running" || t.state === "queued")],
    ["Waiting for you", all.filter((t) => t.state === "waiting")],
    ["Failed or interrupted", all.filter((t) => ["failed", "interrupted", "cancelled"].includes(t.state)).slice(0, 12)],
    ["Finished", all.filter((t) => t.state === "succeeded").slice(0, 12)],
  ];
  const scroll = drawer.querySelector(".ac-body")?.scrollTop || 0;
  const openRows = new Set([...drawer.querySelectorAll(".ac-task[open]")].map((d) => d.dataset.id));
  if (focusId) openRows.add(focusId);
  const body = h("div.ac-body", groups.map(([name, items]) => h("section.ac-group", h("h3", name, h("span.muted", ` ${items.length}`)),
    items.length ? items.map((t) => row(t, openRows.has(t.id))) : h("div.help", name === "Running" ? "Nothing running." : "None."))));
  drawer.replaceChildren(
    h("div.ac-head", h("h2", "Activity"), h("span.help", "Work started in the app, the CLI or a script"),
      h("button.close", { type: "button", "aria-label": "Close", onclick: closeDrawer }, "×")),
    body);
  body.scrollTop = scroll;
  if (focusId) { drawer.querySelector(`.ac-task[data-id="${focusId}"]`)?.scrollIntoView({ block: "nearest" }); focusId = null; }
}

function row(t, open) {
  const done = (t.steps || []).filter((s) => s.state === "done").length;
  const total = (t.steps || []).length;
  const progress = t.progress != null ? Math.round(t.progress * 100) : null;
  const act = async (path, label, body) => {
    try { await api(`/api/tasks/${t.id}/${path}`, { method: "POST", body: body || {} }); toast(label); refreshTasks(); }
    catch (err) { toast(err.message, { kind: "bad" }); }
  };
  const cancel = ["running", "queued"].includes(t.state)
    ? h("button.btn.small", { type: "button", disabled: !t.can_cancel || t.cancel_requested,
        title: t.can_cancel ? "Stops after the step in progress; finished work is kept" : "This task can't stop part-way; it will finish what it is doing",
        onclick: (e) => { e.stopPropagation(); act("cancel", "Cancelling…"); } }, t.cancel_requested ? "Cancelling…" : t.can_cancel ? "Cancel" : "Can't cancel")
    : null;
  const paidWait = t.state === "waiting" && t.error?.details?.kind === "needs_confirmation";
  const retry = t.retryable ? h("button.btn.small", { type: "button", onclick: (e) => { e.stopPropagation(); act("retry", "Started again", paidWait ? { allow_paid: true } : {}); } }, paidWait ? "Allow paid and retry" : "Retry") : null;
  const dismiss = t.state === "waiting" ? h("button.btn.small", { type: "button", onclick: (e) => { e.stopPropagation(); act("dismiss", "Dismissed"); } }, "Dismiss") : null;
  const openBtn = t.result_link ? h("a.btn.small.primary", { href: t.result_link, onclick: () => closeDrawer() }, t.state === "waiting" ? "Decide" : "Open") : null;
  const d = h("details.ac-task", { class: t.state, open, dataset: { id: t.id } },
    h("summary",
      h("span.ac-dot", { class: t.state }),
      h("div.ac-main",
        h("div.ac-title", t.title),
        h("div.help", `${STATE_LABEL[t.state] || t.state} · started by ${WHO[t.started_by] || t.started_by}`,
          " · ", h("span", { dataset: { elapsed: t.id } }, elapsed(liveElapsed(t))),
          total ? ` · ${done}/${total} steps` : "", progress != null && t.state === "running" ? ` · ${progress}%` : ""),
        t.message && t.state !== "succeeded" ? h("div.ac-msg", t.message) : null,
        progress != null && ["running", "queued"].includes(t.state) ? h("div.pbar", h("div.pbar-fill", { style: `width:${progress}%` })) : null),
      h("div.ac-actions", openBtn, cancel, retry, dismiss)),
    h("div.ac-detail",
      t.error ? h("div.ac-error", h("b", t.error.code || "Error"), " ", t.error.message || "", t.error.hint ? h("div.help", t.error.hint) : null) : null,
      total ? h("ol.ac-steps", (t.steps || []).slice(-30).map((s) => h("li", { class: s.state }, s.label))) : null,
      t.log_tail?.length ? h("pre.ac-log", t.log_tail.join("\n")) : null,
      t.command ? h("div.help", "Command: ", h("code", t.command), " ", h("button.linklike", { type: "button", onclick: () => copy(t.command, "Command copied") }, "copy")) : null,
      h("div.help", `${t.kind} · ${t.id}${t.story ? " · story " + t.story : ""}${t.pid ? ` · pid ${t.pid}` : ""}`)));
  return d;
}

// ---------------------------------------------------------------------- banners: cut off, out of date

function initBanners() {
  const banner = h("div.af-banner", { role: "status", hidden: true });
  document.querySelector(".shell")?.prepend(banner);
  let outdatedSince = 0;
  let autoTimer = null;
  onStatus((s) => {
    if (s.connection === "reconnecting" || s.connection === "forbidden") {
      banner.className = "af-banner bad";
      banner.replaceChildren(h("span.spinner"), s.connection === "forbidden"
        ? " The session token is no longer valid (the server restarted with a new one). Reopen the app window."
        : " Server not reachable — reconnecting…", h("span.help", " What you see may be out of date."));
      banner.hidden = false;
    } else if (s.outdated) {
      if (!outdatedSince) outdatedSince = Date.now();
      banner.className = "af-banner info";
      banner.replaceChildren("AssetForge was updated while this page was open. ",
        h("button.btn.small.primary", { type: "button", onclick: () => location.reload() }, "Reload now"),
        h("span.help", " It reloads by itself once nothing is typed or playing."));
      banner.hidden = false;
      clearInterval(autoTimer);
      autoTimer = setInterval(() => { if (safeToReload()) location.reload(); }, 3000);  // a check, not a fetch
    } else {
      banner.hidden = true;
      clearInterval(autoTimer);
    }
    document.body.classList.toggle("af-offline", s.connection === "reconnecting");
  });
}

function playing() {
  const media = [...document.querySelectorAll("audio, video")];
  for (const f of document.querySelectorAll("iframe")) {
    try { media.push(...f.contentDocument.querySelectorAll("audio, video")); } catch { /* another origin */ }
  }
  return media.some((m) => !m.paused && !m.ended);
}

function safeToReload() {
  if (document.querySelector(".af-confirm, .cmdk, .signin")) return false;
  const a = document.activeElement;
  if (a && a.matches?.("input, textarea, select, [contenteditable=true]")) return false;
  return !dirtyFields().some((x) => x.value) && !playing();
}

export { safeToReload };
