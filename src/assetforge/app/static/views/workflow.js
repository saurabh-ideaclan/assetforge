// The story's Workflow tab: what is being created, what has been decided, what you can change, what needs you.
// Twelve real stages from the server (files + approvals), the approval mode, approve / request-changes actions,
// and "Continue" when the writer paused at a gate.
import { api, h, toast, segmented, section } from "/static/lib.js";

const STATE_LABEL = { done: "Done", working: "Working", needs_approval: "Needs your approval", needs_revision: "Needs revision",
                      ready: "Ready to start", blocked: "Blocked", pending: "Later" };

export function workflowTab(root, data, sid, run, refresh) {
  const wf = data.workflow;
  if (!wf) { root.append(h("div.empty", "No workflow yet.")); return; }
  const box = h("div");
  root.append(box);

  async function change(body) {
    try { await api(`/api/stories/${sid}/workflow`, { method: "POST", body }); await refresh(true); }
    catch (err) { toast(err.message); }
  }
  async function note(stage, action) {
    const text = window.prompt(action === "approve" ? "Optional note for the approval" : "What should change?") ;
    if (text === null) return;
    if (action === "changes" && !text.trim()) return toast("Say what should change");
    await change({ action, stage, note: text });
  }

  const current = wf.stages.find((s) => s.id === wf.current) || wf.stages[0];
  const activity = h("div.card.wf-activity",
    h("div.wf-now", h("div.lbl", "Current activity"), h("h2", wf.activity.title), h("p", wf.activity.detail)),
    h("div.wf-lists",
      h("div", h("div.lbl", "Completed"), ul(current.done, "nothing yet")),
      h("div", h("div.lbl", "Working on"), ul(current.working.length ? current.working : (current.state === "working" ? [current.goal] : []), "idle")),
      h("div", h("div.lbl", "Next"), ul(current.next.length ? current.next : nextStages(wf), "nothing"))),
    wf.needs_you.length ? h("div.wf-needs", h("div.lbl", "Human action"), ...wf.needs_you.map((n) => h("div.wf-need",
      h("b", n.title), ` · ${n.action} `,
      h("button.btn.small.primary", { type: "button", onclick: () => note(n.stage, "approve") }, "Approve"),
      h("button.btn.small", { type: "button", onclick: () => note(n.stage, "changes") }, "Request changes")))) : null,
    wf.paused_at ? h("div.wf-paused", `Writing paused at “${titleOf(wf, wf.paused_at)}”. `,
      h("button.btn.small.primary", { type: "button", onclick: () => run({ action: "write" }) }, "Continue after approval")) : null);

  const mode = segmented([["guided", "Guided"], ["auto", "Auto"], ["manual", "Manual"]], wf.mode, (v) => change({ action: "mode", mode: v }));
  const modeCard = h("div.card.wf-mode", h("div", h("b", "Approval mode"), h("p.help", { guided: "Fable asks you at the big creative milestones: characters, story architecture, visual identities, final review.",
    auto: "Fable proceeds on its own; only art spending and the final review wait for you.",
    manual: "Every stage waits for your approval." }[wf.mode])), mode);

  const stages = h("div.wf-stages", ...wf.stages.map((s, i) => {
    const open = s.id === wf.current || s.state === "needs_approval" || s.state === "needs_revision";
    const actions = s.gated && (s.state === "needs_approval" || s.state === "needs_revision" || s.state === "done") ? h("div.actions",
      s.state !== "done" ? h("button.btn.small.primary", { type: "button", onclick: () => note(s.id, "approve") }, "Approve") : null,
      s.state !== "done" ? h("button.btn.small", { type: "button", onclick: () => note(s.id, "changes") }, "Request changes") : null,
      s.state === "done" ? h("button.btn.small", { type: "button", onclick: () => change({ action: "reopen", stage: s.id }) }, "Reopen") : null) : null;
    return h("details.wf-stage", { class: s.state, open },
      h("summary", h("span.wf-num", i + 1), h("span.wf-title", s.title), h("span.wf-state", { class: s.state }, STATE_LABEL[s.state] || s.state),
        s.approval?.state === "approved" ? h("span.help", ` approved ${fmtDate(s.approval.at)}`) : null),
      h("div.wf-body",
        h("p.help", s.goal),
        s.decisions.length ? h("div", h("div.lbl", "Decided"), ul(s.decisions)) : null,
        s.done.length ? h("div", h("div.lbl", "Completed"), ul(s.done)) : null,
        s.working.length ? h("div", h("div.lbl", "Being prepared"), ul(s.working)) : null,
        s.next.length ? h("div", h("div.lbl", "Next"), ul(s.next)) : null,
        s.inputs.length ? h("div", h("div.lbl", "What you can change"), ul(s.inputs)) : null,
        s.approval?.note ? h("p.help", `Note: ${s.approval.note}`) : null,
        actions));
  }));

  const history = wf.history.length ? section("Approval history", h("div", ...wf.history.slice().reverse().map((e) =>
    h("div.feed-row", h("span.muted", fmtDate(e.at)), " ", e.stage ? h("b", titleOf(wf, e.stage)) : null, ` ${e.action}`, e.note ? h("span.help", ` · ${e.note}`) : null))), { note: `${wf.history.length}` }) : null;

  box.replaceChildren(...[activity, modeCard, stages, history].filter(Boolean));
}

function nextStages(wf) {
  const i = wf.stages.findIndex((s) => s.id === wf.current);
  return wf.stages.slice(i + 1, i + 3).map((s) => s.title);
}
function titleOf(wf, id) { return (wf.stages.find((s) => s.id === id) || {}).title || id; }
function ul(items, empty) { return items && items.length ? h("ul.wf-ul", items.map((t) => h("li", t))) : h("div.help", empty || ""); }
function fmtDate(iso) { try { return new Date(iso).toLocaleString(); } catch { return iso || ""; } }
