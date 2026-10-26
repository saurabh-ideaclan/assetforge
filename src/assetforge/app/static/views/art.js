import { api, h, errorBox, toast, copy, chipsInput, paletteInput, swatches, segmented, field, fmt } from "/static/lib.js";

const TEXT_FIELDS = [
  ["genre", "Genre", "survival horror, match-3 puzzle…"], ["theme", "Theme", "derelict space station, enchanted forest…"],
  ["mood", "Mood", "tense, cosy, triumphant…"], ["lighting", "Lighting", "cinematic rim light, flat daylight…"],
  ["perspective", "Perspective", "side view, top-down, isometric…"], ["shapes", "Shapes", "angular, rounded, realistic proportions…"],
  ["outline", "Outlines", "none, thin dark, thick coloured…"], ["shading", "Shading", "cel, soft gradients, painterly…"],
  ["texture", "Texture", "brushed metal, paper grain…"], ["composition", "Composition", "centred, low camera angle…"],
];
export function layerName(layer) {
  const [kind, name] = layer.split(":");
  return { preset: `preset ${name}`, profile: `${name} profile`, style: `style ${name}`, request: name === "art" ? "these settings" : "custom style" }[kind] || layer;
}

const PERSPECTIVES = ["side view", "top-down", "isometric", "three-quarter front", "front view", "first-person UI"];

export async function art(root, { rest, params }) {
  const data = await api("/api/art");
  const tab = params.get("tab") === "presets" ? "presets" : "profiles";
  const body = h("div");
  root.append(
    h("div.page-head",
      h("div", h("h1", "Art direction"),
        h("div.sub", "Give each game its own look. Presets are starting points; every field is free text, so agents and you can describe any style.")),
      segmented([["profiles", `Game profiles (${data.profiles.length})`], ["presets", `Presets (${data.presets.length})`]], tab,
        (v) => (location.hash = `#/art?tab=${v}`))),
    body);
  if (tab === "presets") presetsView(body, data);
  else profilesView(body, data, rest[0] || params.get("project"), params.get("style"));
  return {};
}

// ---------------------------------------------------------------------- presets

function presetsView(root, data) {
  root.append(h("p.help", `Add your own: put NAME.yaml in ${data.custom_presets_dir} (same name replaces a built-in).`),
    h("div.preset-grid", data.presets.map((p) => h("div.card.preset",
      h("div.preset-head", h("b", p.name), p.source === "built-in" ? null : h("span.badge", "yours")),
      h("div", p.summary),
      p.palette?.length ? swatches(p.palette, 16) : null,
      h("div.chips-static", (p.best_for || []).map((t) => h("span.tag", t))),
      h("details", h("summary.help", "What it tells the image model"), h("div.prompt", p.prompt_text)),
      h("div.actions",
        h("a.btn.small", { href: `#/generate?tab=image&style=${encodeURIComponent(p.name)}` }, "Use in Generate"),
        h("a.btn.small", { href: `#/art?tab=profiles&project=new&style=${encodeURIComponent(p.name)}` }, "Start a game profile"))))));
}

// ---------------------------------------------------------------------- profiles

function profilesView(root, data, selected, styleSeed) {
  const list = h("div.profile-list");
  const editor = h("div");
  root.append(h("div.split", list, editor));
  const byProject = Object.fromEntries(data.profiles.map((p) => [p.project, p]));

  function renderList(active) {
    list.innerHTML = "";
    list.append(h("button.btn.primary.block", { onclick: () => open(null) }, "+ New game profile"));
    if (!data.profiles.length) list.append(h("div.empty.small", "No profiles yet. A profile makes every image for that game follow one art direction."));
    for (const p of data.profiles) {
      list.append(h("div.profile-item", { class: p.project === active ? "on" : "", onclick: () => open(p.project) },
        h("div.row", h("b", p.project), h("span.muted", p.art.style || "custom")),
        p.art.palette?.length ? swatches(p.art.palette, 12) : null,
        h("div.help", [p.art.mood, p.art.lighting].filter(Boolean).join(" · ") || "—")));
    }
  }

  function open(project) {
    history.replaceState(null, "", project ? `#/art?tab=profiles&project=${encodeURIComponent(project)}` : "#/art?tab=profiles&project=new");
    renderList(project);
    editor.innerHTML = "";
    editor.append(profileEditor(data, project ? byProject[project] : null, project ? null : styleSeed, (saved) => {
      byProject[saved.project] = saved;
      const i = data.profiles.findIndex((p) => p.project === saved.project);
      if (i >= 0) data.profiles[i] = saved; else data.profiles.push(saved);
      data.profiles.sort((a, b) => a.project.localeCompare(b.project));
      open(saved.project);
    }, (deleted) => {
      data.profiles = data.profiles.filter((p) => p.project !== deleted);
      delete byProject[deleted];
      open(data.profiles[0]?.project || null);
    }));
  }

  open(selected && selected !== "new" && byProject[selected] ? selected : (selected === "new" ? null : data.profiles[0]?.project || null));
}

function profileEditor(data, profile, styleSeed, onSaved, onDeleted) {
  const a = profile?.art || (styleSeed ? { style: styleSeed } : {});
  const presetNames = data.presets.map((p) => p.name);
  const project = h("input", { type: "text", value: profile?.project || "", placeholder: "game name, e.g. space-survivor",
    list: "af-projects", disabled: !!profile });
  const projectList = h("datalist", { id: "af-projects" }, data.projects.filter((p) => !data.profiles.some((x) => x.project === p))
    .map((p) => h("option", { value: p })));
  const style = h("input", { type: "text", value: a.style || "", list: "af-presets", placeholder: "preset or your own words" });
  const presetList = h("datalist", { id: "af-presets" }, data.presets.map((p) => h("option", { value: p.name }, p.summary)));
  const styleNote = h("span.help");
  const syncStyle = () => {
    const p = data.presets.find((x) => x.name === style.value.trim());
    styleNote.textContent = p ? `Preset: ${p.summary}. Fields below override it.` : style.value ? "Custom style (no preset)." : "";
  };
  style.addEventListener("input", syncStyle);
  syncStyle();
  const description = h("textarea", { rows: 2, placeholder: "The overall look in a sentence or two", value: a.description || "" });
  const inputs = Object.fromEntries(TEXT_FIELDS.map(([k, , ph]) => [k, h("input", { type: "text", value: a[k] || "", placeholder: ph,
    list: k === "perspective" ? "af-persp" : undefined })]));
  const perspList = h("datalist", { id: "af-persp" }, PERSPECTIVES.map((p) => h("option", { value: p })));
  const detail = h("select", ["", "low", "medium", "high"].map((d) => h("option", { value: d, selected: (a.detail || "") === d },
    d ? d + (d === "high" ? " (larger, costlier images)" : "") : "not set")));
  const notes = h("textarea", { rows: 2, placeholder: "Anything else the image model should follow", value: a.notes || "" });
  const palette = paletteInput(a.palette || []);
  const avoid = chipsInput(a.avoid || [], { placeholder: "e.g. toy-like objects, then Enter" });
  const refs = chipsInput(profile?.reference_asset_ids || a.references || [], { placeholder: "asset ID, e.g. ast_00042" });
  const sampleType = h("select", ["character", "enemy", "object", "background", "icon", "ui", "tile", "spritesheet"].map((t) => h("option", { value: t }, t)));
  const sample = h("input", { type: "text", placeholder: "sample subject, e.g. scavenger drone", value: "" });
  const previewBox = h("div");
  const out = h("div");

  const gather = () => {
    const art = { style: style.value.trim(), description: description.value.trim(), detail: detail.value, notes: notes.value.trim(),
      palette: palette.values(), avoid: avoid.values(), references: refs.values() };
    for (const [k] of TEXT_FIELDS) art[k] = inputs[k].value.trim();
    return Object.fromEntries(Object.entries(art).filter(([, v]) => (Array.isArray(v) ? v.length : v)));
  };

  async function save() {
    const name = project.value.trim();
    if (!name) { toast("Name the game first"); project.focus(); return; }
    try {
      const { profile: saved } = await api(`/api/art/profiles/${encodeURIComponent(name)}`, { method: "POST", body: { art: gather(), replace: true } });
      toast(`Saved the art profile of ${saved.project}`);
      onSaved(saved);
    } catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
  }

  async function preview() {
    previewBox.innerHTML = "";
    previewBox.append(h("p", h("span.spinner"), " Building the prompt…"));
    const name = project.value.trim();
    try {
      // preview the form as it is now (saved or not): its style preset plus its fields
      const fields = gather();
      const { preview: p } = await api("/api/art/preview", { method: "POST", body: {
        prompt: sample.value.trim() || "the subject", type: sampleType.value, use_profile: false, style: fields.style || undefined, art: fields } });
      previewBox.innerHTML = "";
      previewBox.append(h("div.estimate",
        h("div.help", `Built from: ${p.layers.map(layerName).join(" + ") || "nothing"} · ${p.provider}/${p.model} · about ${fmt.money(p.estimated_cost_usd)} per image`),
        h("div.prompt", p.prompt)));
    } catch (err) { previewBox.innerHTML = ""; previewBox.append(errorBox(err)); }
  }

  const yamlText = h("textarea.mono", { rows: 12, value: profile?.yaml || "game: my-game\nstyle: sci-fi\nmood: dark\npalette: [\"#17212D\", \"#FF7A32\"]\navoid: [toy-like objects]\n" });
  async function importYaml() {
    try {
      const target = profile?.project || project.value.trim() || "_";
      const { profile: saved } = await api(`/api/art/profiles/${encodeURIComponent(target)}`, { method: "POST", body: { yaml: yamlText.value } });
      toast(`Imported the art profile of ${saved.project}`);
      onSaved(saved);
    } catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
  }
  async function remove() {
    if (!confirm(`Delete the art profile of ${profile.project}? Its assets are not touched.`)) return;
    try { await api(`/api/art/profiles/${encodeURIComponent(profile.project)}/delete`, { method: "POST" }); toast("Profile deleted"); onDeleted(profile.project); }
    catch (err) { out.innerHTML = ""; out.append(errorBox(err)); }
  }

  return h("div.card",
    h("div.section-head", h("h2", { style: "margin:0" }, profile ? profile.project : "New game profile"),
      profile ? h("span.help", `updated ${fmt.when(profile.updated_at)}`) : null),
    h("div.form",
      field("Game", project, profile ? null : "Assets generated with this project use the profile"), projectList,
      field("Style", style, styleNote), presetList,
      h("label.wide", h("span.lbl", "Description"), description),
      ...TEXT_FIELDS.map(([k, label]) => field(label, inputs[k])), perspList,
      field("Detail", detail),
      h("label.wide", h("span.lbl", "Palette"), palette, h("span.help", "Click a swatch to remove it. Keep it short: the model is told to stay within these hues.")),
      h("label.wide", h("span.lbl", "Avoid"), avoid),
      h("label.wide", h("span.lbl", "Style references"), refs, h("span.help", "Images whose look new assets should match (sent as style references).")),
      h("label.wide", h("span.lbl", "Notes"), notes)),
    h("div.actions", h("button.btn.primary", { onclick: save }, profile ? "Save changes" : "Create profile"),
      profile ? h("button.btn", { onclick: () => copy(profile.yaml, "YAML copied") }, "Copy YAML") : null,
      profile ? h("a.btn", { href: `#/generate?tab=image&project=${encodeURIComponent(profile.project)}` }, "Generate for this game") : null,
      profile ? h("button.btn.danger", { onclick: remove }, "Delete") : null),
    out,
    h("h3", "Try it"),
    h("div.toolbar", sampleType, sample, h("button.btn", { onclick: preview }, "Show the prompt")),
    h("div.help", "Shows exactly what the image model would be told. Nothing is generated or spent."),
    previewBox,
    h("details", { style: "margin-top:16px" }, h("summary.help", "Import / export YAML (the same file agents use with `art profile set --file`)"),
      yamlText, h("div.actions", h("button.btn", { onclick: importYaml }, profile ? "Replace with this YAML" : "Import YAML"))));
}
