// Publishing: the content backend in the R2 bucket (doc 27). Staging and production channels, the signed index,
// stories (local latest / staging / production), actions behind a confirm step that shows the plan first, the
// change feed, verify results, and what the app team needs (index URL, public key). No innerHTML with data.
import { api, h, fmt, toast, copy, errorBox, segmented } from "/static/lib.js";

const CHANNELS = [["staging", "Staging"], ["production", "Production"]];
const CHANGE_WORDS = { story_added: "added", release_published: "published", rollback: "rolled back", story_unlisted: "unlisted",
  story_relisted: "relisted", promoted: "promoted" };

export async function backendView(root) {
  const state = { channel: "staging", view: null, busy: false, confirm: null, verify: null };
  const connection = h("div");
  const tabs = segmented(CHANNELS, state.channel, (v) => { state.channel = v; draw(); });
  const stories = h("div");
  const confirmBox = h("div");
  const feed = h("div");
  const verifyBox = h("div");
  const appInfo = h("div");
  const refresh = h("button.btn", { type: "button", onclick: () => load(true) }, "Refresh");
  root.append(
    h("div.page-head", h("div", h("h1", "Publishing"),
      h("div.sub", "The published stories in the bucket. Publish goes to staging; promote makes a staging release current in production. Nothing is written until you confirm."))),
    connection, h("div.toolbar", tabs, refresh), confirmBox, stories,
    h("div.pub-cols", feed, h("div", verifyBox, appInfo)));

  async function load(live) {
    refresh.disabled = true;
    try {
      state.view = await api("/api/backend/status" + (live ? "" : "?cached=1"));
      draw();
    } catch (err) { connection.replaceChildren(errorBox(err)); }
    finally { refresh.disabled = false; }
  }

  function draw() {
    const v = state.view;
    if (!v) return;
    drawConnection(v);
    drawStories(v);
    drawFeed(v);
    drawVerify();
    drawAppInfo(v);
  }

  function drawConnection(v) {
    const cfg = v.config || {};
    const c = v.connection || {};
    const facts = h("dl.facts",
      h("dt", "Bucket"), h("dd.mono", cfg.bucket || "not configured"),
      h("dt", "Prefix"), h("dd.mono", cfg.prefix || "(none: the whole bucket)"),
      h("dt", "State"), h("dd", c.ok === true ? h("span.tag.good", "reachable")
        : c.ok === null ? h("span.tag", `cached view${v.cached_at ? " · " + fmt.when(v.cached_at) : ""}`) : h("span.tag.warn", "not reachable")));
    const problem = c.error ? h("div.error-box", h("div", h("b", c.error.code), " — ", c.error.message),
      c.error.hint ? h("div.hint", "Fix: ", c.error.hint) : null) : null;
    connection.replaceChildren(h("div.card", { style: "margin-bottom:12px" }, h("h3", "Connection"), facts, problem));
  }

  function storyRows(v) {
    return v.stories.map((s) => {
      const onStaging = s.staging_releases || [];
      const actions = h("div.pub-actions");
      const btn = (label, fn, opts = {}) => h("button.btn.small", { type: "button", disabled: state.busy || opts.disabled, title: opts.title || "", onclick: fn }, label);
      if (state.channel === "staging") {
        if (s.registered && s.local_latest) {
          actions.append(btn("Plan", () => ask("plan", { story: s.registered, release: s.local_latest }, s)),
            btn(`Publish ${s.local_latest}`, () => ask("publish", { story: s.registered, release: s.local_latest }, s),
              { disabled: s.local_valid === false || s.staging === s.local_latest, title: s.local_valid === false ? "The latest release does not validate" : "" }));
        }
        if (s.staging) actions.append(btn(`Promote ${s.staging}`, () => ask("promote", { story: s.storyId, release: s.staging }, s),
          { disabled: s.production === s.staging }));
      }
      const listed = state.channel === "staging" ? s.staging_releases : s.production_releases;
      const current = s[state.channel];
      const status = s[`${state.channel}_status`];
      const older = (listed || []).filter((r) => r !== current);
      if (older.length) {
        const pick = h("select", older.map((r) => h("option", { value: r }, r)));
        actions.append(h("span", { style: "display:inline-flex;gap:4px" }, pick,
          btn("Roll back", () => ask("rollback", { story: s.storyId, release: pick.value, channel: state.channel }, s))));
      }
      if (current) actions.append(btn(status === "unlisted" ? "Relist" : "Unlist",
        () => ask(status === "unlisted" ? "relist" : "unlist", { story: s.storyId, channel: state.channel }, s)));
      return h("tr",
        h("td", h("b", s.title || s.storyId), h("div.help.mono", s.storyId)),
        h("td.mono", s.local_latest || "—"),
        h("td.mono", s.staging || "—", s.staging_status === "unlisted" ? h("span.tag.warn", " unlisted") : null),
        h("td.mono", s.production || "—", s.production_status === "unlisted" ? h("span.tag.warn", " unlisted") : null),
        h("td", h("span.tag", { class: s.state === "in sync" ? "good" : "" }, s.state)),
        h("td", actions));
    });
  }

  function drawStories(v) {
    const info = v.channels[state.channel] || {};
    const head = h("p.help", `${state.channel}: index sequence ${info.sequence || 0}`,
      info.generatedAt ? ` · generated ${fmt.date(info.generatedAt)}` : "",
      info.stale_pointer ? " · current.json lags behind history (re-run a write to fix)" : "");
    stories.replaceChildren(h("div.card", h("h3", "Stories"), head,
      v.stories.length ? h("div.table-wrap", h("table",
        h("thead", h("tr", ...["Story", "Local", "Staging", "Production", "State", ""].map((t) => h("th", t)))),
        h("tbody", storyRows(v)))) : h("div.empty", "No compiled releases yet. Compile one from a story's Settings tab (Release card).")));
  }

  // ------------------------------------------------------------------ confirm step: the plan first, then the write

  async function ask(action, body, story) {
    state.busy = true; draw();
    try {
      const path = action === "plan" ? "plan" : action;
      const plan = await api(`/api/backend/${path}`, { method: "POST", body });
      state.confirm = { action, body, plan, story };
    } catch (err) { toast(err.message); state.confirm = { action, body, error: err, story }; }
    finally { state.busy = false; draw(); drawConfirm(); }
  }

  function planLines(action, p) {
    if (action === "plan" || action === "publish") {
      const plan = p.plan;
      const t = plan.totals;
      return [h("p", `${plan.story} ${plan.release} (${plan.graph_id}) → ${plan.channel}`),
        h("p.help", `Remote: ${plan.remote}. Upload ${t.upload.count} object(s), ${fmt.bytes(t.upload.bytes)}; already there ${t.skip.count}; conflicts ${t.conflict.count}.`),
        plan.delta ? h("p.help", "Delta: ", h("span.mono", plan.delta)) : null,
        plan.catalog ? h("p.help", `Index ${plan.catalog.key}: sequence ${plan.catalog.current_sequence ?? "none"} → ${plan.catalog.next_sequence}.`) : null,
        ...(plan.errors || []).slice(0, 8).map((e) => h("div.dir-warn", `${e.code} · ${e.where}: ${e.message}`))];
    }
    const lines = [];
    if (p.from_release !== undefined) lines.push(h("p", `${p.story} on ${p.channel}: ${p.from_release || "nothing"} → ${p.release}`));
    if (p.status_to) lines.push(h("p", `${p.story} on ${p.channel}: ${p.status_from} → ${p.status_to}`));
    if (p.objects !== undefined) lines.push(h("p.help", `${p.objects} objects checked in the bucket; ${p.missing.length} missing. Nothing is uploaded; only the index is written.`));
    if (p.next_sequence) lines.push(h("p.help", `Index sequence ${p.current_sequence ?? "none"} → ${p.next_sequence}.`));
    if (p.message) lines.push(h("p.help", p.message));
    (p.missing || []).slice(0, 8).forEach((k) => lines.push(h("div.dir-warn.mono", k)));
    return lines;
  }

  function writable(action, p) {
    if (action === "plan") return false;
    if (action === "publish") return !!p.plan?.publishable;
    if (action === "promote") return !!p.promotable;
    if (p.message) return false; // already in that state
    return !(p.missing && p.missing.length);
  }

  function drawConfirm() {
    const c = state.confirm;
    if (!c) { confirmBox.replaceChildren(); return; }
    const close = () => { state.confirm = null; drawConfirm(); };
    if (c.error) { confirmBox.replaceChildren(h("div.card", errorBox(c.error), h("div.actions", h("button.btn", { type: "button", onclick: close }, "Close")))); return; }
    const verb = { plan: "Plan", publish: "Publish", promote: "Promote", rollback: "Roll back", unlist: "Unlist", relist: "Relist" }[c.action];
    const ok = writable(c.action, c.plan);
    const go = h("button.btn.primary", { type: "button", disabled: !ok || state.busy, onclick: async () => {
      go.disabled = true;
      try {
        const done = await api(`/api/backend/${c.action}`, { method: "POST", body: { ...c.body, confirm: true } });
        const seq = done.sequence ?? done.receipt?.sequence;
        toast(done.written === false ? "Nothing was written" : `${verb} done · index sequence ${seq}`);
        state.confirm = null; drawConfirm(); load(true);
      } catch (err) { toast(err.message); go.disabled = false; }
    } }, c.action === "plan" ? "Close" : `Confirm ${verb.toLowerCase()}`);
    confirmBox.replaceChildren(h("div.card", { style: "margin-bottom:12px;border-color:var(--accent)" },
      h("h3", c.action === "plan" ? "Publish plan (nothing written)" : `${verb}: check the plan`), ...planLines(c.action, c.plan),
      h("div.actions", c.action === "plan" ? null : go, h("button.btn", { type: "button", onclick: close }, c.action === "plan" ? "Close" : "Cancel"))));
  }

  // ------------------------------------------------------------------ feed, verify, app info

  function drawFeed(v) {
    const changes = v.channels[state.channel]?.changes || [];
    feed.replaceChildren(h("div.card", h("h3", `Changes on ${state.channel}`),
      changes.length ? h("div.feed", changes.slice(0, 50).map((c) => h("div.feed-row",
        h("span.mono", `#${c.sequence}`), " ", h("b", c.storyId), " ", CHANGE_WORDS[c.type] || c.type,
        c.releaseId ? h("span.mono", ` ${c.fromReleaseId ? c.fromReleaseId + " → " : ""}${c.releaseId}`) : null,
        c.summary ? h("span.help", ` · ${c.summary.chaptersChanged} changed, ${c.summary.chaptersAdded} added${c.summary.textOnly ? " · text only" : ""} · ${fmt.bytes(c.summary.downloadBytes)}`) : null,
        h("span.help", ` · ${fmt.when(c.at)}`)))) : h("div.empty", "No changes on this channel yet.")));
  }

  function drawVerify() {
    const deep = h("input", { type: "checkbox" });
    const pub = h("input", { type: "checkbox" });
    const run = h("button.btn", { type: "button", onclick: async () => {
      run.disabled = true; run.textContent = "Checking…";
      try { state.verify = await api("/api/backend/verify", { method: "POST", body: { channel: state.channel, deep: deep.checked, public: pub.checked } }); }
      catch (err) { state.verify = { error: err }; }
      drawVerify();
    } }, `Verify ${state.channel}`);
    const r = state.verify;
    let result = null;
    if (r?.error) result = errorBox(r.error);
    else if (r) {
      result = h("div", h("p", h("span.tag", { class: r.healthy ? "good" : "warn" }, r.healthy ? "healthy" : "problems"),
          ` ${r.objects} objects · ${r.ok} ok · ${r.missing.length} missing · ${r.corrupted.length} corrupted`),
        ...[...r.missing, ...r.corrupted].slice(0, 12).map((x) => h("div.dir-warn.mono", `${x.state} ${x.key}`)),
        r.public ? h("p.help", r.public.ok ? "Public read: ok" : `Public read failed: ${r.public.error || (r.public.unreachable || []).length + " unreachable"}`) : null,
        (r.missing.length || r.corrupted.length) ? h("p.help", "Repair re-uploads them from local copies: ", h("span.mono", "assetforge backend repair --yes")) : null);
    }
    verifyBox.replaceChildren(h("div.card", h("h3", "Verify"),
      h("p.help", "Checks every object any listed release needs (reads only)."),
      h("div.actions", run, h("label.help", deep, " download and hash"), h("label.help", pub, " through the public URL")), result));
  }

  function drawAppInfo(v) {
    const url = v.channels[state.channel]?.index_url;
    const key = v.key;
    const row = (label, value, what) => h("div", { style: "margin:6px 0" }, h("div.lbl", label),
      value ? h("div", { style: "display:flex;gap:6px;align-items:center" }, h("code.mono", { style: "overflow-wrap:anywhere" }, value),
        h("button.btn.small", { type: "button", onclick: () => copy(value, `${what} copied`) }, "Copy")) : h("div.help", what === "URL"
        ? "Set ASSETFORGE_R2_PUBLIC_BASE to the bucket's public origin." : "Made on the first publish."));
    appInfo.replaceChildren(h("div.card", { style: "margin-top:16px" }, h("h3", "For the app team"),
      row(`Index URL (${state.channel})`, url, "URL"),
      row(key ? `Public key (keyId ${key.key_id})` : "Public key", key?.public_key, "Key"),
      h("p.help", "The full kit (schemas, samples, reference client, integration guide): ", h("span.mono", "assetforge backend client-kit --out DIR"))));
  }

  await load(false);  // the cached view first: the page opens instantly
  load(true);         // then the live one
  return {};
}
