import { api, qs, h, fmt, tz, columnChart, barList, assetCard, badge, errorBox, rangePicker } from "/static/lib.js";

export async function overview(root) {
  const d = await api("/api/overview?tz=" + encodeURIComponent(tz));
  const t = d.spend.totals;
  const month = d.budget.monthly;
  const candidates = d.by_status.candidate || 0;
  const pending = d.jobs.pending;
  const failed = d.jobs.by_status.failed || 0;

  const tiles = [
    ["Assets", fmt.int(d.counts.assets), `${fmt.int(d.counts.files)} files · ${fmt.bytes(d.library.stored_bytes)}`],
    ["Waiting for review", fmt.int(candidates), candidates ? "candidates to approve or reject" : "nothing pending"],
    ["Spend, last 30 days", fmt.money(t.cost_usd), `${fmt.int(t.images)} images · ${fmt.int(t.calls)} API calls`],
    ["This month", fmt.money(month.spent_usd), month.limit_usd ? `of ${fmt.money(month.limit_usd)} budget` : "no monthly budget set"],
    ["Jobs", `${pending.running} running`, `${pending.queued} queued · ${failed} failed overall`],
  ];

  const byType = Object.entries(d.by_type).map(([label, count]) => ({ label, count }));
  const byOrigin = Object.entries(d.by_origin).map(([label, count]) => ({ label, count }));
  const statusRows = Object.entries(d.by_status).map(([s, n]) =>
    h("div", { style: "display:flex;justify-content:space-between;padding:4px 0" }, badge(s), h("span", fmt.int(n))));

  const spendTile = { value: null, note: null, label: null };
  root.append(
    d.library.is_default ? null : h("div.notice",
      h("div", h("b", "This isn't the default library."), " Agents write to ", h("span.mono", d.library.default_home),
        " unless they pass --home, so their new assets and spend won't show here."),
      h("a.btn", { href: "assetforge-action://default-library", title: "Same as File ▸ Use Default Library" }, "Switch to default library")),
    h("div.page-head",
      h("div", h("h1", "Overview"), h("div.sub", d.library.home)),
      h("div.actions", h("a.btn", { href: "#/assets" }, "Browse assets"), h("a.btn.primary", { href: "#/generate" }, "Generate"))),
    h("div.tiles", tiles.map(([label, value, note], i) => {
      const tile = h("div.tile", h("div.label", label), h("div.value", value), h("div.note", note));
      if (i === 2) [spendTile.label, spendTile.value, spendTile.note] = tile.children;
      return tile;
    })),
    h("div.cols", { style: "margin-top:14px" },
      h("div.card", h("h3", { style: "margin-top:0" }, "Assets by type"),
        byType.length ? barList(byType) : h("div.muted", "No assets yet — import or generate some.")),
      h("div.card", h("h3", { style: "margin-top:0" }, "Where they came from"), barList(byOrigin),
        h("h3", "Review status"), statusRows)),
    ...spendSection(d, spendTile),
    h("h2", "Newest assets"),
    d.newest.length
      ? h("div.strip", d.newest.map((a) => assetCard(a, { onclick: () => (location.hash = `#/assets/${a.id}`) })))
      : h("div.empty", "Nothing here yet."),
    h("h2", "Recent jobs"),
    d.recent_jobs.length ? jobsTable(d.recent_jobs) : h("div.empty", "No generation jobs yet."),
  );
}

function spendSection(d, spendTile) {
  const title = h("h2", "Spend · last 30 days");
  const card = h("div.card");
  const link = h("a", { href: "#/usage" }, "Full usage report →");
  const draw = (r, label, params = { range: "last-30-days" }) => {
    title.textContent = `Spend · ${label.toLowerCase()}`;
    spendTile.label.textContent = `Spend, ${label.toLowerCase()}`;
    spendTile.value.textContent = fmt.money(r.totals.cost_usd);
    spendTile.note.textContent = `${fmt.int(r.totals.images)} images · ${fmt.int(r.totals.calls)} API calls`;
    link.href = "#/usage" + qs(params.range ? { range: params.range } : { from: params.from, to: params.to });
    card.innerHTML = "";
    card.append(
      r.totals.calls
        ? columnChart(r.series, {
            value: (p) => p.cost_usd,
            label: (p) => (p.period.length === 10 ? p.period.slice(5) : p.period.replace("week of ", "")),
            tooltip: (p) => h("div", h("b", p.period), h("br"), `${fmt.money(p.cost_usd)} · ${p.images} image(s)`, h("br"), `${fmt.int(p.total_tokens)} tokens`),
          })
        : h("div.muted", `No paid API calls in this range (${label.toLowerCase()}).`),
      h("div.help", { style: "margin-top:6px" },
        d.pricing.fetched_at ? `Prices fetched ${fmt.when(d.pricing.fetched_at)}. ` : "Using built-in prices. ", link));
  };
  const picker = rangePicker({
    range: "last-30-days",
    onChange: async (p) => {
      const days = p.range ? null : (new Date(p.to) - new Date(p.from)) / 86400000;
      const interval = p.range === "this-year" || p.range === "all" || days > 120 ? "month" : days > 45 ? "week" : "day";
      try {
        const { report } = await api("/api/report" + qs({ range: p.range, from: p.from, to: p.to, tz: p.tz, interval }));
        draw(report, p.label, p);
      } catch (err) { card.innerHTML = ""; card.append(errorBox(err)); }
    },
  });
  draw({ totals: d.spend.totals, series: d.spend.series, range: d.spend.range }, "Last 30 days");
  return [h("div.section-head", title, picker), card];
}

export function jobsTable(jobs, onRow) {
  return h("div.table-wrap", h("table",
    h("tr", h("th", "Job"), h("th", "Kind"), h("th", "Status"), h("th", "What"), h("th", "Model"), h("th.num", "Cost"), h("th", "When")),
    jobs.map((j) => h("tr", { class: "click", onclick: () => (onRow ? onRow(j) : (location.hash = j.asset_id ? `#/assets/${j.asset_id}` : `#/jobs/${j.id}`)) },
      h("td.mono", j.id), h("td", j.kind), h("td", badge(j.status)),
      h("td", { style: "max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, j.summary || "–"),
      h("td", [j.provider, j.model].filter(Boolean).join(" / ") || "–"),
      h("td.num", j.cost_usd ? fmt.money(j.cost_usd) : "–"), h("td", fmt.when(j.created_at))))));
}
