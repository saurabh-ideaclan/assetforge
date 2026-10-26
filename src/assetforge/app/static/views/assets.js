import { api, qs, h, fmt, badge, assetCard, isAudio, thumbUrl, fileUrl, copy, toast, errorBox, chipsInput, swatches,
  segmented, section, field } from "/static/lib.js";
import { layerName } from "/static/views/art.js";

const PAGE = 60;
const FILTERS = [
  ["type", "types", "Type"], ["kind", "kinds", "Kind"], ["origin", "origins", "Origin"], ["project", "projects", "Project"],
  ["style", "styles", "Style"], ["category", "categories", "Category"], ["genre", "genres", "Genre"], ["theme", "themes", "Theme"],
  ["collection", "collections", "Collection"],
];
const LOOK = ["color", "orientation", "tone", "transparent"];
const COLORS = { red: "#e53935", orange: "#fb8c00", yellow: "#fdd835", green: "#43a047", teal: "#00897b", blue: "#1e88e5",
  purple: "#8e24aa", pink: "#ec407a", brown: "#795548", black: "#111111", white: "#f5f5f5", gray: "#9e9e9e" };
const STATUS_TABS = [["", "All"], ["candidate", "To review"], ["approved", "Approved"], ["rejected", "Rejected"]];

export async function assets(root, { rest, params }) {
  const state = { q: params.get("q") || "", sort: params.get("sort") || "newest", filters: {}, offset: 0, total: 0,
                  loading: false, selected: rest[0] || null, items: [] };
  for (const key of [...FILTERS.map(([k]) => k), ...LOOK, "status", "similar"]) if (params.get(key)) state.filters[key] = params.get(key);
  const { facets } = await api("/api/facets");
  const candidates = (facets.statuses || []).find((s) => s.value === "candidate")?.count || 0;

  const grid = h("div.grid");
  const sentinel = h("div.sentinel");
  const count = h("span.muted");
  const detail = h("div.detail");
  const layout = h("div.browser", h("div", grid, sentinel), detail);

  const search = h("input", { type: "search", placeholder: "Search: “dark forest background”, “transparent enemy”…  ( / )", value: state.q,
    oninput: debounce((e) => { state.q = e.target.value; delete state.filters.similar; reload(); }, 250) });
  const sort = h("select", { onchange: (e) => { state.sort = e.target.value; reload(); } },
    ["newest", "oldest", "updated", "name", "type"].map((s) => h("option", { value: s, selected: s === state.sort }, `Sort: ${s}`)));
  const statusSeg = segmented(STATUS_TABS.map(([v, l]) => [v, v === "candidate" && candidates ? `${l} (${candidates})` : l]),
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
  const panel = h("div.filter-panel", h("div.toolbar", selects), h("div.toolbar", colorRow, orientSeg, toneSeg, alphaSeg));
  const activeChips = h("div.active-filters");
  const filterBtn = h("button.btn", { onclick: () => { panel.classList.toggle("open"); } }, "Filters");

  root.append(
    h("div.page-head", h("div", h("h1", "Assets"), h("div.sub", count)), statusSeg),
    h("div.toolbar", search, sort, filterBtn),
    panel, activeChips, layout,
    h("div.kbd-hint", "Keys: ← → move · A approve · X reject · F find similar · Esc close · / search"));

  function setFilter(key, value) {
    if (value) state.filters[key] = value; else delete state.filters[key];
    reload();
  }

  function renderChips() {
    activeChips.innerHTML = "";
    const entries = Object.entries(state.filters).filter(([k]) => k !== "status");
    filterBtn.textContent = entries.length ? `Filters (${entries.length})` : "Filters";
    for (const [k, v] of entries) {
      const label = k === "similar" ? `looks like ${v}` : k === "transparent" ? (v === "true" ? "transparent" : "opaque") : `${k}: ${v}`;
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
      if (!state.offset && !data.items.length) grid.append(h("div.empty", { style: "grid-column:1/-1" }, "No assets match. Try fewer filters."));
      for (const a of data.items) {
        state.items.push(a);
        grid.append(assetCard(a, { onclick: () => select(a.id), selected: a.id === state.selected }));
      }
      state.offset += data.items.length;
    } catch (err) {
      grid.append(errorBox(err));
    } finally {
      state.loading = false;
    }
  }

  function reload() {
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

  function refreshCard(asset) {
    const old = grid.querySelector(`.asset[data-id="${asset.id}"]`);
    if (old) old.replaceWith(assetCard(asset, { onclick: () => select(asset.id), selected: asset.id === state.selected }));
  }

  async function review(action, force = false) {
    if (!state.selected) return;
    try {
      const { review: r } = await api(`/api/assets/${state.selected}/review`, { method: "POST", body: { action, force } });
      toast(`${state.selected} ${r.status}`);
      const reviewed = state.selected;
      const next = neighbour(1) !== reviewed ? neighbour(1) : neighbour(-1);
      const { asset } = await api(`/api/assets/${reviewed}`);
      if (state.filters.status && state.filters.status !== asset.status) {
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
    if (e.target.closest?.("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) {
      if ((e.metaKey || e.ctrlKey) && e.key === "f") { e.preventDefault(); search.focus(); search.select(); }
      return;
    }
    if (e.key === "/") { e.preventDefault(); search.focus(); search.select(); }
    else if (e.key === "ArrowRight" || e.key === "j") { e.preventDefault(); select(neighbour(1)); }
    else if (e.key === "ArrowLeft" || e.key === "k") { e.preventDefault(); select(neighbour(-1)); }
    else if (e.key === "Escape") select(null);
    else if (e.key === "a" && state.selected) review("approve");
    else if (e.key === "x" && state.selected) review("reject");
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

  return {
    cleanup: () => { observer.disconnect(); document.removeEventListener("keydown", onKey); },
    update: ({ rest: r }) => { if ((r[0] || null) !== state.selected) select(r[0] || null); },
  };
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------------------------------------------------------------------- detail

function renderDetail({ asset, lineage, job, usage, frames, can_regenerate: canRegenerate, siblings }, { onClose, onChanged, onSimilar, onReviewed }) {
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
      preview.append(h("img", { src: thumbUrl(variant.id, 512), alt: "" }),
        h("audio", { controls: true, src: fileUrl(variant.id), preload: "metadata" }));
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
  const statusBadge = h("span", badge(asset.status));
  const reviewBar = h("div.review-bar", statusBadge,
    asset.status !== "approved" ? h("button.btn.small.primary", { onclick: () => onReviewed("approve") }, "Approve ", h("kbd", "A")) : null,
    siblings?.length && asset.status === "candidate" ? h("button.btn.small", { title: "Approve this one and reject the other candidates from the same run", onclick: () => act("pick") }, `Pick (reject ${siblings.length} other${siblings.length > 1 ? "s" : ""})`) : null,
    asset.status !== "rejected" ? h("button.btn.small", { onclick: () => onReviewed("reject") }, "Reject ", h("kbd", "X")) : null,
    asset.status !== "candidate" ? h("button.btn.small", { onclick: () => act("candidate") }, "Back to review") : null);

  async function act(action, force = false) {
    try {
      const { review } = await api(`/api/assets/${asset.id}/review`, { method: "POST", body: { action, force } });
      toast(`${asset.id} ${review.status}${review.rejected?.length ? `; rejected ${review.rejected.length} other(s)` : ""}`);
      const fresh = await api(`/api/assets/${asset.id}`);
      onChanged(fresh.asset);
      statusBadge.innerHTML = "";
      statusBadge.append(badge(fresh.asset.status));
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

  const issues = qc ? [...qc.errors.map((e) => h("span.issue.err", e)), ...qc.warnings.map((w) => h("span.issue.warn", w))] : [];
  const nearDupes = qc?.near_duplicates?.length ? h("div.help", "Looks the same as: ", qc.near_duplicates.map((d) =>
    h("a", { href: `#/assets/${d.asset_id}`, style: "margin-right:6px" }, d.asset_id))) : null;

  const similarStrip = h("div.mini-strip");
  if (isImage) {
    api(`/api/assets/${asset.id}/similar?limit=6`).then(({ items }) => {
      if (!items.length) { similarStrip.append(h("span.muted", "Nothing similar yet.")); return; }
      for (const s of items) similarStrip.append(h("a.mini", { href: `#/assets/${s.id}`, title: `${s.name} · similarity ${(s.score * 100).toFixed(0)}%` },
        h("img", { src: thumbUrl(s.primary_variant.id, 128), alt: "" })));
    }).catch(() => similarStrip.append(h("span.muted", "Similarity unavailable (run `assetforge reindex`).")));
  }

  if (current) show(current);
  return h("div",
    preview,
    h("div.body",
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
      animBlock,
      qc ? [h("h3", "Quality checks"), issues.length ? issues : h("span.issue", "no issues"), nearDupes] : null,
      section("Edit details", editForm(asset, onChanged), { note: "name, category, genre, theme, tags" }),
      canRegenerate ? section("Ask for another version", regenerateForm(asset), { note: "with review feedback" }) : null,
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
  const columns = h("input", { type: "number", min: 1, max: 16, value: 4 });
  const rows = h("input", { type: "number", min: 1, max: 16, value: 2 });
  const cell = h("input", { type: "number", min: 16, max: 2048, placeholder: "fit largest" });
  const anchor = h("select", h("option", { value: "bottom" }, "bottom (feet on one line)"), h("option", { value: "center" }, "center"));
  const animations = h("input", { type: "text", placeholder: "walk=0-3@8, idle=4-7@4, die=8-11@10:once" });
  const names = h("input", { type: "text", placeholder: "optional: comma-separated, row by row" });
  const out = h("div");
  async function run() {
    out.innerHTML = "";
    out.append(h("p.help", h("span.spinner"), " Slicing…"));
    try {
      const { slice: s } = await api(`/api/assets/${asset.id}/slice`, { method: "POST", body: {
        columns: +columns.value, rows: +rows.value, cell: cell.value ? +cell.value : undefined, anchor: anchor.value,
        animations: animations.value.split(",").map((x) => x.trim()).filter(Boolean),
        names: names.value.split(",").map((x) => x.trim()).filter(Boolean) } });
      out.innerHTML = "";
      out.append(h("div.help", `${s.report.found}/${s.report.expected} frames, cell ${s.report.cell.join("×")} px → `,
        h("a", { href: `#/assets?collection=${encodeURIComponent(s.collection)}` }, s.collection),
        s.report.issues.length ? h("div", s.report.issues.map((i) => h("span.issue.warn", i))) : null),
        h("div.mini-strip", s.frames.slice(0, 12).map((f) => h("a.mini", { href: `#/assets/${f.asset_id}`, title: f.name },
          h("img", { src: `/media/thumb/${f.variant_id}?size=128`, alt: "" })))));
    } catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
  }
  return h("div",
    h("div.form.compact", field("Columns", columns), field("Rows", rows), field("Cell size (px)", cell), field("Anchor", anchor),
      h("label.wide", h("span.lbl", "Animations"), animations, h("span.help", "cells numbered from 0, row by row; fps after @")),
      h("label.wide", h("span.lbl", "Frame names"), names)),
    h("div.actions", h("button.btn.small.primary", { onclick: run }, "Slice")),
    h("p.help", "Every frame is scaled by one factor and placed on a common baseline, so the character never jumps."),
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

function shapeFacts(file) {
  if (!file?.aspect) return null;
  const orient = file.aspect > 1.15 ? "landscape" : file.aspect < 0.87 ? "portrait" : "square";
  const tone = file.mean_luma == null ? "" : file.mean_luma < 0.32 ? ", dark" : file.mean_luma > 0.68 ? ", light" : "";
  const alpha = file.alpha_fraction ? `, ${Math.round(file.alpha_fraction * 100)}% transparent` : "";
  return `${orient} ${file.aspect.toFixed(2)}${tone}${alpha}`;
}
