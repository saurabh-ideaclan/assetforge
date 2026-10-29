// AssetForge desktop UI: hash router and view mounting.
import { api, h, errorBox, watchLibrary } from "/static/lib.js";
import { overview } from "/static/views/overview.js";
import { assets } from "/static/views/assets.js";
import { generate } from "/static/views/generate.js";
import { usage } from "/static/views/usage.js";
import { jobsView } from "/static/views/jobs.js";
import { art } from "/static/views/art.js";

const views = { overview, assets, generate, art, usage, jobs: jobsView };
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
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
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
