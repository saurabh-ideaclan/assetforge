import { api, h, fmt, errorBox, thumbUrl, fileUrl, toast, badge, chipsInput, paletteInput, swatches, segmented, section, field, pollJob } from "/static/lib.js";
import { layerName } from "/static/views/art.js";
import { soundTab } from "/static/views/sound.js";

export async function generate(root, { params }) {
  const tab = params.get("tab") === "sound" ? "sound" : "image";
  const [audio, image, artData] = await Promise.all([tab === "sound" ? api("/api/sound/options") : null,
    tab === "sound" ? null : api("/api/image/options"), tab === "sound" ? null : api("/api/art")]);
  const body = h("div");
  const seg = h("div.seg",
    h("button", { class: tab === "image" ? "on" : "", onclick: () => (location.hash = "#/generate?tab=image") }, "Image"),
    h("button", { class: tab === "sound" ? "on" : "", onclick: () => (location.hash = "#/generate?tab=sound") }, "Sound"));
  root.append(h("div.page-head", h("div", h("h1", "Generate"),
    h("div.sub", tab === "sound" ? "Sound effects, ambience loops and music, synthesized locally: free, offline, reproducible with a seed." :
      "AI images with your art direction. See the exact prompt and the cost before anything is spent.")), seg), body);
  if (tab === "sound") soundTab(body, audio, params);
  else imageForm(body, image, artData, params);
  return {}; // no `update`: switching tabs changes the hash and remounts the view
}

// ---------------------------------------------------------------------- image

function imageForm(root, opts, artData, params) {
  const configured = opts.providers.filter((p) => p.configured && p.name !== "fake");
  const profiles = Object.fromEntries(artData.profiles.map((p) => [p.project, p]));
  const presets = Object.fromEntries(artData.presets.map((p) => [p.name, p]));

  // what
  const prompt = h("textarea", { rows: 3, placeholder: "What to draw: subject, pose, action, setting. e.g. Hunched graveyard ghoul with lantern eyes, side view" });
  const type = h("select", opts.types.map((t) => h("option", { value: t, selected: t === (params.get("type") || "character") }, t)));
  const project = h("input", { type: "text", list: "gen-projects", placeholder: "optional: applies its art profile", value: params.get("project") || "" });
  const projectList = h("datalist", { id: "gen-projects" }, [...new Set([...artData.projects, ...Object.keys(profiles)])].sort().map((p) => h("option", { value: p })));
  const purpose = h("input", { type: "text", placeholder: "optional, e.g. boss of level 3, shop button" });
  const name = h("input", { type: "text", placeholder: "default: from the prompt" });
  const profileNote = h("div.profile-note");

  // look
  const style = h("input", { type: "text", list: "gen-styles", placeholder: "preset or your own words", value: params.get("style") || "" });
  const styleList = h("datalist", { id: "gen-styles" }, opts.styles.map((s) => h("option", { value: s.name }, s.description)));
  const styleNote = h("span.help");
  const useProfile = h("input", { type: "checkbox", checked: true });
  const mood = h("input", { type: "text", placeholder: "e.g. tense, cosy" });
  const lighting = h("input", { type: "text", placeholder: "e.g. moonlight from the left" });
  const perspective = h("input", { type: "text", placeholder: "e.g. side view, top-down" });
  const composition = h("input", { type: "text", placeholder: "e.g. low camera angle" });
  const detail = h("select", ["", "low", "medium", "high"].map((d) => h("option", { value: d }, d || "from style/profile")));
  const palette = paletteInput([]);
  const avoid = chipsInput([], { placeholder: "e.g. cute faces, then Enter" });

  // sprite sheet
  const columns = h("input", { type: "number", min: 1, max: 12, value: 4 });
  const rows = h("input", { type: "number", min: 1, max: 12, value: 2 });
  let oneCharacter = true;
  const sheetKind = segmented([["one", "One character's poses"], ["items", "Different items"]], "one", (v) => (oneCharacter = v === "one"));
  const poses = h("textarea", { rows: 4, placeholder: "One line per cell, left to right, top to bottom:\nwalk contact\nwalk passing\n…" });
  const slice = h("input", { type: "checkbox", checked: true });
  const animations = h("input", { type: "text", placeholder: "optional, e.g. walk=0-3@8, idle=4-7@4" });
  const sheetBox = h("div.card.sub-card",
    h("h3", { style: "margin-top:0" }, "Sprite sheet"),
    h("p.help", "Draw every frame in one image so the design stays consistent, then cut it into aligned frames."),
    h("div.form", field("Columns", columns), field("Rows", rows), h("label.wide", h("span.lbl", "Contents"), sheetKind),
      h("label.wide", h("span.lbl", "Cells"), poses),
      h("label.check", slice, h("span", "Slice into aligned frames after generating")), field("Animations", animations, "cells are numbered from 0")));
  const syncType = () => (sheetBox.style.display = ["spritesheet", "animation"].includes(type.value) ? "" : "none");
  type.addEventListener("change", syncType);
  syncType();

  // output
  const provider = h("select", h("option", { value: "auto" }, "auto (best configured)"),
    opts.providers.map((p) => h("option", { value: p.name, disabled: !p.configured },
      `${p.name} — ${p.default_models.generate}${p.configured ? "" : ` (set ${p.key_env})`}`)));
  const quality = h("select", opts.qualities.map((q) => h("option", { value: q, selected: q === "auto" }, q)));
  const aspect = h("select", h("option", { value: "" }, "default for type"), opts.aspects.map((a) => h("option", { value: a }, a)));
  const n = h("input", { type: "number", min: 1, max: 4, value: 1 });
  const transparent = h("select", h("option", { value: "" }, "default for type"), h("option", { value: "true" }, "transparent"), h("option", { value: "false" }, "opaque"));
  const format = h("select", ["png", "webp"].map((f) => h("option", { value: f }, f)));
  const maxCost = h("input", { type: "number", min: 0, step: 0.01, placeholder: "no cap" });
  const category = h("input", { type: "text", placeholder: "e.g. creature, weapon" });

  const estimateBox = h("div");
  const out = h("div");
  const b = opts.budget;
  const budgetLine = `Spent today ${fmt.money(b.daily.spent_usd)}${b.daily.limit_usd ? ` of ${fmt.money(b.daily.limit_usd)}` : ""} · this month ${fmt.money(b.monthly.spent_usd)}${b.monthly.limit_usd ? ` of ${fmt.money(b.monthly.limit_usd)}` : ""}`;

  const syncNotes = () => {
    const p = profiles[project.value.trim()];
    profileNote.innerHTML = "";
    if (p) {
      profileNote.append(h("span", "Art profile: ", h("b", p.art.style || "custom"), [p.art.mood, p.art.lighting].filter(Boolean).length ? ` · ${[p.art.mood, p.art.lighting].filter(Boolean).join(" · ")}` : ""),
        p.art.palette?.length ? swatches(p.art.palette, 12) : null,
        h("a", { href: `#/art?tab=profiles&project=${encodeURIComponent(p.project)}` }, "edit"));
    } else if (project.value.trim()) {
      profileNote.append(h("span.muted", "No art profile for this game yet. "), h("a", { href: "#/art?tab=profiles&project=new" }, "Create one"));
    }
    const preset = presets[style.value.trim()];
    styleNote.textContent = preset ? preset.summary : style.value.trim() ? "Custom style" : (p ? "From the art profile" : "No style: the model picks one");
  };
  project.addEventListener("input", syncNotes);
  style.addEventListener("input", syncNotes);
  syncNotes();

  const generateBtn = h("button.btn.primary", { onclick: () => estimate(true) }, "Preview & generate…");
  root.append(
    configured.length ? "" : h("div.error-box", { style: "margin-bottom:12px" },
      "No image provider is configured. Add OPENAI_API_KEY or GEMINI_API_KEY to the environment or a .env file in the library folder, then restart AssetForge."),
    h("div.gen-layout",
      h("div",
        h("div.card",
          h("div.form",
            h("label.wide", h("span.lbl", "Prompt"), prompt),
            field("Type", type), field("Game / project", project), projectList, field("Purpose", purpose), field("Name", name),
            h("div.wide", profileNote))),
        h("div.card.sub-card",
          h("h3", { style: "margin-top:0" }, "Look"),
          h("div.form",
            field("Style", style, styleNote), styleList,
            h("label.check", useProfile, h("span", "Apply the game's art profile")),
            field("Mood", mood), field("Lighting", lighting), field("Perspective", perspective), field("Composition", composition),
            field("Detail", detail),
            h("label.wide", h("span.lbl", "Palette"), palette),
            h("label.wide", h("span.lbl", "Avoid"), avoid))),
        sheetBox,
        section("Output and catalog", h("div.form",
          field("Provider", provider), field("Quality", quality), field("Aspect", aspect), field("Candidates", n),
          field("Background", transparent), field("Format", format), field("Max cost per call ($)", maxCost), field("Category", category)),
          { note: "provider, size, background, cost cap" }),
        h("div.actions.sticky-actions", h("button.btn", { onclick: () => estimate(false) }, "Preview prompt & cost"), generateBtn,
          h("span.help", budgetLine))),
      h("div.gen-side", estimateBox, out, h("div.card.side-help",
        h("h3", { style: "margin-top:0" }, "Tips"),
        h("ul.tips",
          h("li", "Pick the game: its art profile sets the look. ", h("a", { href: "#/art" }, "Art direction")),
          h("li", "Describe the subject and pose in the prompt; describe the look under Look."),
          h("li", "For animation, choose type spritesheet: all frames in one image stay consistent."),
          h("li", "Preview first: you see the exact prompt and cost, nothing is spent."))))));

  function request() {
    const art = { mood: mood.value.trim(), lighting: lighting.value.trim(), perspective: perspective.value.trim(),
      composition: composition.value.trim(), detail: detail.value, palette: palette.values(), avoid: avoid.values() };
    const artClean = Object.fromEntries(Object.entries(art).filter(([, v]) => (Array.isArray(v) ? v.length : v)));
    const isSheet = ["spritesheet", "animation"].includes(type.value);
    const cells = poses.value.split("\n").map((l) => l.trim()).filter(Boolean);
    return {
      prompt: prompt.value.trim(), type: type.value, style: style.value.trim() || undefined, provider: provider.value,
      quality: quality.value, aspect: aspect.value || undefined, n: +n.value || 1, format: format.value,
      transparent: transparent.value === "" ? undefined : transparent.value === "true",
      project: project.value.trim() || undefined, max_cost: maxCost.value !== "" ? +maxCost.value : undefined,
      purpose: purpose.value.trim() || undefined, name: name.value.trim() || undefined, use_profile: useProfile.checked,
      category: category.value.trim() || undefined, art: Object.keys(artClean).length ? artClean : undefined,
      sheet: isSheet ? { columns: +columns.value || 1, rows: +rows.value || 1, poses: cells, one_character: oneCharacter } : undefined,
    };
  }

  async function estimate(thenConfirm = false) {
    if (prompt.value.trim().length < 3) { toast("Write a prompt first"); prompt.focus(); return; }
    estimateBox.innerHTML = "";
    estimateBox.append(h("div.card", h("span.spinner"), " Building the prompt…"));
    try {
      const { estimate: e } = await api("/api/generate/image/estimate", { method: "POST", body: request() });
      estimateBox.innerHTML = "";
      const confirmBtn = h("button.btn.primary", { onclick: () => start(confirmBtn) }, `Generate for about ${fmt.money(e.estimated_cost_usd)}`);
      estimateBox.append(h("div.card.estimate",
        h("div.cost", fmt.money(e.estimated_cost_usd), h("span.muted", { style: "font-size:13px;font-weight:400" }, " estimated per call")),
        h("div.help", `${e.provider} / ${e.model} · transparency: ${e.transparency}`),
        e.art_layers?.length ? h("div.layers", "Art direction: ", e.art_layers.map((l) => h("span.tag", layerName(l)))) : h("div.help", "No art direction: add a style or a game profile for a specific look."),
        h("h3", "Exact prompt"), h("div.prompt", e.prompt),
        h("div.actions", confirmBtn, h("button.btn", { onclick: () => (estimateBox.innerHTML = "") }, "Close"))));
      estimateBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
      if (thenConfirm) confirmBtn.focus();
    } catch (err) {
      estimateBox.innerHTML = "";
      estimateBox.append(errorBox(err));
    }
  }

  async function start(button) {
    button.disabled = true;
    const req = request();
    out.innerHTML = "";
    const status = h("div.card", h("span.spinner"), h("span", " Starting…"));
    out.append(status);
    try {
      const res = await api("/api/generate/image", { method: "POST", body: req });
      estimateBox.innerHTML = "";
      let result = res.result;
      if (res.state !== "done") {
        if (!res.job_id) throw new Error("Generation did not start");
        const done = await pollJob(res.job_id, (s) => (status.lastChild.textContent = ` Generating… ${s}s (${res.job_id}). Usually 10–60 s.`));
        result = done.result;
      }
      await showResult(result, req);
    } catch (err) {
      out.innerHTML = "";
      out.append(errorBox(err));
      button.disabled = false;
    }
  }

  async function showResult(r, req) {
    out.innerHTML = "";
    const cards = h("div.results");
    out.append(h("div.card",
      h("h3", { style: "margin-top:0" }, r.reused ? "This exact request ran before — reused" : `Generated with ${r.provider}`),
      h("p.help", `${r.model} · ${r.cost_usd !== null && r.cost_usd !== undefined ? fmt.money(r.cost_usd) : "cost unknown"} · ${r.job_id || ""}`),
      cards, h("p.help", "New images are candidates. Approve the keepers; reject or regenerate the rest from the asset page.")));
    for (const a of r.assets) cards.append(candidateCard(a));
    if (req.sheet && slice.checked) {
      for (const a of r.assets) {
        const note = h("div.help", h("span.spinner"), " Slicing into frames…");
        out.append(note);
        try {
          const { slice: s } = await api(`/api/assets/${a.asset_id}/slice`, { method: "POST", body: {
            columns: req.sheet.columns, rows: req.sheet.rows, names: oneCharacter ? [] : req.sheet.poses,
            animations: animations.value.split(",").map((x) => x.trim()).filter(Boolean) } });
          note.innerHTML = "";
          note.append(`${s.report.found}/${s.report.expected} frames → `, h("a", { href: `#/assets?collection=${encodeURIComponent(s.collection)}` }, s.collection),
            s.report.issues.length ? h("span.issue.warn", { style: "margin-left:6px" }, s.report.issues.join(", ")) : null);
        } catch (err) { note.innerHTML = ""; note.append(errorBox(err)); }
      }
    }
  }
}

function candidateCard(a) {
  const status = h("span", badge("candidate"));
  const act = async (action) => {
    try {
      const { review } = await api(`/api/assets/${a.asset_id}/review`, { method: "POST", body: { action } });
      status.innerHTML = "";
      status.append(badge(review.status));
      toast(`${a.asset_id} ${review.status}`);
    } catch (err) { toast(err.message); }
  };
  return h("div.asset.static",
    h("a.thumb", { href: `#/assets/${a.asset_id}` }, h("img", { src: thumbUrl(a.variant_id), alt: "" })),
    h("div.meta", h("div.name", a.asset_id), h("div.line", status, `${a.width}×${a.height}`, a.has_alpha ? "alpha" : ""),
      (a.qc.issues || []).length ? h("div", (a.qc.issues || []).map((i) => h("span.issue.warn", i))) : null,
      h("div.mini-actions", h("button.btn.small", { onclick: () => act("approve") }, "Approve"),
        h("button.btn.small", { onclick: () => act("reject") }, "Reject"),
        h("a.btn.small", { href: `#/assets/${a.asset_id}` }, "Open"))));
}
