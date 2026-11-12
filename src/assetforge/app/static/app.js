// AssetForge desktop UI: hash router and view mounting.
import { api, h, errorBox } from "/static/lib.js";
import { connect, watch, beginScope, endScope } from "/static/store.js";
import { rerender } from "/static/live.js";
import { initActivity } from "/static/activity.js";
import { overview } from "/static/views/overview.js";
import { assets } from "/static/views/assets.js";
import { generate } from "/static/views/generate.js";
import { usage } from "/static/views/usage.js";
import { jobsView } from "/static/views/jobs.js";
import { art } from "/static/views/art.js";
import { story } from "/static/views/story.js";
import { ai } from "/static/views/ai.js";
import { audioStudio } from "/static/views/audio_studio.js";
import { audioLab } from "/static/views/audio_lab.js";
import { backendView } from "/static/views/backend.js";
import { initPalette } from "/static/palette.js";

const views = { overview, assets, generate, art, story, usage, jobs: jobsView, ai, audio: audioStudio, lab: audioLab, backend: backendView };
const main = document.getElementById("view");
let current = { name: null, cleanup: null };

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "") || "overview";
  const [path, query = ""] = raw.split("?");
  const [name, ...rest] = path.split("/");
  return { name: views[name] ? name : "overview", rest, params: new URLSearchParams(query) };
}

async function route() {
  const { name, rest, params } = parseHash();
  highlightNav(name, params);
  setCrumbs(name, rest, params);
  // a view can handle in-page changes itself; returning false asks for a fresh mount (e.g. different filters)
  if (current.name === name && current.update && current.update({ rest, params }) !== false) return;
  if (current.cleanup) current.cleanup();
  endScope(current.scope);
  main.innerHTML = "";
  main.scrollTop = 0; // each page starts at the top (main is the scroll container)
  const scope = beginScope();
  try {
    const mounted = (await views[name](main, { rest, params })) || {};
    current = { name, scope, ...mounted };
    liveView(current);
  } catch (err) {
    main.append(errorBox(err), h("div.actions", h("button.btn", { type: "button", onclick: () => route() }, "Retry")));
    current = { name: null, scope };
  }
  main.focus({ preventScroll: true });
}

window.addEventListener("hashchange", route);

// Live data: every view says what it depends on (`topics`), and either refreshes itself (`refresh`) or is redrawn in
// place by the shell, keeping scroll, open sections and anything typed. Form-only views list no topics.
const TOPICS = { overview: ["library", "jobs"], usage: ["jobs"] };
function liveView(view) {
  const topics = view.topics || TOPICS[view.name];
  if (!topics) return;
  const mine = view;
  watch(topics, async (changed) => {
    if (current !== mine) return;
    if (mine.refresh) return mine.refresh(changed);
    const { rest, params } = parseHash();
    let mounted = {};
    const swapped = await rerender(main, async (fresh) => { mounted = (await views[mine.name](fresh, { rest, params })) || {}; });
    if (swapped && current === mine) { mine.cleanup?.(); Object.assign(mine, mounted); }
  });
}

connect();
initActivity();

(async () => {
  try {
    const info = await api("/api/version");
    document.getElementById("library-path").textContent = info.home;
  } catch { /* the connection banner says why */ }
  route();
})();

export { h };

// ---------------------------------------------------------------------- shell: nav, breadcrumbs, theme, badges

const SECTION = { overview: "Overview", assets: "Assets", generate: "Create", art: "Art direction", story: "Stories", usage: "Usage & cost", jobs: "Jobs", audio: "Music & Ambience", lab: "Audio Workbench", backend: "Publishing" };

function highlightNav(name, params) {
  const sub = name === "assets" ? (params.get("status") === "candidate" ? "review" : params.get("kind") === "image" ? "assets-images"
    : params.get("kind") === "audio" ? "assets-audio" : "assets") : name;
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === sub));
}

/** Breadcrumbs: section › item. Views can refine the last part with window.afCrumb("Title"). */
function setCrumbs(name, rest, params) {
  const el = document.getElementById("crumbs");
  if (!el) return;
  const parts = [h("a", { href: `#/${name}` }, SECTION[name] || name)];
  if (name === "assets" && params.get("status") === "candidate") parts.push(h("span.sep", "›"), h("b", "Review"));
  else if (rest[0]) parts.push(h("span.sep", "›"), h("b", { id: "crumb-last" }, rest[0] === "new" ? "New story" : rest[0]));
  el.replaceChildren(...parts);
}
window.afCrumb = (title) => { const b = document.getElementById("crumb-last"); if (b) b.textContent = title; };

function setTheme(mode) {
  try { localStorage.setItem("af-theme", mode); } catch { /* private mode */ }
  if (mode === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", mode);
  document.querySelectorAll("[data-theme-set]").forEach((b) => b.classList.toggle("on", b.dataset.themeSet === mode));
}
let savedTheme = "auto";
try { savedTheme = localStorage.getItem("af-theme") || "auto"; } catch { /* ignore */ }
setTheme(savedTheme);
document.querySelectorAll("[data-theme-set]").forEach((b) => b.addEventListener("click", () => setTheme(b.dataset.themeSet)));
initPalette({ setTheme });
document.querySelectorAll("nav a[data-nav]").forEach((a) => { if (!a.title) a.title = a.textContent.trim(); });

// The Review badge: candidates waiting for a decision, recounted when the library changes.
async function reviewBadge() {
  try {
    const d = await api("/api/assets?status=candidate&limit=1");
    const b = document.getElementById("review-badge");
    if (b) { b.textContent = d.total > 999 ? "999+" : String(d.total); b.hidden = !d.total; b.classList.add("warn"); }
  } catch { /* ignore */ }
}
reviewBadge();
watch("library", reviewBadge, { debounce: 600 });
