// Stories: write branching visual novels with your own AI subscription (Claude, ChatGPT/Codex, Gemini), watch them
// being written, read and play every chapter as soon as it exists, improve them from feedback, make their art.
// Progress comes from the story's files and the library's jobs, so work started from the CLI or by agents shows too.
import { api, h, errorBox, toast, segmented, field, fmt, copy, thumbUrl, playVariant, section } from "/static/lib.js";
import { directionForm, loadDirectionOptions } from "/static/views/direction.js";
import { workflowTab } from "/static/views/workflow.js";
import { charactersTab } from "/static/views/characters.js";
import { loadAI, modelSelect, pickerValue } from "/static/views/ai.js";

const TASK_LABEL = { write: "Writing", improve: "Improving from feedback", check: "Continuity check", refine: "Revising a chapter",
                     assets_plan: "Estimating art & sound", assets: "Making art & sound", export: "Exporting for a game" };
const STAGE = { idea: ["Draft", ""], world: ["Building the world", "running"], cast: ["Creating characters", "running"],
                writing: ["Writing", "running"], written: ["Ready to play", "approved"] };

export async function story(root, { rest, params }) {
  if (rest[0] === "new") return wizard(root);
  if (rest[0]) return detail(root, rest[0], params.get("tab") || "workflow", params.get("chapter"));
  return hub(root);
}

function elapsed(s) { return s == null ? "" : s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`; }

function cover(s, size = "") {
  if (s.cover) return h("img.cover" + size, { src: s.cover, alt: "" });
  let hash = 0;
  for (const ch of s.title) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const a = hash % 360, b = (a + 50) % 360;
  const initials = s.title.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return h("div.cover.gen" + size, { style: `background:linear-gradient(135deg,hsl(${a} 45% 32%),hsl(${b} 55% 18%))` }, initials);
}

function bar(done, total, label) {
  const pct = total ? Math.round((100 * done) / total) : 0;
  return h("div.pbar", { title: label || `${done}/${total}` }, h("div.pbar-fill", { style: `width:${pct}%` }));
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


// ---------------------------------------------------------------------- hub (catalog of every story)

async function hub(root) {
  const head = h("div.page-head",
    h("div", h("h1", "Stories"), h("div.sub", "Interactive visual novels written by your own AI subscription. Open one to watch it being written, read it, or play it.")),
    h("a.btn.primary.big", { href: "#/story/new" }, "+ New story"));
  const search = h("input", { type: "search", placeholder: "Search stories…", "aria-label": "Search stories" });
  let filter = "all";
  const seg = segmented([["all", "All"], ["active", "In progress"], ["playable", "Ready to play"]], filter, (v) => { filter = v; draw(); });
  const grid = h("div.story-grid");
  const subs = h("div.sub-card");
  root.append(head, h("div.toolbar", search, seg), grid, h("div.section-head", h("h2", "Your AI writers")), subs);
  search.addEventListener("input", () => draw());
  let data = { stories: [] };

  function draw() {
    const q = search.value.trim().toLowerCase();
    const items = data.stories.filter((s) =>
      (filter === "all" || (filter === "active" ? s.progress.stage !== "written" || s.busy : s.playable)) &&
      (!q || `${s.title} ${s.premise} ${s.genre}`.toLowerCase().includes(q)));
    grid.replaceChildren(...(items.length ? items.map(card) : [h("div.empty",
      data.stories.length ? "No stories match." : h("div", h("p", "No stories yet."), h("a.btn.primary", { href: "#/story/new" }, "Write your first story")))]));
  }

  function card(s) {
    const p = s.progress;
    const [done, total] = p.steps.chapters;
    const [stageLabel, stageClass] = s.busy ? ["Writing…", "running"] : (STAGE[p.stage] || [p.stage, ""]);
    const now = p.running[0];
    return h("div.card.story-card",
      h("a.story-cover", { href: `#/story/${s.id}`, "aria-label": `Open ${s.title}` }, cover(s)),
      h("div.story-body",
        h("div.row", h("a.story-title", { href: `#/story/${s.id}` }, s.title), h("span.badge", { class: stageClass }, stageLabel)),
        h("div.help.clamp2", s.logline),
        total ? h("div.story-progress", bar(done, total), h("span.help", `${done}/${total} chapters`)) : h("div.help", "Not planned yet"),
        now ? h("div.live", h("span.spinner"), " ", now.label, h("span.muted", ` · ${elapsed(now.seconds)}`)) : null,
        h("div.actions.tight",
          h("a.btn.small", { href: `#/story/${s.id}` }, "Open"),
          s.playable ? h("a.btn.small.primary", { href: `#/story/${s.id}?tab=play` }, "▶ Play") : null,
          h("span.muted.small", `${s.genre} · ${s.provider || "auto"}${s.model ? " " + s.model : ""}`))));
  }

  async function load() {
    data = await api("/api/stories");
    draw();
  }
  await load();
  loadProviders().then((info) => subs.replaceChildren(subscriptions(info))).catch((err) => subs.replaceChildren(errorBox(err)));
  const timer = setInterval(() => { if (data.stories.some((s) => s.busy)) load().catch(() => {}); }, 3000);
  return { cleanup: () => clearInterval(timer), refresh: load };
}

// ---------------------------------------------------------------------- new story wizard

// Example ideas deliberately span worlds and registers: none of them points at the operator's own country.
const IDEAS = [
  "A knight executed for a treason she did not commit wakes up three years earlier, the morning before the choice that doomed her.",
  "The lowest-ranked hunter in the city survives a gate collapse and comes out with a power the guild's rank scanners cannot read.",
  "A kitchen boy at a wizards' academy is the only person who can hear the dead headmaster, and the dead headmaster wants revenge.",
];

async function wizard(root) {
  const [info, dirInfo] = await Promise.all([loadProviders(), loadDirectionOptions()]);
  const st = { step: 1, premise: "", title: "", tone: "", audience: "mature", chapters: 10, endings: 3, direction: null };
  const box = h("div.card.wizard");
  root.append(h("div.page-head", h("div", h("a.help", { href: "#/story" }, "← Stories"), h("h1", "New story"),
    h("div.sub", "Four steps. Only the idea is required; pick a creative-direction preset and fine-tune anything you like."))), box);
  let writer = null;
  let form = null;
  const chip = (value, current, set) => h("button.chip-btn", { type: "button", class: value === current ? "on" : "", onclick: () => { set(value); render(); } }, value);

  function render() {
    const steps = ["Idea", "Direction", "Length", "Writer"].map((t, i) => h("span.wstep", { class: i + 1 === st.step ? "on" : i + 1 < st.step ? "done" : "" }, `${i + 1} ${t}`));
    const body = h("div.wbody");
    if (st.step === 1) {
      const premise = h("textarea", { rows: 4, value: st.premise, placeholder: "What is the story about? One or two sentences: who, what they want, what it costs." });
      premise.addEventListener("input", () => (st.premise = premise.value));
      const title = h("input", { type: "text", value: st.title, placeholder: "Optional — the writer names it during world-building" });
      title.addEventListener("input", () => (st.title = title.value));
      const tone = h("input", { type: "text", value: st.tone, placeholder: "Optional, e.g. tense, dry humour, slow-building dread" });
      tone.addEventListener("input", () => (st.tone = tone.value));
      body.append(h("label", h("span.lbl", "Your idea"), premise),
        h("div.help", "Need inspiration? ", ...IDEAS.map((idea, i) => h("button.linklike", { type: "button", onclick: () => { st.premise = idea; render(); } }, `Example ${i + 1}`))),
        h("label", h("span.lbl", "Title"), title),
        h("label", h("span.lbl", "Tone in your own words"), tone),
        h("div.lbl", "Rating"), h("div.chip-row", ["all ages", "teen", "mature"].map((a) => chip(a, st.audience, (v) => (st.audience = v)))),
        h("div.help", "The rating limits content. Who the story is for, where it is set and how it is told come next."));
      setTimeout(() => premise.focus(), 0);
    } else if (st.step === 2) {
      if (!form) {
        const first = dirInfo.presets.find((p) => p.name === "medieval-regression-power-fantasy") || dirInfo.presets[0];
        form = directionForm(dirInfo, st.direction || { preset: first?.name, ...(first?.direction || {}) }, { onChange: (v) => { st.direction = v; } });
      }
      body.append(h("div.help", "Everything here shapes the world, names, plot, chapters and art. Nothing is inferred from you: a global story is set where you say."),
        form, h("div.actions", h("button.btn.small", { type: "button", onclick: () => form.saveAsPreset() }, "Save as preset")));
    } else if (st.step === 3) {
      const range = h("input", { type: "range", min: 4, max: 20, value: st.chapters });
      const label = h("b", `${st.chapters} chapters`);
      range.addEventListener("input", () => { st.chapters = +range.value; label.textContent = `${st.chapters} chapters`; });
      body.append(h("div.lbl", "Chapters per playthrough"), h("div.row.wrap", range, label),
        h("div.help", "Each chapter is one scene of about 15–25 lines. Branches add a few extra chapters you only see on some routes."),
        h("div.lbl", "Endings"), h("div.chip-row", [1, 2, 3, 4].map((n) => chip(String(n), String(st.endings), (v) => (st.endings = +v)))));
    } else {
      writer = writerPicker(info, {}, null);
      body.append(writer, h("div.help", "You can change the writer at any time; finished chapters stay as they are."));
    }
    const back = h("button.btn", { type: "button", disabled: st.step === 1, onclick: () => { st.step--; render(); } }, "Back");
    const next = st.step < 4
      ? h("button.btn.primary", { type: "button", onclick: () => {
          if (st.step === 1 && st.premise.trim().length < 10) return toast("Describe the idea in a sentence or two");
          st.step++; render(); } }, "Next")
      : h("button.btn.primary", { type: "button", onclick: create }, "Create and start writing");
    box.replaceChildren(h("div.wsteps", steps), body, h("div.actions", back, next));
  }

  async function create(e) {
    const btn = e.target;
    btn.disabled = true;
    // no title: a working title until the writer names the story during world-building
    const title = st.title.trim() || "Untitled story";
    try {
      const dir = form ? form.value() : (st.direction || { preset: "medieval-regression-power-fantasy", direction: {} });
      const made = await api("/api/stories", { method: "POST", body: {
        title, premise: st.premise.trim(), genre: (dir.direction?.genre || "drama").replace(/-/g, " "), tone: st.tone, audience: st.audience,
        preset: dir.preset, direction: dir.direction,
        scenes: st.chapters + st.endings, endings: st.endings, title_from_writer: !st.title.trim(),
        ...(writer ? writer.value() : {}) } });
      await api(`/api/stories/${made.id}/run`, { method: "POST", body: { action: "write" } });
      location.hash = `#/story/${made.id}`;
    } catch (err) { btn.disabled = false; box.append(errorBox(err)); }
  }
  render();
  return {};
}

// ---------------------------------------------------------------------- one story

async function detail(root, sid, tab, chapterParam) {
  let data = await api(`/api/stories/${sid}`);
  const head = h("div");
  const tracker = h("div");
  const body = h("div");
  root.append(head, tracker, body);
  let timer = null;
  let selected = chapterParam || null;

  function primaryAction() {
    const p = data.progress;
    const task = data.task?.state === "running" ? data.task : null;
    if (p.active || task) return h("span.btn.big.ghost", h("span.spinner"), " ", task ? (TASK_LABEL[task.action] || task.action) : "Writing…",
      p.eta_seconds ? h("span.muted", ` · about ${elapsed(p.eta_seconds)} left`) : null);
    if (!data.progress.steps.world) return h("button.btn.primary.big", { type: "button", onclick: () => run({ action: "write" }) }, "Start writing");
    if (p.steps.chapters[0] < p.steps.chapters[1]) return h("button.btn.primary.big", { type: "button", onclick: () => run({ action: "write" }) }, "Continue writing");
    return h("a.btn.primary.big", { href: `#/story/${sid}?tab=play` }, "▶ Play");
  }

  function renderHead() {
    const c = data.config;
    window.afCrumb?.(c.title);
    head.replaceChildren(
      h("a.help", { href: "#/story" }, "← All stories"),
      h("div.story-head",
        cover({ title: c.title, cover: (data.progress && data.cover) || null }, ".lg"),
        h("div.story-head-text", h("h1", c.title), h("div.sub", data.world?.logline || c.premise),
          h("div.help", [c.genre, c.audience, c.art_style, `${c.provider || "Fable-managed writer"}${c.model ? " · " + c.model : ""}`].filter(Boolean).join(" · ")),
          data.workflow ? h("div.story-activity", h("span.act-dot", { class: data.workflow.activity.title.startsWith("Working") ? "on" : data.workflow.needs_you.length ? "wait" : "" }),
            h("b", data.workflow.activity.title), h("span.help", ` · ${data.workflow.activity.detail}`),
            data.workflow.needs_you.length ? h("a.tag.warn", { href: `#/story/${sid}?tab=workflow` }, `${data.workflow.needs_you.length} waiting for you`) : null) : null),
        h("div.story-head-action", primaryAction())),
      h("div.tabs-row", segmented([["workflow", "Workflow"], ["characters", "Characters"], ["dynamics", "Dynamics"], ["chapters", "Chapters"], ["play", "▶ Play"], ["assets", "Assets"], ["improve", "Improve"], ["settings", "Settings"]], tab,
        (v) => (location.hash = `#/story/${sid}?tab=${v}`))));
  }

  function renderTracker() {
    const p = data.progress;
    const [done, total] = p.steps.chapters;
    const step = (label, ok, extra) => h("span.tstep", { class: ok ? "done" : "" }, ok ? "✓ " : "○ ", label, extra || "");
    const art = p.steps.assets;
    const items = [step("World", p.steps.world), step("Characters", p.steps.cast), step("Plan", p.steps.outline),
      h("span.tstep.grow", { class: total && done === total ? "done" : "" }, `Chapters ${done}/${total || "?"}`, total ? bar(done, total) : null),
      art ? step("Art & sound", art[0] === art[1], ` ${art[0]}/${art[1]}`) : step("Art & sound", false)];
    const live = p.running.map((r) => h("div.live", h("span.spinner"), " ", r.label, h("span.muted", ` · ${elapsed(r.seconds)}`)));
    const task = data.task;
    if (task?.state === "running" && !p.running.length) live.push(h("div.live", h("span.spinner"), " ", TASK_LABEL[task.action] || task.action, h("span.muted", ` · ${elapsed(task.elapsed)}`)));
    if (task?.state === "failed") live.push(errorBox(task.error || {}));
    const who = (f) => f.chosen_by === "managed" ? "Fable chose" : f.chosen_by === "override" ? "you chose" : f.chosen_by ? `${f.chosen_by} plan` : "";
    const feed = p.feed.map((f) => h("div.feed-row", h("span", { class: f.status === "failed" ? "bad" : "good" }, f.status === "failed" ? "✗" : "✓"), " ", f.label,
      h("span.muted", ` · ${f.seconds}s · ${fmt.date ? fmt.date(f.at) : f.at}${f.provider ? " · " + f.provider : ""}${f.model ? " " + f.model : ""}${f.billing === "api" ? " (API, billed)" : f.billing ? " (subscription)" : ""}${who(f) ? " · " + who(f) : ""}`),
      f.fallbacks?.length ? h("div.help", `Fell back: ${f.fallbacks.map((x) => `${x.provider} (${x.kind})`).join(", ")} → ${f.provider}`) : null,
      f.error ? h("div.help", f.error) : null));
    tracker.replaceChildren(h("div.card.tracker", h("div.tsteps", items), ...live,
      feed.length ? h("details.feed", h("summary.help", "Activity"), feed) : null));
  }

  async function run(payload) {
    try { await api(`/api/stories/${sid}/run`, { method: "POST", body: payload }); await refresh(true); }
    catch (err) { toast(err.message); }
  }

  let lastSignature = "";
  async function refresh(force = false) {
    data = await api(`/api/stories/${sid}`);
    renderHead();
    renderTracker();
    const signature = JSON.stringify([data.progress.chapters.map((c) => c.state), data.task?.state, data.workflow?.current, data.workflow?.needs_you?.length]);
    if (force || signature !== lastSignature) { lastSignature = signature; if (tab === "chapters" || tab === "workflow" || tab === "characters") renderBody(); }
    const busy = data.progress.active || data.task?.state === "running";
    clearTimeout(timer);
    timer = setTimeout(() => refresh().catch(() => {}), busy ? 2500 : 15000);
  }

  function renderBody() {
    const scroll = body.querySelector(".reader")?.scrollTop;
    body.innerHTML = "";
    const views = { workflow: (r, d, s, f) => workflowTab(r, d, s, f, refresh), characters: (r, d, s, f) => charactersTab(r, d, s, f, refresh),
                    chapters: chaptersTab, play: playTab, improve: improveTab, assets: storyAssetsTab, settings: settingsTab, dynamics: dynamicsTab };
    (views[tab] || workflowTab)(body, data, sid, run, { selected, select: (id) => { selected = id; history.replaceState(null, "", `#/story/${sid}?tab=chapters&chapter=${id}`); renderBody(); } });
    if (scroll) body.querySelector(".reader") && (body.querySelector(".reader").scrollTop = scroll);
  }

  renderHead();
  renderTracker();
  lastSignature = JSON.stringify([data.progress.chapters.map((c) => c.state), data.task?.state]);
  renderBody();
  timer = setTimeout(() => refresh().catch(() => {}), 2500);
  return { cleanup: () => clearTimeout(timer), refresh: () => refresh() };
}

// ---------------------------------------------------------------------- chapters: rail + reader

function chaptersTab(root, data, sid, run, { selected, select }) {
  const chapters = data.progress.chapters;
  if (!chapters.length) {
    const working = data.progress.active || data.task?.state === "running";
    root.append(h("div.empty", working ? h("div", h("span.spinner"), " ", data.progress.running[0]?.label || "Getting started",
      "… chapters will appear here one by one as they're written.")
      : h("div", h("p", "Nothing written yet."), h("button.btn.primary", { type: "button", onclick: () => run({ action: "write" }) }, "Start writing"))));
    if (data.world) root.append(worldCard(data));
    return;
  }
  const current = chapters.find((c) => c.id === selected) || chapters.find((c) => c.state === "written") || chapters[0];
  const icon = { written: "✓", writing: "⟳", revising: "⟳", planned: "○" };
  const rail = h("div.rail", chapters.map((c) => h("button.rail-item", {
    type: "button", class: `${c.state}${c.id === current.id ? " on" : ""}`, onclick: () => select(c.id) },
    h("span.rail-num", c.number), h("span.rail-text", h("b", c.title), h("span.help", `${c.location.replace(/_/g, " ")} · ${c.time}${c.ending ? " · ending" : ""}`)),
    h("span.rail-icon", { title: c.state }, c.state === "writing" || c.state === "revising" ? h("span.spinner") : icon[c.state]))));
  root.append(h("div.chapters", rail, h("div.reader", reader(current, data, sid, run, select))));
  if (data.world) root.append(worldCard(data));
}

function reader(c, data, sid, run, select) {
  const titles = Object.fromEntries(data.progress.chapters.map((x) => [x.id, x]));
  const head = h("div.reader-head", h("div", h("div.help", `Chapter ${c.number}`), h("h2", c.title)),
    c.state === "written" || c.state === "revising"
      ? h("a.btn.primary", { href: `#/story/${sid}?tab=play&chapter=${c.id}` }, "▶ Play from here") : null);
  const scene = data.written?.[c.id];
  if (!scene) return h("div", head, h("div.planned-card",
    c.state === "writing" ? h("p", h("span.spinner"), " Being written right now…") : h("p.help", "Not written yet. This is the plan:"),
    h("p", c.summary), c.choices.length ? h("div.help", "Ends with a choice: ", c.choices.join(" / ")) : null));
  const names = Object.fromEntries((data.cast?.characters || []).map((ch) => [ch.id, ch.name]));
  const locName = Object.fromEntries((data.cast?.locations || []).map((l) => [l.id, l.name]));
  const cond = (cs) => (cs || []).map((x) => `${x.var} ${x.op} ${x.value}`).join(" and ");
  const lines = scene.nodes.map((n) => {
    const line = nodeLine(n);
    return line && n.when?.length ? h("div.vn-when", h("div.help", `Only if ${cond(n.when)}`), line) : line;
  });
  function nodeLine(n) {
    if (n.kind === "choice" && !n.choices.some((ch) => ch.goto)) return h("div.vn-choice", h("div.help", "Choice (the chapter carries on)"),
      n.choices.map((ch) => h("div.vn-option", h("div", `➜ ${ch.text}`, ch.require ? h("span.muted", `  (only if ${cond([ch.require])})`) : null),
        (ch.reaction || []).map((r) => r.speaker ? h("div.vn-line", h("b.speaker", names[r.speaker] || r.speaker), h("div", r.text)) : h("div.vn-line.narr", r.text)))));
    if (n.kind === "place") return h("div.vn-stage", `📍 ${locName[n.location] || n.location}${n.time ? ` · ${n.time}` : ""}${n.text ? ` — ${n.text}` : ""}`);
    if (n.kind === "music") return h("div.vn-stage", `♪ ${n.music}`);
    if (n.kind === "branch") return h("div.vn-choice", h("div.help", "Goes on depending on earlier choices"),
      [...n.branches.map((b) => h("button.linklike", { type: "button", onclick: () => select(b.goto) }, `If ${cond(b.when)} → ${titles[b.goto]?.title || b.goto}`)),
       h("button.linklike", { type: "button", onclick: () => select(n.goto) }, `Otherwise → ${titles[n.goto]?.title || n.goto}`)]);
    if (n.kind === "say") return h("div.vn-line", h("b.speaker", names[n.speaker] || n.speaker), n.expression ? h("span.expr", n.expression) : null, h("div", n.text));
    if (n.kind === "narrate") return h("div.vn-line.narr", n.text);
    if (n.kind === "choice") return h("div.vn-choice", h("div.help", "Choice"), n.choices.map((ch) =>
      h("button.linklike", { type: "button", onclick: () => select(ch.goto) }, `➜ ${ch.text}`, h("span.muted", `  → ${titles[ch.goto]?.title || ch.goto}`))));
    if (n.kind === "jump") return h("div.vn-next", h("button.linklike", { type: "button", onclick: () => select(n.goto) }, `Next: ${titles[n.goto]?.title || n.goto} →`));
    if (n.kind === "ending") return h("div.vn-end", `THE END — ${(data.outline?.endings || []).find((e) => e.id === n.ending)?.title || n.ending}`);
    if (n.kind === "cg") return h("div.vn-stage", `🖼 ${n.text || n.cg}`);
    if (n.kind === "sfx") return h("div.vn-stage", `🔊 ${n.text || n.cue}`);
    if (n.kind === "show") return h("div.vn-stage", `${names[n.character] || n.character} enters${n.expression ? ` (${n.expression})` : ""}`);
    if (n.kind === "hide") return h("div.vn-stage", `${names[n.character] || n.character} leaves`);
    return null;
  }
  const audioPanel = h("div.card.audio-panel");
  sceneAudioPanel(audioPanel, data, sid, c.id, run);
  const note = h("input", { type: "text", placeholder: "What should change in this chapter?" });
  return h("div", head, h("div.vn-lines.big", lines),
    h("div.actions", note, h("button.btn", { type: "button", onclick: () => note.value.trim() && run({ action: "refine", scene: c.id, note: note.value.trim() }) }, "Revise chapter")),
    audioPanel);
}

// ---------------------------------------------------------------------- scene audio panel

const CUE_KIND = { ambience: "Ambience", music: "Music", sfx: "Sound", accent: "Accent", silence: "Silence", stop: "Stop" };
const LIB_STATUS = { needs_generation: "needs generation", needs_review: "generated · needs review", approved: "approved", revision: "requires revision" };

/** The Studio takes the cue as a brief: what it is for, where and when it plays, under the story's project and key. */
function studioLink(c, v, sid) {
  const parts = [`${c.kind === "music" ? "Music" : "Ambience"} for a visual-novel chapter: ${c.what}.`];
  if (c.purpose) parts.push(`Purpose: ${c.purpose}.`);
  if (v.plan?.summary) parts.push(`Chapter: ${v.plan.summary}`);
  const q = new URLSearchParams({ kind: c.kind, brief: parts.join(" "), project: v.project || `vn-${sid}`, key: c.asset_key, story: sid });
  return `#/audio?${q}`;
}

function sceneAudioPanel(root, data, sid, sceneId, run) {
  root.replaceChildren(h("h3", "Audio direction"), h("div.help", h("span.spinner"), " Loading the audio plan…"));
  let picker = null;
  loadAI().then((info) => { picker = modelSelect(info.providers, {}); }).catch(() => {});
  api(`/api/stories/${sid}/audio/${sceneId}`).then(draw).catch((err) => root.replaceChildren(h("h3", "Audio direction"), errorBox(err)));

  async function post(body) {
    try { const v = await api(`/api/stories/${sid}/audio/${sceneId}`, { method: "POST", body }); draw(v); }
    catch (err) { toast(err.message); }
  }
  function draw(v) {
    const lineText = Object.fromEntries(v.lines.map((l) => [l.i, l.text]));
    const head = h("div.ap-head", h("h3", "Audio direction"),
      h("div.actions",
        h("button.btn.small", { type: "button", onclick: () => run({ action: "audio_plan", scene: sceneId, ...(picker ? pickerValue(picker) : {}) }) }, v.plan ? "Plan again" : "Plan the soundscape"),
        v.plan ? h("button.btn.small.primary", { type: "button", onclick: () => post({ action: "apply" }) }, "Apply to chapter") : null,
        v.plan ? h("button.btn.small", { type: "button", onclick: () => run({ action: "audio_make", scene: sceneId }) }, "Make missing sounds") : null,
        v.applied_nodes ? h("a.btn.small", { href: `#/story/${sid}?tab=play&chapter=${sceneId}` }, "▶ Preview with audio") : null));
    if (!v.plan) { root.replaceChildren(head, h("p.help", "No audio plan yet. Fable reads the chapter, the sound profile and the catalog, then proposes ambience, music, sound events and silence with a purpose each.")); return; }
    const p = v.plan;
    const strip = h("div.ap-strip", v.intensity.map((s) => h("div.ap-col", { title: `line ${s.i}${s.silent ? " · silence" : ""}${s.events.length ? " · " + s.events.map((e) => e.what).join(", ") : ""}` },
      h("div.ap-music", { style: `height:${Math.round(s.music * 100)}%` }), h("div.ap-amb", { style: `height:${Math.round(s.ambience * 60)}%` }),
      s.events.length ? h("div.ap-ev", { class: s.events.some((e) => e.kind === "accent") ? "accent" : "" }) : null)));
    const review = v.review ? h("div.ap-review", h("span.help", `Profile budget for ~${v.review.words} words: music ${v.review.budget.music}, sfx ${v.review.budget.sfx}, accents ${v.review.budget.accent}. `),
      ...v.review.issues.map((i) => h("div.dir-warn", `${i.level === "error" ? "✗" : "⚠"} ${i.message}`)),
      v.review.issues.length ? null : h("span.good", "No review warnings")) : null;
    const rows = v.cues.map((c) => {
      const play = c.asset?.variant_id ? h("button.sa-play", { type: "button", title: "Preview", onclick: (ev) => { ev.stopPropagation(); playVariant(c.asset.variant_id, ev.currentTarget); } }, "▶") : h("span.sa-play.off", "·");
      const where = c.at === 0 ? "before the first line" : `line ${c.at}: “${(lineText[c.at] || "").slice(0, 70)}”`;
      const status = c.library_status ? h("span.sa-status", { class: c.library_status === "approved" ? "approved" : c.library_status === "needs_review" ? "candidate" : "missing" }, LIB_STATUS[c.library_status] || c.library_status)
        : h("span.sa-status", { class: "planned" }, c.kind === "silence" || c.kind === "stop" ? "no asset needed" : "planned");
      const atInput = h("input", { type: "number", min: 0, max: v.lines.length, value: c.at, title: "Line it plays with" });
      atInput.addEventListener("change", () => post({ action: "update", cue: c.id, patch: { at: +atInput.value } }));
      const purpose = h("input", { type: "text", value: c.purpose, title: "Purpose" });
      purpose.addEventListener("change", () => post({ action: "update", cue: c.id, patch: { purpose: purpose.value } }));
      return h("div.ap-cue", { class: c.status },
        play,
        h("div.ap-main",
          h("div", h("b", CUE_KIND[c.kind] || c.kind), ` · ${c.what}`, c.loop ? h("span.tag", "loops") : null, c.when?.length ? h("span.tag", `only if ${c.when.map((w) => `${w.var} ${w.op} ${w.value}`).join(" & ")}`) : null, " ", status),
          c.text ? h("div.help", c.text) : null,
          h("div.ap-fields", h("label", h("span.lbl", "Why"), purpose), h("label", h("span.lbl", "At"), atInput), h("span.help", where))),
        h("div.ap-actions",
          c.asset && c.library_status !== "approved" ? h("button.btn.small.primary", { type: "button", onclick: () => post({ action: "approve_asset", cue: c.id }) }, "Approve sound") : null,
          c.asset ? h("button.btn.small", { type: "button", onclick: () => post({ action: "regenerate", cue: c.id }) }, "Regenerate") : null,
          c.asset ? h("button.btn.small", { type: "button", onclick: () => post({ action: "reject_asset", cue: c.id }) }, "Reject sound") : null,
          h("button.btn.small", { type: "button", onclick: () => { const w = window.prompt("Replace with (recipe, catalog scene, mood or a description)", c.what); if (w) post({ action: "update", cue: c.id, patch: { what: w, status: "planned" } }); } }, "Replace"),
          h("button.btn.small", { type: "button", onclick: () => post({ action: "remove", cue: c.id }) }, "Remove"),
          (c.kind === "music" || c.kind === "ambience") && c.asset_key
            ? h("a.btn.small", { href: studioLink(c, v, sid), title: "Direct, render and compare candidates for this cue in the Music & Ambience Studio" }, "Open in Studio") : null));
    });
    const add = h("button.btn.small", { type: "button", onclick: () => {
      const kind = window.prompt("Cue kind: ambience, music, sfx, accent, silence or stop", "sfx"); if (!kind) return;
      const what = window.prompt(kind === "music" ? "Mood" : kind === "ambience" ? "Catalog scene or location id" : kind === "silence" ? "music" : "Recipe or description", ""); if (what === null) return;
      const at = window.prompt("Line number it plays with (0 = before the first line)", "0"); if (at === null) return;
      const purpose = window.prompt("Why is this sound here?", ""); if (!purpose) return;
      post({ action: "add", cue_data: { id: `${kind}_${Date.now().toString(36).slice(-4)}`, kind, what: what || "music", purpose, at: +at, text: kind === "sfx" ? what : "" } }); } }, "+ Add a cue");
    root.replaceChildren(head,
      h("p.help", h("b", p.environment), p.mood ? ` · ${p.mood}` : "", h("br"), p.summary),
      h("div.lbl", "Audio intensity along the chapter (music height, ambience, events; dark = silence)"), strip, review,
      h("div.ap-cues", rows), h("div.actions", add),
      p.applied_at ? h("div.help", `Applied to the chapter ${new Date(p.applied_at).toLocaleString()} (${v.applied_nodes} cue nodes).`) : h("div.help", "Not applied yet: the chapter plays without these cues until you apply them."),
      picker ? field("Model for the next plan", picker) : null);
  }
}

function worldCard(data) {
  const w = data.world;
  return h("details.section", h("summary", h("span", "World & characters")),
    h("div.card", h("p", h("b", w.logline)), h("p.help", w.setting), h("p.help", h("b", "Look: "), w.art_direction), h("p.help", h("b", "Music: "), w.music_direction)),
    data.cast ? h("div.cols.sub-card", data.cast.characters.map((c) => h("div.card", h("div.row", h("b", c.name), h("span.tag", c.role)),
      h("div.help", c.personality), h("div.help", h("i", c.look)), h("div", c.expressions.map((e) => h("span.tag", e)))))) : null);
}

// ---------------------------------------------------------------------- improve, art & sound, play, settings

function improveTab(root, data, sid, run) {
  if (!data.scenes) { root.append(h("div.empty.small", "Write the story first.")); return; }
  const fb = h("textarea", { placeholder: "e.g. Tomas should matter more in the middle; the ending feels rushed; add a lighter, funny moment at the market." });
  const useCheck = h("input", { type: "checkbox", checked: !!data.check?.issues?.length });
  const scenesSel = h("select", { multiple: true, size: Math.min(6, data.scenes.length), "aria-label": "Limit to scenes" },
    data.scenes.map((s) => h("option", { value: s.id }, `${s.title} (${s.id})`)));
  const pickerBox = h("div");
  let picker = null;
  loadAI().then((info) => { picker = modelSelect(info.providers, {}); pickerBox.replaceChildren(field("Model for this run", picker, "Leave on managed, or pick e.g. Opus for an independent evaluation.")); }).catch(() => {});
  const withModel = (body) => ({ ...body, ...(picker ? pickerValue(picker) : {}) });
  const altScene = h("select", data.scenes.map((s) => h("option", { value: s.id }, `${s.title} (${s.id})`)));
  const altNote = h("input", { type: "text", placeholder: "Optional angle, e.g. a darker opening" });
  root.append(h("div.card",
    h("h2", "Improve with feedback"),
    h("p.help", "An editor pass turns your notes into concrete per-scene revisions, then rewrites those scenes (old versions are kept as .back)."),
    h("div.form", h("label.wide", h("span.lbl", "Your feedback"), fb),
      h("label.check", useCheck, "Also fix the continuity issues"),
      field("Only these scenes (optional)", scenesSel), pickerBox),
    h("div.actions",
      h("button.btn.primary", { type: "button", onclick: () => run(withModel({ action: "improve", feedback: fb.value, use_check: useCheck.checked,
        scenes: [...scenesSel.selectedOptions].map((o) => o.value) })) }, "Improve story"),
      h("button.btn", { type: "button", onclick: () => run(withModel({ action: "check" })) }, "Re-evaluate continuity with this model"))),
    h("div.card.sub-card", h("h3", "Alternatives for one chapter"),
      h("p.help", "Writes three alternative versions with different angles; nothing changes until you choose one (the current version is kept as a backup)."),
      h("div.form", field("Chapter", altScene), field("Angle", altNote)),
      h("div.actions", h("button.btn", { type: "button", onclick: () => run(withModel({ action: "alternatives", scene: altScene.value, n: 3, note: altNote.value })) }, "Write 3 alternatives"),
        h("button.btn", { type: "button", onclick: () => showAlternatives(root, sid, altScene.value, run) }, "Show alternatives"))));
  if (data.check) root.append(h("div.section-head", h("h2", "Continuity report")), h("div.card",
    h("p.help", data.check.summary),
    (data.check.issues || []).map((i) => h("div.vn-issue", h("span.issue", { class: i.severity === "high" ? "err" : "warn" }, i.severity),
      h("b", ` ${i.scene}: `), i.problem, i.suggestion ? h("div.help", `→ ${i.suggestion}`) : null))));
  if (data.feedback?.length) root.append(h("div.section-head", h("h2", "Feedback rounds")),
    ...data.feedback.slice().reverse().map((f) => h("div.card.sub-card", h("div.help", fmt.date ? fmt.date(f.at) : f.at),
      h("p", h("b", "You: "), f.feedback || "(continuity issues)"), h("p.help", f.summary),
      h("div", f.edits.map((e) => h("span.tag", { title: e.note }, `${f.revised.includes(e.scene) ? "✓" : "✗"} ${e.scene}`))))));
}

async function showAlternatives(root, sid, scene, run) {
  let box = root.querySelector(".alts");
  if (!box) { box = h("div.card.alts"); root.append(box); }
  box.replaceChildren(h("span.spinner"));
  try {
    const r = await api(`/api/stories/${sid}/alternatives/${scene}`);
    if (!r.items?.length) { box.replaceChildren(h("p.help", "No alternatives written for this chapter yet.")); return; }
    box.replaceChildren(h("h3", `Alternatives for ${scene}`), ...r.items.map((a) => h("div.alt",
      h("div", h("b", a.id), ` · ${a.angle}`, h("span.help", ` · ${a.words} words, ${a.choices} choices${a.ok ? "" : " · fails a structural check"} · ${a.provider || ""} ${a.model || ""}`)),
      h("div.help", (a.scene?.nodes || []).filter((n) => n.kind === "narrate" || n.kind === "say").slice(0, 4).map((n) => n.text).join(" / ")),
      h("div.actions", h("button.btn.small.primary", { type: "button", onclick: async () => {
        try { await api(`/api/stories/${sid}/alternatives/${scene}/${a.id}`, { method: "POST", body: {} }); toast(`Chapter replaced with ${a.id}`); location.reload(); }
        catch (err) { toast(err.message); } } }, "Use this version")))));
  } catch (err) { box.replaceChildren(errorBox(err)); }
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


function playTab(root, data, sid, run, { selected } = {}) {
  if (!data.progress.playable_chapters) {
    root.append(h("div.empty", data.progress.active ? h("div", h("span.spinner"), " The first chapter is being written; you can play it as soon as it's done.")
      : "Write the story first."));
    return;
  }
  const chapter = new URLSearchParams(location.hash.split("?")[1] || "").get("chapter") || selected;
  const src = () => `/preview/${sid}/${chapter ? `?scene=${encodeURIComponent(chapter)}&` : "?"}t=${Date.now()}`;
  const frame = h("iframe.vn-frame", { src: src(), title: "Story player", allow: "autoplay" });
  const [done, total] = data.progress.steps.chapters;
  root.append(
    h("div.toolbar",
      h("button.btn", { type: "button", onclick: () => (frame.src = src()) }, "↻ Reload with latest chapters"),
      chapter ? h("a.btn", { href: `#/story/${sid}?tab=play` }, "Start from the beginning") : null,
      h("span.help", done < total ? `${done} of ${total} chapters written — unwritten chapters play as their plan.` : "Every chapter is written.")),
    frame,
    h("p.help", "Click or press Space to advance · 1–9 picks a choice · H shows history. Art and sound appear here as soon as they're made."));
}

function settingsTab(root, data, sid, run) {
  const c = data.config;
  const info = loadProviders();
  const writerBox = h("div", h("span.spinner"));
  info.then((i) => writerBox.replaceChildren(writerPicker(i, { provider: c.provider, model: c.model }, async (v) => {
    try { await api(`/api/stories/${sid}/settings`, { method: "POST", body: v }); toast("Writer saved"); }
    catch (err) { toast(err.message); } })));
  const dirBox = h("div");
  const dirSummary = data.direction ? h("p", data.direction.summary) : h("p.help", "This story was made before creative direction existed; it keeps its original behaviour unless you set one.");
  const dirWarnings = h("div", ...((data.direction?.warnings || []).map((w) => h("div.dir-warn", "⚠ ", w.message))));
  const editBtn = h("button.btn", { type: "button", onclick: async () => {
    const dirInfo = await loadDirectionOptions();
    const form = directionForm(dirInfo, c.direction || {}, { compact: true });
    const save = async (confirm = false) => {
      const v = form.value();
      try {
        const r = await api(`/api/stories/${sid}/settings`, { method: "POST", body: { direction: v.direction, preset: v.preset, confirm } });
        if (r.needs_confirm) {
          const ok = window.confirm("Changing the direction leaves parts of the story as they are:\n\n" + r.impact.join("\n") + "\n\nApply anyway? Only new or rewritten steps follow the new direction.");
          if (ok) return save(true);
          return;
        }
        toast("Creative direction saved"); location.reload();
      } catch (err) { toast(err.message); }
    };
    dirBox.replaceChildren(form, h("div.actions", h("button.btn.primary", { type: "button", onclick: () => save(false) }, "Save direction"),
      h("button.btn", { type: "button", onclick: () => form.saveAsPreset() }, "Save as preset"),
      h("button.btn", { type: "button", onclick: () => dirBox.replaceChildren() }, "Cancel")));
  } }, c.direction ? "Edit creative direction" : "Set a creative direction");
  const names = data.names ? h("details.section", h("summary", h("span", "Naming guide"), h("span.muted", data.names.name_order)),
    h("div", h("p.help", data.names.tradition), h("p", h("b", "Given: "), data.names.given_names.join(", ")),
      data.names.family_names?.length ? h("p", h("b", "Family: "), data.names.family_names.join(", ")) : null,
      data.names.place_names?.length ? h("p", h("b", "Places: "), data.names.place_names.join(", ")) : null,
      data.names.titles?.length ? h("p", h("b", "Titles: "), data.names.titles.join("; ")) : null,
      data.names.reserved?.length ? h("p", h("b", "Reserved: "), data.names.reserved.join(", ")) : null)) : null;
  root.append(
    h("div.card", h("h3", "Creative direction"), dirSummary, dirWarnings, h("div.actions", editBtn), dirBox, names),
    h("div.card", h("h3", "Writer"), h("p.help", "Used for the next chapters, revisions and improvements."), writerBox),
    h("div.card.sub-card", h("h3", "Export for a game"),
      h("p.help", "Writes a standalone folder (index.html, story.json, art and sound) you can ship or load into an engine."),
      h("div.actions", h("button.btn", { type: "button", onclick: () => run({ action: "export" }) }, data.exported ? "Export again" : "Export"),
        h("span.help.mono", `${data.dir}/build`))),
    h("div.card.sub-card", h("h3", "Files"), h("p.help.mono", data.dir),
      h("p.help", "story.json, world.json, cast.json, outline.json, scenes/*.json and notes.md (the whole story as readable text).")));
}


// ---------------------------------------------------------------------- story assets (what the story uses, where)

function storyAssetsTab(root, data, sid, run) {
  const box = h("div", h("div.empty.small", h("span.spinner"), " Loading the story's assets…"));
  root.append(box);
  api(`/api/stories/${sid}/assets`).then((a) => renderStoryAssets(box, a, data, sid, run))
    .catch((err) => box.replaceChildren(errorBox(err)));
}

function renderStoryAssets(box, a, data, sid, run) {
  let show = "all";      // all | missing | made
  let chapter = null;    // chapter id lens
  const chapterTitles = Object.fromEntries((data.progress?.chapters || []).map((c) => [c.id, `${c.number}. ${c.title}`]));
  const keep = (e) => (show === "all" || (show === "missing" ? !e.asset : !!e.asset)) && (!chapter || e.chapters.some((c) => c.id === chapter));

  const chips = (e, max = 3) => h("div.sa-chips", e.chapters.length
    ? e.chapters.slice(0, max).map((c) => h("button.sa-chip", { type: "button", title: `Show only chapter “${c.title}”`, onclick: () => { chapter = c.id; draw(); } }, chapterTitles[c.id] || c.title))
      .concat(e.chapters.length > max ? [h("span.muted.small", { title: e.chapters.slice(max).map((c) => chapterTitles[c.id] || c.title).join("\n") }, `+${e.chapters.length - max} more`)] : [])
    : [h("span.muted.small", "not used yet")]);
  const status = (e) => e.asset ? h("span.sa-status", { class: e.asset.status }, e.asset.status) : h("span.sa-status.missing", "not made");
  const open = (e) => e.asset && (location.hash = `#/assets/${e.asset.id}`);

  function tile(e, label, { wide = false } = {}) {
    const v = e.asset?.primary_variant;
    const img = v && v.file?.kind === "image" ? h("img", { src: thumbUrl(v.id, wide ? 512 : 256), loading: "lazy", alt: label }) : null;
    return h("div.sa-tile", { class: `${wide ? "wide" : ""}${e.asset ? "" : " missing"}`, title: e.key, onclick: () => open(e) },
      h("div.sa-thumb", img || h("span.muted.small", e.asset ? "no preview" : "not made yet")),
      h("div.sa-cap", h("b", label), e.asset ? h("span.sa-dot", { class: e.asset.status, title: e.asset.status }) : h("span.sa-dot.missing", { title: "not made yet" })), chips(e));
  }

  function soundRow(e, label, extra) {
    const v = e.asset?.primary_variant;
    const btn = v ? h("button.sa-play", { type: "button", title: "Play", onclick: (ev) => { ev.stopPropagation(); playVariant(v.id, btn); } }, "▶") : h("span.sa-play.off", "·");
    return h("div.sa-row", { class: e.asset ? "" : "missing" }, btn,
      h("div.sa-row-main", h("div", h("b", label), " ", status(e)), extra ? h("div.help", extra) : null),
      chips(e, 5), e.asset ? h("button.btn.small", { type: "button", onclick: () => open(e) }, "Open") : null);
  }

  const section = (id, title, count, body) => body.length ? h("section.sa-section", { id: `sa-${id}` },
    h("h2", title, h("span.muted.small", ` ${count}`)), ...body) : null;

  function draw() {
    const t = a.totals;
    const head = h("div.card.sa-head",
      h("div", h("div.sa-big", `${t.made} of ${t.total}`), h("div.help", `assets made · ${t.approved} approved · ${t.total - t.made} missing`)),
      segmented([["all", "All"], ["missing", "Missing"], ["made", "Made"]], show, (v) => { show = v; draw(); }),
      chapter ? h("button.btn.small", { type: "button", onclick: () => { chapter = null; draw(); } }, `Chapter: ${chapterTitles[chapter] || chapter}  ✕`) : null,
      h("nav.sa-nav", ["characters", "places", "cgs", "ui", "sound"].map((id) => h("a", { href: `#sa-${id}`, onclick: (ev) => { ev.preventDefault(); box.querySelector(`#sa-${id}`)?.scrollIntoView({ behavior: "smooth" }); } },
        { characters: "Characters", places: "Places", cgs: "Event art", ui: "UI", sound: "Sound" }[id]))));

    const characters = a.characters.map((c) => {
      const exprs = c.expressions.filter(keep);
      if (!exprs.length) return null;
      const used = new Set(c.expressions.flatMap((e) => e.chapters.map((x) => x.id)));
      return h("div.sa-char", h("div.sa-char-head", h("b", c.name), h("span.tag", c.role),
        h("span.help", `${c.lines} lines · in ${used.size} chapters`)), h("div.sa-strip", exprs.map((e) => tile(e, e.expression.replace(/_/g, " ")))));
    }).filter(Boolean);

    const times = ["day", "evening", "night"];
    const places = a.locations.map((l) => {
      const cells = times.map((tm) => l.times.find((x) => x.time === tm)).map((e, i) => e && keep(e) ? tile(e, times[i], { wide: true }) : h("div.sa-cell-empty", e ? "" : "—"));
      const amb = l.ambience && keep(l.ambience) ? soundRow(l.ambience, "Ambience", null) : null;
      if (!l.times.some(keep) && !amb) return null;
      return h("div.sa-place", h("div.sa-char-head", h("b", l.name), h("span.help.clamp2", l.description)), h("div.sa-matrix", cells), amb);
    }).filter(Boolean);

    const cgs = a.cgs.filter(keep).map((e) => h("div", tile(e, e.ref.replace(/_/g, " "), { wide: true }), h("div.help.clamp2", e.text)));
    const ui = a.ui.filter(keep).map((e) => tile(e, e.ref, { wide: true }));
    const music = a.music.filter(keep).map((e) => soundRow(e, `Music · ${e.ref}`, e.asset?.description));
    const sfx = a.sfx.filter(keep).map((e) => soundRow(e, e.ref.replace(/_/g, " "), e.text));

    box.replaceChildren(head,
      section("characters", "Characters & expressions", a.characters.reduce((n, c) => n + c.expressions.length, 0), characters),
      section("places", "Places by time of day", a.locations.length, places.length ? [h("div.sa-times", h("span"), ...times.map((tm) => h("span.help", tm))), ...places] : []),
      section("cgs", "Event art (CGs)", a.cgs.length, cgs.length ? [h("div.sa-grid.wide", cgs)] : []),
      section("ui", "Title & UI", a.ui.length, ui.length ? [h("div.sa-grid.wide", ui)] : []),
      section("sound", "Music & sound effects", a.music.length + a.sfx.length, [...music, ...sfx]),
      h("details.section.sa-make", h("summary", h("span", "Make missing art & sound"), h("span.muted", "estimate first; images use your image API key")), (() => { const d = h("div"); assetsTab(d, data, sid, run); return d; })()));
  }
  draw();
}

// ---------------------------------------------------------------------- dynamics: planned curves vs the written text, and the branching map

const TRACK_COLORS = { valence: "#4f46e5", tension: "#b42318", suspense: "#b54708", capability: "#027a48", status: "#0b6bcb", family_conflict: "#7a5af8" };

function lineChart(tracks, planned, measured, chapters, help) {
  // one SVG: x = chapter, y = value; planned solid, measured dashed; the valence track is drawn on its own -5..5 band
  const W = 720, H = 300, L = 44, R = 16, T = 18, B = 44;
  const n = Math.max(1, chapters.length - 1);
  const x = (i) => L + (i * (W - L - R)) / n;
  const yv = (v, lo, hi) => T + ((hi - v) * (H - T - B)) / (hi - lo);
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "dyn-chart");
  const el = (tag, attrs, text) => { const e = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
  for (let g = 0; g <= 10; g += 2) { svg.append(el("line", { x1: L, x2: W - R, y1: yv(g, 0, 10), y2: yv(g, 0, 10), class: "grid" })); svg.append(el("text", { x: L - 6, y: yv(g, 0, 10) + 4, class: "tick", "text-anchor": "end" }, String(g))); }
  chapters.forEach((c, i) => svg.append(el("text", { x: x(i), y: H - B + 16, class: "tick", "text-anchor": "middle" }, c.id.length > 9 ? c.id.slice(0, 8) + "…" : c.id)));
  const pointsFor = (src, track) => chapters.map((c, i) => { const p = src[c.id]; return p ? [x(i), track === "valence" ? yv(p[track], -5, 5) : yv(p[track], 0, 10)] : null; });
  for (const t of tracks) {
    const color = TRACK_COLORS[t] || "#888";
    const draw = (src, dashed) => {
      const pts = pointsFor(src, t);
      let d = "", pen = false;
      pts.forEach((pt) => { if (!pt) { pen = false; return; } d += (pen ? " L " : " M ") + pt[0].toFixed(1) + " " + pt[1].toFixed(1); pen = true; });
      if (d) svg.append(el("path", { d, fill: "none", stroke: color, "stroke-width": dashed ? 1.5 : 2.2, "stroke-dasharray": dashed ? "5 4" : "", opacity: dashed ? 0.9 : 1 }));
      pts.forEach((pt, i) => { if (!pt) return; const dot = el("circle", { cx: pt[0], cy: pt[1], r: dashed ? 2.5 : 3.5, fill: color, opacity: dashed ? 0.8 : 1 });
        dot.append(el("title", {}, `${t} ${dashed ? "(text estimate)" : "(planned)"} · ${chapters[i].title}: ${src[chapters[i].id][t]}`)); svg.append(dot); });
    };
    draw(planned, false);
    if (Object.keys(measured).length) draw(measured, true);
  }
  const legend = h("div.dyn-legend", ...tracks.map((t) => h("span.dyn-key", { title: help[t] || t }, h("i", { style: `background:${TRACK_COLORS[t]}` }), t.replace("_", " "))),
    h("span.help", " · solid: planned · dashed: estimated from the written text · valence on its own −5…+5 band"));
  return h("div", svg, legend);
}

function branchingMap(graph) {
  // columns = chapters in reading order; a node per scene; edges drawn as curves; decisions and convergence marked
  if (!graph) return h("p.help", "No outline yet.");
  // columns: declared chapters, else the reading-order number (branches of one chapter share a column)
  let cols = graph.chapters;
  if (!cols.length || cols.length === graph.nodes.length) {
    const byNum = new Map();
    for (const n of graph.nodes) { const k = n.number ?? 99; if (!byNum.has(k)) byNum.set(k, { id: `ch${k}`, title: n.title.replace(/^\d+\.\s*/, ""), scenes: [] }); byNum.get(k).scenes.push(n.id); }
    cols = [...byNum.entries()].sort((a, b) => a[0] - b[0]).map(([, c]) => c);
  }
  const colOf = {}; cols.forEach((c, i) => c.scenes.forEach((sid) => { colOf[sid] = i; }));
  const rows = {}; const rowOf = {};
  for (const n of graph.nodes) { const c = colOf[n.id] ?? cols.length; rows[c] = rows[c] || []; rowOf[n.id] = rows[c].length; rows[c].push(n.id); }
  const CW = 150, RH = 64, L = 20, T = 40, W = L * 2 + CW * Math.max(cols.length, 1), H = T + RH * Math.max(1, ...Object.values(rows).map((r) => r.length)) + 20;
  const svgNS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, text) => { const e = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "branch-map" });
  const pos = (sid) => ({ x: L + (colOf[sid] ?? cols.length) * CW + 8, y: T + (rowOf[sid] || 0) * RH });
  cols.forEach((c, i) => svg.append(el("text", { x: L + i * CW + 8, y: 18, class: "tick" }, `${i + 1}. ${c.title}`.slice(0, 22))));
  const conv = new Set(graph.convergence);
  for (const e of graph.edges) {
    const a = pos(e.from), b = pos(e.to); if (!a || !b || colOf[e.to] == null) continue;
    const x1 = a.x + 128, y1 = a.y + 18, x2 = b.x, y2 = b.y + 18, mx = (x1 + x2) / 2;
    const path = el("path", { d: `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`, fill: "none", stroke: e.kind === "choice" ? "#4f46e5" : e.kind === "branch" ? "#b54708" : "#8a8a90", "stroke-width": 1.4, "stroke-dasharray": e.kind === "branch" ? "4 3" : "" });
    path.append(el("title", {}, `${e.kind}${e.label ? ": " + e.label : ""}${e.sets?.length ? " · sets " + e.sets.join(", ") : ""}`));
    svg.append(path);
  }
  for (const n of graph.nodes) {
    const p = pos(n.id); const g = el("g", { transform: `translate(${p.x},${p.y})` });
    const decision = graph.decisions.some((d) => d.scene === n.id);
    g.append(el("rect", { width: 128, height: 36, rx: 8, class: `node${n.ending ? " end" : ""}${decision ? " decision" : ""}${conv.has(n.id) ? " conv" : ""}` }));
    g.append(el("text", { x: 8, y: 16, class: "node-title" }, n.title.length > 18 ? n.title.slice(0, 17) + "…" : n.title));
    g.append(el("text", { x: 8, y: 29, class: "node-sub" }, [decision ? "decision" : "", conv.has(n.id) ? "convergence" : "", n.ending ? "ending" : ""].filter(Boolean).join(" · ") || n.location));
    g.append(el("title", {}, `${n.title}\n${n.hook ? "Hook: " + n.hook : ""}${n.callback ? "\nCallback: " + n.callback : ""}`));
    svg.append(g);
  }
  const legend = h("div.help", "Solid: the next scene or a choice (blue); dashed orange: a gated branch (stats decide). Outlined nodes are decisions; double-ringed ones are convergence points. Hover for the option text and what it sets.");
  const decisions = h("ul", ...graph.decisions.map((d) => h("li", h("b", d.title), ": ", d.options.map((o) => `${o.text} → ${o.goto}`).join(" | "))));
  const endings = h("ul", ...graph.endings.map((e) => h("li", h("b", e.title), ` (${e.kind})`, e.requires.length ? h("span.help", ` · needs ${e.requires.join(" and ")}`) : h("span.help", " · open"))));
  return h("div", h("div.branch-scroll", svg), legend, section("Major decisions", decisions, { open: true }), section("Endings and what earns them", endings), section("Variables", h("ul", ...graph.variables.map((v) => h("li", h("code", v.name), ` ${v.kind || ""} · ${v.meaning}`)))));
}

async function dynamicsTab(root, data, sid, run) {
  const box = h("div"); root.append(box);
  let d;
  try { d = await api(`/api/stories/${sid}/dynamics`); } catch (err) { box.append(errorBox(err)); return; }
  const chapters = d.chapters || [];
  const planned = {}; (d.planned?.points || []).forEach((p) => { planned[p.chapter] = p; });
  const measured = d.measured || {};
  const findings = (d.reviewed?.findings || []).map((f) => h("li", { class: f.level }, h("b", f.code.replace(/_/g, " ")), ": ", f.message, f.chapters?.length ? h("span.help", ` · ${f.chapters.join(", ")}`) : null));
  const busy = data.task?.state === "running";
  const controls = h("div.row.wrap",
    h("button.btn.primary", { type: "button", disabled: busy || !data.progress.steps.outline, onclick: () => run({ action: "dynamics_plan", fresh: !!d.planned }) }, d.planned ? "Rate the plan again" : "Rate the plan"),
    ...chapters.filter((c) => c.written).map((c) => h("button.btn", { type: "button", disabled: busy, onclick: () => run({ action: "dynamics_estimate", chapter: c.id, fresh: !!measured[c.id] }) }, `Estimate ${c.id}`)),
    h("span.help", d.reviewed?.method || "Ratings come from the writer; estimates from the written text are a model's reading, not a measurement."));
  box.append(
    h("h2", "Narrative dynamics"),
    h("p.help", "Six tracks per chapter. For a power fantasy the engine is the gap between capability (what he can do) and status (how the world rates him): it should open early and close late, in public."),
    controls,
    d.planned ? lineChart(d.tracks, planned, measured, chapters, d.help) : h("p.help", "Not rated yet. Rate the plan once the outline exists."),
    d.planned?.rationale ? h("p.help", d.planned.rationale) : null,
    findings.length ? section("Findings", h("ul.findings", ...findings), { open: true }) : (d.planned ? h("p.ok", "No shape problems found in the plan.") : null),
    d.planned ? section("Milestones by chapter", h("ul", ...(d.planned.points || []).map((p) => h("li", h("b", p.title || p.chapter), ": ",
      p.milestones.length ? p.milestones.map((m) => `${m.kind}: ${m.text}`).join(" · ") : h("span.help", "none"), p.note ? h("div.help", p.note) : null))), { open: false }) : null,
    h("h2", "Branching"),
    branchingMap(d.graph));
}
