// Character Studio: the cast dashboard, one structured profile per character (identity, personality, narrative,
// visual), approval and locks per section, AI revision proposals you compare and apply, concept candidates, the
// master visual reference and the consistency review of generated expressions.
import { api, h, toast, errorBox, section, field, chipsInput, thumbUrl, segmented } from "/static/lib.js";
import { loadAI, modelSelect, pickerValue } from "/static/views/ai.js";

const FIELDS = {
  identity: [["name", "Name"], ["aliases", "Aliases", "chips"], ["age", "Age"], ["role", "Narrative role"], ["origin", "Origin"], ["social_position", "Social position"], ["occupation", "Occupation or faction"]],
  personality: [["traits", "Core traits", "chips"], ["desires", "Desires and motivations", "area"], ["fears", "Fears", "area"], ["flaws", "Flaws", "area"], ["contradictions", "Internal contradictions", "area"], ["moral_boundaries", "Moral boundaries", "area"], ["under_pressure", "Under pressure", "area"], ["dialogue_style", "Dialogue style", "area"]],
  narrative: [["arc", "Character arc", "area"], ["secrets", "Secrets", "area"], ["conflicts", "Conflicts", "area"], ["milestones", "Major milestones", "chips"], ["route_variations", "Route-specific variations", "area"]],
  visual: [["appearance", "Physical appearance", "area"], ["face", "Face shape"], ["eyes", "Eyes"], ["hair", "Hair"], ["build", "Body proportions"], ["features", "Distinguishing features"], ["expressions", "Typical expressions", "chips"], ["costume", "Costume design", "area"], ["accessories", "Accessories"], ["palette", "Colour palette", "chips"], ["silhouette", "Silhouette"], ["art_notes", "Art direction notes", "area"]],
};
const SECTION_TITLE = { identity: "Identity", personality: "Personality", narrative: "Narrative", visual: "Visual identity" };
const STATE = { proposed: "Draft", approved: "Approved", changes_requested: "Changes requested", superseded: "Superseded" };

export function charactersTab(root, data, sid, run, refresh) {
  const box = h("div", h("div.empty.small", h("span.spinner"), " Loading the cast…"));
  root.append(box);
  let selected = null;
  let ai = null;
  async function load() {
    const [c, info] = await Promise.all([api(`/api/stories/${sid}/characters`), ai || loadAI()]);
    ai = info;
    return c;
  }
  load().then((c) => draw(c)).catch((err) => box.replaceChildren(errorBox(err)));

  function draw(c) {
    if (!c.cast.length) { box.replaceChildren(h("div.empty", h("p", "No characters yet."), h("p.help", "They appear here as soon as the cast is created; approve them before the story is planned."))); return; }
    const rows = c.cast.map((r) => h("button.cs-row", { type: "button", class: selected === r.id ? "on" : "", onclick: () => { selected = r.id; draw(c); } },
      h("div.cs-cell.name", h("b", r.name), h("div.help", r.role)),
      h("div.cs-cell", r.summary || h("span.muted", "—"), r.motivation ? h("div.help.clamp2", r.motivation) : null),
      h("div.cs-cell", r.relationship || h("span.muted", "—")),
      h("div.cs-cell", h("span.cs-dev", { class: r.development === "approved" ? "good" : "" }, r.development)),
      h("div.cs-cell", h("span.tag", { class: r.visual_identity === "approved" ? "good" : "" }, `visual: ${r.visual_identity}`), r.reference ? h("div.help", r.reference_stale ? "reference out of date" : "master reference set") : h("div.help", "no reference yet")),
      h("div.cs-cell", Object.entries(r.sections).map(([s, st]) => h("span.cs-dot", { class: st, title: `${SECTION_TITLE[s]}: ${STATE[st]}` })), r.proposals ? h("span.tag.warn", `${r.proposals} proposal${r.proposals > 1 ? "s" : ""}`) : null)));
    const head = h("div.cs-head", h("span", "Character"), h("span", "Personality · motivation"), h("span", "To the protagonist"), h("span", "Development"), h("span", "Visual identity"), h("span", "Approvals"));
    const dash = h("div.card.cs-dash", h("h3", "Cast"), head, ...rows);
    const detail = selected ? profileEditor(c, c.profiles[selected], c.cast.find((r) => r.id === selected)) : h("div.help", "Select a character to review and finalise it.");
    box.replaceChildren(dash, detail);
  }

  function profileEditor(c, p, row) {
    const card = h("div.card.cs-profile");
    const sectionsEl = h("div");
    async function post(path, body) {
      try { const r = await api(`/api/stories/${sid}/characters/${p.id}${path}`, { method: "POST", body }); toast("Saved"); const c2 = await load(); draw(c2); return r; }
      catch (err) { toast(err.message); }
    }
    const sections = Object.keys(FIELDS).map((s) => {
      const st = (p.approvals[s] || {}).state || "proposed";
      const locked = p.locked.includes(s);
      const values = { ...p[s] };
      const inputs = FIELDS[s].map(([key, label, kind]) => {
        let el;
        if (kind === "chips") el = chipsInput(values[key] || [], { onchange: (v) => (values[key] = v) });
        else { el = kind === "area" ? h("textarea", { rows: 2, value: values[key] || "", disabled: locked }) : h("input", { type: "text", value: values[key] || "", disabled: locked }); el.addEventListener("input", () => (values[key] = el.value)); }
        return field(label, el);
      });
      const save = h("button.btn.small.primary", { type: "button", disabled: locked, onclick: () => post("", { patch: { [s]: values }, note: `edited ${s}` }) }, "Save changes");
      const approve = h("button.btn.small", { type: "button", onclick: () => post("/approval", { section: s, state: st === "approved" ? "proposed" : "approved" }) }, st === "approved" ? "Reopen" : "Approve");
      const changes = h("button.btn.small", { type: "button", onclick: () => { const t = window.prompt("What should change?"); if (t) post("/approval", { section: s, state: "changes_requested", note: t }); } }, "Request changes");
      const lock = h("button.btn.small", { type: "button", onclick: () => post("/lock", { section: s, locked: !locked }) }, locked ? "Unlock" : "Lock");
      return section(SECTION_TITLE[s], h("div", h("div.cs-sec-head", h("span.cs-state", { class: st }, STATE[st]), locked ? h("span.tag", "locked") : null, (p.approvals[s] || {}).note ? h("span.help", ` · ${(p.approvals[s] || {}).note}`) : null),
        h("div.dir-grid", ...inputs), h("div.actions", save, approve, changes, lock)), { open: s === "identity" || st !== "approved", note: STATE[st] });
    });

    // AI revision with a model override; proposals compare + apply
    const req = h("textarea", { rows: 2, placeholder: "e.g. Make her more morally ambiguous; keep everything else unchanged." });
    const picker = modelSelect(ai.providers, {});
    const ask = h("button.btn.primary", { type: "button", onclick: async () => {
      if (!req.value.trim()) return toast("Say what should change");
      ask.disabled = true;
      await run({ action: "character", character: p.id, request: req.value.trim(), ...pickerValue(picker) });
      ask.disabled = false; toast("Fable is drafting a proposal; it appears below when ready"); } }, "Ask for a revision");
    const proposals = p.proposals.map((pr) => h("div.cs-proposal",
      h("div", h("b", pr.request), h("span.help", ` · ${pr.provider || ""} ${pr.model || ""} · changes: ${pr.changed.join(", ") || "nothing"}`)),
      h("p.help", pr.notes),
      ...pr.changed.map((s) => h("details.section", h("summary", h("span", `${SECTION_TITLE[s]} (proposed)`)), h("div", diff(p[s], pr.sections[s])))),
      h("div.actions", h("button.btn.small.primary", { type: "button", onclick: () => post(`/proposals/${pr.id}`, {}) }, "Apply all"),
        ...pr.changed.map((s) => h("button.btn.small", { type: "button", onclick: () => post(`/proposals/${pr.id}`, { sections: [s] }) }, `Apply ${SECTION_TITLE[s].toLowerCase()} only`)),
        h("button.btn.small", { type: "button", onclick: () => post(`/proposals/${pr.id}`, { discard: true }) }, "Discard"))));
    const revise = section("Ask Fable to revise this character", h("div", field("Request", req), field("Model", picker, "Leave on managed, or pick a model for this revision only."), h("div.actions", ask),
      proposals.length ? h("div", h("div.lbl", "Proposals (nothing changes until you apply one)"), ...proposals) : null), { open: proposals.length > 0, note: proposals.length ? `${proposals.length} waiting` : "" });

    // visual pipeline
    const spec = h("p.cs-spec", visualSpec(p.visual) || "Write the visual identity above first.");
    const nBox = h("input", { type: "number", min: 1, max: 6, value: 3 });
    const gen = h("button.btn", { type: "button", onclick: async () => { gen.disabled = true; await run({ action: "concepts", character: p.id, n: +nBox.value }); gen.disabled = false; toast("Generating concepts; candidates appear in Review and below when done"); } }, "Generate concept candidates");
    const candidates = h("div.cs-candidates", h("span.help", "Candidates appear here after generation (also under Review)."));
    api(`/api/assets?project=${encodeURIComponent(data.config.project || "vn-" + data.config.slug)}&q=${encodeURIComponent(data.config.slug + "_" + p.id + "_concept")}&limit=12`).then((r) => {
      const items = (r.assets || r.items || []).filter((a) => (a.name || "").includes(`_${p.id}_concept`) || (a.name || "").endsWith(`_${p.id}_neutral`));
      if (!items.length) return;
      candidates.replaceChildren(...items.map((a) => h("div.cs-cand", { class: a.status }, h("img", { src: thumbUrl(a.primary_variant?.id, 256), alt: a.name }),
        h("div.help", `${a.status}${p.reference?.asset_id === a.id ? " · master" : ""}`),
        a.status !== "rejected" && p.reference?.asset_id !== a.id ? h("button.btn.small.primary", { type: "button", onclick: () => post("/master", { asset_id: a.id }) }, "Make this the master") : null)));
    }).catch(() => {});
    const consistency = h("div.help", "Consistency review runs against the master reference once expressions exist.");
    if (p.reference) api(`/api/stories/${sid}/characters/${p.id}/consistency`).then((r) => {
      consistency.replaceChildren(h("div.lbl", "Consistency of generated expressions"), r.items.length ? h("ul.wf-ul", r.items.map((i) => h("li", `${i.expression}: `, h("span", { class: i.verdict === "consistent" ? "good" : "bad" }, i.verdict), ` (shape distance ${i.distance}, palette ${i.palette})`))) : h("p.help", "No expressions generated yet."), h("p.help", r.note));
    }).catch(() => {});
    const visual = section("Visual development", h("div",
      h("div.lbl", "Written visual specification (what every image must keep)"), spec,
      p.reference ? h("div.help", `Master reference: ${p.reference.asset_id} · approved ${new Date(p.reference.at).toLocaleDateString()}${row.reference_stale ? " · the spec changed since: regenerate and re-approve" : ""}`) : h("div.help", "No master reference yet: generate candidates, pick one, and it becomes the reference every sprite and CG is made from."),
      h("div.row.wrap", nBox, gen), candidates, consistency), { open: true });

    const acting = actingPanel(p);

    const impact = h("div.help");
    api(`/api/stories/${sid}/characters/${p.id}/impact`).then((i) => (impact.textContent = i.summary)).catch(() => {});
    card.append(h("div.cs-profile-head", h("h2", p.identity.name || p.id), h("span.tag", p.identity.role), h("span.help", `version ${p.version}`), impact),
      ...sections, revise, visual, acting);
    return card;

    // ---- acting: the profile, the sprites the chapters use, and the performances the story asks for
    function actingPanel(p) {
      const body = h("div", h("span.spinner"), " Reading the acting plan…");
      const wrap = section("Acting and performances", body, { open: true, note: "story-driven" });
      const FIELDS_A = [["resting_posture", "Resting posture"], ["mannerisms", "Mannerisms"], ["expression_style", "Expresses or suppresses"], ["presence", "Presence"],
        ["gestures", "Characteristic gestures"], ["under_stress", "Under stress"], ["development", "Over the arc"]];
      const DIR = ["face", "eyes", "head", "shoulders", "torso", "hands", "weight", "intensity", "silhouette"];
      async function postA(path, payload) {
        try { const r = await api(`/api/stories/${sid}/acting${path}`, { method: "POST", body: payload }); toast("Saved"); await render(); return r; }
        catch (err) { toast(err.message); }
      }
      async function render() {
        let a;
        try { a = (await api(`/api/stories/${sid}/acting?character=${p.id}`)).characters[0]; } catch (err) { body.replaceChildren(errorBox(err)); return; }
        const prof = a.profile;
        const profBtn = h("button.btn.small", { type: "button", onclick: async () => { profBtn.disabled = true; await run({ action: "acting_profile", character: p.id, fresh: !!prof }); toast("Drafting the acting profile; it appears here when ready"); } }, prof ? "Draft again" : "Draft the acting profile");
        const values = { ...(prof || {}) };
        const profForm = prof ? h("div", h("div.cs-sec-head", h("span.cs-state", { class: prof.state }, STATE[prof.state] || prof.state), prof.note ? h("span.help", ` · ${prof.note}`) : null,
            prof.made_by?.provider ? h("span.help", ` · drafted by ${prof.made_by.provider} ${prof.made_by.model || ""}`) : null),
          h("div.dir-grid", ...FIELDS_A.map(([k, label]) => { const el = h("textarea", { rows: 2, value: values[k] || "" }); el.addEventListener("input", () => (values[k] = el.value)); return field(label, el); }),
            field("Around others", h("ul", ...(prof.around_others || []).map((o) => h("li", h("b", o.who), ": ", o.how)))),
            field("Would never", h("ul", ...(prof.out_of_character || []).map((o) => h("li", o))))),
          h("div.actions", h("button.btn.small.primary", { type: "button", onclick: () => postA(`/${p.id}/profile`, { patch: Object.fromEntries(FIELDS_A.map(([k]) => [k, values[k] || ""])), note: "edited" }) }, "Save changes"),
            h("button.btn.small", { type: "button", onclick: () => postA(`/${p.id}/profile`, { state: prof.state === "approved" ? "proposed" : "approved" }) }, prof.state === "approved" ? "Reopen" : "Approve"),
            h("button.btn.small", { type: "button", onclick: () => { const t = window.prompt("What should change?"); if (t) postA(`/${p.id}/profile`, { state: "changes_requested", note: t }); } }, "Request changes"), profBtn))
          : h("div", h("p.help", "No acting profile yet: it is drafted from the approved identity, personality and arc, and every performance below follows it."), h("div.actions", profBtn));
        // sprites the chapters use
        const sprites = h("div.cs-sprites", ...a.sprites.map((s) => h("div.cs-sprite", { class: s.verdict ? (s.verdict.works ? "good" : "bad") : "" },
          s.variant_id ? h("img", { src: thumbUrl(s.variant_id, 128), alt: s.expression, onerror: (e) => e.target.remove() }) : h("div.cs-sprite-none", "not made"),
          h("div", h("b", s.expression), s.performance ? h("span.help", " · performance") : null),
          h("div.help", s.scenes.length ? `used in ${s.scenes.join(", ")}` : "used in no written chapter"),
          s.verdict ? h("div.help", { class: s.verdict.works ? "good" : "bad" }, `${s.verdict.works ? "works" : "does not work"}: ${s.verdict.note}`) : null)));
        const analyze = h("button.btn.small.primary", { type: "button", disabled: !prof, onclick: async () => { analyze.disabled = true; await run({ action: "acting_analyze", character: p.id, fresh: !!a.analyzed }); toast("Reading the chapters for required performances; results appear here when ready"); } }, a.analyzed ? "Analyze the chapters again" : "Analyze the chapters");
        // performances
        const perfCard = (pf) => {
          const dir = { ...pf.direction };
          const dirForm = h("div.dir-grid", ...DIR.map((k) => { const el = h("input", { type: "text", value: dir[k] || "", disabled: pf.status !== "proposed" }); el.addEventListener("input", () => (dir[k] = el.value)); return field(k, el); }));
          const actions = pf.status === "proposed" ? h("div.actions",
            pf.need !== "reuse" ? h("button.btn.small.primary", { type: "button", onclick: () => postA(`/performances/${pf.id}`, { action: "approve", direction: dir }) }, pf.need === "face_only" ? "Approve (face on existing body)" : "Approve (new performance)") : null,
            h("button.btn.small", { type: "button", onclick: () => { const sp = window.prompt("Reuse which existing sprite?", pf.reuse_sprite || "neutral"); if (sp) postA(`/performances/${pf.id}`, { action: "reuse", reuse_sprite: sp }); } }, "Reuse existing"),
            h("button.btn.small", { type: "button", onclick: () => postA(`/performances/${pf.id}`, { action: "modify", direction: dir }) }, "Save direction"),
            h("button.btn.small", { type: "button", onclick: () => { const t = window.prompt("Why dismiss?") || ""; postA(`/performances/${pf.id}`, { action: "dismiss", note: t }); } }, "Dismiss"))
            : pf.status === "approved" && pf.need !== "reuse" ? h("div.actions", h("button.btn.small.primary", { type: "button", onclick: async (e) => { e.target.disabled = true; await run({ action: "acting_generate", performance: pf.id }); toast("Generating from the master reference"); } }, "Generate from the master"))
            : null;
          return h("div.cs-perf", { class: pf.status },
            h("div", h("span.tag", { class: pf.status === "generated" ? "good" : pf.status === "dismissed" ? "" : "warn" }, pf.status), " ", h("b", `${pf.scene} · lines ${pf.where}`), h("span.help", ` · ${pf.need === "reuse" ? "reuse " + (pf.reuse_sprite || "existing") : pf.need === "face_only" ? "face change on " + (pf.reuse_sprite || "an existing body") : "new performance"} → ${pf.name}`)),
            h("p", h("b", "Moment: "), pf.moment), h("p.help", h("b", "Feeling: "), pf.emotional_context, " · ", h("b", "Intent: "), pf.intent, pf.with_whom ? ` · with ${pf.with_whom}` : ""),
            pf.need !== "reuse" ? dirForm : null,
            h("p.help", h("b", "Why: "), pf.justification), actions);
        };
        const order = { proposed: 0, approved: 1, generated: 2, dismissed: 3 };
        const perfs = [...a.performances].sort((x, y) => order[x.status] - order[y.status]);
        body.replaceChildren(
          h("div.lbl", "Acting profile"), profForm,
          h("div.lbl", "Sprites the chapters use"), a.sprites.length ? sprites : h("p.help", "No sprites yet."),
          h("div.row.wrap", analyze, h("span.help", a.analyzed ? `last analyzed ${new Date(a.analyzed).toLocaleString()}` : "The analysis reads every written chapter this character appears in and proposes only what the story needs.")),
          h("div.lbl", `Performances (${a.proposed.length} to decide, ${a.missing.length} approved and not yet generated)`),
          perfs.length ? h("div", ...perfs.map(perfCard)) : h("p.help", "None proposed yet."));
      }
      render();
      return wrap;
    }
  }
}

function visualSpec(v) {
  const parts = [v.appearance, v.build && `Build: ${v.build}`, v.face && `Face: ${v.face}`, v.eyes && `Eyes: ${v.eyes}`, v.hair && `Hair: ${v.hair}`,
    v.features && `Distinguishing features: ${v.features}`, v.costume && `Costume: ${v.costume}`, v.accessories && `Accessories: ${v.accessories}`,
    v.palette?.length && `Colours: ${v.palette.join(", ")}`, v.silhouette && `Silhouette: ${v.silhouette}`, v.art_notes];
  return parts.filter(Boolean).map((x) => String(x).replace(/\.$/, "") + ".").join(" ");
}

function diff(before, after) {
  const keys = Object.keys(after || {});
  return h("div.cs-diff", ...keys.filter((k) => JSON.stringify(before?.[k]) !== JSON.stringify(after[k])).map((k) =>
    h("div.cs-diff-row", h("b", k), h("div.cs-old", String(Array.isArray(before?.[k]) ? before[k].join(", ") : before?.[k] || "—")), h("div.cs-new", String(Array.isArray(after[k]) ? after[k].join(", ") : after[k] || "—")))));
}
