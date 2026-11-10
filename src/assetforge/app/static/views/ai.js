// AI & workflow settings: which providers are really usable (subscription vs API), the execution plan (Fable-managed
// or a manual order), the paid-fallback policy, per-task overrides, and a reusable model picker for actions.
import { api, h, toast, errorBox, section, field, segmented } from "/static/lib.js";

let infoCache = null;
export async function loadAI(refresh = false) {
  if (!infoCache || refresh) infoCache = await api("/api/ai/plan" + (refresh ? "?refresh=1" : ""));
  return infoCache;
}

/** A compact provider + model picker. value: {provider, model}; returns an element with .value(). */
export function modelSelect(providers, value = {}, { allowAuto = true } = {}) {
  const sel = h("select.model-select");
  if (allowAuto) sel.append(h("option", { value: "" }, "Fable picks (managed)"));
  for (const p of providers) {
    if (!p.installed) continue;
    const label = `${p.name} · ${p.billing === "api" ? "API (billed)" : "subscription"}${p.usable ? "" : " · not signed in"}`;
    const group = h("optgroup", { label });
    const models = p.models?.length ? p.models : [{ id: "", label: "Default" }];
    for (const m of models) group.append(h("option", { value: `${p.name}|${m.id || ""}`, selected: value.provider === p.name && (value.model || "") === (m.id || "") }, `${m.label || m.id || "Default"}${m.worked ? " ✓" : ""}`));
    sel.append(group);
  }
  sel.value = value.provider ? `${value.provider}|${value.model || ""}` : "";
  sel.value_ = () => { const [provider, model] = (sel.value || "|").split("|"); return provider ? { provider, model: model || null } : {}; };
  sel.value = sel.value || "";
  return sel;
}

export function pickerValue(sel) { return sel.value_ ? sel.value_() : {}; }

export async function ai(root) {
  window.afCrumb?.("AI & workflow");
  const info = await loadAI(true);
  const plan = info.plan;
  root.append(h("div.page-head", h("div", h("h1", "AI & workflow"), h("div.sub", "Which services write for you, in what order, and when a billed API may step in."))));

  // providers: real capability, never implied
  const rows = info.providers.map((p) => h("tr",
    h("td", h("b", p.name), p.note ? h("div.help", p.note) : null),
    h("td", p.billing === "api" ? h("span.tag.warn", "API · billed per token") : h("span.tag", "Subscription")),
    h("td", p.installed ? (p.signed_in === false ? h("span.bad", "not signed in") : p.signed_in ? h("span.good", `signed in · ${p.account || ""}`) : h("span.muted", p.account || "unknown")) : h("span.muted", p.reason || "not installed")),
    h("td", (p.models || []).map((m) => h("span.chip", `${m.label || m.id || "default"}${m.worked ? " ✓" : ""}`))),
    h("td.help", p.login_hint || "")));
  root.append(h("div.card", h("h3", "Services"), h("p.help", "Subscriptions run through the vendors' own signed-in command-line tools. API access is only used when a key and a model are set in .env, and only under the policy below."),
    h("table.ai-table", h("thead", h("tr", h("th", "Service"), h("th", "Billing"), h("th", "Status"), h("th", "Models"), h("th", ""))), h("tbody", rows))));

  // the plan
  const st = { ...plan };
  const managed = segmented([["managed", "Fable managed"], ["manual", "Manual order"]], st.managed ? "managed" : "manual", (v) => { st.managed = v === "managed"; draw(); });
  const policy = segmented([["confirm", "Ask me first"], ["allow", "Allow"], ["never", "Never"]], st.paid_fallback, (v) => (st.paid_fallback = v));
  const limit = h("input", { type: "number", min: 0, step: 1, value: st.daily_api_limit_usd ?? "", placeholder: "no limit" });
  limit.addEventListener("input", () => (st.daily_api_limit_usd = limit.value === "" ? null : +limit.value));
  const stepsBox = h("div");
  const perTaskBox = h("div");

  function stepsEditor(list, onchange) {
    const box = h("div.ai-steps");
    const redraw = () => {
      box.replaceChildren(...list.map((s, i) => {
        const sel = modelSelect(info.providers, s, { allowAuto: false });
        sel.addEventListener("change", () => { Object.assign(list[i], pickerValue(sel)); onchange(); });
        return h("div.row", h("span.muted", `${i + 1}.`), sel, h("button.btn.small", { type: "button", onclick: () => { list.splice(i, 1); redraw(); onchange(); } }, "Remove"));
      }), h("button.btn.small", { type: "button", onclick: () => { const first = info.providers.find((p) => p.usable) || info.providers[0]; list.push({ provider: first?.name || "claude-cli", model: null }); redraw(); onchange(); } }, "+ Add a fallback"));
    };
    redraw();
    return box;
  }

  function draw() {
    stepsBox.replaceChildren(st.managed
      ? h("p.help", "Fable picks per task: the first signed-in subscription in order (Claude, ChatGPT/Codex, Gemini), Opus for planning, character and review work and Sonnet for writing on Claude. Billed APIs come last and follow the policy below.")
      : stepsEditor(st.steps, () => {}));
    const kinds = Object.entries(info.task_kinds);
    perTaskBox.replaceChildren(...kinds.map(([kind, label]) => {
      const current = (st.per_task[kind] || [])[0] || {};
      const sel = modelSelect(info.providers, current);
      sel.addEventListener("change", () => { const v = pickerValue(sel); if (v.provider) st.per_task[kind] = [v]; else delete st.per_task[kind]; });
      return field(label, sel);
    }));
  }
  draw();
  const save = h("button.btn.primary", { type: "button", onclick: async () => {
    try { const r = await api("/api/ai/plan", { method: "POST", body: st }); infoCache = null; toast("AI plan saved"); Object.assign(plan, r.plan); }
    catch (err) { toast(err.message); } } }, "Save");
  root.append(h("div.card",
    h("h3", "Execution plan"),
    field("Who chooses the model", managed, "Managed: Fable chooses per task from what is signed in. Manual: your order below is used for every task."),
    stepsBox,
    field("Billed API fallback", policy, "When every subscription option fails or is rate-limited: ask before spending, spend automatically, or never."),
    field("Daily API limit (USD)", limit, "Paid calls stop when the day's estimated API spend passes this."),
    section("Per-task overrides", h("div.dir-grid", perTaskBox), { note: "optional: pin a model for a kind of work" }),
    h("div.actions", save)));
  return {};
}
