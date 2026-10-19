import { api, qs, h, fmt, badge, errorBox } from "/static/lib.js";
import { jobsTable } from "/static/views/overview.js";

export async function jobsView(root, { rest, params }) {
  const state = { status: params.get("status") || "", kind: params.get("kind") || "" };
  const status = h("select", ["", "queued", "running", "succeeded", "failed", "cancelled"].map((s) =>
    h("option", { value: s, selected: s === state.status }, s ? `Status: ${s}` : "Status: all")));
  const kind = h("select", ["", "image", "image_edit", "audio", "audio_vary", "request"].map((k) =>
    h("option", { value: k, selected: k === state.kind }, k ? `Kind: ${k}` : "Kind: all")));
  const list = h("div");
  const detail = h("div");
  [status, kind].forEach((el) => el.addEventListener("change", () => { state.status = status.value; state.kind = kind.value; load(); }));
  root.append(h("div.page-head", h("div", h("h1", "Jobs"), h("div.sub", "Every generation run: what was asked, which provider answered, what it cost."))),
    h("div.toolbar", status, kind, h("button.btn", { onclick: load }, "Refresh")), detail, list);

  async function load() {
    list.innerHTML = "";
    try {
      const { jobs } = await api("/api/jobs" + qs({ status: state.status, kind: state.kind, limit: 200 }));
      list.append(jobs.length ? jobsTable(jobs, (j) => (location.hash = `#/jobs/${j.id}`)) : h("div.empty", "No jobs."));
    } catch (err) { list.append(errorBox(err)); }
  }

  async function show(id) {
    detail.innerHTML = "";
    if (!id) return;
    try {
      const { job } = await api(`/api/jobs/${id}`);
      detail.append(h("div.card", { style: "margin-bottom:16px" },
        h("div.title", { style: "display:flex;justify-content:space-between" },
          h("h3", { style: "margin:0" }, job.id, " ", badge(job.status)),
          h("button.close", { onclick: () => (location.hash = "#/jobs") }, "×")),
        h("dl.facts",
          h("dt", "Kind"), h("dd", job.kind), h("dt", "Provider"), h("dd", [job.provider, job.model].filter(Boolean).join(" / ") || "–"),
          h("dt", "Cost"), h("dd", job.cost_usd ? fmt.money(job.cost_usd) : "–"), h("dt", "Attempts"), h("dd", job.attempts),
          h("dt", "Created"), h("dd", fmt.date(job.created_at)),
          h("dt", "Asset"), h("dd", job.asset_id ? h("a", { href: `#/assets/${job.asset_id}` }, job.asset_id) : "–")),
        job.error ? h("div", { style: "margin-top:10px" }, errorBox(job.error)) : null,
        h("details", { style: "margin-top:10px" }, h("summary.help", "Request"), h("pre.prompt.mono", JSON.stringify(job.request, null, 2)))));
    } catch (err) { detail.append(errorBox(err)); }
  }

  await load();
  await show(rest[0]);
  return { update: ({ rest: r }) => show(r[0]) };
}
