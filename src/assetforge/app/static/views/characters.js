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

    const impact = h("div.help");
    api(`/api/stories/${sid}/characters/${p.id}/impact`).then((i) => (impact.textContent = i.summary)).catch(() => {});
    card.append(h("div.cs-profile-head", h("h2", p.identity.name || p.id), h("span.tag", p.identity.role), h("span.help", `version ${p.version}`), impact),
      ...sections, revise, visual);
    return card;
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
