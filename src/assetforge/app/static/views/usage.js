import { api, qs, h, fmt, tz, columnChart, errorBox, rangePicker } from "/static/lib.js";

export async function usage(root, { params }) {
  const state = { range: params.get("range") || "last-30-days", from: params.get("from") || "", to: params.get("to") || "",
                  interval: params.get("interval") || "day", tz: params.get("tz") || tz };
  let current = state.from ? { from: state.from, to: state.to } : { range: state.range };
  const interval = h("select", { title: "Group by" }, ["day", "week", "month"].map((i) => h("option", { value: i, selected: i === state.interval }, `Group by ${i}`)));
  const zone = h("input", { type: "text", value: state.tz, style: "width:150px", title: "Timezone (IANA name, UTC or local)" });
  const picker = rangePicker({ range: state.range, from: state.from, to: state.to,
                               onChange: (p) => { current = p.from ? { from: p.from, to: p.to } : { range: p.range }; load(); } });
  const body = h("div");
  [interval, zone].forEach((el) => el.addEventListener("change", load));

  root.append(h("div.page-head", h("div", h("h1", "Usage & cost"), h("div.sub", "Token usage and spend for OpenAI and Gemini, priced with the latest fetched rates."))),
    h("div.toolbar", picker, interval, h("label.help", "Timezone ", zone), h("button.btn", { onclick: load }, "Refresh")), body);

  async function load() {
    const p = { ...current, interval: interval.value, tz: zone.value };
    history.replaceState(null, "", "#/usage" + qs(p));
    body.innerHTML = "";
    body.append(h("p", h("span.spinner"), " Loading…"));
    try {
      const { report: r } = await api("/api/report" + qs(p));
      body.innerHTML = "";
      body.append(render(r));
    } catch (err) {
      body.innerHTML = "";
      body.append(errorBox(err));
    }
  }
  await load();
  return { update: () => {} };
}

function render(r) {
  const t = r.totals;
  const tiles = [
    ["Total cost", fmt.money(t.cost_usd), `estimated ${fmt.money(t.estimated_cost_usd)} before calls`],
    ["Images", fmt.int(t.images), `${fmt.int(t.calls)} API calls`],
    ["Tokens", fmt.int(t.total_tokens), `${fmt.int(t.output_image_tokens)} image output`],
    ["Avg per image", fmt.money(t.avg_cost_per_image), "actual"],
    ["Estimate accuracy", t.estimate_error_pct === null ? "–" : `${t.estimate_error_pct > 0 ? "+" : ""}${t.estimate_error_pct}%`, "estimate vs actual"],
  ];
  const table = (rows, label) => rows.length ? h("div.table-wrap", h("table",
    h("tr", h("th", label), h("th.num", "Calls"), h("th.num", "Images"), h("th.num", "Input tokens"), h("th.num", "Output tokens"), h("th.num", "Cost"), h("th.num", "Share"), h("th.num", "$/image")),
    rows.map((x) => h("tr", h("td", x.key), h("td.num", fmt.int(x.calls)), h("td.num", fmt.int(x.images)),
      h("td.num", fmt.int(x.input_text_tokens + x.input_image_tokens)), h("td.num", fmt.int(x.output_image_tokens + x.output_text_tokens)),
      h("td.num", fmt.money(x.cost_usd)), h("td.num", `${(x.share * 100).toFixed(1)}%`), h("td.num", fmt.money(x.avg_cost_per_image)))))) : h("div.muted", "No usage in this range.");
  return h("div",
    h("p.help", `${r.range.from || "beginning"} → ${r.range.to} (${r.range.tz}) · prices ${r.pricing.fetched_at ? "fetched " + fmt.when(r.pricing.fetched_at) : "built-in"}`),
    h("div.tiles", tiles.map(([a, b, c]) => h("div.tile", h("div.label", a), h("div.value", b), h("div.note", c)))),
    h("h2", `Cost per ${r.range.interval}`),
    h("div.card", t.calls ? columnChart(r.series, {
      value: (p) => p.cost_usd,
      label: (p) => (p.period.length === 10 ? p.period.slice(5) : p.period.replace("week of ", "")),
      tooltip: (p) => h("div", h("b", p.period), h("br"), `${fmt.money(p.cost_usd)} · ${p.images} image(s) · ${p.calls} call(s)`, h("br"), `${fmt.int(p.total_tokens)} tokens`),
    }) : h("div.muted", "No paid API calls in this range.")),
    h("h2", "By provider"), table(r.by_provider, "Provider"),
    h("h2", "By model"), table(r.by_model, "Model"),
    h("h2", "By project"), table(r.by_project, "Project"),
    h("h2", "Most expensive calls"),
    r.top_jobs.length ? h("div.table-wrap", h("table",
      h("tr", h("th", "Job"), h("th", "When"), h("th", "Model"), h("th.num", "Images"), h("th.num", "Tokens"), h("th.num", "Cost"), h("th", "Prompt")),
      r.top_jobs.map((j) => h("tr", { class: "click", onclick: () => j.job_id && (location.hash = `#/jobs/${j.job_id}`) },
        h("td.mono", j.job_id || "–"), h("td", fmt.date(j.created_at)), h("td", `${j.provider}/${j.model}`), h("td.num", j.images),
        h("td.num", fmt.int(j.total_tokens)), h("td.num", fmt.money(j.cost_usd)),
        h("td", { style: "max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap", title: j.prompt || "" }, j.prompt || "–"))))) : h("div.muted", "None."),
    h("p.help", { style: "margin-top:14px" }, `Prices: ${r.pricing.source_url}`));
}
