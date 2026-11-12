// Live re-rendering that keeps what the person is doing: scroll positions, open <details>, anything typed (or a
// field that has focus: the update waits until it loses focus), selection classes, playing audio (the player lives
// outside views). New and changed items (elements with data-live-key) are marked for a moment.
//
//   rerender(el, (fresh) => draw(fresh))          redraw el in place (render into a detached node, then swap)
//   panel(el, { url, topics, render })            a self-refreshing panel: cached draw, fetch, error with retry
import { api, h, errorBox } from "/static/lib.js";
import { watch, cached, load } from "/static/store.js";

// ---------------------------------------------------------------------- what the person touched

// Typed-in fields and toggled <details> are remembered so a redraw can put them back.
document.addEventListener("input", (e) => { if (e.target instanceof Element) e.target.__afTouched = true; }, true);
document.addEventListener("change", (e) => { if (e.target instanceof Element) e.target.__afTouched = true; }, true);
document.addEventListener("click", (e) => {
  const summary = e.target instanceof Element ? e.target.closest("summary") : null;
  if (summary?.parentElement?.tagName === "DETAILS") summary.parentElement.__afToggled = true;
}, true);

const EDITABLE = "input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]):not([type=range]), textarea, select, [contenteditable=true]";

/** True when the person is in the middle of something inside `el` (a focused text field). */
export function busyIn(el) {
  const a = document.activeElement;
  return !!(a && el.contains(a) && a.matches(EDITABLE) && !a.readOnly);
}

/** Fields the person typed into anywhere on the page (the app does not reload over them). */
export function dirtyFields(root = document) {
  return [...root.querySelectorAll("input, textarea, select")].filter((x) => x.__afTouched && x.isConnected && !x.readOnly && x.type !== "search");
}

function fieldKey(el) {
  return [el.tagName, el.type || "", el.name || el.getAttribute("aria-label") || el.placeholder || el.title || el.closest("label")?.textContent?.trim().slice(0, 40) || ""].join("|");
}
function detailsKey(d) { return (d.querySelector(":scope > summary")?.textContent || "").trim().slice(0, 80); }
function scrollKey(el, root) {
  const parts = [];
  for (let x = el; x && x !== root; x = x.parentElement) parts.unshift(`${x.tagName}.${[...x.classList].sort().join(".")}`);
  return parts.join(">");
}
function keyed(list, keyFn) {
  const seen = new Map();
  return list.map((el) => { const k = keyFn(el); const n = seen.get(k) || 0; seen.set(k, n + 1); return [`${k}#${n}`, el]; });
}

export function snapshot(root) {
  const fields = keyed([...root.querySelectorAll("input, textarea, select")], fieldKey)
    .filter(([, el]) => el.__afTouched)
    .map(([k, el]) => [k, el.type === "checkbox" || el.type === "radio" ? { checked: el.checked } : el.multiple
      ? { selected: [...el.selectedOptions].map((o) => o.value) } : { value: el.value, start: el.selectionStart, end: el.selectionEnd }, el === document.activeElement]);
  const details = keyed([...root.querySelectorAll("details")], detailsKey).filter(([, d]) => d.__afToggled).map(([k, d]) => [k, d.open]);
  const scrolls = [...root.querySelectorAll("*")].filter((el) => el.scrollTop > 0 || el.scrollLeft > 0)
    .map((el) => [scrollKey(el, root), el.scrollTop, el.scrollLeft]);
  const marks = new Map([...root.querySelectorAll("[data-live-key]")].map((el) => [el.dataset.liveKey, [el.dataset.liveSig ?? el.textContent, el.dataset.liveState]]));
  const main = root.closest("main") || document.querySelector("main");
  return { fields, details, scrolls, marks, mainTop: main?.scrollTop ?? 0, main };
}

export function restore(root, snap, { mark = true } = {}) {
  const fieldMap = new Map(keyed([...root.querySelectorAll("input, textarea, select")], fieldKey));
  for (const [k, v, focused] of snap.fields) {
    const el = fieldMap.get(k);
    if (!el) continue;
    if ("checked" in v) el.checked = v.checked;
    else if ("selected" in v) [...el.options].forEach((o) => (o.selected = v.selected.includes(o.value)));
    else if (el.value !== v.value) el.value = v.value;
    el.__afTouched = true;
    if (focused) { el.focus({ preventScroll: true }); try { if (v.start != null) el.setSelectionRange(v.start, v.end); } catch { /* not a text field */ } }
  }
  const detailMap = new Map(keyed([...root.querySelectorAll("details")], detailsKey));
  for (const [k, open] of snap.details) { const d = detailMap.get(k); if (d) { d.open = open; d.__afToggled = true; } }
  for (const [k, top, left] of snap.scrolls) {
    const el = [...root.querySelectorAll("*")].find((x) => scrollKey(x, root) === k);
    if (el) { el.scrollTop = top; el.scrollLeft = left; }
  }
  if (snap.main) snap.main.scrollTop = snap.mainTop;
  if (mark && snap.marks.size) {
    for (const el of root.querySelectorAll("[data-live-key]")) {
      const before = snap.marks.get(el.dataset.liveKey);
      const now = el.dataset.liveSig ?? el.textContent;
      if (before === undefined) flash(el, "af-new");
      else if (before[1] === "missing" && el.dataset.liveState && el.dataset.liveState !== "missing") flash(el, "af-new");
      else if (before[0] !== now) flash(el, "af-changed");
    }
  }
}

export function flash(el, cls = "af-changed") {
  el.classList.remove("af-new", "af-changed");
  void el.offsetWidth;  // restart the animation
  el.classList.add(cls);
  setTimeout(() => el.classList.remove(cls), 2600);
}

const deferred = new WeakMap();

/**
 * Redraw `root` in place: `render(fresh)` fills a detached element (it may be async), then its children replace
 * root's in one swap. Typed text, toggled sections, scroll and focus are put back. While a field inside has focus the
 * redraw waits for it to lose focus (a small note says an update is waiting).
 */
export async function rerender(root, render, { force = false, mark = true } = {}) {
  if (!force && busyIn(root)) {
    if (!deferred.has(root)) {
      const note = h("div.af-pending", { role: "status" }, "Updated in the background — ", h("button.linklike", { type: "button", onclick: () => run() }, "show the latest"));
      const run = () => {
        const latest = deferred.get(root)?.render || render;
        note.remove(); root.removeEventListener("focusout", onOut); deferred.delete(root);
        rerender(root, latest, { force: true, mark });
      };
      const onOut = () => setTimeout(() => { if (!busyIn(root) && deferred.has(root)) run(); }, 50);
      root.addEventListener("focusout", onOut);
      root.prepend(note);
      deferred.set(root, { render, note });
    } else deferred.get(root).render = render;
    return false;
  }
  const snap = snapshot(root);
  const fresh = document.createElement(root.tagName === "TBODY" ? "tbody" : "div");
  await render(fresh);
  const keepHeight = root.offsetHeight;
  root.style.minHeight = keepHeight ? `${keepHeight}px` : "";
  root.replaceChildren(...fresh.childNodes);
  restore(root, snap, { mark });
  requestAnimationFrame(() => { root.style.minHeight = ""; });
  return true;
}

// ---------------------------------------------------------------------- self-refreshing panels

/**
 * A panel that loads `url()` and draws it with `render(data, el, ctx)`, redraws at once from the cache when it is
 * rebuilt, refreshes when any of `topics` changes, and shows an error with Retry without losing what it showed.
 * Returns { reload, data }.
 */
export function panel(el, { url, topics = [], render, loading = "Loading…", mark = true, deps = null }) {
  const api_ = { data: undefined, reload };
  const u = () => (typeof url === "function" ? url() : url);
  const hit = cached(u());
  if (hit !== undefined) { api_.data = hit; drawInto(hit, false); }
  else el.replaceChildren(h("div.empty.small", h("span.spinner"), " ", loading));
  reload(hit !== undefined);
  if (topics.length) watch(topics, () => reload(true), { owner: el });

  async function drawInto(data, live) {
    if (!live) { el.replaceChildren(); await render(data, el, api_); return; }
    await rerender(el, (fresh) => render(data, fresh, api_), { mark });
  }
  async function reload(live = true) {
    try {
      const data = await load(u());
      const sig = JSON.stringify([data, deps ? deps() : null]);
      const same = api_.data !== undefined && sig === api_.sig;
      api_.data = data;
      api_.sig = sig;
      if (!same) await drawInto(data, live && el.childNodes.length > 0 && !el.querySelector(":scope > .empty.small .spinner"));
      el.querySelector(":scope > .af-panel-error")?.remove();
    } catch (err) {
      const box = h("div.af-panel-error", errorBox(err), h("button.btn.small", { type: "button", onclick: () => { box.remove(); reload(true); } }, "Retry"));
      if (api_.data === undefined) el.replaceChildren(box);
      else { el.querySelector(":scope > .af-panel-error")?.remove(); el.prepend(box); }
    }
  }
  return api_;
}

/** Retry wrapper for a whole section that failed to load: returns an element with the error and a Retry button. */
export function retryBox(err, retry) {
  return h("div.af-panel-error", errorBox(err), h("button.btn.small", { type: "button", onclick: retry }, "Retry"));
}

// ---------------------------------------------------------------------- confirm (paid or irreversible actions)

/**
 * One confirm dialog for paid or irreversible actions: what will happen, what it costs, and a plain yes/no.
 * Resolves true or false. `lines` are bullet points; `cost` a string or number (USD); `danger` styles the button.
 */
export function confirmAction({ title, lines = [], cost = null, note = "", confirm = "Continue", danger = false }) {
  return new Promise((resolve) => {
    const done = (v) => { backdrop.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); done(false); } };
    const costText = cost === null || cost === undefined ? null : typeof cost === "number" ? `$${cost.toFixed(cost < 1 ? 3 : 2)}` : String(cost);
    const yes = h("button.btn.primary", { type: "button", class: danger ? "danger" : "", onclick: () => done(true) }, confirm);
    const box = h("div.af-confirm", { role: "alertdialog", "aria-modal": "true", "aria-label": title },
      h("h3", title),
      lines.length ? h("ul", lines.filter(Boolean).map((l) => h("li", l))) : null,
      costText ? h("div.af-cost", h("span", "Estimated cost"), h("b", costText)) : null,
      note ? h("p.help", note) : null,
      h("div.actions", h("button.btn", { type: "button", onclick: () => done(false) }, "Cancel"), yes));
    const backdrop = h("div.cmdk-backdrop.af-confirm-backdrop", { onclick: (e) => e.target === backdrop && done(false) }, box);
    document.body.append(backdrop);
    document.addEventListener("keydown", onKey, true);
    yes.focus();
  });
}
