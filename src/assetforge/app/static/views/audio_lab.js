// Audio Workbench (#/lab/<asset or variant>): listen, see what the engine measured, read the findings in
// plain words, apply a targeted correction (a new variant, re-measured), compare against the project's
// sound identity, and record approval or feedback. Every control maps to a real operation; the ones that
// need a recomposition say so and hand off to the Studio.
import { api, h, toast, errorBox, section, field, playVariant } from "/static/lib.js";

const SEVERITY = { hard: ["Unusable", "bad"], problem: ["Problem", "warn"], note: ["Note", "muted"] };
const CONFIDENCE = { measured: "measured", estimated: "estimated", listen: "needs a listener" };

// human-friendly refinement controls → real operations (or the honest hand-off)
const CONTROLS = [
  { label: "Reduce harshness", ops: [{ op: "peak_eq", hz: 3200, q: 1.2, gain_db: -3 }], why: "a 3 dB bell cut around 3.2 kHz" },
  { label: "Take the rumble out", ops: [{ op: "highpass", hz: 90 }], why: "high-pass at 90 Hz" },
  { label: "Clear the reading band", ops: [{ op: "dialogue_dip", db: -4 }], why: "a broad 4 dB dip across 1–4 kHz" },
  { label: "Warm it (less top)", ops: [{ op: "shelf", hz: 6000, gain_db: -3, high: true }], why: "a high shelf, -3 dB above 6 kHz" },
  { label: "Open it up (more air)", ops: [{ op: "shelf", hz: 6000, gain_db: 2, high: true }], why: "a high shelf, +2 dB above 6 kHz" },
  { label: "Let it breathe", ops: [{ op: "expand", ratio: 1.3 }], why: "gentle upward expansion" },
  { label: "Even the level", ops: [{ op: "compress", ratio: 2.0 }], why: "2:1 compression, makeup to the same loudness" },
  { label: "Improve the loop", ops: [{ op: "reloop", crossfade_ms: 120 }], why: "equal-power crossfade of the tail into the head" },
  { label: "Narrow the image", ops: [{ op: "narrow", width: 0.6 }], why: "sides at 60 %" },
];
const RECOMPOSE = [
  { label: "Less repetitive", feedback: "Too repetitive" }, { label: "Reduce musical density", feedback: "Too busy; thin the arrangement" },
  { label: "Less dramatic", feedback: "Too dramatic for the scene" }, { label: "More natural instrumentation", feedback: "Sounds artificial; more natural instruments" },
  { label: "More tension, same loudness", feedback: "More tension without more loudness" }, { label: "More environmental realism", feedback: "Not atmospheric enough; a plausible place" },
];

export async function audioLab(root, { rest }) {
  const ref = rest[0];
  window.afCrumb?.("Workbench");
  root.append(h("div.page-head", h("div", h("h1", "Audio Workbench"), h("div.sub", "What the engine measured, what it thinks is wrong, and the operations that address it. Nothing here has listened; you have."))));
  const page = h("div.as-page");
  root.append(page);
  if (!ref) { page.append(h("p.help", "Open an asset from the Studio or the assets grid: #/lab/<asset id>.")); return; }
  const status = h("div.live", h("span.spinner"), " Measuring…");
  page.append(status);
  let d;
  try { d = await api(`/api/audio/analysis/${encodeURIComponent(ref)}`); }
  catch (err) { page.replaceChildren(errorBox(err)); return; }
  status.remove();
  draw(d);

  function draw(d) {
    page.replaceChildren();
    const r = d.report, t = r.technical, s = d.sections;
    // ---- header: identity + listen
    page.append(h("div.card", h("div.row",
      h("button.sa-play", { type: "button", title: "Listen", onclick: (ev) => playVariant(d.variant_id, ev.currentTarget) }, "▶"),
      h("div", h("b", `${d.asset_id} · ${d.variant_id} · ${d.kind}${d.loop === true ? " · loop" : d.loop === false ? " · one-shot" : ""}`),
        h("div.help", d.brief ? `Brief: ${d.brief}` : "No brief on record: intent is unknown, so voices and energy are only noted."))),
      h("ul.lab-lines", (s.what_it_is || []).map((x) => h("li", x)))));

    // ---- findings
    const findings = d.findings.length ? d.findings.map((f) => {
      const [word, cls] = SEVERITY[f.severity];
      const fix = f.corrections[0];
      const where = f.where?.length ? h("span.help", ` at ${f.where.slice(0, 3).map((w) => `${w.start_s}–${w.end_s} s`).join(", ")}`) : null;
      const btn = fix && fix.kind === "dsp" ? h("button.btn.small", { type: "button", onclick: () => applyOps(fix.action === "replace_segment" ? planOpsFor(f) : [{ op: fix.action, ...fix.params }], `${f.code} → ${fix.action}`) }, `Apply: ${fix.action}`)
        : fix && (fix.kind === "composition" || fix.kind === "regenerate" || fix.kind === "layer") ? h("a.btn.small", { href: studioHref(d, fix) }, `In the Studio: ${fix.action}`)
        : fix && fix.kind === "method" ? h("span.help", `method change: ${fix.action}`) : null;
      return h("div.lab-finding", { class: cls },
        h("div", h("b", `${word} · ${f.code.replace(/_/g, " ")}`), h("span.help", ` · ${CONFIDENCE[f.confidence]} · cause: ${f.cause.replace(/_/g, " ")}`), where),
        h("div", f.message), fix ? h("div.help", `Why this fix: ${fix.why}`) : null, btn ? h("div.actions", btn) : null,
        f.corrections.length > 1 ? h("div.help", "Other options: " + f.corrections.slice(1).map((c) => `${c.action} (${c.kind})`).join(", ")) : null);
    }) : [h("p.good", "No problems found by measurement. Whether it is right for the scene is yours to judge.")];
    const sm = d.summary;
    page.append(h("div.card", h("h3", "Findings"), h("div.help", `${sm.usable ? "Usable" : "Unusable"} · ${sm.hard.length} hard, ${sm.problems.length} problems, ${sm.notes.length} notes` +
      (Object.keys(sm.causes).length ? ` · causes: ${Object.entries(sm.causes).map(([k, v]) => `${k} ×${v}`).join(", ")}` : "") +
      (sm.needs_listening.length ? ` · needs a listener: ${sm.needs_listening.join(", ")}` : "")), ...findings));

    // ---- refinement controls
    const ctrls = h("div.chip-row", CONTROLS.map((c) => h("button.chip-btn", { type: "button", title: c.why, onclick: () => applyOps(c.ops, c.label) }, c.label)));
    const recomp = h("div.chip-row", RECOMPOSE.map((c) => h("a.chip-btn", { href: studioHref(d, null, c.feedback), title: "This needs a recomposition: it opens the Studio with the feedback filled in" }, c.label + " ↗")));
    page.append(h("div.card", h("h3", "Refine"), h("p.help", "Left: real DSP operations, applied as a new variant and re-measured. Right: changes to the composition or the layers, which the Studio renders again."),
      h("div.cols", field("Signal", ctrls), field("Composition / layers", recomp))));

    // ---- analysis panels
    const tl = d.timelines;
    const vis = d.visuals;
    const img = (name, title) => vis[name] ? h("figure.lab-fig", h("img", { src: vis[name], alt: title }), h("figcaption", title)) : null;
    page.append(h("div.card", h("h3", "Signal"),
      img("waveform", "Waveform (red = full scale, yellow lines = clicks)"),
      canvasLine("Loudness: momentary (thin) and short-term (bold), LUFS", tl.loudness.momentary, tl.loudness.short_term, { min: -60, max: 0, step: tl.loudness.step_s }),
      h("ul.lab-lines", [
        h("li", `Integrated ${tl.loudness.integrated} LUFS · range ${tl.loudness.lra} LU · true peak ${t.true_peak_dbtp} dBTP · crest ${t.crest_db} dB`),
        h("li", `Stereo: correlation ${t.stereo.correlation}, width ${t.stereo.width}, balance ${t.stereo.balance_db} dB` + (t.stereo.low_correlation_share ? ` (${Math.round(t.stereo.low_correlation_share * 100)} % of seconds under 0.2)` : "")),
        t.loop ? h("li", `Loop seam: ${t.loop.seam_ok ? "clean" : "audible"} (click ${t.loop.loop_click}, level jump ${t.loop.loop_level_jump_db} dB vs ${t.loop.level_change_p95_db} dB natural movement)`) : null,
        h("li", `Silence: leading ${t.silence.leading_ms} ms, trailing ${t.silence.trailing_ms} ms, gaps ${t.silence.gaps.length}; clicks ${t.discontinuities.count}`)])));
    if (tl.balance) {
      page.append(h("div.card", h("h3", "Spectrum"), img("spectrogram", "Spectrogram (log frequency, 30 Hz–16 kHz)"), img("mel", "Mel spectrogram"),
        canvasBars("Octave balance (dB re loudest octave)", tl.balance.octave_centres_hz.map((c) => c >= 1000 ? `${c / 1000}k` : String(c)), tl.balance.octaves_db, { min: -60, max: 0 }),
        h("ul.lab-lines", [h("li", `Tilt ${tl.balance.tilt_db_per_octave} dB/octave · rumble ${pct(tl.balance.shares.rumble)} · mud ${pct(tl.balance.shares.mud)} · reading band (1–4 kHz) ${pct(tl.balance.shares.dialogue)} · harsh (2–5 kHz) ${pct(tl.balance.shares.harsh)} · hiss (>8 kHz) ${pct(tl.balance.shares.hiss)}`),
          h("li", `${r.spectral.texture}; percussive share ${pct(r.spectral.transients.percussive_share)}, attacks ${r.spectral.transients.attack_db} dB`)]),
        canvasLine("Spectral centre over time (Hz)", tl.spectral.centroid_hz, null, { min: 0, max: Math.max(4000, ...tl.spectral.centroid_hz), step: 1 / tl.spectral.frame_rate * (tl.spectral.centroid_hz.length ? Math.max(1, Math.round(r.spectral.frame_rate * tl.duration_s / tl.spectral.centroid_hz.length)) : 1) })));
    }
    if (tl.music) {
      const m = tl.music;
      page.append(h("div.card", h("h3", "Music"), img("chroma", "Chromagram (pitch classes over time)"), img("similarity", "Self-similarity (bright diagonals = repeated material)"),
        h("ul.lab-lines", (s.structure || []).map((x) => h("li", x))),
        canvasBars("Energy arc by quarter (LU vs mean)", ["Q1", "Q2", "Q3", "Q4"], m.energy_arc.quarters_lu || [], { min: -6, max: 6 })));
    }
    if (tl.speech || tl.events) {
      page.append(h("div.card", h("h3", "Voices and sound events"), h("ul.lab-lines", [...(s.voices || []), ...(s.signatures || [])].map((x) => h("li", x))),
        tl.speech ? strip("Voice-likeness per second (brighter = more voice-like)", tl.speech.map((x) => x.score), tl.speech.map((x) => x.voice_like)) : null,
        tl.events ? eventStrip(tl.events) : null));
    }
    // ---- identity + preference
    const pl = d.placement;
    const fb = h("input", { type: "text", placeholder: "your words (optional)" });
    page.append(h("div.card", h("h3", "Sound identity and your verdict"),
      pl ? h("div", h("div.help", `Distance from the project's approved ${d.kind} profile: ${pl.distance}`), pl.notable.length ? h("ul.lab-lines", pl.notable.map((x) => h("li", x))) : h("p.good", "Within the identity on every measure."))
        : h("p.help", "No measured profile for this project yet (assetforge audio profile PROJECT --kind " + d.kind + ")."),
      field("Feedback", fb), h("div.actions",
        h("button.btn.small.primary", { type: "button", onclick: () => prefer("approved", fb.value) }, "Approve"),
        h("button.btn.small", { type: "button", onclick: () => prefer("rejected", fb.value) }, "Reject"))));
  }

  function planOpsFor(f) { return (d.plan.dsp || []).filter((o) => o.op === "replace_segment"); }
  function studioHref(d, fix, feedback) {
    const p = new URLSearchParams({ kind: d.kind, brief: d.brief || "", project: d.report?.project || "" });
    if (feedback) p.set("feedback", feedback);
    if (fix) p.set("feedback", `${fix.action}: ${JSON.stringify(fix.params)}`);
    return `#/audio?${p}`;
  }
  async function applyOps(ops, reason) {
    const live = h("div.live", h("span.spinner"), ` Applying ${reason} and re-measuring…`);
    page.prepend(live);
    try {
      const r = await api("/api/audio/correct", { method: "POST", body: { asset_id: d.asset_id, variant_id: d.variant_id, ops, reason, brief: d.brief, loop: d.loop, label: "corrected" } });
      live.remove();
      if (!r.applied) return toast("Nothing to apply");
      const c = r.comparison || {};
      toast(`${c.verdict || "applied"}: fixed ${(c.fixed || []).join(", ") || "–"}; regressions ${(c.regressions || []).join(", ") || "none"}`);
      location.hash = `#/lab/${r.applied.variant_id}`;
    } catch (err) { live.remove(); toast(err.message); }
  }
  async function prefer(decision, feedback) {
    try {
      await api("/api/audio/preference", { method: "POST", body: { asset_id: d.asset_id, decision, feedback, brief: d.brief, project: d.report?.project } });
      await api(`/api/assets/${d.asset_id}/review`, { method: "POST", body: { action: decision === "approved" ? "approve" : "reject", force: true } }).catch(() => {});
      toast(decision === "approved" ? "Approved and recorded" : "Rejected and recorded");
    } catch (err) { toast(err.message); }
  }
}

function pct(v) { return `${Math.round((v || 0) * 100)} %`; }

function canvasLine(title, a, b, { min, max, step }) {
  const w = 900, hgt = 120;
  const c = h("canvas", { width: w, height: hgt });
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#101018"; ctx.fillRect(0, 0, w, hgt);
  const y = (v) => hgt - ((Math.max(min, Math.min(max, v)) - min) / (max - min)) * (hgt - 8) - 4;
  const line = (vals, colour, width) => {
    if (!vals || !vals.length) return;
    ctx.strokeStyle = colour; ctx.lineWidth = width; ctx.beginPath();
    vals.forEach((v, i) => { const x = (i / Math.max(vals.length - 1, 1)) * w; i ? ctx.lineTo(x, y(v)) : ctx.moveTo(x, y(v)); });
    ctx.stroke();
  };
  ctx.strokeStyle = "#333"; [0.25, 0.5, 0.75].forEach((f) => { ctx.beginPath(); ctx.moveTo(0, hgt * f); ctx.lineTo(w, hgt * f); ctx.stroke(); });
  line(a, "#6a8cff", 1); line(b, "#ffd866", 2.5);
  return h("figure.lab-fig", c, h("figcaption", `${title} (${min}…${max})`));
}

function canvasBars(title, labels, vals, { min, max }) {
  const w = 900, hgt = 130;
  const c = h("canvas", { width: w, height: hgt });
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#101018"; ctx.fillRect(0, 0, w, hgt);
  const n = Math.max(vals.length, 1), bw = w / n;
  vals.forEach((v, i) => {
    const hh = ((Math.max(min, Math.min(max, v)) - min) / (max - min)) * (hgt - 22);
    ctx.fillStyle = v > -6 ? "#ffd866" : "#6a8cff"; ctx.fillRect(i * bw + 6, hgt - 18 - hh, bw - 12, hh);
    ctx.fillStyle = "#aaa"; ctx.font = "11px system-ui"; ctx.textAlign = "center"; ctx.fillText(labels[i] ?? "", i * bw + bw / 2, hgt - 5);
    ctx.fillText(String(Math.round(v * 10) / 10), i * bw + bw / 2, Math.max(10, hgt - 22 - hh));
  });
  return h("figure.lab-fig", c, h("figcaption", title));
}

function strip(title, scores, flags) {
  const w = 900, hgt = 28;
  const c = h("canvas", { width: w, height: hgt });
  const ctx = c.getContext("2d");
  const n = Math.max(scores.length, 1), bw = w / n;
  scores.forEach((v, i) => { const g = Math.round(40 + 200 * Math.min(1, v)); ctx.fillStyle = flags[i] ? `rgb(${g},60,60)` : `rgb(${g / 3},${g / 3},${g / 2})`; ctx.fillRect(i * bw, 0, Math.ceil(bw), hgt); });
  return h("figure.lab-fig", c, h("figcaption", title));
}

const EVENT_COLOURS = { speech_like: "#ff5c5c", rain_like: "#58a6ff", water_drips: "#4fd1c5", wind: "#9aa", footsteps: "#d9a74a", metal: "#c3c3ff", hum: "#b48", music: "#7bd88f", silence: "#222", texture: "#333" };
function eventStrip(events) {
  const w = 900, hgt = 22;
  const c = h("canvas", { width: w, height: hgt });
  const ctx = c.getContext("2d");
  const n = Math.max(events.length, 1), bw = w / n;
  events.forEach((e, i) => { const top = e.labels[0]; ctx.fillStyle = EVENT_COLOURS[top?.[0]] || "#444"; ctx.globalAlpha = 0.35 + 0.65 * (top?.[1] ?? 0.3); ctx.fillRect(i * bw, 0, Math.ceil(bw), hgt); });
  ctx.globalAlpha = 1;
  const legend = Object.entries(EVENT_COLOURS).filter(([k]) => events.some((e) => e.labels.some((l) => l[0] === k))).map(([k, col]) => h("span.lab-key", h("i", { style: `background:${col}` }), k.replace(/_/g, " ")));
  return h("figure.lab-fig", c, h("figcaption", "Acoustic signatures per second (heuristic; brighter = more confident)"), h("div.lab-legend", ...legend));
}
