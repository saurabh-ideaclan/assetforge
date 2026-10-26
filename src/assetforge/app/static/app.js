// AssetForge desktop UI: hash router and view mounting.
import { api, h, errorBox } from "/static/lib.js";
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
  if (current.name === name && current.update) {
    current.update({ rest, params });
    return;
  }
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

(async () => {
  try {
    const info = await api("/api/overview?tz=" + encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone));
    document.getElementById("library-path").textContent = info.library.home;
  } catch { /* the overview view shows the error */ }
  route();
})();

export { h };
