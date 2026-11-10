// Creative direction form: presets, six groups (audience & language, genre & identity, world & culture, narrative
// experience, visual direction, custom instructions), a live summary and contradiction warnings. Used by the new-story
// wizard and the story Settings tab. The server resolves presets and computes warnings (/api/story/direction/check).
import { api, h, toast, section, field, chipsInput } from "/static/lib.js";

let cache = null;
export async function loadDirectionOptions(refresh = false) {
  if (!cache || refresh) cache = await api("/api/story/direction");
  return cache;
}

const LEVELS = ["none", "low", "medium", "high", "central"];

/** Build the form. `value` is a (partial) direction; returns an element with .value() → {preset, direction}. */
export function directionForm(info, value = {}, { onChange, compact = false } = {}) {
  const o = info.options;
  const st = { preset: value.preset || null, d: { ...o.defaults, ...value } };
  const root = h("div.dir");
  const summaryBox = h("div.dir-summary");
  const warnBox = h("div.dir-warnings");
  let timer = null;

  const chips = (key, options, { multi = false, labels = null, obj = null } = {}) => {
    const row = h("div.chip-row");
    const entries = Array.isArray(options) ? options.map((v) => [v, labels?.[v] || v]) : Object.entries(options);
    const get = () => (obj ? (st.d[obj] || {})[key] : st.d[key]);
    const set = (v) => { if (obj) st.d[obj] = { ...(st.d[obj] || {}), [key]: v }; else st.d[key] = v; };
    for (const [v, label] of entries) {
      const on = multi ? (get() || []).includes(v) : get() === v;
      row.append(h("button.chip-btn", { type: "button", class: on ? "on" : "", title: typeof label === "string" && label.length > 28 ? label : "",
        onclick: () => {
          if (multi) { const cur = new Set(get() || []); cur.has(v) ? cur.delete(v) : cur.add(v); set([...cur]); }
          else set(v);
          draw();
        } }, typeof label === "string" && label.length > 28 && !multi ? label.split(/[(:]/)[0].trim() : label));
    }
    return row;
  };
  const text = (key, placeholder, { rows = 0, obj = null } = {}) => {
    const get = () => (obj ? (st.d[obj] || {})[key] : st.d[key]) || "";
    const el = rows ? h("textarea", { rows, placeholder, value: get() }) : h("input", { type: "text", placeholder, value: get() });
    el.addEventListener("input", () => { if (obj) st.d[obj] = { ...(st.d[obj] || {}), [key]: el.value }; else st.d[key] = el.value; schedule(); });
    return el;
  };
  const select = (key, entries) => {
    const el = h("select", entries.map(([v, label]) => h("option", { value: v, selected: st.d[key] === v }, label)));
    el.addEventListener("change", () => { st.d[key] = el.value; if (key === "genre") { const g = o.genres.find((x) => x.id === el.value); if (g && !(st.d.subgenres || []).length) st.d.subgenres = [...g.default_subgenres]; } draw(); });
    return el;
  };

  function presetRow() {
    const row = h("div.chip-row");
    for (const p of info.presets) {
      row.append(h("button.chip-btn", { type: "button", class: st.preset === p.name ? "on" : "", title: p.summary,
        onclick: async () => {
          st.preset = p.name;
          st.d = { ...o.defaults, ...p.direction, preset: p.name };
          draw();
        } }, p.title + (p.source === "user" ? " ★" : "")));
    }
    row.append(h("button.chip-btn", { type: "button", class: st.preset ? "" : "on", title: "Start from the production defaults",
      onclick: () => { st.preset = null; st.d = { ...o.defaults }; draw(); } }, "No preset"));
    return row;
  }

  function groups() {
    const subs = o.subgenres.filter((s) => !st.d.genre || (s.primary || []).includes(st.d.genre) || (st.d.subgenres || []).includes(s.id));
    const subLabels = Object.fromEntries(o.subgenres.map((s) => [s.id, `${s.name}`]));
    const tones = chipsInput(st.d.tone || [], { placeholder: "add a tone", onchange: (v) => { st.d.tone = v; schedule(); } });
    const themes = chipsInput(st.d.themes || [], { placeholder: "e.g. betrayal, hidden power", onchange: (v) => { st.d.themes = v; schedule(); } });
    const reserved = chipsInput(st.d.reserved_names || [], { placeholder: "names that must be used as given", onchange: (v) => { st.d.reserved_names = v; schedule(); } });
    return [
      section("A · Audience and language", h("div.dir-grid",
        field("Target region", chips("region", o.regions), o.help.region),
        field("Readers", chips("readers", o.readers), o.help.readers),
        st.d.region === "custom" ? field("Region note", text("region_note", "Country or market, and what matters about it")) : null,
        field("English writing style", chips("writing_style", o.writing_styles), o.help.writing_style),
        field("Writing notes", text("writing_notes", "Optional: anything about vocabulary, rhythm or voice", { rows: 2 }))),
        { open: true }),
      section("B · Genre and story identity", h("div.dir-grid",
        field("Main genre", select("genre", o.genres.map((g) => [g.id, g.name])), (o.genres.find((g) => g.id === st.d.genre) || {}).promise || o.help.genre),
        field("Subgenres", chips("subgenres", subs.map((s) => s.id), { multi: true, labels: subLabels }), "Premise, mechanic, setting, protagonist or theme; pick what the story needs, not everything."),
        field("Narrative influence", select("tradition", o.traditions.map((t) => [t.id, t.name])), (o.traditions.find((t) => t.id === st.d.tradition) || {}).summary || o.help.tradition),
        field("Emotional themes", themes)),
        { open: true }),
      section("C · World and cultural setting", h("div.dir-grid",
        field("World type", chips("world_type", o.world_types), o.help.world_type),
        field("Cultural inspiration", chips("culture", o.cultures), o.help.culture),
        ["mixed", "custom"].includes(st.d.culture) ? field("Cultural influences", text("culture_note", "Name the influences and how they mix", { rows: 2 })) : null,
        field("Naming conventions", chips("naming", o.naming), o.help.naming),
        field("Naming guidance", text("naming_note", "Optional: flavour, examples, what to avoid")),
        field("Reserved names", reserved),
        section("World characteristics (optional)", h("div.dir-grid",
          field("Technology and civilisation", text("technology", "", { obj: "world" })),
          field("Politics", text("politics", "Kingdoms, empires, guilds, factions", { obj: "world" })),
          field("Magic / supernatural rules", text("magic", "", { obj: "world" })),
          field("Social hierarchy", text("hierarchy", "", { obj: "world" })),
          field("Religion / myth", text("religion", "", { obj: "world" })),
          field("Conflict structure", text("conflict", "", { obj: "world" })),
          field("Power progression rules", text("progression", "How power is gained, ranked and limited", { obj: "world" }))))),
        { open: !compact }),
      section("D · Narrative experience", h("div.dir-grid",
        field("Pacing", chips("pacing", o.pacing)),
        field("Tone", h("div", chips("tone", o.tones, { multi: true }), tones)),
        field("Drama intensity", chips("drama", LEVELS.slice(1, 4))),
        field("Romance emphasis", chips("romance", LEVELS)),
        field("Mystery emphasis", chips("mystery", LEVELS.slice(0, 4))),
        field("Power progression", chips("progression", o.progression)),
        field("Character development", chips("depth", o.depth)),
        field("Player choices", chips("choices", o.choices), o.help.choices),
        field("Cliffhangers", chips("cliffhangers", o.cliffhangers))),
        { open: !compact }),
      section("E · Visual direction", h("div.dir-grid",
        field("Art style", chips("art_style", o.art_styles), o.help.art_style),
        st.d.art_style === "custom" ? field("Custom art direction", text("art_note", "Medium, line work, palette, lighting", { rows: 2 })) : null,
        field("Character design guidance", text("character_design", "Optional")),
        field("Environment style", text("environment", "Optional")),
        field("Colour and mood", text("color_mood", "Optional"))),
        { open: !compact }),
      section("G · Audio direction", h("div.dir-grid",
        field("Audio profile", chips("profile", o.audio_profiles || {}, { obj: "audio" }), "Balanced is the default for a visual novel: the world feels alive without constant music or effects."),
        field("Music frequency", chips("music", o.audio_levels || {}, { obj: "audio" })),
        field("Sound-effect frequency", chips("sfx", o.audio_levels || {}, { obj: "audio" })),
        field("Ambience intensity", chips("ambience", o.audio_levels || {}, { obj: "audio" })),
        field("Dramatic accents", chips("accents", o.audio_levels || {}, { obj: "audio" })),
        field("Preference for silence", chips("silence", o.audio_levels || {}, { obj: "audio" })),
        field("Audio style notes", text("style", "e.g. restrained orchestral, no synths; period instruments only", { obj: "audio" }))),
        { open: false }),
      section("F · Advanced / custom instructions", h("div.dir-grid",
        field("Creative direction (highest priority)", text("instructions", "Free text the writer must follow above everything else", { rows: 3, obj: "custom" })),
        field("Special requirements", text("requirements", "", { rows: 2, obj: "custom" })),
        field("Explicit exclusions", text("exclusions", "Things that must never appear", { rows: 2, obj: "custom" })),
        field("Existing canon to preserve", text("canon", "", { rows: 2, obj: "custom" }))),
        { open: false }),
    ];
  }

  function schedule() { clearTimeout(timer); timer = setTimeout(check, 350); }
  async function check() {
    try {
      const r = await api("/api/story/direction/check", { method: "POST", body: { direction: st.d, preset: st.preset } });
      summaryBox.replaceChildren(h("b", "Summary · "), r.summary);
      warnBox.replaceChildren(...r.warnings.map((w) => h("div.dir-warn", "⚠ ", w.message)));
      onChange?.(root.value());
    } catch (err) { summaryBox.textContent = err.message; }
  }

  function draw() {
    // keep the open/closed state of the groups across redraws
    const open = new Set([...root.querySelectorAll("details.section")].map((d, i) => (d.open ? i : -1)).filter((i) => i >= 0));
    root.replaceChildren(
      h("div.lbl", "Start from a preset"), presetRow(),
      summaryBox, warnBox,
      ...groups().filter(Boolean));
    const details = root.querySelectorAll("details.section");
    if (open.size) details.forEach((d, i) => { d.open = open.has(i); });
    schedule();
  }

  root.value = () => ({ preset: st.preset, direction: st.d });
  root.saveAsPreset = async () => {
    const name = prompt("Preset name");
    if (!name) return;
    try { await api("/api/story/presets", { method: "POST", body: { name, title: name, direction: st.d } }); cache = null; toast(`Preset “${name}” saved`); info.presets = (await loadDirectionOptions(true)).presets; draw(); }
    catch (err) { toast(err.message); }
  };
  draw();
  return root;
}
