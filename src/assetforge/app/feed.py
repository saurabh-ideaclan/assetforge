"""The change feed: one set of revision markers for everything the app shows, so an open page learns about a change
within a second, whoever made it (the app, the CLI, an agent's script, another window).

Topics and what their revision is made of:

- `library`  assets + variants + events (counts, newest update, newest event)
- `jobs`     generation jobs (count, newest update)
- `tasks`    the durable task table (core/tasks.py), including live tasks that went silent
- `stories`  the story registry (stories.json)
- `story:<id>:<area>` per registered story, from the story folder's files (mtime + size), plus for `art` the library
  assets keyed `vn/<slug>/…` and for `progress` the story project's text jobs. Areas: text, cast, art, audio,
  checks, releases, workflow, settings, progress
- `publishing` the library's `backend/` state files and every story's releases
- `logins`   provider sign-ins started from the app
- `build`    the running code: Python (hashed at start) and static files (re-checked while running)

A single watcher thread recomputes the snapshot (about a millisecond per story) once a second while anyone is
listening (an SSE stream or a poll in the last 30 s), and every few seconds otherwise. `GET /api/events` streams it as
server-sent events; `GET /api/changes?since=<seq>` is the polling fallback with the same payload.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assetforge.core import tasks as tasks_mod

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

PACKAGE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

# story folder entries per area: (sub-directory or "", suffixes or names)
AREAS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "text": [("", ("outline.json", "script.json", "world.json", "notes.md", "feedback.json")), ("scenes", (".json",)),
             ("alternatives", (".json",))],
    "cast": [("", ("cast.json", "characters.json", "acting.json", "names.json"))],
    "art": [("", ("visual_canon.json",))],
    "audio": [("", ("sound.json",)), ("audio", (".json",))],
    "checks": [("", ("lint.json", "clarity.json", "visual_check.json", "evaluation.json", "check.json", "dynamics.json",
                     "payoffs.json", "rules.json")), ("clarity", (".json",))],
    "releases": [("releases", ("*",)), ("build", ("index.html", "story.js"))],
    "workflow": [("", ("workflow.json",))],
    "settings": [("", ("story.json",))],
}  # fmt: skip
STORY_AREAS = (*AREAS, "progress")
LISTEN_WINDOW = 30.0
RING = 64


def _sig(parts: list[Any]) -> str:
    return hashlib.blake2b(repr(parts).encode(), digest_size=8).hexdigest()


def _stat_entries(directory: Path, names: tuple[str, ...]) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                name = entry.name
                if name.startswith(".") or name.endswith((".back", ".tmp")):
                    continue
                if "*" in names or name in names or any(name.endswith(n) for n in names if n.startswith(".")):
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    out.append((name, st.st_mtime_ns, st.st_size))
    except OSError:
        return out
    return sorted(out)


def _releases(root: Path) -> list[Any]:
    """Top of releases/ plus each release's own JSON files (never the blob tree)."""
    rel = root / "releases"
    parts: list[Any] = _stat_entries(rel, ("*",))
    for name, *_ in parts[:]:
        if (rel / name).is_dir() and name != "blobs":
            parts.extend((name, *x) for x in _stat_entries(rel / name, (".json",)))
    return parts


def story_files(root: Path) -> dict[str, str]:
    """Area → revision of the story's own files."""
    out = {}
    for area, specs in AREAS.items():
        parts: list[Any] = []
        for sub, names in specs:
            if area == "releases" and sub == "releases":
                parts.append(_releases(root))
            else:
                parts.append(_stat_entries(root / sub if sub else root, names))
        out[area] = _sig(parts)
    return out


# ---------------------------------------------------------------------- build id


def _python_hash() -> str:
    h = hashlib.blake2b(digest_size=8)
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        try:
            h.update(str(path.relative_to(PACKAGE_DIR)).encode())
            h.update(path.read_bytes())
        except OSError:
            continue
    return h.hexdigest()


def static_signature() -> str:
    parts = []
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            try:
                st = path.stat()
            except OSError:
                continue
            parts.append((str(path.relative_to(STATIC_DIR)), st.st_mtime_ns, st.st_size))
    return _sig(parts)


PYTHON_HASH = _python_hash()  # the code this process is running (it cannot change without a restart)


def build_id(static: str | None = None) -> str:
    return f"{PYTHON_HASH}-{(static or static_signature())[:8]}"


# ---------------------------------------------------------------------- snapshot


@dataclass
class Snapshot:
    seq: int
    at: float
    revisions: dict[str, str]
    changed: list[str] = field(default_factory=list)

    def payload(self, *, build: str, started_at: float, since: Snapshot | None = None, full: bool = False) -> dict[str, Any]:
        if since is not None:
            changed = sorted(k for k, v in self.revisions.items() if since.revisions.get(k) != v)
            changed += sorted(k for k in since.revisions if k not in self.revisions)
        else:
            changed = list(self.changed)
        return {"seq": self.seq, "at": self.at, "build": build, "started_at": started_at, "full": full,
                "revisions": self.revisions, "changed": changed}  # fmt: skip


class _StoryCache:
    """Story id → (root, slug, project), refreshed when stories.json or a story.json changes."""

    def __init__(self) -> None:
        self.key: Any = None
        self.items: dict[str, tuple[Path, str, str]] = {}

    def get(self, home: Path) -> dict[str, tuple[Path, str, str]]:
        from assetforge.story.project import registered

        reg = home / "stories.json"
        try:
            st = reg.stat()
            key: list[Any] = [st.st_mtime_ns, st.st_size]
        except OSError:
            key = [None]
        roots = registered(home) if key != [None] else {}
        for sid, root in roots.items():
            try:
                s = (root / "story.json").stat()
                key.append((sid, s.st_mtime_ns))
            except OSError:
                key.append((sid, None))
        if key == self.key:
            return self.items
        items: dict[str, tuple[Path, str, str]] = {}
        import json

        for sid, root in roots.items():
            try:
                cfg = json.loads((root / "story.json").read_text())
            except (OSError, ValueError):
                continue
            slug = cfg.get("slug") or sid
            items[sid] = (root, slug, cfg.get("project") or f"vn-{slug}")
        self.key, self.items = key, items
        return items


def compute(lib: Library, cache: _StoryCache, static: str) -> dict[str, str]:
    conn = lib.conn
    home = Path(lib.paths.home)
    assets = conn.execute("SELECT count(*), max(updated_at) FROM assets").fetchone()
    variants = conn.execute("SELECT count(*) FROM asset_variants").fetchone()[0]
    events = conn.execute("SELECT max(rowid) FROM asset_events").fetchone()[0]
    jobs = conn.execute("SELECT count(*), max(updated_at) FROM generation_jobs").fetchone()
    rev: dict[str, str] = {
        "library": f"{assets[0]}:{variants}:{assets[1] or ''}:{events or 0}",
        "jobs": f"{jobs[0]}:{jobs[1] or ''}",
        "tasks": tasks_mod.revision(lib),
        "build": build_id(static),
    }
    stories = cache.get(home)
    rev["stories"] = _sig(sorted((sid, str(r[0])) for sid, r in stories.items()))
    # story art: library assets keyed vn/<slug>/…, grouped by slug in one query
    art: dict[str, list[Any]] = {}
    for row in conn.execute("SELECT key, status, primary_variant_id, updated_at FROM assets WHERE key LIKE 'vn/%' ORDER BY key"):
        slug = row[0].split("/", 2)[1] if row[0].count("/") >= 2 else ""
        art.setdefault(slug, []).append(tuple(row))
    # story progress: the project's text jobs (who is writing what right now)
    progress: dict[str, str] = {}
    for row in conn.execute("SELECT json_extract(request, '$.project') AS p, count(*), max(updated_at), "
                            "sum(status = 'running') FROM generation_jobs WHERE kind = 'text' GROUP BY p"):  # fmt: skip
        if row[0]:
            progress[row[0]] = f"{row[1]}:{row[2]}:{row[3]}"
    releases = []
    for sid, (root, slug, project) in stories.items():
        files = story_files(root)
        for area, value in files.items():
            if area == "art":
                value = _sig([value, art.get(slug, [])])
            rev[f"story:{sid}:{area}"] = value
        rev[f"story:{sid}:progress"] = progress.get(project, "")
        releases.append(files["releases"])
    backend = _stat_entries(home / "backend", (".json",))
    rev["publishing"] = _sig([backend, releases])
    rev["logins"] = _logins()
    return rev


def _logins() -> str:
    try:
        from assetforge.llm import login
    except ImportError:  # pragma: no cover
        return ""
    sessions = getattr(login, "_sessions", {})
    return _sig(sorted((k, getattr(s, "state", ""), bool(getattr(s, "url", ""))) for k, s in list(sessions.items())))


# ---------------------------------------------------------------------- the watcher


class Feed:
    def __init__(self, home: Path, *, interval: float = 1.0, idle_interval: float = 5.0, recover_every: float = 10.0) -> None:
        self.home = home
        self.interval = interval
        self.idle_interval = idle_interval
        self.recover_every = recover_every
        self.started_at = time.time()
        self._cond = threading.Condition()
        self._ring: OrderedDict[int, Snapshot] = OrderedDict()
        self._current: Snapshot | None = None
        self._listeners = 0
        self._last_poll = 0.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._cache = _StoryCache()
        self._static = static_signature()
        self._static_at = time.time()
        self.errors = 0

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="change-feed", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=3)

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def build(self) -> str:
        return build_id(self._static)

    def _run(self) -> None:
        from assetforge.core.catalog import Library

        lib = Library(self.home, pooled=True)
        last_recover = 0.0
        try:
            while not self._stop.is_set():
                try:
                    now = time.time()
                    if now - self._static_at > 2:
                        self._static, self._static_at = static_signature(), now
                    if now - last_recover > self.recover_every:
                        tasks_mod.recover(lib)
                        last_recover = now
                    self._publish(compute(lib, self._cache, self._static))
                except Exception:  # noqa: BLE001 - a bad file must never stop the feed
                    self.errors += 1
                busy = self._listeners > 0 or time.time() - self._last_poll < LISTEN_WINDOW
                self._wake.wait(self.interval if busy else self.idle_interval)
                self._wake.clear()
        finally:
            lib.close()

    def _publish(self, revisions: dict[str, str]) -> None:
        with self._cond:
            prev = self._current
            if prev is not None and prev.revisions == revisions:
                return
            seq = (prev.seq + 1) if prev else 1
            changed = sorted(k for k, v in revisions.items() if prev is None or prev.revisions.get(k) != v)
            if prev is not None:
                changed += sorted(k for k in prev.revisions if k not in revisions)
            snap = Snapshot(seq, time.time(), revisions, changed if prev else [])
            self._current = snap
            self._ring[seq] = snap
            while len(self._ring) > RING:
                self._ring.popitem(last=False)
            self._cond.notify_all()

    # -------------------------------------------------------------- reading

    def poke(self) -> None:
        """Recompute now (after the app itself changed something)."""
        self._wake.set()

    def current(self, timeout: float = 5.0) -> Snapshot:
        with self._cond:
            if self._current is None:
                self._wake.set()
                self._cond.wait_for(lambda: self._current is not None or self._stop.is_set(), timeout)
            if self._current is None:
                raise RuntimeError("change feed not ready")
            return self._current

    def wait(self, after_seq: int, timeout: float) -> Snapshot:
        with self._cond:
            self._cond.wait_for(lambda: (self._current is not None and self._current.seq != after_seq) or self._stop.is_set(),
                                timeout)  # fmt: skip
            return self._current  # type: ignore[return-value]

    def since(self, seq: int) -> Snapshot | None:
        with self._cond:
            return self._ring.get(seq)

    def payload(self, since: int | None = None) -> dict[str, Any]:
        self._last_poll = time.time()
        snap = self.current()
        base = self.since(since) if since is not None else None
        full = since is None or base is None
        return snap.payload(build=self.build, started_at=self.started_at, since=base, full=full) if base else \
            {**snap.payload(build=self.build, started_at=self.started_at, full=True), "changed": []}

    def listening(self, delta: int) -> None:
        with self._cond:
            self._listeners += delta
        if delta > 0:
            self._wake.set()
