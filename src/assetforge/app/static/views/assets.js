import { api, qs, h, fmt, assetCard, isAudio, thumbUrl, fileUrl, copy, toast, errorBox, chipsInput, swatches,
  segmented, section, field, playVariant } from "/static/lib.js";
import { layerName } from "/static/views/art.js";

const PAGE = 60;
const FILTERS = [
  ["type", "types", "Type"], ["kind", "kinds", "Kind"], ["origin", "origins", "Origin"], ["project", "projects", "Project"],
  ["style", "styles", "Style"], ["category", "categories", "Category"], ["genre", "genres", "Genre"], ["theme", "themes", "Theme"],
  ["collection", "collections", "Collection"],
];
const LOOK = ["color", "orientation", "tone", "transparent", "loopable", "bpm", "key"];
const COLORS = { red: "#e53935", orange: "#fb8c00", yellow: "#fdd835", green: "#43a047", teal: "#00897b", blue: "#1e88e5",
  purple: "#8e24aa", pink: "#ec407a", brown: "#795548", black: "#111111", white: "#f5f5f5", gray: "#9e9e9e" };
const STATUS_TABS = [["", "All"], ["candidate", "To review"], ["approved", "Approved"], ["rejected", "Rejected"], ["archived", "Archive"]];

// Thumbnail density: grid minimum column width in px (S / M / L), remembered per viewer.
const DENSITY = { S: 110, M: 160, L: 230 };
const DENSITY_ORDER = ["S", "M", "L"];
const DENSITY_KEY = "assetforge.assets.density";
function loadDensity() {
  try { const v = localStorage.getItem(DENSITY_KEY); return DENSITY[v] ? v : "M"; } catch { return "M"; }
}
function saveDensity(v) {
  try { localStorage.setItem(DENSITY_KEY, v); } catch { /* private window or blocked storage: size just isn't remembered */ }
}

// Quick filter chips. Each one sets a single filter key; "All" clears the keys these chips own.
const QUICK = [
  ["all", "All", null, null],
  ["candidates", "Candidates", "status", "candidate"],
  ["images", "Images", "kind", "image"],
  ["audio", "Audio", "kind", "audio"],
  ["transparent", "Transparent", "transparent", "true"],
];
const QUICK_KEYS = ["status", "kind", "transparent"];

/** Status as a small coloured dot plus its word (never colour alone). */
function statusTag(status) {
  return h("span.ab-status", { class: `ab-st-${status}`, title: `Status: ${status}` }, h("i.ab-dot", { "aria-hidden": "true" }), status);
}

function fmtTime(s) {
  if (!Number.isFinite(s)) return "–";
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return m ? `${m}:${sec.toFixed(1).padStart(4, "0")}` : `${sec.toFixed(1)} s`;
}

export async function assets(root, { rest, params }) {
  const state = { q: params.get("q") || "", sort: params.get("sort") || "newest", filters: {}, offset: 0, total: 0,
                  loading: false, selected: rest[0] || null, items: [] };
  for (const key of [...FILTERS.map(([k]) => k), ...LOOK, "status", "similar", "parent", "group"]) if (params.get(key)) state.filters[key] = params.get(key);
  const { facets } = await api("/api/facets");
  const candidates = (facets.statuses || []).find((s) => s.value === "candidate")?.count || 0;

  const grid = h("div.grid.ab-grid");
  const sentinel = h("div.sentinel");
  const count = h("span.muted");
  const detail = h("div.detail");
  const layout = h("div.browser", h("div", grid, sentinel), detail);

  const search = h("input", { type: "search", placeholder: "Search: “dark forest background”, “transparent enemy”…  ( / )", value: state.q,
    oninput: debounce((e) => { state.q = e.target.value; delete state.filters.similar; reload(); }, 250) });
  const sort = h("select", { onchange: (e) => { state.sort = e.target.value; reload(); } },
    ["newest", "oldest", "updated", "name", "type"].map((s) => h("option", { value: s, selected: s === state.sort }, `Sort: ${s}`)));
  const archivedCount = (facets.statuses || []).find((s) => s.value === "archived")?.count || 0;
  const statusSeg = segmented(STATUS_TABS.map(([v, l]) => [v, v === "candidate" && candidates ? `${l} (${candidates})`
    : v === "archived" && archivedCount ? `${l} (${archivedCount})` : l]),
    state.filters.status || "", (v) => { if (v) state.filters.status = v; else delete state.filters.status; reload(); });

  // filter panel
  const selects = FILTERS.map(([key, facetKey, label]) => {
    const options = facets[facetKey] || [];
    if (!options.length) return null;
    return h("select", { title: label, dataset: { key }, onchange: (e) => setFilter(key, e.target.value) },
      h("option", { value: "" }, `${label}: all`),
      options.map((o) => h("option", { value: o.value, selected: state.filters[key] === o.value }, `${o.value} (${o.count})`)));
  }).filter(Boolean);
  const colorRow = h("div.color-filter", h("span.lbl", "Colour"), Object.entries(COLORS).map(([name, hex]) =>
    h("button.swatch", { type: "button", title: name, dataset: { color: name }, style: `background:${hex}`,
      class: state.filters.color === name ? "on" : "", onclick: () => setFilter("color", state.filters.color === name ? "" : name) })));
  const orientSeg = segmented([["", "Any shape"], ["landscape", "Landscape"], ["portrait", "Portrait"], ["square", "Square"]],
    state.filters.orientation || "", (v) => setFilter("orientation", v));
  const toneSeg = segmented([["", "Any tone"], ["dark", "Dark"], ["mid", "Mid"], ["light", "Light"]], state.filters.tone || "", (v) => setFilter("tone", v));
  const alphaSeg = segmented([["", "Any background"], ["true", "Transparent"], ["false", "Opaque"]], state.filters.transparent || "",
    (v) => setFilter("transparent", v));
  const loopSeg = segmented([["", "Any sound"], ["true", "Loops"], ["false", "One-shots"]], state.filters.loopable || "",
    (v) => setFilter("loopable", v));
  const bpmInput = h("input", { type: "number", min: 40, max: 240, placeholder: "BPM ±8", value: state.filters.bpm || "", style: "width:90px",
    onchange: (e) => setFilter("bpm", e.target.value) });
  const keyInput = h("input", { type: "text", placeholder: "Key: A minor / minor", value: state.filters.key || "", style: "width:150px",
    onchange: (e) => setFilter("key", e.target.value.trim()) });
  const panel = h("div.filter-panel", h("div.toolbar", selects), h("div.toolbar", colorRow, orientSeg, toneSeg, alphaSeg),
    h("div.toolbar", h("span.lbl", "Sound"), loopSeg, bpmInput, keyInput));
  const activeChips = h("div.active-filters");
  const filterBtn = h("button.btn", { onclick: () => { panel.classList.toggle("open"); } }, "Filters");
  const groupBtn = h("button.btn", { title: "Frames cut from a sheet are shown inside the sheet; switch to see them as separate cards",
    onclick: () => setFilter("group", state.filters.group === "false" ? "" : "false") });
  const syncGroupBtn = () => (groupBtn.textContent = state.filters.group === "false" ? "Frames: separate" : "Frames: in their sheet");
  const selectBtn = h("button.btn", { title: "Pick several assets to approve, archive or edit together (or ⌘-click cards)",
    onclick: () => setSelectMode(!selectMode) }, "Select");
  let selectMode = false;

  // thumbnail size
  let density = loadDensity();
  const densityBtns = DENSITY_ORDER.map((d) => h("button", { type: "button", dataset: { d },
    title: `${{ S: "Small", M: "Medium", L: "Large" }[d]} thumbnails (⌘= / ⌘−)`, onclick: () => setDensity(d) }, d));
  const densityCtl = h("div.ab-density", { role: "group", "aria-label": "Thumbnail size" }, densityBtns);
  const thumbSize = () => (density === "L" ? 512 : 256);
  function setDensity(d) {
    if (!DENSITY[d]) return;
    const changed = d !== density;
    density = d;
    saveDensity(d);
    grid.style.setProperty("--ab-min", `${DENSITY[d]}px`);
    grid.dataset.density = d;
    densityBtns.forEach((b) => { const on = b.dataset.d === d; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
    if (changed) grid.querySelectorAll(".asset[data-variant]").forEach((c) => {
      const img = c.querySelector(".thumb img");
      if (img) img.src = thumbUrl(c.dataset.variant, thumbSize());
    });
  }
  const stepDensity = (n) => setDensity(DENSITY_ORDER[Math.min(2, Math.max(0, DENSITY_ORDER.indexOf(density) + n))]);
  setDensity(density);

  // quick filter chips
  const kindCount = (v) => (facets.kinds || []).find((k) => k.value === v)?.count;
  const quickCounts = { candidates: candidates || null, images: kindCount("image"), audio: kindCount("audio") };
  const quickRow = h("div.ab-quick", { role: "toolbar", "aria-label": "Quick filters" });
  function renderQuick() {
    quickRow.innerHTML = "";
    const none = QUICK_KEYS.every((k) => !state.filters[k]);
    for (const [id, label, key, value] of QUICK) {
      const on = key ? state.filters[key] === value : none;
      const n = quickCounts[id];
      quickRow.append(h("button.ab-qchip", { type: "button", class: on ? "on" : "", "aria-pressed": String(on), dataset: { q: id },
        onclick: () => quickFilter(key, value, on) }, label, n ? h("span.n", fmt.int(n)) : null));
    }
  }
  function quickFilter(key, value, on) {
    if (!key) QUICK_KEYS.forEach((k) => delete state.filters[k]);
    else if (on) delete state.filters[key];
    else {
      state.filters[key] = value;
      // transparency only applies to images
      if (key === "kind" && value === "audio") delete state.filters.transparent;
      if (key === "transparent" && state.filters.kind === "audio") delete state.filters.kind;
    }
    reload();
  }

  root.append(
    h("div.page-head.ab-head", h("div", h("h1", "Assets"), h("div.sub", count)), statusSeg),
    h("div.toolbar.ab-toolbar", search, sort, filterBtn, groupBtn, selectBtn, h("span.ab-spacer"), densityCtl),
    panel, activeChips, quickRow, layout,
    h("div.kbd-hint", "Keys: ← → move · Space quick look · A approve · X reject · E archive/restore · F find similar · S select mode · ⌘A select all shown · ⌘= / ⌘− thumbnail size · ⌘/⇧-click to add to the selection · Esc clear/close · / search"));

  function setFilter(key, value) {
    if (value) state.filters[key] = value; else delete state.filters[key];
    reload();
  }

  // Keep a segmented control showing the filter's current value (filters can change from chips or "Clear all").
  function syncSeg(seg, value) {
    if (!seg || seg.value === value) return;
    seg.value = value;
    if (typeof seg.setLabels === "function" && Array.isArray(seg.options)) seg.setLabels(seg.options.map((o) => o[1]));
  }

  function clearAll() {
    state.q = "";
    search.value = "";
    state.filters = {};
    selects.forEach((s) => (s.value = ""));
    reload();
  }

  function emptyState() {
    const filtered = state.q || Object.keys(state.filters).length;
    return h("div.empty.ab-empty", { style: "grid-column:1/-1" },
      h("div", filtered ? "Nothing matches this search and these filters." : "No assets in this library yet."),
      filtered ? h("button.btn.small", { type: "button", onclick: clearAll }, "Clear search and filters")
        : h("a.btn.small.primary", { href: "#/generate" }, "Generate an asset"));
  }

  function renderChips() {
    activeChips.innerHTML = "";
    syncGroupBtn();
    renderQuick();
    syncSeg(statusSeg, state.filters.status || "");
    syncSeg(orientSeg, state.filters.orientation || "");
    syncSeg(toneSeg, state.filters.tone || "");
    syncSeg(alphaSeg, state.filters.transparent || "");
    syncSeg(loopSeg, state.filters.loopable || "");
    const entries = Object.entries(state.filters).filter(([k]) => k !== "status" && k !== "group");
    filterBtn.textContent = entries.length ? `Filters (${entries.length})` : "Filters";
    for (const [k, v] of entries) {
      const label = k === "similar" ? `like ${v}` : k === "parent" ? `frames of ${v}` : k === "transparent" ? (v === "true" ? "transparent" : "opaque")
        : k === "loopable" ? (v === "true" ? "loops" : "one-shots") : k === "bpm" ? `${v} bpm` : `${k}: ${v}`;
      activeChips.append(h("span.chip", label, h("button", { title: "Remove", onclick: () => setFilter(k, "") }, "×")));
    }
    if (entries.length) activeChips.append(h("button.btn.small", { onclick: () => { state.filters = state.filters.status ? { status: state.filters.status } : {};
      selects.forEach((s) => (s.value = "")); reload(); } }, "Clear all"));
    // keep the panel controls in step
    selects.forEach((s) => (s.value = state.filters[s.dataset.key] || ""));
    colorRow.querySelectorAll(".swatch").forEach((b) => b.classList.toggle("on", b.dataset.color === state.filters.color));
  }

  function syncHash() {
    const p = { q: state.q, sort: state.sort !== "newest" ? state.sort : "", ...state.filters };
    const path = state.selected ? `#/assets/${state.selected}` : "#/assets";
    history.replaceState(null, "", path + qs(p));
  }

  async function load() {
    if (state.loading || (state.offset && state.offset >= state.total)) return;
    state.loading = true;
    try {
      const data = await api("/api/assets" + qs({ q: state.q, sort: state.sort, ...state.filters, offset: state.offset, limit: PAGE }));
      state.total = data.total;
      count.textContent = state.filters.similar ? `${fmt.int(data.total)} assets, most similar to ${state.filters.similar} first`
        : `${fmt.int(data.total)} asset${data.total === 1 ? "" : "s"}`;
      if (!state.offset && !data.items.length) grid.append(emptyState());
      for (const a of data.items) {
        state.items.push(a);
        grid.append(card(a));
      }
      state.offset += data.items.length;
    } catch (err) {
      grid.append(errorBox(err));
    } finally {
      state.loading = false;
    }
  }

  function reload() {
    renderStatusActions();
    state.offset = 0;
    state.total = 0;
    state.items = [];
    grid.innerHTML = "";
    renderChips();
    syncHash();
    load();
  }

  async function select(id) {
    state.selected = id;
    grid.querySelectorAll(".asset").forEach((c) => c.classList.toggle("selected", c.dataset.id === id));
    grid.querySelector(".asset.selected")?.scrollIntoView({ block: "nearest" });
    syncHash();
    if (ql.el) { if (id) renderQuickLook(id); else closeQuickLook(); }
    if (!id) { layout.classList.remove("with-detail"); detail.innerHTML = ""; return; }
    layout.classList.add("with-detail");
    detail.innerHTML = "";
    detail.append(h("div.body", h("span.spinner"), " Loading…"));
    try {
      const data = await api(`/api/assets/${id}`);
      if (state.selected !== id) return;
      detail.innerHTML = "";
      detail.append(renderDetail(data, {
        onClose: () => select(null),
        onChanged: (asset) => refreshCard(asset),
        onReviewed: (action) => review(action),
        onSimilar: (ref) => { state.q = ""; search.value = ""; state.filters = { similar: ref }; reload(); },
      }));
      detail.scrollTop = 0;
    } catch (err) {
      detail.innerHTML = "";
      detail.append(h("div.body", errorBox(err)));
    }
  }

  function card(a) {
    const el = assetCard(a, { selected: a.id === state.selected, onclick: (e) => {
      if (selectMode || e.metaKey || e.ctrlKey || e.shiftKey) { togglePick(a.id, e.shiftKey); return; }
      select(a.id);
    } });
    el.classList.add("ab-card");
    el.prepend(h("button.pick", { type: "button", title: "Select", "aria-label": "Select",
      onclick: (e) => { e.stopPropagation(); togglePick(a.id, e.shiftKey); } }, "✓"));
    el.classList.toggle("picked", picked.has(a.id));
    const v = a.primary_variant;
    if (v) el.dataset.variant = v.id;
    // status: dot + word instead of a coloured pill
    const pill = el.querySelector(".meta .badge");
    if (pill) pill.replaceWith(statusTag(a.status)); else el.querySelector(".meta")?.append(statusTag(a.status));
    const thumb = el.querySelector(".thumb");
    if (thumb) {
      const img = thumb.querySelector("img");
      if (img && v && thumbSize() !== 256) img.src = thumbUrl(v.id, thumbSize());
      thumb.append(h("button.ab-ql-btn", { type: "button", title: "Quick Look (Space)", "aria-label": "Quick Look",
        onclick: (e) => { e.stopPropagation(); openQuickLook(a.id); } }, "⤢"));
      if (isAudio(a) && v) {
        el.classList.add("ab-audio");
        if (!thumb.querySelector(".play")) {
          const btn = h("button.play", { type: "button", title: "Play", onclick: (e) => { e.stopPropagation(); playVariant(v.id, btn); } }, "▶");
          thumb.append(btn);
        }
        thumb.append(h("div.ab-progress", { "aria-hidden": "true" }, h("i")));
      }
    }
    return el;
  }

  function refreshCard(asset) {
    const i = state.items.findIndex((x) => x.id === asset.id);
    const merged = i >= 0 ? { ...state.items[i], ...asset } : asset;
    if (i >= 0) state.items[i] = merged;
    const old = grid.querySelector(`.asset[data-id="${asset.id}"]`);
    if (old) old.replaceWith(card(merged));
  }

  // ---- card audio: a thin progress line over the waveform of the card that is playing
  const player = document.getElementById("player");
  let progressFrame = 0;
  function tickProgress() {
    progressFrame = 0;
    if (!player) return;
    const vid = (player.dataset.src || "").split("/").pop();
    const playing = !player.paused && !player.ended && vid;
    grid.querySelectorAll(".ab-card.ab-playing").forEach((c) => {
      if (!playing || c.dataset.variant !== vid) { c.classList.remove("ab-playing"); setBar(c, 0); }
    });
    if (!playing) return;
    const c = grid.querySelector(`.asset[data-variant="${CSS.escape(vid)}"]`);
    if (c) { c.classList.add("ab-playing"); setBar(c, player.duration ? player.currentTime / player.duration : 0); }
    progressFrame = requestAnimationFrame(tickProgress);
  }
  function setBar(c, p) {
    const bar = c.querySelector(".ab-progress i");
    if (bar) bar.style.transform = `scaleX(${Math.min(1, Math.max(0, p))})`;
  }
  const onPlayerChange = () => { if (!progressFrame) tickProgress(); };
  if (player) ["play", "pause", "ended", "emptied"].forEach((ev) => player.addEventListener(ev, onPlayerChange));

  // ---- Quick Look: a large preview over everything; ← → step through the grid, A / X review, Space / Esc close
  const ql = { el: null, audio: null, seq: 0, frame: 0 };

  function openQuickLook(id) {
    id = id || state.selected;
    if (!id) return;
    if (!ql.el) {
      ql.el = h("div.ab-ql", { role: "dialog", "aria-modal": "true", "aria-label": "Quick Look", tabIndex: -1,
        onclick: (e) => { if (e.target === ql.el) closeQuickLook(); } });
      document.body.append(ql.el);
    }
    document.activeElement?.blur?.();
    ql.el.focus({ preventScroll: true });
    if (state.selected !== id) select(id); else renderQuickLook(id);
  }

  function stopQuickLookAudio() {
    cancelAnimationFrame(ql.frame);
    ql.frame = 0;
    if (ql.audio) { ql.audio.pause(); ql.audio.removeAttribute("src"); ql.audio.load(); ql.audio = null; }
  }

  function closeQuickLook() {
    ql.seq += 1;
    stopQuickLookAudio();
    ql.el?.remove();
    ql.el = null;
    grid.querySelector(".asset.selected")?.scrollIntoView({ block: "nearest" });
  }

  function quickLookStep(n) {
    const next = neighbour(n);
    if (next && next !== state.selected) select(next);
  }

  async function renderQuickLook(id) {
    const token = ++ql.seq;
    stopQuickLookAudio();
    let a = state.items.find((x) => x.id === id);
    if (!a) {
      try { a = (await api(`/api/assets/${id}`)).asset; }
      catch (err) { if (token === ql.seq && ql.el) ql.el.replaceChildren(h("div.ab-ql-box", h("div.ab-ql-stage", errorBox(err)))); return; }
    }
    if (token !== ql.seq || !ql.el) return;
    const ids = [...grid.querySelectorAll(".asset")].map((c) => c.dataset.id);
    const i = ids.indexOf(id);
    if (i >= ids.length - 4) load();  // keep stepping past the loaded page
    const v = a.primary_variant;
    const kind = v?.file?.kind;
    let stage;
    if (!v) stage = h("div.ab-ql-none", "No file");
    else if (kind === "audio") stage = quickLookAudio(a, v);
    else if (kind === "image" || kind === "vector") {
      const img = h("img.ab-ql-img", { src: kind === "image" ? fileUrl(v.id) : thumbUrl(v.id, 512), alt: a.name });
      img.addEventListener("load", () => img.classList.toggle("ab-pixel", Math.max(img.naturalWidth, img.naturalHeight) <= 128));
      stage = img;
    } else stage = h("div.ab-ql-none", `${v.file.ext} file`);
    const more = state.offset < state.total;
    const facts = [a.type, fmt.file(v?.file), a.project].filter(Boolean).join(" · ");
    ql.el.replaceChildren(h("div.ab-ql-box",
      h("div.ab-ql-head",
        h("div.ab-ql-title", h("b", { title: a.name }, a.name), statusTag(a.status), h("span.muted", facts)),
        h("span.muted.ab-ql-pos", i >= 0 ? `${fmt.int(i + 1)} of ${fmt.int(Math.max(state.total, ids.length))}` : ""),
        h("button.ab-ql-close", { type: "button", title: "Close (Space or Esc)", "aria-label": "Close", onclick: closeQuickLook }, "×")),
      h("div.ab-ql-stage", { class: kind === "audio" ? "audio" : "" }, stage,
        h("button.ab-ql-nav.prev", { type: "button", title: "Previous (←)", "aria-label": "Previous", disabled: i <= 0, onclick: () => quickLookStep(-1) }, "‹"),
        h("button.ab-ql-nav.next", { type: "button", title: "Next (→)", "aria-label": "Next", disabled: i < 0 || (i >= ids.length - 1 && !more),
          onclick: () => quickLookStep(1) }, "›")),
      h("div.ab-ql-foot",
        a.status !== "approved" ? h("button.btn.small.primary", { type: "button", onclick: () => review("approve") }, "Approve ", h("kbd", "A")) : null,
        a.status !== "rejected" ? h("button.btn.small", { type: "button", onclick: () => review("reject") }, "Reject ", h("kbd", "X")) : null,
        h("button.btn.small", { type: "button", title: "Close Quick Look and keep this asset open in the panel", onclick: closeQuickLook }, "Details"),
        h("span.ab-spacer"),
        h("span.help", `← → browse · A approve · X reject${kind === "audio" ? " · P play/pause" : ""} · Space / Esc close`))));
  }

  function quickLookAudio(a, v) {
    const audio = h("audio", { src: fileUrl(v.id), preload: "auto", loop: !!a.metadata?.audio?.loop });
    const fill = h("i");
    const time = h("span.ab-ql-time.mono", "–");
    const btn = h("button.ab-ql-play", { type: "button", title: "Play / pause (P)", "aria-label": "Play or pause", onclick: () => toggle() }, "▶");
    const wave = h("div.ab-ql-wave", { title: "Click to jump", onclick: (e) => {
      const r = wave.getBoundingClientRect();
      if (audio.duration && r.width) { audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration; update(); }
    } }, h("img", { src: thumbUrl(v.id, 512), alt: "" }), h("div.ab-ql-wave-fill", fill));
    function update() {
      const d = audio.duration;
      fill.style.width = d ? `${(audio.currentTime / d) * 100}%` : "0";
      time.textContent = `${fmtTime(audio.currentTime)} / ${fmtTime(d)}`;
    }
    function tick() { update(); ql.frame = audio.paused ? 0 : requestAnimationFrame(tick); }
    function toggle() {
      if (audio.paused) { player?.pause(); audio.play().catch(() => toast("Couldn't play this sound")); } else audio.pause();
    }
    audio.addEventListener("play", () => { btn.textContent = "❚❚"; cancelAnimationFrame(ql.frame); tick(); });
    audio.addEventListener("pause", () => { btn.textContent = "▶"; update(); });
    audio.addEventListener("ended", () => { btn.textContent = "▶"; update(); });
    audio.addEventListener("loadedmetadata", update);
    ql.audio = audio;
    ql.toggleAudio = toggle;
    player?.pause();
    audio.play().catch(() => { /* autoplay refused: the button still works */ });
    return h("div.ab-ql-audio", wave, h("div.ab-ql-audio-row", btn, time,
      a.metadata?.audio?.loop ? h("span.help", "seamless loop") : null), audio);
  }

  // ---- multi-select + bulk actions
  const picked = new Set();
  let lastPicked = null;
  const bulkCount = h("b");
  const editPanel = h("div.bulk-edit");
  const statusActions = h("span.bulk-group");
  const bulkBar = h("div.bulk-bar",
    h("div.bulk-row",
      bulkCount,
      h("button.btn.small", { onclick: selectAllShown }, "All shown"),
      h("button.btn.small", { title: "Every asset matching the current search and filters, not just the loaded ones", onclick: selectAllMatching }, "All matching"),
      h("span.sep"),
      statusActions,
      h("span.sep"),
      h("button.btn.small", { onclick: () => toggleEdit("edit") }, "Edit details…"),
      h("button.btn.small", { onclick: () => toggleEdit("collection") }, "Add to collection…"),
      h("span.sep"),
      h("button.btn.small", { title: "Clear the selection (Esc)", onclick: clearPicked }, "Done")),
    editPanel);
  root.append(bulkBar);

  async function refreshCounts() {
    try {
      const { facets: f } = await api("/api/facets");
      const n = (v) => (f.statuses || []).find((x) => x.value === v)?.count || 0;
      const labels = STATUS_TABS.map(([v, l]) => (v === "candidate" || v === "archived") && n(v) ? `${l} (${n(v)})` : l);
      statusSeg.setLabels(labels);
      quickCounts.candidates = n("candidate") || null;
      quickCounts.images = (f.kinds || []).find((k) => k.value === "image")?.count;
      quickCounts.audio = (f.kinds || []).find((k) => k.value === "audio")?.count;
      renderQuick();
    } catch { /* counts are cosmetic */ }
  }

  function renderStatusActions() {
    statusActions.innerHTML = "";
    const inArchive = state.filters.status === "archived";
    statusActions.append(
      inArchive ? h("button.btn.small.primary", { onclick: () => bulk("restore") }, "Restore") : h("button.btn.small.primary", { onclick: () => bulk("approve") }, "Approve"),
      inArchive ? "" : h("button.btn.small", { onclick: () => bulk("reject") }, "Reject"),
      inArchive ? "" : h("button.btn.small", { onclick: () => bulk("archive") }, "Archive"),
      h("button.btn.small", { title: "Back to the review queue", onclick: () => bulk("candidate") }, "To review"));
  }
  renderStatusActions();

  function setSelectMode(on) {
    selectMode = on;
    selectBtn.classList.toggle("primary", on);
    selectBtn.textContent = on ? "Selecting… (click cards)" : "Select";
    grid.classList.toggle("selecting", on);
    if (!on && !picked.size) bulkBar.classList.remove("show");
    if (on) bulkBar.classList.add("show");
    renderPicked();
  }

  function togglePick(id, range) {
    const ids = [...grid.querySelectorAll(".asset")].map((c) => c.dataset.id);
    if (!picked.size && state.selected && state.selected !== id && !selectMode) picked.add(state.selected);
    if (range && lastPicked && ids.includes(lastPicked)) {
      const [a, b] = [ids.indexOf(lastPicked), ids.indexOf(id)].sort((x, y) => x - y);
      ids.slice(a, b + 1).forEach((i) => picked.add(i));
    } else if (picked.has(id)) picked.delete(id);
    else picked.add(id);
    lastPicked = id;
    renderPicked();
  }

  function selectAllShown() {
    grid.querySelectorAll(".asset").forEach((c) => picked.add(c.dataset.id));
    renderPicked();
  }

  async function selectAllMatching() {
    try {
      const { ids, total } = await api("/api/asset-ids" + qs({ q: state.q, sort: state.sort, ...state.filters }));
      ids.forEach((i) => picked.add(i));
      renderPicked();
      if (total > ids.length) toast(`Selected the first ${ids.length} of ${total}`);
    } catch (err) { toast(err.message); }
  }

  function clearPicked() {
    picked.clear();
    editPanel.innerHTML = "";
    setSelectMode(false);
  }

  function renderPicked() {
    grid.querySelectorAll(".asset").forEach((c) => c.classList.toggle("picked", picked.has(c.dataset.id)));
    bulkCount.textContent = picked.size ? `${picked.size} selected` : "Click cards to select";
    bulkBar.classList.toggle("show", picked.size > 0 || selectMode);
    bulkBar.querySelectorAll(".bulk-row button").forEach((b) => {
      if (!["All shown", "All matching", "Done"].includes(b.textContent)) b.disabled = !picked.size;
    });
  }

  function toggleEdit(kind) {
    if (editPanel.dataset.kind === kind && editPanel.childElementCount) { editPanel.innerHTML = ""; editPanel.dataset.kind = ""; return; }
    editPanel.innerHTML = "";
    editPanel.dataset.kind = kind;
    const out = h("div");
    if (kind === "collection") {
      const name = h("input", { type: "text", list: "bulk-collections", placeholder: "collection name (new or existing)" });
      const list = h("datalist", { id: "bulk-collections" }, (facets.collections || []).map((c) => h("option", { value: c.value })));
      const go = async () => {
        if (!name.value.trim()) { name.focus(); return; }
        try {
          const r = await api("/api/collections/add", { method: "POST", body: { collection: name.value.trim(), ids: [...picked] } });
          toast(`Added ${r.added} to ${r.collection.name} (${r.collection.asset_count} in it now)`);
          editPanel.innerHTML = "";
        } catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
      };
      name.addEventListener("keydown", (e) => e.key === "Enter" && go());
      editPanel.append(h("div.toolbar", name, list, h("button.btn.small.primary", { onclick: go }, `Add ${picked.size}`)), out);
      name.focus();
      return;
    }
    const inputs = Object.fromEntries(["project", "category", "subcategory", "genre", "theme", "style"].map((k) =>
      [k, h("input", { type: "text", placeholder: "keep as is" })]));
    const type = h("select", h("option", { value: "" }, "keep as is"), (facets.types || []).map((t) => h("option", { value: t.value }, t.value)),
      ["character", "enemy", "object", "sprite", "icon", "ui", "background", "tile", "texture", "effect", "spritesheet", "animation"]
        .filter((t) => !(facets.types || []).some((x) => x.value === t)).map((t) => h("option", { value: t }, t)));
    const addTags = chipsInput([], { placeholder: "tags to add" });
    const removeTags = chipsInput([], { placeholder: "tags to remove" });
    const apply = async () => {
      const set = {};
      for (const [k, el] of Object.entries(inputs)) {
        const v = el.value.trim();
        if (v === "-") set[k] = ""; else if (v) set[k] = v;
      }
      if (type.value) set.type = type.value;
      const body = { ids: [...picked], set, add_tags: addTags.values(), remove_tags: removeTags.values() };
      try {
        const r = await api("/api/assets/bulk-update", { method: "POST", body });
        toast(`Updated ${r.count} asset${r.count === 1 ? "" : "s"}`);
        editPanel.innerHTML = "";
        reload();
        if (state.selected && picked.has(state.selected)) select(state.selected);
      } catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
    };
    editPanel.append(h("div.form.compact",
      ...Object.entries(inputs).map(([k, el]) => field(k[0].toUpperCase() + k.slice(1), el)), field("Type", type),
      h("label.wide", h("span.lbl", "Add tags"), addTags), h("label.wide", h("span.lbl", "Remove tags"), removeTags)),
      h("div.actions", h("button.btn.small.primary", { onclick: apply }, `Apply to ${picked.size}`),
        h("span.help", "Empty fields stay as they are; type - to clear a field.")), out);
  }

  async function bulk(action, force = false) {
    const ids = [...picked];
    if (!ids.length) return;
    if (ids.length >= 20 && ["reject", "archive"].includes(action) && !confirm(`${action === "archive" ? "Archive" : "Reject"} ${ids.length} assets?`)) return;
    try {
      const { review: r } = await api("/api/review/bulk", { method: "POST", body: { ids, action, force } });
      const n = action === "restore" ? Object.keys(r.restored || {}).length : ids.length;
      const verb = { approve: "approved", reject: "rejected", archive: "moved to the archive", restore: "restored", candidate: "sent back to review" }[action];
      toast(`${n} asset${n === 1 ? "" : "s"} ${verb}`);
      refreshCounts();
      picked.clear();
      editPanel.innerHTML = "";
      renderPicked();
      reload();
    } catch (err) {
      if (err.code === "POLICY_VIOLATION" && action === "approve" && confirm(`${err.message}\n\nApprove anyway?`)) return bulk(action, true);
      toast(err.message);
    }
  }

  async function review(action, force = false) {
    if (!state.selected) return;
    try {
      const { review: r } = await api(`/api/assets/${state.selected}/review`, { method: "POST", body: { action, force } });
      toast(`${state.selected} ${r.status === "archived" ? "moved to the archive" : r.status}`);
      refreshCounts();
      const reviewed = state.selected;
      const next = neighbour(1) !== reviewed ? neighbour(1) : neighbour(-1);
      const { asset } = await api(`/api/assets/${reviewed}`);
      const stillHere = state.filters.status ? asset.status === state.filters.status : asset.status !== "archived";
      if (!stillHere) {
        grid.querySelector(`.asset[data-id="${reviewed}"]`)?.remove();  // it left this view (e.g. "To review")
        state.total -= 1;
        count.textContent = `${fmt.int(state.total)} asset${state.total === 1 ? "" : "s"}`;
        select(next && next !== reviewed ? next : null);
      } else {
        refreshCard(asset);
        select(reviewed);
      }
    } catch (err) {
      if (err.code === "POLICY_VIOLATION" && action === "approve" && confirm(`${err.message}\n\nApprove anyway?`)) return review(action, true);
      toast(err.message);
    }
  }

  function neighbour(step) {
    const ids = [...grid.querySelectorAll(".asset")].map((c) => c.dataset.id);
    const i = ids.indexOf(state.selected);
    return ids[Math.min(ids.length - 1, Math.max(0, (i < 0 ? -1 : i) + step))] || null;
  }

  const onKey = (e) => {
    if (ql.el) {  // Quick Look is open: its keys only, nothing reaches the grid behind it
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      const handled = ["Escape", " ", "ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "j", "k", "a", "x", "p"].includes(k);
      if (handled) e.preventDefault();
      if (k === "Escape" || k === " ") closeQuickLook();
      else if (k === "ArrowRight" || k === "ArrowDown" || k === "j") quickLookStep(1);
      else if (k === "ArrowLeft" || k === "ArrowUp" || k === "k") quickLookStep(-1);
      else if (k === "a" && !e.repeat && state.selected) review("approve");
      else if (k === "x" && !e.repeat && state.selected) review("reject");
      else if (k === "p" && ql.audio) ql.toggleAudio?.();
      return;
    }
    if (e.target.closest?.("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) {
      if ((e.metaKey || e.ctrlKey) && e.key === "f") { e.preventDefault(); search.focus(); search.select(); }
      if ((e.metaKey || e.ctrlKey) && e.key === "a" && !e.target.closest?.("input, textarea, select")) { e.preventDefault(); selectAllShown(); }
      if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === "=" || e.key === "+")) { e.preventDefault(); stepDensity(1); }
      if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === "-" || e.key === "_")) { e.preventDefault(); stepDensity(-1); }
      return;
    }
    if (e.key === " " && !e.repeat && state.selected && !e.target.closest?.("button, a, summary, audio, video, [contenteditable]")) {
      e.preventDefault(); openQuickLook(state.selected);
    }
    else if (e.key === "/") { e.preventDefault(); search.focus(); search.select(); }
    else if (e.key === "ArrowRight" || e.key === "j") { e.preventDefault(); select(neighbour(1)); }
    else if (e.key === "ArrowLeft" || e.key === "k") { e.preventDefault(); select(neighbour(-1)); }
    else if (e.key === "Escape") { if (picked.size || selectMode) clearPicked(); else select(null); }
    else if (e.key === "s" && !e.repeat) setSelectMode(!selectMode);
    else if (e.key === "a" && state.selected) review("approve");
    else if (e.key === "x" && state.selected) review("reject");
    else if (e.key === "e" && state.selected) review(state.filters.status === "archived" ? "restore" : "archive");
    else if (e.key === "f" && state.selected) { state.q = ""; search.value = ""; state.filters = { similar: state.selected }; reload(); }
  };
  document.addEventListener("keydown", onKey);

  const observer = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && load(), { rootMargin: "400px" });
  observer.observe(sentinel);
  if (Object.keys(state.filters).some((k) => k !== "status" && k !== "similar")) panel.classList.add("open");
  renderChips();
  syncHash();
  await load();
  if (state.selected) select(state.selected);

  // New or changed assets (an agent or the CLI just made something): reload what is shown, in place,
  // keeping scroll, selection and picks; new cards get a brief highlight.
  async function refresh({ assets: changed }) {
    if (!changed || state.loading) return;
    const known = new Set(state.items.map((a) => a.id));
    const limit = Math.min(600, Math.max(PAGE, state.offset));
    const data = await api("/api/assets" + qs({ q: state.q, sort: state.sort, ...state.filters, offset: 0, limit }));
    const top = root.closest("main")?.scrollTop ?? 0;
    state.items = data.items;
    state.total = data.total;
    state.offset = data.items.length;
    count.textContent = state.filters.similar ? `${fmt.int(data.total)} assets, most similar to ${state.filters.similar} first`
      : `${fmt.int(data.total)} asset${data.total === 1 ? "" : "s"}`;
    const cards = data.items.map((a) => {
      const el = card(a);
      if (known.size && !known.has(a.id)) el.classList.add("fresh");
      return el;
    });
    grid.replaceChildren(...(cards.length ? cards : [emptyState()]));
    const scroller = root.closest("main");
    if (scroller) scroller.scrollTop = top;
    onPlayerChange();
  }

  return {
    refresh,
    cleanup: () => {
      observer.disconnect();
      document.removeEventListener("keydown", onKey);
      closeQuickLook();
      cancelAnimationFrame(progressFrame);
      if (player) ["play", "pause", "ended", "emptied"].forEach((ev) => player.removeEventListener(ev, onPlayerChange));
    },
    update: ({ rest: r, params: p }) => {
      const wanted = new URLSearchParams(p);
      const have = new URLSearchParams(qs({ q: state.q, sort: state.sort !== "newest" ? state.sort : "", ...state.filters }).slice(1));
      wanted.sort(); have.sort();
      if (wanted.toString() !== have.toString()) return false;  // new filters: remount with them
      if ((r[0] || null) !== state.selected) select(r[0] || null);
    },
  };
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------------------------------------------------------------------- detail

function renderDetail({ asset, lineage, job, usage, frames, can_regenerate: canRegenerate, siblings, parent_name: parentName, sound }, { onClose, onChanged, onSimilar, onReviewed }) {
  let current = asset.primary_variant;
  const preview = h("div.preview");
  const variantList = h("div.variants");
  let stopAnimation = null;

  function show(variant) {
    stopAnimation?.();
    current = variant;
    preview.innerHTML = "";
    preview.classList.toggle("audio", variant.file.kind === "audio");
    if (variant.file.kind === "image") {
      preview.append(h("img", { src: fileUrl(variant.id), alt: asset.name }));
    } else if (variant.file.kind === "audio") {
      const loops = !!asset.metadata.audio?.loop;
      const player = h("audio", { controls: true, src: fileUrl(variant.id), preload: "metadata", loop: loops });
      preview.append(h("img", { src: thumbUrl(variant.id, 512), alt: "" }), player,
        h("label.help", h("input", { type: "checkbox", checked: loops, onchange: (e) => (player.loop = e.target.checked) }),
          loops ? " loop (seamless: listen to the seam)" : " loop"));
    } else if (variant.file.kind === "vector") {
      preview.append(h("img", { src: thumbUrl(variant.id, 512), alt: asset.name }));
    } else {
      preview.append(h("div.muted", `${variant.file.ext} file`));
    }
    variantList.querySelectorAll(".variant").forEach((el) => el.classList.toggle("on", el.dataset.id === variant.id));
  }

  for (const v of asset.variants) {
    variantList.append(h("div.variant", { dataset: { id: v.id }, onclick: () => show(v) },
      h("div.row", h("b", v.label + (asset.primary_variant?.id === v.id ? " ★" : "")), h("span.muted.mono", v.id)),
      h("div.row", h("span", fmt.file(v.file)), h("span.muted", fmt.bytes(v.file.size_bytes))),
      v.params?.ops ? h("div.muted", "← " + v.params.ops.map((o) => o.op).join(", ")) : null));
  }

  const gen = asset.metadata.generator || {};
  const qc = asset.metadata.qc;
  const isImage = ["image", "vector"].includes(asset.primary_variant?.file?.kind);
  const facts = [
    ["Type", asset.type], ["Origin", asset.origin], ["Key", asset.key], ["Project", asset.project], ["Style", asset.style],
    ["Category", [asset.category, asset.subcategory].filter(Boolean).join(" / ")], ["Genre", asset.genre], ["Theme", asset.theme],
    ["Colours", swatches(asset.primary_variant?.file?.colors, 16)], ["Shape", shapeFacts(asset.primary_variant?.file)],
    ["Scope", asset.scope], ["License", asset.license], ["Commercial use", asset.commercial_use],
    ["Collections", asset.collections.length ? asset.collections.map((c) => h("a", { href: `#/assets?collection=${encodeURIComponent(c)}`, style: "margin-right:6px" }, c)) : null],
    ["Created", fmt.date(asset.created_at)], ["ID", h("span.mono", asset.id)],
  ];

  // review bar
  const statusBadge = h("span", statusTag(asset.status));
  const reviewBar = h("div.review-bar", statusBadge,
    asset.status !== "approved" ? h("button.btn.small.primary", { onclick: () => onReviewed("approve") }, "Approve ", h("kbd", "A")) : null,
    siblings?.length && asset.status === "candidate" ? h("button.btn.small", { title: "Approve this one and reject the other candidates from the same run", onclick: () => act("pick") }, `Pick (reject ${siblings.length} other${siblings.length > 1 ? "s" : ""})`) : null,
    asset.status !== "rejected" ? h("button.btn.small", { onclick: () => onReviewed("reject") }, "Reject ", h("kbd", "X")) : null,
    asset.status !== "candidate" && asset.status !== "archived" ? h("button.btn.small", { onclick: () => act("candidate") }, "Back to review") : null,
    asset.status === "archived"
      ? h("button.btn.small.primary", { title: "Bring it back with its previous status", onclick: () => onReviewed("restore") }, "Restore ", h("kbd", "E"))
      : h("button.btn.small", { title: "Hide it from every view except Archive (nothing is deleted)", onclick: () => onReviewed("archive") }, "Archive ", h("kbd", "E")));

  async function act(action, force = false) {
    try {
      const { review } = await api(`/api/assets/${asset.id}/review`, { method: "POST", body: { action, force } });
      toast(`${asset.id} ${review.status}${review.rejected?.length ? `; rejected ${review.rejected.length} other(s)` : ""}`);
      const fresh = await api(`/api/assets/${asset.id}`);
      onChanged(fresh.asset);
      statusBadge.innerHTML = "";
      statusBadge.append(statusTag(fresh.asset.status));
    } catch (err) {
      if (err.code === "POLICY_VIOLATION" && action === "approve" && confirm(`${err.message}\n\nApprove anyway?`)) return act(action, true);
      toast(err.message);
    }
  }

  // how it was made
  const generation = [];
  if (gen.engine === "assetforge-synth") {
    generation.push(h("dl.facts", dlRows([["Recipe", gen.recipe], ["Preset", gen.preset || "–"], ["Seed", gen.seed],
      ["Variation", gen.variation], ["Effects", (gen.effects || []).map((e) => e.name).join(", ") || "none"]])));
  } else if (gen.engine === "ai-image") {
    generation.push(h("dl.facts", dlRows([["Provider", gen.provider], ["Model", gen.model], ["Transparency", gen.transparency],
      ["Cost", usage ? fmt.money(usage.cost_usd) : fmt.money(gen.cost_usd)],
      ["Tokens", usage ? `${fmt.int(usage.total_tokens)} (${fmt.int(usage.output_image_tokens)} image out)` : null],
      ["Art direction", gen.art_layers?.length ? gen.art_layers.map(layerName).join(" + ") : null],
      ["Job", job ? h("a.mono", { href: `#/jobs/${job.id}` }, job.id) : gen.job_id]])),
      h("div.prompt", gen.prompt || ""),
      gen.final_prompt && gen.final_prompt !== gen.prompt ? h("details", h("summary.help", "Exact prompt sent"), h("div.prompt", gen.final_prompt)) : null);
    if (asset.metadata.regeneration) {
      const r = asset.metadata.regeneration;
      generation.push(h("div.help", `Attempt ${r.attempt} after review of `, h("a", { href: `#/assets/${r.previous}` }, r.previous), `: “${r.feedback}”`));
    }
  } else if (gen.engine === "agent") {
    generation.push(h("dl.facts", dlRows([["Made by", `agent (${gen.method})`], ["Source file", gen.source_file],
      ["Art direction", gen.art_layers?.length ? gen.art_layers.join(" → ") : null]])),
      gen.script ? h("details", h("summary.help", `Generator script (${gen.script_name})`), h("pre.prompt.mono", gen.script)) : null);
  } else if (asset.metadata.derived) {
    generation.push(h("div.help", "Derived from ", h("a", { href: `#/assets/${asset.metadata.derived.from_asset}` }, asset.metadata.derived.from_asset)));
  } else if (asset.metadata.frame) {
    const f = asset.metadata.frame;
    generation.push(h("div.help", "Frame ", f.cell + 1, " of sheet ", h("a", { href: `#/assets/${f.sheet}` }, f.sheet),
      f.animation ? ` · ${f.animation} @ ${f.fps} fps` : ""));
  }

  // animation player
  const anim = asset.metadata.animation;
  const animBlock = [];
  if (anim && frames?.length) {
    const names = Object.keys(anim.animations || {});
    const pick = h("select", [...names.map((n) => h("option", { value: n }, `${n} (${anim.animations[n].frames.length} frames)`)),
      h("option", { value: "__all" }, `all ${frames.length} frames`)]);
    const playBtn = h("button.btn.small.primary", { onclick: play }, "▶ Play");
    function play() {
      stopAnimation?.();
      const chosen = pick.value === "__all" ? { frames: frames.map((f) => f.name), fps: 8, loop: true } : anim.animations[pick.value];
      const seq = chosen.frames.map((n) => frames.find((f) => f.name === n)).filter(Boolean);
      if (!seq.length) return;
      preview.innerHTML = "";
      preview.classList.remove("audio");
      const img = h("img.pixel", { alt: "animation" });
      preview.append(img, h("div.anim-label", `${pick.value === "__all" ? "all frames" : pick.value} · ${chosen.fps} fps`));
      seq.forEach((f) => { const pre = new Image(); pre.src = fileUrl(f.variant_id); });
      let i = 0;
      img.src = fileUrl(seq[0].variant_id);
      const timer = setInterval(() => {
        i += 1;
        if (i >= seq.length) { if (!chosen.loop) { clearInterval(timer); return; } i = 0; }
        img.src = fileUrl(seq[i].variant_id);
      }, 1000 / (chosen.fps || 8));
      stopAnimation = () => { clearInterval(timer); stopAnimation = null; };
      playBtn.textContent = "■ Stop";
      playBtn.onclick = () => { stopAnimation?.(); show(current); playBtn.textContent = "▶ Play"; playBtn.onclick = play; };
    }
    animBlock.push(h("h3", "Animation"), h("div.toolbar", pick, playBtn),
      h("dl.facts", dlRows([["Grid", `${anim.columns} × ${anim.rows}`], ["Cell", anim.cell ? `${anim.cell[0]} × ${anim.cell[1]} px, anchor ${anim.anchor}` : null],
        ["Frames", anim.collection ? h("a", { href: `#/assets?collection=${encodeURIComponent(anim.collection)}` }, `${frames.length} in ${anim.collection}`) : null],
        ...Object.entries(anim.animations || {}).map(([n, a]) => [n, `${a.frames.length} frames · ${a.fps} fps · ${a.loop ? "loop" : "once"}`])])),
      anim.slice_report?.issues?.length ? h("div", anim.slice_report.issues.map((i) => h("span.issue.warn", i))) : null);
  }

  const soundBlock = sound ? soundSection(sound, asset, dlRows) : [];

  const issues = qc ? [...qc.errors.map((e) => h("span.issue.err", e)), ...qc.warnings.map((w) => h("span.issue.warn", w))] : [];
  const nearDupes = qc?.near_duplicates?.length ? h("div.help", "Looks the same as: ", qc.near_duplicates.map((d) =>
    h("a", { href: `#/assets/${d.asset_id}`, style: "margin-right:6px" }, d.asset_id))) : null;

  const similarStrip = h("div.mini-strip");
  const partsGrid = h("div.parts-grid");
  if (asset.children) {
    api(`/api/assets?parent=${asset.id}&limit=200&sort=name`).then(({ items }) => {
      for (const part of items) partsGrid.append(h("a.part", { href: `#/assets/${part.id}`, title: `${part.name} (${part.status})` },
        h("img", { src: thumbUrl(part.primary_variant.id, 128), alt: "" }), h("span", part.name.replace(`${asset.name.replace(/_?sheet.*$/, "")}_`, ""))));
    }).catch(() => partsGrid.append(h("span.muted", "Couldn't load the frames.")));
  }
  if (isImage) {
    api(`/api/assets/${asset.id}/similar?limit=6`).then(({ items }) => {
      if (!items.length) { similarStrip.append(h("span.muted", "Nothing similar yet.")); return; }
      for (const s of items) similarStrip.append(h("a.mini", { href: `#/assets/${s.id}`, title: `${s.name} · similarity ${(s.score * 100).toFixed(0)}%` },
        h("img", { src: thumbUrl(s.primary_variant.id, 128), alt: "" })));
    }).catch(() => similarStrip.append(h("span.muted", "Similarity unavailable (run `assetforge reindex`).")));
  }

  // stories that use this asset (cosmetic: any failure just leaves the section out)
  const storiesBox = h("div.ab-stories");
  try {
    api(`/api/assets/${encodeURIComponent(asset.id)}/stories`).then((data) => {
      const stories = Array.isArray(data?.stories) ? data.stories : [];
      if (!stories.length) return;
      const seen = new Set();
      const chips = stories.filter((s) => {
        const k = `${s.story}|${s.role}`;
        if (!s.story || seen.has(k)) return false;
        seen.add(k);
        return true;
      }).map((s) => h("a.ab-story-chip", { href: `#/story/${encodeURIComponent(s.story)}?tab=assets`, title: s.key ? `as ${s.key}` : "" },
        h("span", s.title || s.story), s.role ? h("span.ab-role", ` · ${s.role}`) : null));
      storiesBox.append(h("h3", "Used in stories"), h("div.ab-story-chips", chips));
    }).catch(() => {});
  } catch { /* never break the panel */ }

  if (current) show(current);
  return h("div",
    preview,
    h("div.body",
      asset.parent_asset_id ? h("div.part-of-line", "◱ Frame of ", h("a", { href: `#/assets/${asset.parent_asset_id}` }, parentName || asset.parent_asset_id),
        " · ", h("a", { href: `#/assets?parent=${asset.parent_asset_id}` }, "all its frames")) : "",
      h("div.title", h("h1", asset.name), h("button.close", { title: "Close (Esc)", onclick: onClose }, "×")),
      reviewBar,
      asset.description && asset.description !== gen.prompt ? h("p.muted", asset.description) : null,
      h("div.actions",
        h("button.btn.small", { onclick: () => copy(current.file.path, "Path copied") }, "Copy path"),
        h("button.btn.small", { onclick: async () => { try { await api("/api/reveal", { method: "POST", body: { variant_id: current.id } }); } catch (e) { toast(e.message); } } }, "Show in Finder"),
        h("button.btn.small", { onclick: () => copy(asset.id, "ID copied") }, "Copy ID"),
        isImage ? h("button.btn.small", { onclick: () => onSimilar(asset.id) }, "Find similar ", h("kbd", "F")) : null),
      h("dl.facts", dlRows(facts)),
      asset.tags.length ? h("div", { style: "margin-top:10px" }, asset.tags.map((t) => h("a.tag", { href: `#/assets?q=${encodeURIComponent(t)}` }, t))) : null,
      storiesBox,
      animBlock,
      soundBlock,
      asset.children ? [h("h3", h("span", `Frames (${asset.children})`), " ", h("a", { href: `#/assets?parent=${asset.id}`, style: "font-weight:400" }, "open as a list")), partsGrid] : "",
      qc ? [h("h3", "Quality checks"), issues.length ? issues : h("span.issue", "no issues"), nearDupes] : null,
      section("Edit details", editForm(asset, onChanged), { note: "name, category, genre, theme, tags" }),
      canRegenerate ? section("Ask for another version", regenerateForm(asset), { note: "with review feedback" }) : null,
      isProcedural(asset) ? section("More versions", soundVaryForm(asset), { note: asset.type === "music" ? "new takes, melody or arrangement" : "new seeded takes" }) : null,
      sound?.engine === "assetforge-music" ? section("Edit music", musicEditForm(asset, sound), { note: "tempo, key, instruments, parts" }) : null,
      isImage && asset.primary_variant?.file?.kind === "image" ? section("Slice into frames", sliceForm(asset),
        { open: ["spritesheet", "animation"].includes(asset.type) && !anim, note: "sprite sheet → aligned frames" }) : null,
      isImage ? [h("h3", "Looks similar"), similarStrip] : null,
      h("h3", `Variants (${asset.variants.length})`), variantList,
      generation.length ? [h("h3", "How it was made"), generation] : null,
      lineageBlock(lineage),
      asset.usage.length ? [h("h3", "Used by"), asset.usage.map((u) => h("div", h("b", u.project), u.usage_key ? ` as ${u.usage_key}` : ""))] : null,
    ),
  );

  function dlRows(rows) {
    return rows.filter(([, v]) => v !== null && v !== undefined && v !== "").flatMap(([k, v]) => [h("dt", k), h("dd", v)]);
  }
}

function editForm(asset, onChanged) {
  const inputs = {
    name: h("input", { type: "text", value: asset.name }),
    description: h("textarea", { rows: 2, value: asset.description || "" }),
    category: h("input", { type: "text", value: asset.category || "", placeholder: "creature, weapon, environment…" }),
    subcategory: h("input", { type: "text", value: asset.subcategory || "" }),
    genre: h("input", { type: "text", value: asset.genre || "", placeholder: "platformer, puzzle…" }),
    theme: h("input", { type: "text", value: asset.theme || "", placeholder: "dark-fantasy, space…" }),
    style: h("input", { type: "text", value: asset.style || "" }),
    project: h("input", { type: "text", value: asset.project || "" }),
  };
  const tags = chipsInput(asset.tags);
  const out = h("div");
  async function save() {
    const body = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value.trim()]));
    body.tags = tags.values();
    try {
      const { asset: updated } = await api(`/api/assets/${asset.id}/update`, { method: "POST", body });
      toast("Saved");
      onChanged(updated);
      out.innerHTML = "";
    } catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
  }
  return h("div.form.compact",
    field("Name", inputs.name), field("Project", inputs.project),
    h("label.wide", h("span.lbl", "Description"), inputs.description),
    field("Category", inputs.category), field("Subcategory", inputs.subcategory), field("Genre", inputs.genre),
    field("Theme", inputs.theme), field("Style", inputs.style),
    h("label.wide", h("span.lbl", "Tags"), tags),
    h("div.wide", h("button.btn.primary.small", { onclick: save }, "Save"), out));
}

function isProcedural(asset) {
  const engine = asset.metadata.generator?.engine || "";
  return ["assetforge-synth", "assetforge-design", "assetforge-ambience", "assetforge-music"].includes(engine);
}

function takeCards(result) {
  const loop = !!result.audio?.loop;
  return h("div.results", (result.variants || []).map((v) => h("div.card",
    h("b", (v.label || "").replace(/_(ogg|mp3|wav)$/, "")),
    h("audio", { controls: true, src: fileUrl(v.variant_id), preload: "metadata", loop, style: "width:100%" }))));
}

function soundVaryForm(asset) {
  const music = asset.metadata.generator?.engine === "assetforge-music" && asset.metadata.generator?.spec?.compose;
  const n = h("input", { type: "number", min: 1, max: 8, value: 2 });
  const out = h("div");
  async function run(keep) {
    out.innerHTML = "";
    out.append(h("p.help", h("span.spinner"), " Rendering…"));
    try {
      const { result } = await api(`/api/assets/${asset.id}/sound/vary`, { method: "POST", body: { variants: +n.value || 1, keep } });
      out.innerHTML = "";
      if (result.asset_id !== asset.id) {
        out.append(h("p", "New piece: ", h("a", { href: `#/assets/${result.asset_id}` }, result.asset.name), h("span.muted", " (linked to this one)")), takeCards(result));
      } else {
        out.append(h("p.help", `Added ${result.variants.length} take(s) to this asset (reopen it to see them in the list).`), takeCards(result));
      }
    } catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
  }
  return h("div",
    h("div.form.compact", field("How many", n)),
    h("div.actions",
      h("button.btn.small.primary", { onclick: () => run("nothing") }, music ? "New takes (new pieces)" : "More takes"),
      music ? h("button.btn.small", { onclick: () => run("chords"), title: "Same key, tempo and chords; a new melody" }, "New melody, same chords") : null,
      music ? h("button.btn.small", { onclick: () => run("melody"), title: "Same tune; new accompaniment, bass and drums" }, "New arrangement, same melody") : null),
    h("p.help", "Free and local. Every take is reproducible from its seed."), out);
}

function musicEditForm(asset, sound) {
  const a = sound.audio || {};
  const tempo = h("input", { type: "number", min: 40, max: 240, placeholder: `${a.bpm ?? ""}` });
  const key = h("input", { type: "text", placeholder: a.key || "e.g. E minor" });
  const transpose = h("input", { type: "number", min: -12, max: 12, value: 0 });
  const roles = ["melody", "harmony", "bass", "drums", "arp", "counter"];
  const mutes = Object.fromEntries(roles.map((r) => [r, h("input", { type: "checkbox" })]));
  const picks = {};
  const pickBox = h("div.form.compact");
  api("/api/sound/options").then(({ instruments }) => {
    for (const r of roles) {
      const list = instruments.filter((i) => (r === "drums") === (i.family === "drums"));
      picks[r] = h("select", h("option", { value: "" }, "keep"), list.map((i) => h("option", { value: i.name }, i.name)));
      pickBox.append(field(r, picks[r]));
    }
  }).catch(() => {});
  const loop = h("select", [["", "keep"], ["true", "seamless loop"], ["false", "not a loop"]].map(([v, l]) => h("option", { value: v }, l)));
  const out = h("div");
  async function run() {
    const instruments = Object.fromEntries(Object.entries(picks).filter(([, el]) => el.value).map(([r, el]) => [r, el.value]));
    const body = { tempo: tempo.value ? +tempo.value : undefined, key: key.value.trim() || undefined, transpose: +transpose.value || 0,
      instruments, mute: roles.filter((r) => mutes[r].checked), loop: loop.value ? loop.value === "true" : undefined,
      project: asset.project || undefined };
    out.innerHTML = "";
    out.append(h("p.help", h("span.spinner"), " Re-rendering the score…"));
    try {
      const { result } = await api(`/api/assets/${asset.id}/music/edit`, { method: "POST", body });
      out.innerHTML = "";
      out.append(h("p", "Saved as ", h("a", { href: `#/assets/${result.asset_id}` }, result.asset.name),
        h("span.muted", ` · ${result.audio.bpm} bpm · ${result.audio.key || ""}`)), takeCards(result));
    } catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
  }
  return h("div",
    h("div.form.compact", field("Tempo", tempo), field("Key", key), field("Transpose", transpose, "semitones"), field("Loop", loop)),
    h("h4", "Instruments"), pickBox,
    h("h4", "Leave out"), h("div.toolbar", roles.map((r) => h("label.help", mutes[r], ` ${r}`))),
    h("div.actions", h("button.btn.small.primary", { onclick: run }, "Render edited version")),
    h("p.help", "Plays the same score with your changes and saves it as a new asset linked to this one."), out);
}

function regenerateForm(asset) {
  const feedback = h("textarea", { rows: 3, placeholder: "What to change, concretely: “too cartoonish; realistic proportions, grittier texture, no outline”" });
  const style = h("input", { type: "text", list: "gen-styles-r", placeholder: "keep the same", value: "" });
  const avoid = chipsInput([], { placeholder: "e.g. cute faces" });
  const n = h("input", { type: "number", min: 1, max: 4, value: 1 });
  const reject = h("input", { type: "checkbox", checked: true });
  const out = h("div");
  const body = () => ({ feedback: feedback.value.trim(), style: style.value.trim() || undefined, n: +n.value || 1,
    art: avoid.values().length ? { avoid: avoid.values() } : undefined, reject_previous: reject.checked });
  async function run(dry) {
    if (feedback.value.trim().length < 3) { toast("Say what to change"); feedback.focus(); return; }
    out.innerHTML = "";
    out.append(h("p.help", h("span.spinner"), dry ? " Building the revised prompt…" : " Generating a new version… (10–60 s)"));
    try {
      const { regenerate: r } = await api(`/api/assets/${asset.id}/regenerate`, { method: "POST", body: { ...body(), dry_run: dry } });
      out.innerHTML = "";
      if (dry) {
        out.append(h("div.estimate", h("div.help", `Attempt ${r.attempt} of ${r.limit} · about ${fmt.money(r.estimated_cost_usd)}`), h("div.prompt", r.prompt)));
      } else {
        out.append(h("div.help", `Attempt ${r.attempt} of ${r.limit}. New candidate${r.result.assets.length > 1 ? "s" : ""}: `),
          h("div.mini-strip", r.result.assets.map((a) => h("a.mini", { href: `#/assets/${a.asset_id}` }, h("img", { src: thumbUrl(a.variant_id, 128), alt: "" })))));
      }
    } catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
  }
  return h("div",
    h("div.form.compact", h("label.wide", h("span.lbl", "Feedback"), feedback), field("Switch style", style), field("Candidates", n),
      h("label.wide", h("span.lbl", "Also avoid"), avoid),
      h("label.check.wide", reject, h("span", "Reject this version"))),
    h("div.actions", h("button.btn.small", { onclick: () => run(true) }, "Preview prompt"), h("button.btn.small.primary", { onclick: () => run(false) }, "Regenerate")),
    h("p.help", "Re-runs the original request with your feedback. Attempts per original are capped (generation.max_regenerations)."),
    out);
}

function sliceForm(asset) {
  const columns = h("input", { type: "number", min: 1, max: 16, placeholder: "auto" });
  const rows = h("input", { type: "number", min: 1, max: 16, placeholder: "auto" });
  const fit = h("select", h("option", { value: "auto" }, "auto"), h("option", { value: "shared" }, "one scale (animation frames)"),
    h("option", { value: "each" }, "fill each cell (icons / items)"));
  const cell = h("input", { type: "number", min: 16, max: 2048, placeholder: "fit largest" });
  const anchor = h("select", h("option", { value: "bottom" }, "bottom (feet on one line)"), h("option", { value: "center" }, "center"));
  const animations = h("input", { type: "text", placeholder: "walk=0-3@8, idle=4-7@4, die=8-11@10:once" });
  const names = h("input", { type: "text", placeholder: "auto: from the prompt" });
  const replace = h("input", { type: "checkbox", checked: true });
  const status = h("div.help");
  const overlay = h("div.slice-preview");
  const out = h("div");
  const variantId = asset.primary_variant?.id;

  const body = (extra = {}) => ({
    columns: columns.value ? +columns.value : undefined, rows: rows.value ? +rows.value : undefined, fit: fit.value,
    cell: cell.value ? +cell.value : undefined, anchor: anchor.value,
    animations: animations.value.split(",").map((x) => x.trim()).filter(Boolean),
    names: names.value.split(",").map((x) => x.trim()).filter(Boolean), replace_previous: replace.checked, ...extra });

  function drawOverlay(plan) {
    overlay.innerHTML = "";
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${plan.width} ${plan.height}`);
    const line = (x1, y1, x2, y2) => { const l = document.createElementNS(ns, "line");
      Object.entries({ x1, y1, x2, y2, class: "grid-line" }).forEach(([k, v]) => l.setAttribute(k, v)); svg.append(l); };
    for (let c = 1; c < plan.columns; c++) line(plan.width * c / plan.columns, 0, plan.width * c / plan.columns, plan.height);
    for (let r = 1; r < plan.rows; r++) line(0, plan.height * r / plan.rows, plan.width, plan.height * r / plan.rows);
    for (const [x, y, w, hh] of plan.boxes) {
      const rect = document.createElementNS(ns, "rect");
      Object.entries({ x: x * plan.width, y: y * plan.height, width: w * plan.width, height: hh * plan.height, class: "subject-box" })
        .forEach(([k, v]) => rect.setAttribute(k, v));
      svg.append(rect);
    }
    overlay.append(h("img", { src: fileUrl(variantId), alt: "" }), svg);
  }

  async function preview() {
    status.textContent = "Reading the sheet…";
    try {
      const { plan } = await api(`/api/assets/${asset.id}/slice`, { method: "POST", body: body({ dry_run: true }) });
      if (!columns.value) columns.placeholder = String(plan.columns);
      if (!rows.value) rows.placeholder = String(plan.rows);
      const src = { request: "from how it was generated", prompt: "from its prompt", detected: "detected in the image", given: "as you set it" }[plan.source];
      status.innerHTML = "";
      status.append(`${plan.columns} × ${plan.rows} grid (${src}) · ${plan.subjects} subjects found · fit: ${plan.fit === "each" ? "fill each cell" : "one scale"}`,
        plan.previous_frames ? ` · re-slicing replaces ${plan.previous_frames} earlier frames` : "",
        plan.report.issues.length ? h("div", plan.report.issues.map((i) => h("span.issue.warn", i))) : "");
      if (plan.report.crowded_cells?.length && plan.detected) suggest(plan.detected);
      drawOverlay(plan);
    } catch (err) { status.innerHTML = ""; status.append(errorBox(err)); }
  }

  function suggest([c, r]) {
    status.append(h("div", h("button.btn.small.primary", { onclick: () => { columns.value = c; rows.value = r; preview(); } }, `Use ${c} × ${r}`)));
  }

  async function run() {
    out.innerHTML = "";
    out.append(h("p.help", h("span.spinner"), " Slicing…"));
    try {
      const { slice: s } = await api(`/api/assets/${asset.id}/slice`, { method: "POST", body: body() });
      out.innerHTML = "";
      out.append(h("div.help", `${s.report.found}/${s.report.expected} frames in a ${s.grid.join(" × ")} grid, cell ${s.report.cell.join("×")} px → `,
        h("a", { href: `#/assets?collection=${encodeURIComponent(s.collection)}` }, s.collection),
        s.archived.length ? ` · ${s.archived.length} earlier frames moved to the archive` : "",
        s.report.issues.length ? h("div", s.report.issues.map((i) => h("span.issue.warn", i))) : null),
        h("div.mini-strip", s.frames.slice(0, 24).map((f) => h("a.mini", { href: `#/assets/${f.asset_id}`, title: f.name },
          h("img", { src: `/media/thumb/${f.variant_id}?size=128`, alt: "" })))));
    } catch (err) {
      out.innerHTML = "";
      out.append(errorBox(err));
      if (err.details?.suggested_grid) out.append(h("div", { style: "margin-top:6px" },
        h("button.btn.small.primary", { onclick: () => { [columns.value, rows.value] = err.details.suggested_grid; preview(); run(); } },
          `Slice as ${err.details.suggested_grid.join(" × ")} instead`)));
    }
  }

  [columns, rows, fit].forEach((el) => el.addEventListener("change", preview));
  preview();
  return h("div",
    overlay, status,
    h("div.form.compact", field("Columns", columns), field("Rows", rows), field("Scale", fit), field("Anchor", anchor),
      field("Cell size (px)", cell),
      h("label.wide", h("span.lbl", "Animations"), animations, h("span.help", "cells numbered from 0, row by row; fps after @")),
      h("label.wide", h("span.lbl", "Frame names"), names),
      h("label.check.wide", replace, h("span", "Move frames from an earlier slice of this sheet to the archive"))),
    h("div.actions", h("button.btn.small", { onclick: preview }, "Check grid"), h("button.btn.small.primary", { onclick: run }, "Slice")),
    h("p.help", "Leave the grid empty to detect it. A grid that would put two things in one cell is refused."),
    out);
}

function lineageBlock(graph) {
  // Ancestors read "edited from X"; descendants read "edited version: Y" (the relation is stored on the newer asset).
  const label = (relation, key) => {
    const verb = relation.split("_")[0];
    return key === "parents" ? `${verb} from` : `${verb} version`;
  };
  const items = (nodes, key) => h("ul.tree", nodes.map((n) => h("li",
    `${label(n.relation, key)}: `, h("a", { href: `#/assets/${n.asset_id}` }, n.name), ` (${n.status})`,
    n[key]?.length ? items(n[key], key) : null)));
  if (!graph.ancestors.length && !graph.descendants.length) return null;
  return [h("h3", "Lineage"),
    graph.ancestors.length ? [h("div.help", "Came from"), items(graph.ancestors, "parents")] : null,
    graph.descendants.length ? [h("div.help", "Used to make"), items(graph.descendants, "children")] : null];
}

export { isAudio };

// Designed sounds, ambience and music: musical facts, every take with its stems/versions, and the MIDI score.
function soundSection(sound, asset, dlRows) {
  const a = sound.audio || {};
  const brief = sound.brief || {};
  const facts = [["Engine", sound.engine.replace("assetforge-", "")], ["Tempo", a.bpm ? `${a.bpm} bpm` : null], ["Key", a.key],
    ["Meter", a.meter], ["Bars", a.bars], ["Length", a.length_s ? `${(+a.length_s).toFixed(2)} s` : null],
    ["Loop", a.loop ? `seamless, ${(+a.loop_start || 0).toFixed(2)}–${(+a.loop_end || 0).toFixed(2)} s` : null],
    ["Scene", a.scene], ["Layers", a.layers?.join(", ")], ["Genre", brief.genre], ["Mood", brief.mood],
    ["Energy", brief.energy != null ? brief.energy : null], ["Instruments", a.instruments?.join(", ")]];
  const takes = sound.takes.map((t) => h("div.card.sub-card",
    h("b", t.label.replace(/_(ogg|mp3|wav)$/, "")), " ", h("span.muted", `${((t.duration_ms || 0) / 1000).toFixed(2)} s`),
    h("audio", { controls: true, src: fileUrl(t.variant_id), preload: "none", loop: !!a.loop, style: "width:100%" }),
    Object.entries(t.extras || {}).map(([label, x]) => h("div.player", h("span.help", label.replace("stem_", "stem: ")),
      h("audio", { controls: true, src: fileUrl(x.variant_id), preload: "none", loop: !!a.loop }))),
    t.files?.midi ? h("a.help", { href: fileUrl(t.files.midi.variant_id), download: `${asset.name}_${t.label}.mid` }, "Download MIDI score") : null));
  return [h("h3", asset.type === "music" ? "Music" : asset.type === "ambience" ? "Ambience" : "Sound design"),
    h("dl.facts", dlRows(facts)), takes,
    h("div.help", "Agents: `assetforge music edit|vary` or `audio vary` make new versions; `music score` prints the score.")];
}

function shapeFacts(file) {
  if (!file?.aspect) return null;
  const orient = file.aspect > 1.15 ? "landscape" : file.aspect < 0.87 ? "portrait" : "square";
  const tone = file.mean_luma == null ? "" : file.mean_luma < 0.32 ? ", dark" : file.mean_luma > 0.68 ? ", light" : "";
  const alpha = file.alpha_fraction ? `, ${Math.round(file.alpha_fraction * 100)}% transparent` : "";
  return `${orient} ${file.aspect.toFixed(2)}${tone}${alpha}`;
}
