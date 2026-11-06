// AssetForge desktop UI: hash router and view mounting.
import { api, h, errorBox, watchLibrary } from "/static/lib.js";
import { overview } from "/static/views/overview.js";
import { assets } from "/static/views/assets.js";
import { generate } from "/static/views/generate.js";
import { usage } from "/static/views/usage.js";
import { jobsView } from "/static/views/jobs.js";
import { art } from "/static/views/art.js";
import { story } from "/static/views/story.js";
import { initPalette } from "/static/palette.js";

const views = { overview, assets, generate, art, story, usage, jobs: jobsView };
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
  main.innerHTML = "";
  main.scrollTop = 0; // each page starts at the top (main is the scroll container)
  try {
    const mounted = (await views[name](main, { rest, params })) || {};
    current = { name, ...mounted };
  } catch (err) {
    main.append(errorBox(err));
    current = { name: null };
  }
  main.focus({ preventScroll: true });
}

window.addEventListener("hashchange", route);

// Views that only show library data re-render when it changes; views with forms (generate, sound, art)
// are left alone so nothing typed is lost. A view can refresh itself in place by returning `refresh`.
const AUTO_REMOUNT = new Set(["overview", "jobs", "usage"]);
let refreshing = false;
watchLibrary(async (changed) => {
  if (refreshing || !current.name) return;
  refreshing = true;
  try {
    if (current.refresh) await current.refresh(changed);
    else if (AUTO_REMOUNT.has(current.name)) {
      const top = main.scrollTop;
      const { rest, params } = parseHash();
      const fresh = h("div");
      const mounted = (await views[current.name](fresh, { rest, params })) || {};
      if (current.cleanup) current.cleanup();
      main.replaceChildren(...fresh.childNodes);  // swap in one go: no flash of an empty page
      current = { name: current.name, ...mounted };
      main.scrollTop = top;
    }
  } catch { /* keep showing what we have */ }
  finally { refreshing = false; }
});

(async () => {
  try {
    const info = await api("/api/overview?tz=" + encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone));
    document.getElementById("library-path").textContent = info.library.home;
  } catch { /* the overview view shows the error */ }
  route();
})();

export { h };

// Sidebar: what is being written right now, wherever it was started (app, CLI or an agent).
async function pollActivity() {
  try {
    const a = await api("/api/activity");
    const pill = document.getElementById("activity-pill");
    const badge = document.getElementById("story-badge");
    if (!pill || !badge) return;
    if (a.stories.length) {
      const s = a.stories[0];
      const more = a.stories.length > 1 ? ` +${a.stories.length - 1} more` : "";
      pill.replaceChildren(h("span.spinner"), ` Writing “${s.title}”${s.running > 1 ? ` (${s.running} at once)` : ""}${more}`);
      pill.href = `#/story/${s.id}`;
      pill.hidden = false;
      badge.textContent = String(a.stories.length);
      badge.hidden = false;
    } else { pill.hidden = true; badge.hidden = true; }
  } catch { /* offline: try again */ }
}
pollActivity();
setInterval(() => document.visibilityState === "visible" && pollActivity(), 4000);


// ---------------------------------------------------------------------- shell: nav, breadcrumbs, theme, badges

const SECTION = { overview: "Overview", assets: "Assets", generate: "Create", art: "Art direction", story: "Stories", usage: "Usage & cost", jobs: "Jobs" };

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

async function pollReview() {
  try {
    const d = await api("/api/assets?status=candidate&limit=1");
    const b = document.getElementById("review-badge");
    if (b) { b.textContent = d.total > 999 ? "999+" : String(d.total); b.hidden = !d.total; b.classList.add("warn"); }
  } catch { /* ignore */ }
}
pollReview();
setInterval(() => document.visibilityState === "visible" && pollReview(), 15000);
