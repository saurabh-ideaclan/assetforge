import { api, h, errorBox, thumbUrl, fileUrl, badge, segmented, field } from "/static/lib.js";

// Generate → Sound: effects (recipes or layered designs), ambience loops, and music (compose or stingers).
export function soundTab(root, data, params) {
  const mode = ["effects", "ambience", "music"].includes(params.get("mode")) ? params.get("mode") : "effects";
  const body = h("div");
  root.append(h("div.toolbar", segmented([["effects", "Effects"], ["ambience", "Ambience"], ["music", "Music"]], mode,
    (v) => (location.hash = `#/generate?tab=sound&mode=${v}`))), body);
  const profiles = h("datalist", { id: "sound-projects" }, data.profiles.map((p) => h("option", { value: p.project }, describeProfile(p.sound))));
  body.append(profiles);
  ({ effects: effectsForm, ambience: ambienceForm, music: musicForm })[mode](body, data);
}

function describeProfile(s) {
  return [s.genre, s.mood, s.key, s.space].filter(Boolean).join(" · ") || "sound profile";
}

// ---------------------------------------------------------------------- shared

function common() {
  const els = {
    variants: h("input", { type: "number", min: 1, max: 16, value: 1 }),
    seed: h("input", { type: "number", min: 0, placeholder: "random" }),
    format: h("select", ["ogg", "wav", "mp3"].map((f) => h("option", { value: f }, f))),
    name: h("input", { type: "text", placeholder: "automatic" }),
    project: h("input", { type: "text", list: "sound-projects", placeholder: "optional: applies its sound profile" }),
  };
  els.fields = [field("Project", els.project), field("Name", els.name), field("Takes", els.variants),
    field("Seed", els.seed), field("Format", els.format)];
  els.values = () => ({
    variants: +els.variants.value || 1, seed: els.seed.value !== "" ? +els.seed.value : undefined, format: els.format.value,
    name: els.name.value || undefined, project: els.project.value || undefined,
  });
  return els;
}

function slider(min, max, step, value) {
  const input = h("input", { type: "range", min, max, step, value });
  const out = h("span.help", String(value));
  input.addEventListener("input", () => (out.textContent = input.value));
  return { input, el: h("span", { style: "display:flex;gap:8px;align-items:center" }, input, out) };
}

function loopPlayer(variantId, { loop = false, label } = {}) {
  const audio = h("audio", { controls: true, src: fileUrl(variantId), preload: "metadata", loop });
  const box = h("input", { type: "checkbox", checked: loop, onchange: (e) => (audio.loop = e.target.checked) });
  return h("div.player", label ? h("div.help", label) : null, audio, h("label.help", box, " loop"));
}

async function submit(button, out, path, body, render) {
  button.disabled = true;
  out.innerHTML = "";
  out.append(h("p", h("span.spinner"), " Rendering… (long pieces with stems take a few seconds)"));
  try {
    const { result } = await api(path, { method: "POST", body });
    out.innerHTML = "";
    out.append(render(result));
  } catch (err) {
    out.innerHTML = "";
    out.append(errorBox(err));
  } finally {
    button.disabled = false;
  }
}

function engineResult(result, { pianoRoll = false } = {}) {
  const audio = result.audio || {};
  const facts = [audio.bpm && `${audio.bpm} bpm`, audio.key, audio.meter, audio.bars && `${audio.bars} bars`,
    audio.length_s && `${(+audio.length_s).toFixed(1)}s`, audio.loop && "seamless loop", audio.scene && `scene ${audio.scene}`]
    .filter(Boolean).join(" · ");
  return h("div",
    h("h2", result.reused ? "Already in the library" : "Done"),
    h("p", h("a", { href: `#/assets/${result.asset_id}` }, result.asset.name), " ", badge(result.asset.status), " ",
      h("span.muted", facts)),
    h("div.results.wide", result.variants.map((v) => h("div.card",
      h("b", v.label.replace(/_(ogg|mp3|wav)$/, "")),
      loopPlayer(v.variant_id, { loop: !!audio.loop }),
      Object.entries(v.extras || {}).length ? h("details", h("summary.help", `${Object.keys(v.extras).length} stems / versions`),
        Object.entries(v.extras).map(([label, x]) => loopPlayer(x.variant_id, { loop: !!audio.loop, label }))) : null,
      v.files?.midi ? h("a.help", { href: fileUrl(v.files.midi.variant_id), download: `${result.asset.name}.mid` }, "MIDI score") : null,
      pianoRoll && v.info?.score ? roll(v.info.score) : null,
      h("div.help", `${((v.duration_ms || 0) / 1000).toFixed(2)}s · peak ${v.qc?.peak_dbfs ?? "-"} dBFS`)))));
}

// Read-only piano roll of a composed score: one colour per track.
function roll(score) {
  const canvas = h("canvas", { width: 520, height: 140, style: "width:100%;height:140px;background:var(--surface-2);border-radius:6px" });
  const notes = score.tracks.filter((t) => !t.instrument.startsWith("drums")).flatMap((t, i) => t.notes.map((n) => ({ ...n, i })));
  if (!notes.length) return null;
  const pitch = (p) => (typeof p === "number" ? p : 60);
  const lo = Math.min(...notes.map((n) => pitch(n.pitch))) - 1, hi = Math.max(...notes.map((n) => pitch(n.pitch))) + 1;
  const end = Math.max(...notes.map((n) => n.start + n.beats));
  const colors = ["#6aa6ff", "#ffb35c", "#7fd17f", "#d98cff", "#ff7f8a", "#5ce0d0"];
  requestAnimationFrame(() => {
    const ctx = canvas.getContext("2d");
    for (const n of notes) {
      ctx.fillStyle = colors[n.i % colors.length];
      const x = (n.start / end) * canvas.width, w = Math.max(2, (n.beats / end) * canvas.width - 1);
      const y = canvas.height - ((pitch(n.pitch) - lo) / (hi - lo)) * canvas.height;
      ctx.fillRect(x, y - 3, w, 3);
    }
  });
  return canvas;
}

// ---------------------------------------------------------------------- effects

function effectsForm(root, { recipes, presets }) {
  const byName = Object.fromEntries(recipes.map((r) => [r.name, r]));
  const groups = {};
  for (const r of recipes) (groups[r.category] ||= []).push(r);
  const recipe = h("select", Object.entries(groups).sort().map(([cat, list]) =>
    h("optgroup", { label: cat }, list.map((r) => h("option", { value: r.name }, `${r.name} — ${r.description}`)))));
  const preset = h("select", h("option", { value: "" }, "none"), presets.map((p) => h("option", { value: p.name }, `${p.name} — ${p.description}`)));
  const duration = h("input", { type: "number", step: "0.05", min: "0.01" });
  const variation = h("input", { type: "number", min: 0, max: 1, step: 0.05, value: 0.3 });
  const params = h("div.form");
  const help = h("div.help");
  const c = common();
  c.variants.value = 3;
  const inputs = {};
  const syncRecipe = () => {
    const r = byName[recipe.value];
    duration.value = r.default_duration;
    const range = r.params.find((p) => p.name === "duration");
    duration.min = range.min; duration.max = range.max;
    help.textContent = `${r.description}. Tags: ${r.tags.join(", ")}. Duration ${range.min}–${range.max}s.`;
    params.innerHTML = "";
    for (const k of Object.keys(inputs)) delete inputs[k];
    for (const p of r.params.filter((p) => !["duration"].includes(p.name))) {
      const input = p.kind === "choice"
        ? h("select", p.choices.map((ch) => h("option", { value: ch, selected: ch === p.default }, ch)))
        : h("input", { type: "number", min: p.min, max: p.max, step: p.kind === "int" ? 1 : (p.max - p.min) / 100, value: p.default });
      inputs[p.name] = { input, p };
      params.append(field(p.name, input, p.description));
    }
  };
  recipe.addEventListener("change", syncRecipe);
  syncRecipe();
  const designText = h("textarea.mono", { rows: 10, placeholder: '{"layers": [\n  {"recipe": "charge_up", "params": {"duration": 0.6}},\n  {"recipe": "explosion", "start": 0.55, "gain_db": -3},\n  {"patch": {"duration": 1, "oscillators": [{"wave": "noise_brown"}],\n             "filter": {"type": "lowpass", "cutoff": 900}}, "start": 0.6, "pan": 0.3}\n], "space": "cave"}' });
  const out = h("div");
  const button = h("button.btn.primary", { onclick: run }, "Generate sound");
  const designButton = h("button.btn", { onclick: runDesign }, "Render design");

  root.append(h("div.card",
    h("div.form",
      h("label.wide", "Recipe", recipe, help),
      field("Preset", preset), field("Duration (s)", duration), field("Variation (0–1)", variation), ...c.fields),
    h("details", { style: "margin-top:10px" }, h("summary.help", "Recipe parameters"), params),
    h("div.actions", button, h("span.help", "Runs locally in about a second. New sounds start as candidates."))),
  h("div.card.sub-card", h("h3", "Layered design"),
    h("p.help", "Stack recipes and synth patches in time, with gain, pan, pitch, repeats and effects. Same JSON as `assetforge audio design -f`."),
    designText, h("div.actions", designButton)), out);

  async function run() {
    const changed = {};
    for (const [k, { input, p }] of Object.entries(inputs)) {
      const v = p.kind === "choice" ? input.value : +input.value;
      if (v !== p.default) changed[k] = v;
    }
    const v = c.values();
    const req = { recipe: recipe.value, variants: v.variants, variation: +variation.value, format: v.format,
      duration: duration.value ? +duration.value : undefined, preset: preset.value || undefined, params: changed,
      seed: v.seed, name: v.name, project: v.project };
    button.disabled = true;
    out.innerHTML = "";
    out.append(h("p", h("span.spinner"), " Synthesizing…"));
    try {
      const { result } = await api("/api/generate/audio", { method: "POST", body: req });
      out.innerHTML = "";
      out.append(h("h2", result.reused ? "Already in the library" : "Generated"),
        h("p", h("a", { href: `#/assets/${result.asset_id}` }, result.asset.name), " ", badge(result.asset.status)),
        h("div.results", result.variants.map((x) => h("div.card",
          h("img", { src: thumbUrl(x.variant_id), style: "width:100%;border-radius:6px;background:#181c28", alt: "" }),
          h("audio", { controls: true, src: fileUrl(x.variant_id), preload: "metadata" }),
          h("div.help", `${x.label} · ${(x.duration_ms / 1000).toFixed(2)}s · peak ${x.qc.peak_dbfs} dBFS`)))));
    } catch (err) {
      out.innerHTML = "";
      out.append(errorBox(err));
    } finally {
      button.disabled = false;
    }
  }

  function runDesign() {
    let design;
    try { design = JSON.parse(designText.value || designText.placeholder); } catch (err) { out.innerHTML = ""; out.append(errorBox(new Error(`Design is not valid JSON: ${err.message}`))); return; }
    submit(designButton, out, "/api/generate/design", { ...(design.layers ? { design } : { patch: design }), ...c.values() }, (r) => engineResult(r));
  }
}

// ---------------------------------------------------------------------- ambience

function ambienceForm(root, { scenes, layers }) {
  const byName = Object.fromEntries(scenes.map((s) => [s.name, s]));
  const scene = h("select", scenes.map((s) => h("option", { value: s.name }, `${s.name} — ${s.description}`)));
  const length = h("input", { type: "number", min: 4, max: 600, value: 60 });
  const intensity = slider(0, 1, 0.05, 0.5);
  const time = h("select", [["", "as the scene"], ["day", "day"], ["night", "night"]].map(([v, l]) => h("option", { value: v }, l)));
  const distance = slider(0, 1, 0.05, 0);
  const space = h("select", [h("option", { value: "" }, "scene default"), ...["dry", "room", "hall", "cave", "outdoor", "underwater", "space"].map((s) => h("option", { value: s }, s))]);
  const stems = h("input", { type: "checkbox" });
  const versions = h("input", { type: "checkbox" });
  const mixer = h("div.form");
  const extraBed = h("select", [h("option", { value: "" }, "add a bed…"), ...layers.beds.map((b) => h("option", { value: b.name }, `${b.name} — ${b.description}`))]);
  const extraEvent = h("select", [h("option", { value: "" }, "add an event…"), ...layers.events.map((e) => h("option", { value: e.name }, `${e.name} — ${e.description}`))]);
  const c = common();
  const rows = {};
  const added = { beds: [], events: [] };
  const help = h("div.help");
  const addRow = (name, kind) => {
    const on = h("input", { type: "checkbox", checked: true });
    const level = h("input", { type: "number", min: -24, max: 12, step: 1, value: 0, style: "width:70px" });
    rows[name] = { on, level };
    mixer.append(h("label", h("span", on, ` ${name} `, h("span.muted", kind)), h("span.help", level, " dB")));
  };
  const syncScene = () => {
    const s = byName[scene.value];
    help.textContent = `${s.description}. Space: ${s.space}.`;
    mixer.innerHTML = "";
    for (const k of Object.keys(rows)) delete rows[k];
    added.beds = []; added.events = [];
    s.beds.forEach((b) => addRow(b.bed, "bed"));
    s.events.forEach((e) => addRow(e.event, "event"));
  };
  extraBed.addEventListener("change", () => { if (extraBed.value && !rows[extraBed.value]) { added.beds.push({ bed: extraBed.value }); addRow(extraBed.value, "bed (added)"); } extraBed.value = ""; });
  extraEvent.addEventListener("change", () => { if (extraEvent.value && !rows[extraEvent.value]) { added.events.push({ event: extraEvent.value, rate: 4 }); addRow(extraEvent.value, "event (added)"); } extraEvent.value = ""; });
  scene.addEventListener("change", syncScene);
  syncScene();
  const out = h("div");
  const button = h("button.btn.primary", { onclick: run }, "Render ambience");
  root.append(h("div.card",
    h("div.form",
      h("label.wide", "Scene", scene, help),
      field("Loop length (s)", length), field("Intensity", intensity.el, "calm … stormy"), field("Time of day", time),
      field("Distance", distance.el, "hear it from further away"), field("Space", space),
      field("Stems", h("span", stems, " each layer as its own loop")),
      field("Versions", h("span", versions, " also calm (0.2) and intense (0.9)")), ...c.fields),
    h("h3", "Layers"), mixer, h("div.toolbar", extraBed, extraEvent),
    h("div.actions", button, h("span.help", "Seamless stereo loop, rendered locally. A minute of audio takes a few seconds."))), out);

  function run() {
    const mute = Object.entries(rows).filter(([, r]) => !r.on.checked).map(([n]) => n);
    const levels = Object.fromEntries(Object.entries(rows).filter(([, r]) => +r.level.value !== 0).map(([n, r]) => [n, +r.level.value]));
    const req = { scene: scene.value, length: +length.value || 60, intensity: +intensity.input.value,
      time_of_day: time.value || undefined, distance: +distance.input.value, space: space.value || undefined,
      beds: added.beds, events: added.events, mute, levels, stems: stems.checked,
      intensities: versions.checked ? [0.2, 0.9] : [], ...c.values() };
    submit(button, out, "/api/generate/ambience", req, (r) => engineResult(r));
  }
}

// ---------------------------------------------------------------------- music

function musicForm(root, { genres, stingers, instruments, soundfont }) {
  let kind = "compose";
  const genre = h("select", genres.map((g) => h("option", { value: g.name }, `${g.name} — ${g.description}`)));
  const byGenre = Object.fromEntries(genres.map((g) => [g.name, g]));
  const mood = h("input", { type: "text", placeholder: "e.g. heroic, tense, calm, sad, playful" });
  const tempo = h("input", { type: "number", min: 40, max: 240, placeholder: "genre default" });
  const key = h("input", { type: "text", placeholder: "e.g. D minor (random if empty)" });
  const bars = h("input", { type: "number", min: 1, max: 256, placeholder: "or seconds →" });
  const seconds = h("input", { type: "number", min: 2, max: 600, placeholder: "e.g. 60" });
  const form = h("select", [["loop", "seamless loop"], ["linear", "linear cue (intro → outro)"]].map(([v, l]) => h("option", { value: v }, l)));
  const stinger = h("select", stingers.map((s) => h("option", { value: s }, s.replace("_", " "))));
  const energy = slider(0, 1, 0.05, 0.5);
  const stems = h("input", { type: "checkbox" });
  const layersBox = h("input", { type: "checkbox" });
  const backend = h("select", [["auto", soundfont.ready ? "auto (SoundFont)" : "auto (synth)"], ["synth", "synth"], ["soundfont", "SoundFont"]]
    .map(([v, l]) => h("option", { value: v, disabled: v === "soundfont" && !soundfont.ready }, l)));
  const melodic = instruments.filter((i) => i.family !== "drums");
  const kits = instruments.filter((i) => i.family === "drums");
  const roleSelect = (role, list) => h("select", { "data-role": role }, h("option", { value: "" }, "genre default"),
    list.map((i) => h("option", { value: i.name }, `${i.name} (${i.family})`)));
  const roles = { melody: roleSelect("melody", melodic), harmony: roleSelect("harmony", melodic), bass: roleSelect("bass", melodic), drums: roleSelect("drums", kits) };
  const help = h("div.help");
  const syncGenre = () => {
    const g = byGenre[genre.value];
    help.textContent = `${g.tempo[0]}–${g.tempo[1]} bpm · ${g.scales.join(", ")} · ${Object.entries(g.instruments).map(([r, i]) => `${r}: ${i}`).join(", ")}`;
  };
  genre.addEventListener("change", syncGenre);
  syncGenre();
  const c = common();
  const composeFields = h("div.form",
    h("label.wide", "Genre", genre, help), field("Mood", mood), field("Tempo (bpm)", tempo), field("Key", key),
    field("Bars", bars), field("Seconds", seconds), field("Form", form), field("Energy", energy.el, "sparse … full and busy"),
    field("Stems", h("span", stems, " drums / bass / harmony / melody")),
    field("Intensity layers", h("span", layersBox, " also calm (0.2) and intense (0.9) versions, same tune")));
  const stingerGenre = h("select", genres.map((g) => h("option", { value: g.name, selected: g.name === "orchestral_adventure" }, g.name)));
  const stingerFields = h("div.form", { style: "display:none" }, field("Stinger", stinger), field("Style", stingerGenre));
  const switcher = segmented([["compose", "Track"], ["stinger", "Stinger / jingle"]], kind, (v) => {
    kind = v;
    composeFields.style.display = v === "compose" ? "" : "none";
    stingerFields.style.display = v === "stinger" ? "" : "none";
  });
  const out = h("div");
  const button = h("button.btn.primary", { onclick: run }, "Compose");
  root.append(h("div.card",
    h("div.toolbar", switcher, h("span.help", soundfont.ready ? "SoundFont installed: realistic instruments." :
      "Synth instruments. For realistic piano/strings/brass: brew install fluid-synth, then `assetforge audio soundfont install --yes`.")),
    composeFields, stingerFields,
    h("details", { style: "margin-top:10px" }, h("summary.help", "Instruments per role"),
      h("div.form", ...Object.entries(roles).map(([r, el]) => field(r, el)))),
    h("div.form", { style: "margin-top:10px" }, field("Instruments", backend), ...c.fields),
    h("div.actions", button, h("span.help", "Original music from the seed; the score is saved (JSON + MIDI) for later edits."))), out);

  function run() {
    const instrumentsByRole = Object.fromEntries(Object.entries(roles).filter(([, el]) => el.value).map(([r, el]) => [r, el.value]));
    const base = { instruments: instrumentsByRole, backend: backend.value, ...c.values() };
    const req = kind === "stinger"
      ? { ...base, stinger: stinger.value, form: "stinger", genre: stingerGenre.value }
      : { ...base, genre: genre.value, mood: mood.value || undefined, tempo: tempo.value ? +tempo.value : undefined,
        key: key.value || undefined, bars: bars.value ? +bars.value : undefined,
        seconds: !bars.value && seconds.value ? +seconds.value : undefined, form: form.value, energy: +energy.input.value,
        stems: stems.checked, intensity_layers: layersBox.checked ? [0.2, 0.9] : [] };
    submit(button, out, "/api/generate/music", req, (r) => engineResult(r, { pianoRoll: true }));
  }
}
