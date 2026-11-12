// Music & Ambience Studio: a brief (free text, or a story chapter's cue) → the proposed direction in plain words
// → editable creative settings → a small batch of candidates, reviewed by measurement → listen, compare, give
// feedback in words, approve, and save approved pieces as the project's sound identity.
import { api, h, toast, errorBox, section, field, segmented, playVariant, chipsInput } from "/static/lib.js";
import { loadAI, modelSelect, pickerValue } from "/static/views/ai.js";
import { watch } from "/static/store.js";

const FEEDBACK = ["Too dramatic", "Too repetitive", "Doesn't feel medieval", "Too much percussion", "Not atmospheric enough",
                  "Sounds artificial", "Too distracting during dialogue", "Too dark", "Too bright", "Closer to the reference"];
const PROBLEM = { too_busy: "too busy", too_static: "too static", in_dialogue_band: "fights the text", too_bright: "too bright", too_dull: "too dull",
                  sub_rumble: "rumbles", repetitive: "repetitive", mechanical_events: "events too regular", loop_seam: "loop seam audible", clicks: "clicks",
                  loudness_off: "loudness off target", flat_dynamics: "nothing changes" };

export async function audioStudio(root, { params }) {
  window.afCrumb?.("Music & Ambience");
  const ai = await loadAI().catch(() => ({ providers: [] }));
  const st = { kind: params.get("kind") || "music", brief: params.get("brief") || "", project: params.get("project") || "",
               key: params.get("key") || "", direction: null, expected: null, task: null, result: null,
               feedback: params.get("feedback") ? [params.get("feedback")] : [], story: params.get("story") || "" };
  root.append(h("div.page-head", h("div", h("h1", "Music & Ambience Studio"),
    h("div.sub", "Describe the scene; Fable writes the direction, renders a few candidates, measures them, and shows you the best. You listen and decide."))));
  const page = h("div.as-page");
  root.append(page);

  // step 1: brief
  const kindSeg = segmented([["music", "Music"], ["ambience", "Ambience"]], st.kind, (v) => { st.kind = v; st.direction = null; drawDirection(); });
  const brief = h("textarea", { rows: 3, placeholder: "e.g. A lonely medieval castle corridor at night. The protagonist has just discovered a betrayal. Cold, mysterious, emotionally restrained.", value: st.brief });
  brief.addEventListener("input", () => (st.brief = brief.value));
  const project = h("input", { type: "text", placeholder: "project (e.g. vn-the-unrung) for the sound identity", value: st.project });
  project.addEventListener("input", () => (st.project = project.value));
  const keyInput = h("input", { type: "text", placeholder: "optional asset key, e.g. vn/the-unrung/music/tense", value: st.key });
  keyInput.addEventListener("input", () => (st.key = keyInput.value));
  const picker = modelSelect(ai.providers || [], {});
  const directBtn = h("button.btn.primary", { type: "button", onclick: () => direct() }, "Propose a direction");
  const briefCard = h("div.card", h("h3", "1 · The scene"), field("Kind", kindSeg), field("Brief", brief),
    h("div.cols", field("Project", project, "Approved pieces become this project's sound identity."), field("Story key", keyInput, "Approving under a key wires it into the story.")),
    field("Writer for the direction", picker, "Leave on managed: Opus plans soundscapes when it is signed in."), h("div.actions", directBtn));

  // step 2: direction
  const dirCard = h("div.card");
  function drawDirection() {
    if (!st.direction) { dirCard.replaceChildren(h("h3", "2 · Proposed direction"), h("p.help", "Nothing yet. Propose a direction from the brief, then adjust it here.")); return; }
    const d = st.direction;
    const edit = (key, el) => { el.addEventListener("change", () => { d[key] = el.type === "number" || el.type === "range" ? +el.value : el.value; }); return el; };
    const num = (key, min, max, step = 0.05) => edit(key, h("input", { type: "range", min, max, step, value: d[key] ?? 0 }));
    const common = [
      st.kind === "music" ? field("Summary", h("p", `${d.genre} · ${d.mood} · ${d.instruments.map((p) => `${p.role}: ${p.instrument}`).join(", ") || "template instruments"} · ${d.percussion} percussion · arc ${d.arc}`))
                          : field("Summary", h("p", `${d.environment} · ${d.indoor ? "indoor" : "outdoor"} · ${d.scene ? "scene " + d.scene + " + " : ""}${d.beds.map((b) => b.bed).join(", ")} · events: ${d.events.map((e) => e.event).join(", ") || "none"} · space ${d.space}`)),
      field("Why", h("p.help", d.rationale || d.purpose || d.emotion || "")),
    ];
    const musicControls = st.kind === "music" ? [
      field("Emotional mood", edit("mood", h("input", { type: "text", value: d.mood }))),
      field("Genre template", edit("genre", h("input", { type: "text", value: d.genre }))),
      field(`Intensity (energy ${d.energy})`, num("energy", 0, 1)), field(`Melody prominence (${d.melody})`, num("melody", 0, 1)),
      field("Percussion", edit("percussion", h("select", ["none", "light", "full", "auto"].map((v) => h("option", { value: v, selected: d.percussion === v }, v))))),
      field("Dynamic arc", edit("arc", h("select", ["flat", "rise", "rise_fall", "fall"].map((v) => h("option", { value: v, selected: d.arc === v }, v))))),
      field(`Warmth (${d.warmth})`, num("warmth", 0, 1)), field(`Length (s)`, edit("seconds", h("input", { type: "number", min: 8, max: 240, value: d.seconds }))),
      field("Loop", edit("loop", h("select", [["true", "loop"], ["false", "one-off"]].map(([v, l]) => h("option", { value: v, selected: String(d.loop) === v }, l))))),
    ] : [
      field("Environment", edit("environment", h("input", { type: "text", value: d.environment }))),
      field(`Background activity (${d.activity})`, num("activity", 0, 1)), field(`Intensity (${d.intensity})`, num("intensity", 0, 1)),
      field("Space", edit("space", h("select", ["dry", "room", "hall", "cave", "outdoor"].map((v) => h("option", { value: v, selected: d.space === v }, v))))),
      field(`Low cut (${d.low_cut} Hz)`, edit("low_cut", h("input", { type: "range", min: 0, max: 300, step: 10, value: d.low_cut }))),
      field(`High cut (${d.high_cut || "off"})`, edit("high_cut", h("input", { type: "range", min: 0, max: 12000, step: 500, value: d.high_cut }))),
      field(`Evolution (${d.evolve})`, num("evolve", 0, 1)), field("Loop length (s)", edit("length", h("input", { type: "number", min: 10, max: 180, value: d.length }))),
    ];
    const avoid = chipsInput(d.avoid || [], { placeholder: "things to avoid", onchange: (v) => (d.avoid = v) });
    const n = h("select", [1, 2, 3, 4].map((v) => h("option", { value: v, selected: v === 3 }, `${v} candidate${v > 1 ? "s" : ""}`)));
    const produceBtn = h("button.btn.primary", { type: "button", onclick: () => produce(+n.value) }, "Generate candidates");
    dirCard.replaceChildren(h("h3", "2 · Proposed direction"), ...common,
      section("Adjust creative settings", h("div.dir-grid", ...musicControls, field("Avoid", avoid)), { open: false }),
      section("Advanced (raw direction)", h("pre.as-json", JSON.stringify(d, null, 1)), { open: false }),
      h("div.actions", n, produceBtn));
  }

  // step 3: production + candidates
  const runCard = h("div.card");
  function drawRun() {
    const t = st.task;
    if (!t) { runCard.replaceChildren(h("h3", "3 · Candidates"), h("p.help", "Generate candidates to listen and compare.")); return; }
    const steps = h("ul.wf-ul", t.steps.map((s, i) => h("li", { class: i < t.step ? "good" : i === t.step && t.state === "running" ? "" : "muted" }, (i < t.step ? "✓ " : i === t.step && t.state === "running" ? "⟳ " : "○ ") + s)));
    if (t.state === "running") { runCard.replaceChildren(h("h3", "3 · Candidates"), h("div.live", h("span.spinner"), ` ${t.label} · ${t.elapsed}s`), steps); return; }
    if (t.state === "failed") { runCard.replaceChildren(h("h3", "3 · Candidates"), errorBox(t.error || {})); return; }
    const r = t.result;
    st.result = r;
    const cands = r.candidates.map((c, i) => h("div.as-cand", { class: c.asset_id === r.chosen.asset_id ? "chosen" : "" },
      h("div.as-cand-head", h("b", i === 0 ? "Recommended" : "Alternative"), h("span.help", ` · ${c.name} · round ${c.round + 1} · score ${c.review.score}`)),
      h("div.row", h("button.sa-play", { type: "button", title: "Listen", onclick: (ev) => playVariant(c.variant_id, ev.currentTarget) }, "▶"),
        h("span.help", c.reason)),
      c.review.problems.length ? h("div.help", "Measured issues: " + c.review.problems.map((p) => PROBLEM[p.code] || p.code).join(", ")) : h("div.good.help", "No measured issues"),
      h("div.actions",
        h("button.btn.small.primary", { type: "button", onclick: () => approve(c, r.candidates.filter((x) => x !== c)) }, "Approve this one"),
        h("button.btn.small", { type: "button", onclick: () => approve(c, r.candidates.filter((x) => x !== c), true) }, "Approve + save as reference"),
        h("a.btn.small", { href: `#/lab/${c.variant_id}`, title: "Measure, diagnose and correct this candidate in the Workbench" }, "Inspect"))));
    const rounds = h("div.help", r.rounds.map((x) => `round ${x.round + 1}: best ${x.best_score}${x.refinements?.length ? " → " + x.refinements.join("; ") : ""}`).join(" · "));
    const chips = h("div.chip-row", FEEDBACK.map((f) => h("button.chip-btn", { type: "button", class: st.feedback.includes(f) ? "on" : "", onclick: () => { st.feedback.includes(f) ? st.feedback.splice(st.feedback.indexOf(f), 1) : st.feedback.push(f); drawRun(); } }, f)));
    const more = h("input", { type: "text", placeholder: "…or say it in your own words" });
    const refineBtn = h("button.btn", { type: "button", onclick: () => refine([...st.feedback, more.value].filter(Boolean).join("; ")) }, "Refine with this feedback");
    runCard.replaceChildren(h("h3", "3 · Candidates"), h("p.help", `${r.method}. ${r.rejected.length} rejected by review before you saw them.`), rounds,
      ...(r.limitation ? [h("div.dir-warn", "⚠ " + r.limitation)] : []), h("div.as-cands", cands),
      section("Feedback", h("div", chips, field("More", more), h("div.actions", refineBtn)), { open: true }));
  }

  async function direct(feedback) {
    if (!st.brief.trim()) return toast("Describe the scene first");
    directBtn.disabled = true;
    try {
      const r = await api("/api/audio/direct", { method: "POST", body: { brief: st.brief, kind: st.kind, project: st.project || null, feedback: feedback || null, fresh: !!feedback, ...pickerValue(picker) } });
      st.direction = r.direction; st.expected = r.expected; drawDirection();
      toast(`Direction by ${r.directed_by?.used?.provider || r.directed_by?.chosen_by || "fallback"}`);
    } catch (err) { toast(err.message); } finally { directBtn.disabled = false; }
  }
  async function produce(n) {
    try {
      const r = await api("/api/audio/produce", { method: "POST", body: { brief: st.brief, kind: st.kind, project: st.project || null, direction: st.direction, candidates: n, key: null, ...pickerValue(picker) } });
      st.task = r.task; drawRun(); poll(r.task_id);
    } catch (err) { toast(err.message); }
  }
  // the run's progress comes through the change feed (topic `tasks`): no polling here
  let stopWatching = null;
  function poll(id) {
    stopWatching?.();
    const tick = async () => {
      try {
        const r = await api(`/api/audio/tasks/${id}`);
        st.task = r.task; drawRun();
        if (r.task.state !== "running") { stopWatching?.(); stopWatching = null; }
      } catch (err) { toast(err.message); }
    };
    stopWatching = watch("tasks", tick, { debounce: 100 });
    tick();
  }
  async function refine(feedback) {
    if (!feedback) return toast("Pick or write some feedback");
    await direct(feedback);
    st.feedback = [];
    await produce(3);
  }
  async function approve(c, others, asReference = false) {
    try {
      await api("/api/audio/approve", { method: "POST", body: { asset_id: c.asset_id, key: st.key || null, project: st.project || null, reject: others.map((o) => o.asset_id), as_reference: asReference, note: st.brief.slice(0, 120) } });
      toast(asReference ? "Approved and saved as a reference" : "Approved");
      if (st.project) loadStyle();
    } catch (err) { toast(err.message); }
  }

  // sound identity
  const styleCard = h("div.card");
  async function loadStyle() {
    if (!st.project) { styleCard.replaceChildren(h("h3", "Sound identity"), h("p.help", "Set a project to see and build its approved references.")); return; }
    try {
      const g = await api(`/api/audio/style/${st.project}`);
      styleCard.replaceChildren(h("h3", `Sound identity · ${st.project}`),
        g.references.length ? h("div", g.references.map((r) => h("div.row", h("b", r.name), h("span.tag", r.kind), h("span.help", ` ${r.note || ""} · centre ${r.measured.centroid_hz} Hz, ${r.measured.onset_rate} events/s`),
          h("button.btn.small", { type: "button", onclick: async () => { await api(`/api/audio/style/${st.project}`, { method: "POST", body: { action: "remove_reference", asset_id: r.asset_id } }); loadStyle(); } }, "Remove")))) : h("p.help", "No approved references yet. Approve a candidate with “save as reference” and later directions follow it."));
    } catch (err) { styleCard.replaceChildren(h("h3", "Sound identity"), errorBox(err)); }
  }

  page.append(briefCard, dirCard, runCard, styleCard);
  drawDirection(); drawRun(); loadStyle();
  if (params.get("task")) poll(params.get("task"));  // opened from the Activity centre: show that run
  if (st.brief) direct();
  return {};
}
