// The client store: the one place that listens to the server. It holds the change feed's revisions (library, jobs,
// tasks, every story by area, publishing, logins, build), tells subscribers which topics changed, and keeps a small
// cache of fetched JSON so a panel can redraw at once and refresh behind the scenes.
//
//   watch(["story:kite-season:*", "tasks"], (changed) => …, { owner: element })
//
// Topics may end in "*" or contain "*" for one segment. A subscription is dropped when its owner element leaves the
// page (or when the view's scope ends). Callbacks are debounced and never overlap. Server-sent events first
// (/api/events), polling (/api/changes) when they are not available; reconnection is automatic.
import { api } from "/static/lib.js";

const state = {
  revisions: {}, seq: 0, startedAt: null, build: null, firstBuild: null,
  connection: "connecting", since: Date.now(), lastMessage: 0,
};
const subs = new Set();
const statusSubs = new Set();
let scope = null;
let es = null;
let pollTimer = null;
let watchdog = null;
let failures = 0;

function toRegex(pattern) {
  return new RegExp("^" + pattern.split("*").map((p) => p.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*") + "$");
}

/** Subscribe to topics; fn(changedTopics) runs after a change (debounced). Returns an unsubscribe function. */
export function watch(topics, fn, { owner = null, debounce = 150 } = {}) {
  const list = Array.isArray(topics) ? topics : [topics];
  const sub = { patterns: list.map(toRegex), fn, owner, debounce, pending: new Set(), timer: null, running: false, again: false,
                seen: owner ? owner.isConnected : true, scope };
  subs.add(sub);
  if (scope) scope.add(sub);
  return () => subs.delete(sub);
}

/** Views mount inside a scope; ending it drops every subscription made while it was open. */
export function beginScope() { scope = new Set(); return scope; }
export function endScope(s) { if (!s) return; for (const sub of s) subs.delete(sub); s.clear(); if (scope === s) scope = null; }

export function onStatus(fn) { statusSubs.add(fn); fn(status()); return () => statusSubs.delete(fn); }
export function status() {
  return { connection: state.connection, build: state.build, outdated: !!(state.firstBuild && state.build && state.build !== state.firstBuild),
           startedAt: state.startedAt };
}
export function revision(topic) { return state.revisions[topic]; }
function emitStatus() { const s = status(); statusSubs.forEach((fn) => { try { fn(s); } catch { /* a bad listener */ } }); }

function setConnection(c) { if (state.connection !== c) { state.connection = c; emitStatus(); } }

function dispatch(changed) {
  if (!changed.length) return;
  for (const sub of [...subs]) {
    if (sub.owner) {
      if (sub.owner.isConnected) sub.seen = true;
      else if (sub.seen) { subs.delete(sub); continue; }
      else continue;
    }
    const hit = changed.filter((t) => sub.patterns.some((re) => re.test(t)));
    if (!hit.length) continue;
    hit.forEach((t) => sub.pending.add(t));
    schedule(sub);
  }
}

function schedule(sub) {
  clearTimeout(sub.timer);
  sub.timer = setTimeout(async () => {
    if (sub.running) { sub.again = true; return; }
    const topics = [...sub.pending];
    sub.pending.clear();
    sub.running = true;
    try { await sub.fn(topics); } catch (err) { console.warn("live update failed", err); }
    finally {
      sub.running = false;
      if (sub.again || sub.pending.size) { sub.again = false; schedule(sub); }
    }
  }, sub.debounce);
}

function apply(payload) {
  state.lastMessage = Date.now();
  failures = 0;
  const restarted = state.startedAt !== null && payload.started_at !== state.startedAt;
  state.startedAt = payload.started_at;
  state.seq = payload.seq;
  if (!state.firstBuild) state.firstBuild = payload.build;
  const buildChanged = state.build !== payload.build;
  state.build = payload.build;
  const known = state.revisions;
  const first = !Object.keys(known).length;
  const next = payload.revisions || {};
  let changed;
  if (payload.full || restarted) changed = Object.keys({ ...known, ...next }).filter((k) => known[k] !== next[k]);
  else changed = (payload.changed || []).filter((k) => known[k] !== next[k]);
  state.revisions = { ...next };
  if (buildChanged) emitStatus();
  if (!first) dispatch(changed);
}

// ---------------------------------------------------------------------- transport

function openStream() {
  if (!window.EventSource) return startPolling();
  try { es?.close(); } catch { /* ignore */ }
  es = new EventSource(`/api/events${state.seq ? `?since=${state.seq}` : ""}`);
  const onData = (e) => { try { apply(JSON.parse(e.data)); setConnection("live"); } catch { /* malformed */ } };
  es.addEventListener("hello", onData);
  es.addEventListener("change", onData);
  es.addEventListener("ping", () => { state.lastMessage = Date.now(); if (state.connection !== "live") setConnection("live"); });
  es.onopen = () => { stopPolling(); };
  es.onerror = () => {
    failures += 1;
    if (es.readyState === EventSource.CLOSED) { es = null; startPolling(); }
    else if (failures > 1) setConnection("reconnecting");
  };
  clearInterval(watchdog);
  // a stream that went silent (sleep, a proxy, a dead server) is reopened; the server pings every 15 s
  watchdog = setInterval(() => {
    if (es && Date.now() - state.lastMessage > 40000) { setConnection("reconnecting"); openStream(); }
  }, 10000);
}

function startPolling() {
  if (pollTimer) return;
  const tick = async () => {
    pollTimer = setTimeout(tick, document.visibilityState === "visible" ? Math.min(2000 * Math.max(1, failures), 8000) : 10000);
    try {
      const data = await api(`/api/changes${state.seq ? `?since=${state.seq}` : ""}`);
      apply(data);
      setConnection(window.EventSource ? "polling" : "live");
      if (window.EventSource && !es && failures === 0 && Math.random() < 0.2) { stopPolling(); openStream(); } // try the stream again
    } catch (err) {
      failures += 1;
      setConnection(err.code === "FORBIDDEN" ? "forbidden" : "reconnecting");
    }
  };
  pollTimer = setTimeout(tick, 0);
}
function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }

let started = false;
export function connect() {
  if (started) return;
  started = true;
  openStream();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && (!es || es.readyState === EventSource.CLOSED)) { stopPolling(); openStream(); }
  });
  window.addEventListener("online", () => { stopPolling(); openStream(); });
}

// ---------------------------------------------------------------------- cached reads

const cache = new Map();
const MAX_CACHE = 80;

/** The last value fetched from `url` (or undefined). */
export function cached(url) { return cache.get(url); }

/** Fetch `url` as JSON and remember it (stale-while-revalidate for panels). */
export async function load(url) {
  const data = await api(url);
  cache.delete(url);
  cache.set(url, data);
  if (cache.size > MAX_CACHE) cache.delete(cache.keys().next().value);
  return data;
}

// ---------------------------------------------------------------------- tasks

let tasksData = { tasks: [], counts: {} };
const taskSubs = new Set();
let tasksLoading = null;

export function tasks() { return tasksData; }

/** fn(tasksPayload, previous) whenever the task list changes (loaded once at subscription). */
export function onTasks(fn) {
  taskSubs.add(fn);
  if (tasksData.loaded) fn(tasksData, null); else refreshTasks();
  return () => taskSubs.delete(fn);
}

export async function refreshTasks() {
  if (tasksLoading) return tasksLoading;
  tasksLoading = (async () => {
    try {
      const prev = tasksData;
      const data = await api("/api/tasks?limit=60");
      tasksData = { ...data, loaded: true };
      taskSubs.forEach((fn) => { try { fn(tasksData, prev.loaded ? prev : null); } catch (err) { console.warn(err); } });
    } catch { /* the connection banner says why */ }
    finally { tasksLoading = null; }
  })();
  return tasksLoading;
}
watch("tasks", () => refreshTasks(), { debounce: 100 });
scope = null;

/** Resolve when task `id` ends (any terminal or waiting state); onTick(task) on every change. Uses the feed. */
export function waitTask(id, onTick) {
  return new Promise((resolve) => {
    const check = async () => {
      try {
        const { task } = await api(`/api/tasks/${id}`);
        onTick?.(task);
        if (!["queued", "running"].includes(task.state)) { off(); resolve(task); }
      } catch (err) { off(); resolve({ id, state: "failed", error: { message: err.message } }); }
    };
    const off = watch("tasks", check, { debounce: 50 });
    check();
  });
}

/** Resolve when generation job `id` ends; onTick(seconds) while it runs. Uses the feed (jobs topic). */
export function waitJob(id, onTick) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const check = async () => {
      try {
        const j = await api(`/api/jobs/${id}`);
        if (["succeeded", "failed", "cancelled"].includes(j.job.status)) {
          off(); clearInterval(clock);
          j.job.status === "succeeded" ? resolve(j) : reject(Object.assign(new Error(j.job.error?.message || `Job ${j.job.status}`), j.job.error || {}));
        }
      } catch (err) { off(); clearInterval(clock); reject(err); }
    };
    const off = watch(["jobs", "library"], check, { debounce: 50 });
    const clock = setInterval(() => onTick?.(Math.round((Date.now() - started) / 1000)), 1000);  // a clock, not a poll
    check();
  });
}

window.afStore = { watch, status, revision, tasks: () => tasksData, state };  // for debugging and browser tests
