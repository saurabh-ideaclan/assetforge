// ⌘K command palette: jump to any page or story, search assets (with thumbnails), run common actions.
import { api, h, thumbUrl, toast } from "/static/lib.js";
import { tasks as currentTasks } from "/static/store.js";
import { openDrawer } from "/static/activity.js";

const STORY_TABS = [["workflow", "Workflow"], ["characters", "Characters"], ["dynamics", "Dynamics"], ["chapters", "Chapters"],
  ["play", "Play"], ["assets", "Assets"], ["improve", "Improve"], ["settings", "Settings"]];

const PAGES = [
  ["Overview", "#/overview", "◎"], ["Review candidates", "#/assets?status=candidate", "✓"], ["Assets", "#/assets", "▦"],
  ["Images", "#/assets?kind=image", "◧"], ["Sound", "#/assets?kind=audio", "♪"], ["Stories", "#/story", "✎"],
  ["Create an image", "#/generate?tab=image", "✦"], ["Create a sound", "#/generate?tab=sound", "♫"],
  ["Art direction", "#/art", "◐"], ["Jobs", "#/jobs", "≡"], ["Activity", "#activity", "◷"], ["Usage & cost", "#/usage", "$"], ["Publishing", "#/backend", "⇪"],
];

let open = false;

export function initPalette({ setTheme }) {
  const actions = [
    ["New story", () => (location.hash = "#/story/new"), "+"],
    ["Generate an image", () => (location.hash = "#/generate?tab=image"), "✦"],
    ["Make a sound", () => (location.hash = "#/generate?tab=sound"), "♫"],
    ["Theme: light", () => setTheme("light"), "☀"], ["Theme: dark", () => setTheme("dark"), "☾"],
    ["Theme: match the system", () => setTheme("auto"), "◑"],
    ["Reload the window", () => location.reload(), "↻"],
  ];
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); open ? close() : show(actions); }
  });
  document.getElementById("palette-open")?.addEventListener("click", () => show(actions));
}

let backdrop = null;
function close() { backdrop?.remove(); backdrop = null; open = false; }

async function show(actions) {
  if (open) return;
  open = true;
  const input = h("input", { type: "text", placeholder: "Jump to a page or story, search assets, or run an action…", "aria-label": "Search", autocomplete: "off", spellcheck: false });
  const list = h("div.cmdk-list", { role: "listbox" });
  const box = h("div.cmdk", { role: "dialog", "aria-label": "Command palette" }, input, list,
    h("div.cmdk-foot", h("span", "↑↓ move"), h("span", "↵ open"), h("span", "esc close")));
  backdrop = h("div.cmdk-backdrop", { onclick: (e) => e.target === backdrop && close() }, box);
  document.body.append(backdrop);
  input.focus();
  let stories = [];
  api("/api/stories").then((d) => { stories = d.stories; render(); }).catch(() => {});
  let items = [];
  let index = 0;
  let assets = [];
  let seq = 0;

  function render() {
    const q = input.value.trim().toLowerCase();
    const match = (s) => !q || s.toLowerCase().includes(q);
    const groups = [
      ["Pages", PAGES.filter(([t]) => match(t)).map(([t, href, ico]) => ({ title: t, ico, run: () => (href === "#activity" ? openDrawer() : (location.hash = href)), hint: "Go" }))],
      ["Stories", stories.filter((s) => match(`${s.title} ${s.genre}`)).slice(0, 6).map((s) => ({
        title: s.title, sub: `${s.progress.steps.chapters[0]}/${s.progress.steps.chapters[1]} chapters · ${s.genre}`, ico: "✎",
        run: () => (location.hash = `#/story/${s.id}`), hint: "Open" }))],
      ["Running tasks", (currentTasks().tasks || []).filter((t) => ["running", "queued", "waiting"].includes(t.state) && match(`${t.title} ${t.kind}`)).slice(0, 6)
        .map((t) => ({ title: t.title, sub: `${t.state} · ${t.message || t.kind}`, ico: t.state === "waiting" ? "!" : "⟳", run: () => openDrawer(t.id), hint: "Show" }))],
      ["Story tabs", q.length < 2 ? [] : stories.filter((s) => match(`${s.title} ${s.genre}`) || STORY_TABS.some(([, l]) => match(l))).slice(0, 3)
        .flatMap((s) => STORY_TABS.filter(([, l]) => match(`${s.title} ${l}`) || match(l) || match(s.title)).map(([id, l]) => ({
          title: `${s.title} › ${l}`, ico: "✎", run: () => (location.hash = `#/story/${s.id}/${id}`), hint: "Open" }))).slice(0, 10)],
      ["Assets", assets.map((a) => ({ title: a.name, sub: `${a.type} · ${a.status}${a.key ? " · " + a.key : ""}`,
        img: a.primary_variant?.file?.kind === "image" ? thumbUrl(a.primary_variant.id, 128) : null,
        ico: a.primary_variant?.file?.kind === "audio" ? "♪" : "▦", run: () => (location.hash = `#/assets/${a.id}`), hint: "Open" }))],
      ["Actions", actions.filter(([t]) => match(t)).map(([t, fn, ico]) => ({ title: t, ico, run: fn, hint: "Run" }))],
    ].filter(([, xs]) => xs.length);
    items = groups.flatMap(([, xs]) => xs);
    index = Math.min(index, Math.max(0, items.length - 1));
    let k = 0;
    list.replaceChildren(...(items.length ? groups.flatMap(([name, xs]) => [h("div.cmdk-group", name), ...xs.map((it) => {
      const i = k++;
      return h("div.cmdk-item", { role: "option", class: i === index ? "on" : "", onmouseenter: () => { index = i; mark(); }, onclick: () => go(i) },
        h("span.pi-ico", it.img ? h("img", { src: it.img, alt: "" }) : it.ico),
        h("div", h("div", it.title), it.sub ? h("div.pi-sub", it.sub) : null), h("span.pi-hint", it.hint));
    })]) : [h("div.empty.small", "Nothing matches.")]));
  }
  function mark() { list.querySelectorAll(".cmdk-item").forEach((el, i) => el.classList.toggle("on", i === index)); list.querySelector(".cmdk-item.on")?.scrollIntoView({ block: "nearest" }); }
  function go(i) { const it = items[i]; if (!it) return; close(); try { it.run(); } catch (err) { toast(err.message); } }

  let timer = null;
  input.addEventListener("input", () => {
    index = 0; render();
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { assets = []; render(); return; }
    const mine = ++seq;
    timer = setTimeout(async () => {
      try { const d = await api(`/api/assets?q=${encodeURIComponent(q)}&limit=6`); if (mine === seq) { assets = d.items; render(); } } catch { /* ignore */ }
    }, 160);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); index = Math.min(items.length - 1, index + 1); mark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); index = Math.max(0, index - 1); mark(); }
    else if (e.key === "Enter") { e.preventDefault(); go(index); }
  });
  render();
}
