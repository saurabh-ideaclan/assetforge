import { api, qs, h, fmt, badge, assetCard, isAudio, thumbUrl, fileUrl, copy, toast, errorBox } from "/static/lib.js";

const PAGE = 60;
const FILTERS = [
  ["type", "types", "Type"], ["status", "statuses", "Status"], ["kind", "kinds", "Kind"], ["origin", "origins", "Origin"],
  ["project", "projects", "Project"], ["style", "styles", "Style"], ["collection", "collections", "Collection"],
];

export async function assets(root, { rest, params }) {
  const state = { q: params.get("q") || "", sort: params.get("sort") || "newest", filters: {}, offset: 0, total: 0,
                  loading: false, selected: rest[0] || null };
  for (const [key] of FILTERS) if (params.get(key)) state.filters[key] = params.get(key);
  const { facets } = await api("/api/facets");

  const grid = h("div.grid");
  const sentinel = h("div.sentinel");
  const count = h("span.muted");
  const detail = h("div.detail");
  const layout = h("div.browser", h("div", grid, sentinel), detail);

  const search = h("input", { type: "search", placeholder: "Search names, tags, descriptions, prompts…", value: state.q,
    oninput: debounce((e) => { state.q = e.target.value; reload(); }, 250) });
  const sort = h("select", { onchange: (e) => { state.sort = e.target.value; reload(); } },
    ["newest", "oldest", "updated", "name", "type"].map((s) => h("option", { value: s, selected: s === state.sort }, `Sort: ${s}`)));
  const selects = FILTERS.map(([key, facetKey, label]) => {
    const options = facets[facetKey] || [];
    if (!options.length) return null;
    return h("select", { title: label, onchange: (e) => { state.filters[key] = e.target.value; reload(); } },
      h("option", { value: "" }, `${label}: all`),
      options.map((o) => h("option", { value: o.value, selected: state.filters[key] === o.value }, `${o.value} (${o.count})`)));
  });
  const clear = h("button.btn", { onclick: () => { state.filters = {}; state.q = ""; search.value = "";
    selects.forEach((s) => s && (s.value = "")); reload(); } }, "Clear");

  root.append(h("div.page-head", h("div", h("h1", "Assets"), h("div.sub", count))),
    h("div.toolbar", search, sort), h("div.toolbar", selects, clear), layout);

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
      count.textContent = `${fmt.int(data.total)} asset${data.total === 1 ? "" : "s"}`;
      if (!state.offset && !data.items.length) grid.append(h("div.empty", { style: "grid-column:1/-1" }, "No assets match."));
      for (const a of data.items) grid.append(assetCard(a, { onclick: () => select(a.id), selected: a.id === state.selected }));
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
    grid.innerHTML = "";
    syncHash();
    load();
  }

  async function select(id) {
    state.selected = id;
    grid.querySelectorAll(".asset").forEach((c) => c.classList.toggle("selected", c.dataset.id === id));
    syncHash();
    if (!id) { layout.classList.remove("with-detail"); detail.innerHTML = ""; return; }
    layout.classList.add("with-detail");
    detail.innerHTML = "";
    detail.append(h("div.body", h("span.spinner"), " Loading…"));
    try {
      const data = await api(`/api/assets/${id}`);
      detail.innerHTML = "";
      detail.append(renderDetail(data, () => select(null)));
    } catch (err) {
      detail.innerHTML = "";
      detail.append(h("div.body", errorBox(err)));
    }
  }

  const observer = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && load(), { rootMargin: "400px" });
  observer.observe(sentinel);
  syncHash();
  await load();
  if (state.selected) select(state.selected);

  return {
    cleanup: () => observer.disconnect(),
    update: ({ rest: r }) => { if ((r[0] || null) !== state.selected) select(r[0] || null); },
  };
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function renderDetail({ asset, lineage, job, usage }, onClose) {
  let current = asset.primary_variant;
  const preview = h("div.preview");
  const variantList = h("div.variants");

  function show(variant) {
    current = variant;
    preview.innerHTML = "";
    preview.classList.toggle("audio", variant.file.kind === "audio");
    if (variant.file.kind === "image") {
      preview.append(h("img", { src: fileUrl(variant.id), alt: asset.name }));
    } else if (variant.file.kind === "audio") {
      preview.append(h("img", { src: thumbUrl(variant.id, 512), alt: "" }),
        h("audio", { controls: true, src: fileUrl(variant.id), preload: "metadata" }));
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
  const facts = [
    ["Type", asset.type], ["Status", badge(asset.status)], ["Origin", asset.origin], ["Key", asset.key],
    ["Scope", asset.scope], ["Project", asset.project], ["Style", asset.style], ["License", asset.license],
    ["Commercial use", asset.commercial_use], ["Collections", asset.collections.join(", ")],
    ["Created", fmt.date(asset.created_at)], ["ID", h("span.mono", asset.id)],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");

  const generation = [];
  if (gen.engine === "assetforge-synth") {
    generation.push(h("dl.facts", dlRows([["Recipe", gen.recipe], ["Preset", gen.preset || "–"], ["Seed", gen.seed],
      ["Variation", gen.variation], ["Effects", (gen.effects || []).map((e) => e.name).join(", ") || "none"]])));
  } else if (gen.engine === "ai-image") {
    generation.push(h("dl.facts", dlRows([["Provider", gen.provider], ["Model", gen.model], ["Transparency", gen.transparency],
      ["Cost", usage ? fmt.money(usage.cost_usd) : fmt.money(gen.cost_usd)],
      ["Tokens", usage ? `${fmt.int(usage.total_tokens)} (${fmt.int(usage.output_image_tokens)} image out)` : null],
      ["Job", job ? h("a.mono", { href: `#/jobs/${job.id}` }, job.id) : gen.job_id]])),
      h("h3", "Prompt"), h("div.prompt", gen.prompt || ""),
      gen.final_prompt && gen.final_prompt !== gen.prompt ? h("details", h("summary.help", "Exact prompt sent"), h("div.prompt", gen.final_prompt)) : null);
  } else if (asset.metadata.derived) {
    generation.push(h("div.help", `Derived from ${asset.metadata.derived.from_asset}`));
  }

  const issues = qc ? [...qc.errors.map((e) => h("span.issue.err", e)), ...qc.warnings.map((w) => h("span.issue.warn", w))] : [];

  if (current) show(current);
  return h("div",
    preview,
    h("div.body",
      h("div.title", h("h1", asset.name), h("button.close", { title: "Close", onclick: onClose }, "×")),
      asset.description && asset.description !== gen.prompt ? h("p.muted", asset.description) : null,
      h("div.actions",
        h("button.btn", { onclick: () => copy(current.file.path, "Path copied") }, "Copy path"),
        h("button.btn", { onclick: async () => { try { await api("/api/reveal", { method: "POST", body: { variant_id: current.id } }); } catch (e) { toast(e.message); } } }, "Show in Finder"),
        h("button.btn", { onclick: () => copy(asset.id, "ID copied") }, "Copy ID")),
      h("dl.facts", dlRows(facts)),
      asset.tags.length ? h("div", { style: "margin-top:10px" }, asset.tags.map((t) => h("span.tag", t))) : null,
      h("h3", `Variants (${asset.variants.length})`), variantList,
      generation.length ? [h("h3", "How it was made"), generation] : null,
      qc ? [h("h3", "Quality checks"), issues.length ? issues : h("span.issue", "no issues")] : null,
      lineageBlock(lineage),
      asset.usage.length ? [h("h3", "Used by"), asset.usage.map((u) => h("div", h("b", u.project), u.usage_key ? ` as ${u.usage_key}` : ""))] : null,
    ),
  );

  function dlRows(rows) {
    return rows.filter(([, v]) => v !== null && v !== undefined).flatMap(([k, v]) => [h("dt", k), h("dd", v)]);
  }

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
