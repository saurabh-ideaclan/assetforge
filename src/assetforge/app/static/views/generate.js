import { api, h, fmt, errorBox, thumbUrl, fileUrl, toast, badge } from "/static/lib.js";

export async function generate(root, { params }) {
  const tab = params.get("tab") === "image" ? "image" : "sound";
  const [audio, image] = await Promise.all([api("/api/audio/recipes"), api("/api/image/options")]);
  const body = h("div");
  const seg = h("div.seg",
    h("button", { class: tab === "sound" ? "on" : "", onclick: () => (location.hash = "#/generate?tab=sound") }, "Sound effect"),
    h("button", { class: tab === "image" ? "on" : "", onclick: () => (location.hash = "#/generate?tab=image") }, "Image"));
  root.append(h("div.page-head", h("div", h("h1", "Generate"),
    h("div.sub", tab === "sound" ? "Procedural sound effects: free, offline, reproducible with a seed." :
      "AI images from OpenAI or Gemini. You see the cost estimate before anything is spent.")), seg), body);
  if (tab === "sound") soundForm(body, audio);
  else imageForm(body, image);
  return {}; // no `update`: switching tabs changes the hash and remounts the view
}

// ---------------------------------------------------------------------- sound

function soundForm(root, { recipes, presets }) {
  const byName = Object.fromEntries(recipes.map((r) => [r.name, r]));
  const recipe = h("select", recipes.map((r) => h("option", { value: r.name }, `${r.name} — ${r.description}`)));
  const preset = h("select", h("option", { value: "" }, "none"), presets.map((p) => h("option", { value: p.name }, `${p.name} — ${p.description}`)));
  const duration = h("input", { type: "number", step: "0.05", min: "0.01" });
  const variants = h("input", { type: "number", min: 1, max: 16, value: 3 });
  const variation = h("input", { type: "number", min: 0, max: 1, step: 0.05, value: 0.3 });
  const seed = h("input", { type: "number", min: 0, placeholder: "random" });
  const format = h("select", ["ogg", "wav", "mp3"].map((f) => h("option", { value: f }, f)));
  const name = h("input", { type: "text", placeholder: "default: recipe name" });
  const project = h("input", { type: "text", placeholder: "optional" });
  const tags = h("input", { type: "text", placeholder: "comma separated" });
  const help = h("div.help");
  const syncRecipe = () => {
    const r = byName[recipe.value];
    duration.value = r.default_duration;
    const range = r.params.find((p) => p.name === "duration");
    duration.min = range.min; duration.max = range.max;
    help.textContent = `${r.description}. Tags: ${r.tags.join(", ")}. Duration ${range.min}–${range.max}s.`;
  };
  recipe.addEventListener("change", syncRecipe);
  syncRecipe();
  const out = h("div");
  const button = h("button.btn.primary", { onclick: run }, "Generate sound");

  root.append(h("div.card",
    h("div.form",
      h("label.wide", "Recipe", recipe, help),
      h("label", "Preset", preset), h("label", "Duration (s)", duration), h("label", "Variants", variants),
      h("label", "Variation (0–1)", variation), h("label", "Seed", seed), h("label", "Format", format),
      h("label", "Name", name), h("label", "Project", project), h("label", "Tags", tags)),
    h("div.actions", button, h("span.help", "Runs locally in about a second. Generated sounds start as candidates."))), out);

  async function run() {
    button.disabled = true;
    out.innerHTML = "";
    out.append(h("p", h("span.spinner"), " Synthesizing…"));
    const req = {
      recipe: recipe.value, variants: +variants.value || 1, variation: +variation.value, format: format.value,
      duration: duration.value ? +duration.value : undefined, preset: preset.value || undefined,
      seed: seed.value !== "" ? +seed.value : undefined, name: name.value || undefined,
      project: project.value || undefined, tags: tags.value ? tags.value.split(",").map((t) => t.trim()).filter(Boolean) : [],
    };
    try {
      const { result } = await api("/api/generate/audio", { method: "POST", body: req });
      out.innerHTML = "";
      out.append(h("h2", result.reused ? "Already in the library" : "Generated"),
        h("p", h("a", { href: `#/assets/${result.asset_id}` }, result.asset.name), " ", badge(result.asset.status),
          result.reused ? h("span.muted", " — this exact sound existed, so it was reused") : null),
        h("div.results", result.variants.map((v) => h("div.card",
          h("img", { src: thumbUrl(v.variant_id), style: "width:100%;border-radius:6px;background:#181c28", alt: "" }),
          h("audio", { controls: true, src: fileUrl(v.variant_id), preload: "metadata" }),
          h("div.help", `${v.label} · ${(v.duration_ms / 1000).toFixed(2)}s · peak ${v.qc.peak_dbfs} dBFS`)))));
    } catch (err) {
      out.innerHTML = "";
      out.append(errorBox(err));
    } finally {
      button.disabled = false;
    }
  }
}

// ---------------------------------------------------------------------- image

function imageForm(root, opts) {
  const configured = opts.providers.filter((p) => p.configured && p.name !== "fake");
  const prompt = h("textarea", { placeholder: "e.g. Cute cartoon beetle enemy, front view, big eyes" });
  const type = h("select", opts.types.map((t) => h("option", { value: t, selected: t === "character" }, t)));
  const style = h("input", { type: "text", list: "styles", placeholder: "e.g. cute-jungle" });
  const styleList = h("datalist", { id: "styles" }, opts.styles.map((s) => h("option", { value: s.name }, s.description)));
  const provider = h("select", h("option", { value: "auto" }, "auto (best configured)"),
    opts.providers.map((p) => h("option", { value: p.name, disabled: !p.configured },
      `${p.name} — ${p.default_models.generate}${p.configured ? "" : ` (set ${p.key_env})`}`)));
  const quality = h("select", opts.qualities.map((q) => h("option", { value: q, selected: q === "auto" }, q)));
  const aspect = h("select", h("option", { value: "" }, "default for type"), opts.aspects.map((a) => h("option", { value: a }, a)));
  const n = h("input", { type: "number", min: 1, max: 4, value: 1 });
  const transparent = h("select", h("option", { value: "" }, "default for type"), h("option", { value: "true" }, "transparent"), h("option", { value: "false" }, "opaque"));
  const format = h("select", ["png", "webp"].map((f) => h("option", { value: f }, f)));
  const project = h("input", { type: "text", placeholder: "optional" });
  const maxCost = h("input", { type: "number", min: 0, step: 0.01, placeholder: "no cap" });
  const estimateBox = h("div");
  const out = h("div");
  const estimateBtn = h("button.btn", { onclick: estimate }, "Estimate cost");
  const generateBtn = h("button.btn.primary", { onclick: () => estimate(true) }, "Generate…");
  const b = opts.budget;
  const budgetLine = `Spent today ${fmt.money(b.daily.spent_usd)}${b.daily.limit_usd ? ` of ${fmt.money(b.daily.limit_usd)}` : ""} · this month ${fmt.money(b.monthly.spent_usd)}${b.monthly.limit_usd ? ` of ${fmt.money(b.monthly.limit_usd)}` : ""}`;

  root.append(
    configured.length ? null : h("div.error-box", { style: "margin-bottom:12px" },
      "No image provider is configured. Add OPENAI_API_KEY or GEMINI_API_KEY to the environment or a .env file in the library folder, then restart AssetForge."),
    h("div.card",
      h("div.form",
        h("label.wide", "Prompt", prompt),
        h("label", "Type", type), h("label", "Style", style, styleList), h("label", "Provider", provider),
        h("label", "Quality", quality), h("label", "Aspect", aspect), h("label", "Candidates", n),
        h("label", "Background", transparent), h("label", "Format", format), h("label", "Project", project),
        h("label", "Max cost per call ($)", maxCost)),
      h("div.actions", estimateBtn, generateBtn, h("span.help", budgetLine)),
      estimateBox),
    out);

  function request() {
    return {
      prompt: prompt.value.trim(), type: type.value, style: style.value || undefined, provider: provider.value,
      quality: quality.value, aspect: aspect.value || undefined, n: +n.value || 1, format: format.value,
      transparent: transparent.value === "" ? undefined : transparent.value === "true",
      project: project.value || undefined, max_cost: maxCost.value !== "" ? +maxCost.value : undefined,
    };
  }

  async function estimate(thenConfirm = false) {
    if (prompt.value.trim().length < 3) { toast("Write a prompt first"); prompt.focus(); return; }
    estimateBox.innerHTML = "";
    estimateBox.append(h("p", h("span.spinner"), " Estimating…"));
    try {
      const { estimate: e } = await api("/api/generate/image/estimate", { method: "POST", body: request() });
      estimateBox.innerHTML = "";
      const confirm = h("button.btn.primary", { onclick: () => start(confirm) }, `Generate for about ${fmt.money(e.estimated_cost_usd)}`);
      estimateBox.append(h("div.estimate",
        h("div.cost", fmt.money(e.estimated_cost_usd), h("span.muted", { style: "font-size:13px;font-weight:400" }, " estimated")),
        h("div.help", `${e.provider} / ${e.model} · transparency: ${e.transparency}. Other providers may be tried if this one fails.`),
        h("details", h("summary.help", "Exact prompt that will be sent"), h("div.prompt", e.prompt)),
        h("div.actions", confirm, h("button.btn", { onclick: () => (estimateBox.innerHTML = "") }, "Cancel"))));
      if (thenConfirm) confirm.focus();
    } catch (err) {
      estimateBox.innerHTML = "";
      estimateBox.append(errorBox(err));
    }
  }

  async function start(button) {
    button.disabled = true;
    out.innerHTML = "";
    const status = h("p", h("span.spinner"), " Starting…");
    out.append(status);
    try {
      const res = await api("/api/generate/image", { method: "POST", body: request() });
      estimateBox.innerHTML = "";
      if (res.state === "done") return showResult(res.result);
      if (!res.job_id) throw new Error("Generation did not start");
      status.innerHTML = "";
      status.append(h("span.spinner"), ` Generating… (${res.job_id}) — this usually takes 10–60 seconds.`);
      const started = Date.now();
      const timer = setInterval(async () => {
        try {
          const j = await api(`/api/jobs/${res.job_id}`);
          if (j.job.status === "succeeded") { clearInterval(timer); showResult(j.result); }
          else if (j.job.status === "failed") { clearInterval(timer); out.innerHTML = ""; out.append(errorBox(j.job.error || {})); }
          else status.lastChild.textContent = ` Generating… (${res.job_id}, ${Math.round((Date.now() - started) / 1000)}s)`;
        } catch (err) { clearInterval(timer); out.innerHTML = ""; out.append(errorBox(err)); }
      }, 2000);
    } catch (err) {
      out.innerHTML = "";
      out.append(errorBox(err));
      button.disabled = false;
    }
  }

  function showResult(r) {
    out.innerHTML = "";
    out.append(h("h2", r.reused ? "Same request ran before — reused" : `Generated with ${r.provider}`),
      h("p.help", `${r.model} · ${r.cost_usd !== null && r.cost_usd !== undefined ? fmt.money(r.cost_usd) : "cost unknown"} · ${r.job_id || ""}`),
      h("div.results", r.assets.map((a) => h("a.asset", { href: `#/assets/${a.asset_id}` },
        h("div.thumb", h("img", { src: thumbUrl(a.variant_id), alt: "" })),
        h("div.meta", h("div.name", a.asset_id), h("div.line", `${a.width}×${a.height}`, a.has_alpha ? "alpha" : "",
          (a.qc.issues || []).length ? h("span.issue.warn", a.qc.issues.join(", ")) : null))))),
      h("p.help", "New images are candidates; review them with `assetforge review` (in-app review is coming next)."));
  }
}
