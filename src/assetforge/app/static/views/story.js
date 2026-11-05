// Stories: write branching visual novels with any signed-in subscription (Claude, ChatGPT/Codex, Gemini) and any
// model it offers, improve them from your feedback, make their assets and play the demo — all from the app.
import { api, h, errorBox, toast, segmented, field, fmt, copy } from "/static/lib.js";

const TASK_LABEL = { write: "Writing", improve: "Improving from feedback", check: "Continuity check", refine: "Revising scene",
                     assets_plan: "Estimating assets", assets: "Making assets", export: "Exporting demo" };

export async function story(root, { rest, params }) {
  if (rest[0]) return detail(root, rest[0], params.get("tab") || "script");
  return list(root);
}

// ---------------------------------------------------------------------- writer picker (shared)

let providersPromise = null;
function loadProviders(refresh = false) {
  if (!providersPromise || refresh) providersPromise = api("/api/llm/providers" + (refresh ? "?refresh=1" : ""));
  return providersPromise;
}

/** Provider + model selects. Only usable (signed-in) subscriptions are selectable; the rest show how to sign in. */
function writerPicker(info, { provider, model }, onchange) {
  const usable = info.providers.filter((p) => p.usable);
  const provSel = h("select", { "aria-label": "Writer" },
    h("option", { value: "" }, `Auto (${info.recommended || "none signed in"})`),
    info.providers.map((p) => h("option", { value: p.name, disabled: !p.usable },
      `${p.name}${p.account && p.usable ? " — " + p.account : ""}${p.usable ? "" : p.installed ? " (not signed in)" : " (not installed)"}`)));
  provSel.value = provider || "";
  const modelSel = h("select", { "aria-label": "Model" });
  const note = h("div.help");
  function fillModels(keep) {
    const p = info.providers.find((x) => x.name === (provSel.value || info.recommended));
    modelSel.innerHTML = "";
    for (const m of p?.models || []) {
      modelSel.append(h("option", { value: m.id, title: m.description }, `${m.label}${m.worked ? " ✓" : ""}`));
    }
    modelSel.value = keep && [...modelSel.options].some((o) => o.value === keep) ? keep : "";
    note.textContent = p ? [p.billing === "subscription" ? `Uses your ${p.account || "subscription"} — no API key` : "Billed to your API key",
                            p.note, modelSel.selectedOptions[0]?.title].filter(Boolean).join(" · ") : "Sign in to claude, codex or gemini first.";
  }
  provSel.addEventListener("change", () => { fillModels(""); onchange?.({ provider: provSel.value, model: "" }); });
  modelSel.addEventListener("change", () => { fillModels(modelSel.value); onchange?.({ provider: provSel.value, model: modelSel.value }); });
  fillModels(model);
  const signin = info.providers.filter((p) => p.installed && p.signed_in === false && SIGN_IN[p.name])
    .map((p) => h("div.row.wrap", h("span.help", `${p.name} is installed but not signed in.`),
      h("button.btn.small", { type: "button", onclick: (e) => openSignIn(p.name, e.target.closest(".card") || el) }, `Sign in to ${SIGN_IN[p.name]}`)));
  const el = h("div.form.compact",
    field("Writer (your subscription)", provSel), field("Model", modelSel), h("div.wide", note, ...signin,
      usable.length ? null : h("div.help", "No signed-in writer found. Install and sign in to claude, codex or gemini, then refresh.")));
  el.value = () => ({ provider: provSel.value || null, model: modelSel.value || null });
  return el;
}

// ---------------------------------------------------------------------- sign in (link out, code back)

const SIGN_IN = { "claude-cli": "Claude", "codex-cli": "ChatGPT", "gemini-cli": "Google (Gemini)" };

/** Runs the provider's own login: shows its link, takes the code the user pastes back, confirms. */
async function openSignIn(provider, host) {
  host.querySelector(".signin")?.remove();
  const box = h("div.signin.sub-card");
  host.append(box);
  const status = h("div.help");
  const render = (s) => {
    box.replaceChildren(h("h3", `Sign in to ${SIGN_IN[provider]}`));
    if (s.state === "starting") box.append(h("p", h("span.spinner"), " Starting the sign-in…"));
    if (s.url && (s.state === "waiting" || s.state === "verifying")) {
      box.append(
        h("ol.signin-steps",
          h("li", "Open the sign-in page and approve with your account. ",
            h("button.btn.small", { type: "button", onclick: () => api(`/api/llm/login/${s.id}/open`, { method: "POST" }).catch((e) => toast(e.message)) }, "Open in browser"), " ",
            h("button.btn.small", { type: "button", onclick: () => copy(s.url, "Link copied") }, "Copy link")),
          s.needs_code
            ? h("li", "Copy the code the page shows and paste it here (within a few minutes):")
            : h("li", "Finish in the browser; it returns to the app by itself.")),
        h("input.mono.link", { type: "text", readOnly: true, value: s.url, onclick: (e) => e.target.select() }));
      if (s.needs_code) {
        const code = h("input.mono", { type: "text", placeholder: "Paste the code here", autocomplete: "off", spellcheck: false });
        const send = h("button.btn.primary", { type: "button", disabled: s.state === "verifying" }, s.state === "verifying" ? "Checking…" : "Sign in");
        const submit = async () => {
          if (!code.value.trim()) return;
          send.disabled = true;
          try { render((await api(`/api/llm/login/${s.id}/code`, { method: "POST", body: { code: code.value.trim() } })).login); }
          catch (err) { toast(err.message); send.disabled = false; }
        };
        send.addEventListener("click", submit);
        code.addEventListener("keydown", (e) => e.key === "Enter" && submit());
        box.append(h("div.actions", code, send));
      }
    }
    if (s.state === "done") box.append(h("p", "✓ Signed in", s.account ? ` (${s.account})` : "", ". Updating writers…"));
    if (s.state === "failed") box.append(errorBox({ code: "Sign-in failed", message: s.error || "Not signed in" }),
      h("div.actions", h("button.btn", { type: "button", onclick: () => openSignIn(provider, host) }, "Try again")));
    if (["starting", "waiting", "verifying"].includes(s.state))
      box.append(h("div.actions", h("button.btn.small", { type: "button", onclick: () => api(`/api/llm/login/${s.id}/cancel`, { method: "POST" }).then(() => box.remove()) }, "Cancel")));
    box.append(status);
  };
  let s;
  try { s = (await api("/api/llm/login", { method: "POST", body: { provider } })).login; }
  catch (err) { box.replaceChildren(errorBox(err)); return; }
  render(s);
  const timer = setInterval(async () => {
    if (!box.isConnected) { clearInterval(timer); return; }
    try {
      const next = (await api(`/api/llm/login/${s.id}`)).login;
      const changed = next.state !== s.state || next.url !== s.url;
      s = next;
      if (changed) render(s);
      if (["done", "failed", "cancelled"].includes(s.state)) {
        clearInterval(timer);
        if (s.state === "done") {
          toast(`Signed in to ${SIGN_IN[provider]}`);
          await loadProviders(true);
          setTimeout(() => window.dispatchEvent(new HashChangeEvent("hashchange")), 900);
        }
      }
    } catch { /* keep polling */ }
  }, 1500);
}

/** Every subscription and its sign-in state, with sign-in buttons (shown on the Stories page). */
function subscriptions(info) {
  return h("div.card", h("h3", "Your AI subscriptions"),
    h("div.subs", info.providers.filter((p) => p.billing === "subscription").map((p) => h("div.sub-row",
      h("span", h("b", SIGN_IN[p.name] || p.name), h("span.muted", ` ${p.name}`)),
      h("span", p.signed_in ? h("span.badge.approved", `signed in${p.account ? " · " + p.account : ""}`)
        : !p.installed ? h("span.badge", "not installed") : h("span.badge.rejected", "not signed in")),
      p.installed && SIGN_IN[p.name]
        ? h("button.btn.small", { type: "button", onclick: (e) => openSignIn(p.name, e.target.closest(".card")) }, p.signed_in ? "Sign in again" : "Sign in")
        : h("span.help", p.reason || "")))),
    h("div.help", "Signing in runs each company's own tool; your password and tokens never pass through AssetForge."));
}

// ---------------------------------------------------------------------- list + new story

async function list(root) {
  const [data, info] = await Promise.all([api("/api/stories"), loadProviders()]);
  root.append(h("div.page-head", h("div", h("h1", "Stories"),
    h("div.sub", "Branching visual novels written by your own AI subscription, with every sprite, background and sound made for them."))));
  const cards = data.stories.map((s) => h("a.card.story-card", { href: `#/story/${s.id}` },
    h("div.row", h("b", s.title), s.busy ? h("span.badge.running", "working") : s.playable ? h("span.badge.approved", "playable") : null),
    h("div.help", s.premise),
    h("div.help", `${s.status.scenes_written}/${s.status.scenes_planned || "?"} scenes · ${s.genre} · ${s.provider || "auto"}${s.model ? " " + s.model : ""}`)));
  root.append(h("div.story-grid", cards.length ? cards : h("div.empty.small", "No stories yet. Start one below.")),
    h("div.sub-card", subscriptions(info)));

  const title = h("input", { type: "text", placeholder: "The Fog Lantern" });
  const premise = h("textarea", { placeholder: "A lighthouse keeper's apprentice uncovers a smuggling ring hidden in a fog-bound harbour town." });
  const genre = h("input", { type: "text", value: "mystery" });
  const tone = h("input", { type: "text", placeholder: "atmospheric, warm, a little bittersweet" });
  const audience = h("input", { type: "text", value: "teen" });
  const scenes = h("input", { type: "number", min: 2, max: 60, value: 8 });
  const endings = h("input", { type: "number", min: 1, max: 6, value: 2 });
  const style = h("input", { type: "text", placeholder: "soft painted anime (art preset or any style)" });
  const writer = writerPicker(info, {}, null);
  const go = h("button.btn.primary", { type: "button" }, "Create and write");
  const out = h("div");
  go.addEventListener("click", async () => {
    out.innerHTML = "";
    go.disabled = true;
    try {
      const made = await api("/api/stories", { method: "POST", body: {
        title: title.value.trim(), premise: premise.value.trim(), genre: genre.value, tone: tone.value, audience: audience.value,
        scenes: +scenes.value, endings: +endings.value, art_style: style.value, ...writer.value() } });
      await api(`/api/stories/${made.id}/run`, { method: "POST", body: { action: "write" } });
      location.hash = `#/story/${made.id}`;
    } catch (err) { out.append(errorBox(err)); go.disabled = false; }
  });
  root.append(h("div.section-head", h("h2", "New story")),
    h("div.card", h("div.form",
      field("Title", title), field("Genre", genre), field("Audience / rating", audience),
      h("label.wide", h("span.lbl", "Premise"), premise),
      field("Tone", tone), field("Scenes", scenes), field("Endings", endings), field("Art style", style)),
      h("div.sub-card", writer), h("div.actions", go, h("span.help", "Writing runs in the background; you can leave this page.")), out));
  return {};
}

// ---------------------------------------------------------------------- one story

async function detail(root, sid, tab) {
  let data = await api(`/api/stories/${sid}`);
  const info = await loadProviders();
  let poller = null;
  const head = h("div");
  const status = h("div");
  const body = h("div");
  root.append(head, status, body);

  function renderHead() {
    const c = data.config;
    const writer = writerPicker(info, { provider: c.provider, model: c.model }, async (v) => {
      try { data.config = (await api(`/api/stories/${sid}/settings`, { method: "POST", body: v })).config; toast("Writer saved"); }
      catch (err) { toast(err.message); }
    });
    head.replaceChildren(
      h("div.page-head", h("div", h("a.help", { href: "#/story" }, "← Stories"), h("h1", c.title), h("div.sub", c.premise)),
        segmented([["script", "Script"], ["improve", "Improve"], ["assets", "Assets"], ["play", "Play"]], tab,
          (v) => (location.hash = `#/story/${sid}?tab=${v}`))),
      h("div.card", writer, h("div.help.mono", data.dir)));
  }

  function renderStatus() {
    const s = data.status;
    const task = data.task;
    const running = task && task.state === "running";
    const steps = Object.entries(s.steps).map(([k, v]) => h("span.tag", { class: v ? "done" : "" }, `${v ? "✓" : "·"} ${k}`));
    const complete = s.steps.script && s.scenes_written === s.scenes_planned;
    const writeBtn = complete ? h("a.btn", { href: `#/story/${sid}?tab=improve` }, "Improve with feedback")
      : h("button.btn.primary", { type: "button", disabled: running, onclick: () => run({ action: "write" }) },
          !s.steps.world ? "Write story" : "Continue writing");
    const parts = [h("div.row.wrap", ...steps, h("span.help", `${s.scenes_written}/${s.scenes_planned || "?"} scenes`), writeBtn)];
    if (running) parts.push(h("div.notice", h("span", h("span.spinner"), ` ${TASK_LABEL[task.action] || task.action}… ${task.elapsed ?? ""}s`),
      h("span.help", "Each model call is saved as it finishes, so nothing is lost if you leave.")));
    else if (task?.state === "failed") parts.push(errorBox(task.error || {}));
    else if (task?.state === "done") parts.push(h("div.help", `Last: ${TASK_LABEL[task.action] || task.action} finished.`));
    const errors = (data.issues || []).filter((i) => i.level === "error");
    if (errors.length) parts.push(h("div", errors.map((i) => h("span.issue.err", `${i.where}: ${i.message}`))));
    status.replaceChildren(h("div.card.sub-card", ...parts));
  }

  async function run(payload) {
    try {
      await api(`/api/stories/${sid}/run`, { method: "POST", body: payload });
      await refresh();
    } catch (err) { toast(err.message); }
  }

  async function refresh() {
    data = await api(`/api/stories/${sid}`);
    renderStatus();
    const running = data.task?.state === "running";
    if (running && !poller) poller = setInterval(async () => {
      try {
        const t = await api(`/api/story-tasks/${data.task.id}`);
        data.task = t.task; data.status = t.status || data.status;
        renderStatus();
        if (t.task.state !== "running") { clearInterval(poller); poller = null; await refresh(); renderBody(); }
      } catch { clearInterval(poller); poller = null; }
    }, 2000);
  }

  function renderBody() {
    body.innerHTML = "";
    ({ script: scriptTab, improve: improveTab, assets: assetsTab, play: playTab }[tab] || scriptTab)(body, data, sid, run);
  }

  renderHead();
  renderStatus();
  renderBody();
  await refresh();
  return { cleanup: () => poller && clearInterval(poller) };
}

// ---------------------------------------------------------------------- tabs

function scriptTab(root, data, sid, run) {
  if (data.world) root.append(h("div.section-head", h("h2", "World")), h("div.card",
    h("p", h("b", data.world.logline)), h("p.help", data.world.setting), h("p.help", h("b", "Art: "), data.world.art_direction),
    h("p.help", h("b", "Music: "), data.world.music_direction)));
  if (data.cast) root.append(h("div.section-head", h("h2", "Cast")), h("div.cols",
    data.cast.characters.map((c) => h("div.card", h("div.row", h("b", c.name), h("span.tag", c.role)), h("div.help", c.personality),
      h("div.help", h("i", c.look)), h("div", c.expressions.map((e) => h("span.tag", e)))))));
  if (!data.scenes) {
    root.append(h("div.empty.small", data.outline ? "Scenes are being written…" : "Press “Write story” to start."));
    return;
  }
  const names = Object.fromEntries((data.cast?.characters || []).map((c) => [c.id, c.name]));
  root.append(h("div.section-head", h("h2", `Script — ${data.scenes.length} scenes`)));
  for (const s of data.scenes) {
    const note = h("input", { type: "text", placeholder: "What should change in this scene?" });
    const lines = s.nodes.map((n) => {
      if (n.kind === "say") return h("div.vn-line", h("b", names[n.speaker] || n.speaker), n.expression ? h("span.muted", ` (${n.expression})`) : null, ": ", n.text);
      if (n.kind === "narrate") return h("div.vn-line.narr", n.text);
      if (n.kind === "choice") return h("div.vn-choice", n.choices.map((c) => h("div", `➜ ${c.text}`, h("span.muted", `  → ${c.goto}`))));
      if (n.kind === "jump") return h("div.vn-line.muted", `→ ${n.goto}`);
      if (n.kind === "ending") return h("div.vn-line", h("b", `ENDING: ${n.ending}`));
      if (n.kind === "cg" || n.kind === "sfx") return h("div.vn-line.muted", `[${n.kind}: ${n.cg || n.cue}] ${n.text || ""}`);
      return null;
    });
    root.append(h("details.section.vn-scene", h("summary", h("span", s.title), h("span.muted", `${s.id} · ${s.location} · ${s.time} · ${s.music}`)),
      h("div.vn-lines", lines),
      h("div.actions", note, h("button.btn.small", { type: "button", onclick: () => note.value.trim() && run({ action: "refine", scene: s.id, note: note.value.trim() }) }, "Revise scene"))));
  }
}

function improveTab(root, data, sid, run) {
  if (!data.scenes) { root.append(h("div.empty.small", "Write the story first.")); return; }
  const fb = h("textarea", { placeholder: "e.g. Tomas should matter more in the middle; the ending feels rushed; add a lighter, funny moment at the market." });
  const useCheck = h("input", { type: "checkbox", checked: !!data.check?.issues?.length });
  const scenesSel = h("select", { multiple: true, size: Math.min(6, data.scenes.length), "aria-label": "Limit to scenes" },
    data.scenes.map((s) => h("option", { value: s.id }, `${s.title} (${s.id})`)));
  root.append(h("div.card",
    h("h2", "Improve with feedback"),
    h("p.help", "An editor pass turns your notes into concrete per-scene revisions, then rewrites those scenes (old versions are kept as .back)."),
    h("div.form", h("label.wide", h("span.lbl", "Your feedback"), fb),
      h("label.check", useCheck, "Also fix the continuity issues"),
      field("Only these scenes (optional)", scenesSel)),
    h("div.actions",
      h("button.btn.primary", { type: "button", onclick: () => run({ action: "improve", feedback: fb.value, use_check: useCheck.checked,
        scenes: [...scenesSel.selectedOptions].map((o) => o.value) }) }, "Improve story"),
      h("button.btn", { type: "button", onclick: () => run({ action: "check" }) }, "Run continuity check"))));
  if (data.check) root.append(h("div.section-head", h("h2", "Continuity report")), h("div.card",
    h("p.help", data.check.summary),
    (data.check.issues || []).map((i) => h("div.vn-issue", h("span.issue", { class: i.severity === "high" ? "err" : "warn" }, i.severity),
      h("b", ` ${i.scene}: `), i.problem, i.suggestion ? h("div.help", `→ ${i.suggestion}`) : null))));
  if (data.feedback?.length) root.append(h("div.section-head", h("h2", "Feedback rounds")),
    ...data.feedback.slice().reverse().map((f) => h("div.card.sub-card", h("div.help", fmt.date ? fmt.date(f.at) : f.at),
      h("p", h("b", "You: "), f.feedback || "(continuity issues)"), h("p.help", f.summary),
      h("div", f.edits.map((e) => h("span.tag", { title: e.note }, `${f.revised.includes(e.scene) ? "✓" : "✗"} ${e.scene}`))))));
}

function assetsTab(root, data, sid, run) {
  if (!data.assets) { root.append(h("div.empty.small", "Write the story first.")); return; }
  const a = data.assets;
  const provider = h("select", h("option", { value: "auto" }, "auto"), h("option", { value: "openai" }, "OpenAI"), h("option", { value: "gemini" }, "Gemini"));
  const quality = h("select", ["auto", "low", "medium", "high"].map((q) => h("option", { value: q }, q)));
  const groups = ["chars", "bg", "cg", "ui", "audio"];
  const checks = Object.fromEntries(groups.map((g) => [g, h("input", { type: "checkbox", checked: true })]));
  const opts = () => ({ image_provider: provider.value, quality: quality.value, only: groups.filter((g) => checks[g].checked) });
  root.append(h("div.tiles", h("div.tile", h("div.label", "Assets made"), h("div.value", `${a.have}/${a.total}`),
    h("div.note", "missing ones play as placeholders"))),
    h("div.card.sub-card",
      h("p.help", "Sprites (one per expression, edited from the neutral one), backgrounds per time of day, event CGs, UI, music, ambience and sound effects. Images use your image API key; sound is free."),
      h("div.form.compact", field("Image provider", provider), field("Quality", quality),
        ...groups.map((g) => h("label.check", checks[g], { chars: "Characters", bg: "Backgrounds", cg: "CGs", ui: "UI", audio: "Music & sound" }[g]))),
      h("div.actions",
        h("button.btn", { type: "button", onclick: () => run({ action: "assets_plan", ...opts() }) }, "Estimate cost"),
        h("button.btn.primary", { type: "button", onclick: () => confirm("Generate the missing assets? Images are billed to your image API key.") && run({ action: "assets", ...opts() }) }, "Make missing assets"))));
  const result = data.task?.state === "done" && (data.task.action === "assets_plan" || data.task.action === "assets") ? data.task.result : null;
  if (result) {
    root.append(h("div.estimate", h("div", result.estimated_cost_usd != null ? h("span.cost", fmt.money ? fmt.money(result.estimated_cost_usd) : `$${result.estimated_cost_usd}`) : null,
      " ", Object.entries(result.counts).map(([k, v]) => h("span.tag", `${k}: ${v}`)))),
      h("div.table-wrap.sub-card", h("table", h("tr", h("th", "Asset"), h("th", "State"), h("th", "Note")),
        result.items.map((i) => h("tr", h("td", i.label), h("td", i.state), h("td.help", i.error?.message || i.reason || (i.estimated_cost_usd != null ? `$${i.estimated_cost_usd.toFixed(3)}` : "")))))));
  }
  if (a.missing.length) root.append(h("details.section", h("summary", h("span", `Missing (${a.missing.length})`)), h("div.mono", a.missing.map((k) => h("div", k)))));
}

function playTab(root, data, sid, run) {
  const frame = data.playable ? h("iframe.vn-frame", { src: `/play/${sid}/?t=${Date.now()}`, title: "Story demo", allow: "autoplay" }) : null;
  root.append(h("div.toolbar",
    h("button.btn", { type: "button", onclick: () => run({ action: "export" }) }, data.playable ? "Re-export demo" : "Export demo"),
    h("span.help", `Files: ${data.dir}/build — open index.html in any browser, or ship the folder.`)),
    frame || h("div.empty.small", data.scenes ? "Export the demo to play it here." : "Write the story first."));
}
