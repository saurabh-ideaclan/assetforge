// Shared helpers: API calls, DOM building, formatting, toast, audio player, column chart.

export class ApiError extends Error {
  constructor(error) {
    super(error.message || "Request failed");
    this.code = error.code;
    this.hint = error.hint;
    this.details = error.details;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data;
  try { data = await res.json(); } catch { throw new ApiError({ code: "HTTP_" + res.status, message: res.statusText }); }
  if (data.status === "error") throw new ApiError(data.error);
  return data;
}

export function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
    p.set(k, Array.isArray(v) ? v.join(",") : v);
  }
  const s = p.toString();
  return s ? "?" + s : "";
}

/** Tiny hyperscript: h("div.card", {onclick}, "text", child) */
export function h(tag, attrs, ...children) {
  const [name, ...classes] = tag.split(".");
  const el = document.createElement(name || "div");
  if (classes.length) el.className = classes.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className += " " + v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k in el && k !== "list" && k !== "form") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export const fmt = {
  money(v) {
    if (v === null || v === undefined) return "–";
    const a = Math.abs(v);
    return "$" + (a > 0 && a < 1 ? v.toFixed(a < 0.01 ? 4 : 3) : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  },
  int(v) { return v === null || v === undefined ? "–" : Number(v).toLocaleString(); },
  bytes(v) {
    if (!v && v !== 0) return "–";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
  },
  when(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    const s = (Date.now() - d.getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
    return d.toLocaleDateString();
  },
  date(iso) { return iso ? new Date(iso).toLocaleString() : "–"; },
  file(f) {
    if (!f) return "";
    const parts = [f.ext.replace(".", "")];
    if (f.width && f.height) parts.push(`${f.width}×${f.height}`);
    if (f.duration_ms !== null && f.duration_ms !== undefined) parts.push(`${(f.duration_ms / 1000).toFixed(2)}s`);
    return parts.join(" · ");
  },
};

export const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

let toastTimer;
export function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

export function errorBox(err) {
  return h("div.error-box", h("div", h("b", err.code || "Error"), " — ", err.message || String(err)),
    err.hint ? h("div.hint", err.hint) : null);
}

export function badge(text) { return h("span.badge", { class: text }, text); }

export function isAudio(asset) { return asset?.primary_variant?.file?.kind === "audio"; }

export function thumbUrl(variantId, size = 256) { return `/media/thumb/${variantId}?size=${size}`; }
export function fileUrl(variantId) { return `/media/variant/${variantId}`; }

const player = () => document.getElementById("player");
export function playVariant(variantId, button) {
  const audio = player();
  const src = fileUrl(variantId);
  if (audio.dataset.src === src && !audio.paused) { audio.pause(); if (button) button.textContent = "▶"; return; }
  document.querySelectorAll(".play").forEach((b) => (b.textContent = "▶"));
  audio.dataset.src = src;
  audio.src = src;
  audio.play().catch(() => toast("Couldn't play this sound"));
  if (button) {
    button.textContent = "❚❚";
    audio.onended = () => (button.textContent = "▶");
  }
}

export async function copy(text, label = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(label); }
  catch { toast("Copy failed"); }
}

/** Single-series column chart with hover tooltips (dataviz rules: ≤24px bars, 4px rounded tops, recessive grid). */
export function columnChart(points, { value, label, tooltip, format = fmt.money }) {
  const W = 760, H = 230, L = 58, R = 10, T = 10, B = 26;
  const pw = W - L - R, ph = H - T - B;
  const max = Math.max(0, ...points.map(value));
  const nice = niceMax(max);
  const band = pw / Math.max(1, points.length);
  const bw = Math.max(2, Math.min(24, band * 0.6));
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", "100%");
  svg.setAttribute("role", "img");
  svg.classList.add("chart");
  const add = (name, attrs, text) => {
    const el = document.createElementNS(ns, name);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    if (text !== undefined) el.textContent = text;
    svg.append(el);
    return el;
  };
  for (let i = 0; i <= 4; i++) {
    const y = T + ph - (ph * i) / 4;
    add("line", { x1: L, x2: W - R, y1: y, y2: y, class: "gridline" });
    add("text", { x: L - 6, y: y + 4, "text-anchor": "end" }, format((nice * i) / 4));
  }
  const every = Math.max(1, Math.ceil(points.length / 12));
  const wrap = h("div", { style: "position:relative" });
  const tip = h("div.tip");
  points.forEach((p, i) => {
    const v = value(p);
    const x = L + band * i + (band - bw) / 2;
    const bh = nice ? (ph * v) / nice : 0;
    // Hover only on the bar itself (a few px of slack so thin bars are easy to hit);
    // zero days keep a thin strip on the baseline so they can still be inspected.
    const pad = 3;
    const hitH = Math.max(bh + pad, 8);
    const hit = add("rect", { x: x - pad, y: T + ph - hitH, width: bw + 2 * pad, height: hitH, fill: "transparent", class: "hit" });
    const bar = add("path", { class: "bar", d: bh > 0 ? barPath(x, T + ph - bh, bw, bh) : `M${x},${T + ph} h0` });
    hit.addEventListener("mousemove", (e) => {
      const r = wrap.getBoundingClientRect();
      tip.innerHTML = "";
      tip.append(tooltip(p));
      tip.style.display = "block";
      let left = e.clientX - r.left + 12;
      if (left + tip.offsetWidth > r.width) left = e.clientX - r.left - tip.offsetWidth - 12;
      tip.style.left = left + "px";
      tip.style.top = e.clientY - r.top - 10 + "px";
      bar.classList.add("active");
    });
    hit.addEventListener("mouseleave", () => { tip.style.display = "none"; bar.classList.remove("active"); });
    if (i % every === 0) add("text", { x: x + bw / 2, y: H - 8, "text-anchor": "middle" }, label(p));
  });
  add("line", { x1: L, x2: W - R, y1: T + ph, y2: T + ph, class: "axis" });
  wrap.append(svg, tip);
  return wrap;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const e = Math.floor(Math.log10(v));
  for (const s of [1, 2, 2.5, 5, 10]) if (s * 10 ** e >= v) return s * 10 ** e;
  return 10 ** (e + 1);
}

function barPath(x, y, w, hgt, r = 4) {
  r = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + hgt} Z`;
}

export function barList(entries, { total } = {}) {
  const max = Math.max(1, ...entries.map((e) => e.count));
  return h("div.barlist", entries.map((e) =>
    h("div.barrow", h("span", e.label), h("div.track", h("div.fill", { style: `width:${(e.count / max) * 100}%` })),
      h("span.n", fmt.int(e.count)))));
}

export function assetCard(asset, { onclick, selected } = {}) {
  const v = asset.primary_variant;
  const audio = isAudio(asset);
  const thumb = h("div.thumb", { class: audio ? "audio" : "" },
    v ? h("img", { src: thumbUrl(v.id), loading: "lazy", alt: "" }) : h("span.muted", "no file"));
  if (audio && v) {
    const btn = h("button.play", { title: "Play", onclick: (e) => { e.stopPropagation(); playVariant(v.id, btn); } }, "▶");
    thumb.append(btn);
  }
  return h("div.asset", { onclick, class: selected ? "selected" : "", dataset: { id: asset.id } },
    thumb,
    h("div.meta", h("div.name", { title: asset.name }, asset.name),
      h("div.line", badge(asset.status), h("span", asset.type), h("span", fmt.file(v?.file)))));
}

const RANGE_LABELS = {
  "today": "Today", "yesterday": "Yesterday", "this-week": "This week", "last-7-days": "Last 7 days",
  "this-month": "This month", "last-month": "Last month", "last-30-days": "Last 30 days", "this-year": "This year",
  "all": "All time", "custom": "Custom range…",
};

export function rangeLabel(value) { return RANGE_LABELS[value] || value; }

function isoDay(d) { return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }

/**
 * Labelled date-range control: preset dropdown, plus from/to dates for a custom range.
 * `onChange(params)` gets either {range} or {from, to}, always with tz.
 */
export function rangePicker({ range = "last-30-days", from = "", to = "", onChange }) {
  const select = h("select", { title: "Date range" },
    Object.entries(RANGE_LABELS).map(([value, label]) =>
      h("option", { value, selected: from ? value === "custom" : value === range }, label)));
  const today = new Date();
  const fromInput = h("input", { type: "date", value: from || isoDay(new Date(today.getTime() - 29 * 86400000)), max: isoDay(today) });
  const toInput = h("input", { type: "date", value: to || isoDay(today), max: isoDay(today) });
  const apply = h("button.btn", { onclick: emit }, "Apply");
  const custom = h("span.range-custom", { style: from ? "" : "display:none" }, fromInput, h("span.muted", "to"), toInput, apply);

  function emit() {
    if (select.value === "custom") {
      if (!fromInput.value || !toInput.value) return toast("Pick both dates");
      if (fromInput.value > toInput.value) return toast("The start date must be before the end date");
      onChange({ from: fromInput.value, to: toInput.value, tz, label: `${fromInput.value} – ${toInput.value}` });
    } else {
      onChange({ range: select.value, tz, label: rangeLabel(select.value) });
    }
  }
  select.addEventListener("change", () => {
    custom.style.display = select.value === "custom" ? "" : "none";
    if (select.value !== "custom") emit();
  });
  fromInput.addEventListener("keydown", (e) => e.key === "Enter" && emit());
  toInput.addEventListener("keydown", (e) => e.key === "Enter" && emit());
  return h("div.range-picker", h("span.range-title", "Date range"), select, custom);
}

/** Editable list of short strings shown as removable chips (tags, "avoid" items). */
export function chipsInput(values = [], { placeholder = "add and press Enter", onchange } = {}) {
  const items = [...values];
  const list = h("span.chips-list");
  const input = h("input", { type: "text", placeholder });
  const wrap = h("div.chips", list, input);
  const render = () => {
    list.innerHTML = "";
    items.forEach((v, i) => list.append(h("span.chip", v, h("button", { type: "button", title: "Remove",
      onclick: () => { items.splice(i, 1); render(); onchange?.(items); } }, "×"))));
  };
  const add = () => {
    for (const part of input.value.split(",").map((s) => s.trim()).filter(Boolean)) if (!items.includes(part)) items.push(part);
    input.value = "";
    render();
    onchange?.(items);
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
    else if (e.key === "Backspace" && !input.value && items.length) { items.pop(); render(); onchange?.(items); }
  });
  input.addEventListener("blur", () => input.value.trim() && add());
  wrap.addEventListener("click", (e) => e.target === wrap && input.focus());
  render();
  wrap.values = () => { if (input.value.trim()) add(); return [...items]; };
  wrap.set = (next) => { items.splice(0, items.length, ...(next || [])); render(); };
  return wrap;
}

/** Palette editor: colour swatches with a picker, removable, max 12. */
export function paletteInput(values = [], { onchange } = {}) {
  const items = [...values];
  const list = h("span.palette");
  const picker = h("input", { type: "color", value: "#888888", title: "Pick a colour, then Add" });
  const addBtn = h("button.btn.small", { type: "button", title: "Add the picked colour", onclick: () => {
    const v = picker.value.toUpperCase();
    if (!items.includes(v) && items.length < 12) { items.push(v); render(); onchange?.(items); }
  } }, "+ Add");
  const wrap = h("div.palette-input", list, h("span.palette-add", picker, addBtn));
  const render = () => {
    list.innerHTML = "";
    if (!items.length) list.append(h("span.muted", "no palette"));
    items.forEach((v, i) => list.append(h("button.swatch", { type: "button", title: `${v} — click to remove`, style: `background:${v}`,
      onclick: () => { items.splice(i, 1); render(); onchange?.(items); } })));
  };
  render();
  wrap.values = () => [...items];
  wrap.set = (next) => { items.splice(0, items.length, ...(next || [])); render(); };
  return wrap;
}

export function swatches(colors, size = 14) {
  if (!colors?.length) return null;
  return h("span.swatches", colors.map((c) => h("span.sw", {
    title: typeof c === "string" ? c : `${c.name} ${c.hex} · ${Math.round(c.share * 100)}%`,
    style: `width:${size}px;height:${size}px;background:${typeof c === "string" ? c : c.hex}` })));
}

/** Segmented control. options: [[value, label]]; returns the element with .value */
export function segmented(options, value, onchange) {
  const el = h("div.seg");
  const render = () => {
    el.innerHTML = "";
    for (const [v, label] of options) {
      el.append(h("button", { type: "button", class: v === el.value ? "on" : "", onclick: () => { el.value = v; render(); onchange(v); } }, label));
    }
  };
  el.value = value;
  render();
  return el;
}

/** Collapsible section with a title and optional summary text. */
export function section(title, body, { open = false, note = "" } = {}) {
  return h("details.section", { open }, h("summary", h("span", title), note ? h("span.muted", note) : null), body);
}

export function field(label, control, help) {
  return h("label", h("span.lbl", label), control, help ? h("span.help", help) : null);
}

/** Poll a background image job until it ends. Resolves with the job detail payload. */
export function pollJob(jobId, onTick) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const timer = setInterval(async () => {
      try {
        const j = await api(`/api/jobs/${jobId}`);
        if (j.job.status === "succeeded" || j.job.status === "failed" || j.job.status === "cancelled") {
          clearInterval(timer);
          j.job.status === "succeeded" ? resolve(j) : reject(new ApiError(j.job.error || { message: `Job ${j.job.status}` }));
        } else onTick?.(Math.round((Date.now() - started) / 1000));
      } catch (err) { clearInterval(timer); reject(err); }
    }, 2000);
  });
}
