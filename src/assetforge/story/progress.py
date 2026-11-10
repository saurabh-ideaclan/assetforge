"""Where a story stands, read from its files and the library's text jobs, so progress shows the same whether the
app, the CLI or an agent started the work, and survives restarts.

`progress()` → stage, pipeline steps, chapters in reading order (written / writing now / planned), what is running
now with readable labels, a short activity feed and an ETA. `preview_data()` → the player's `window.STORY` for a
story that is still being written: written chapters as they are, unwritten ones as playable stand-ins built from
the outline, art and sound straight from the library.
"""

from __future__ import annotations

import hashlib
import re
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from assetforge.core.util import loads
from assetforge.story.models import Node, Outline, PlannedScene
from assetforge.story.project import StoryProject

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

STALE_SECONDS = 30 * 60  # a "running" job this old belongs to a process that died
STEPS = ("world", "cast", "outline", "chapters", "assets", "playable")


def _ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _base(task: str) -> str:
    """story.scene.s01.p2 / .fix / .repair → story.scene.s01 (one chapter, however many calls write it)."""
    return re.sub(r"(\.p\d+|\.fix|\.repair)+$", "", task)


def _label(task: str, titles: dict[str, str], numbers: dict[str, int]) -> str:
    step = ""
    if m := re.search(r"\.p(\d+)", task):
        step = f" (part {m.group(1)})"
    elif ".fix" in task:
        step = " (fixing what the quality check found)"
    kind, _, ref = _base(task).removeprefix("story.").partition(".")
    chapter = f"chapter {numbers[ref]} · {titles[ref]}" if ref in titles else ref
    return {
        "world": "Building the world", "cast": "Creating the characters and places", "outline": "Planning the chapters",
        "scene": f"Writing {chapter}", "refine": f"Revising {chapter}", "improve": "Editor pass on your feedback",
        "check": "Continuity check",
    }.get(kind, task) + step + (" (fixing the answer's format)" if task.endswith(".repair") else "")  # fmt: skip


def chapter_numbers(plan: Outline) -> dict[str, int]:
    """Chapter number = the declared chapter's position, else the shortest distance from the start + 1
    (branches share numbers)."""
    by_id = {s.id: s for s in plan.scenes}
    depth = {plan.start: 1}
    queue = [plan.start]
    while queue:
        sid = queue.pop(0)
        s = by_id.get(sid)
        if s is None:
            continue
        for nxt in s.exits():
            if nxt not in depth:
                depth[nxt] = depth[sid] + 1
                queue.append(nxt)
    for s in plan.scenes:
        depth.setdefault(s.id, max(depth.values(), default=0) + 1)
    if plan.chapters:  # declared chapters (several scenes each) win over the scene depth
        index = {c.id: i + 1 for i, c in enumerate(plan.chapters)}
        depth = {s.id: index.get(s.chapter or "", depth[s.id]) for s in plan.scenes}
    return depth


def progress(lib: Library, project: StoryProject, *, with_assets: bool = False) -> dict[str, Any]:
    from assetforge.story.steps import scene_order

    config = project.config
    status = project.status()
    steps = status["steps"]
    plan: Outline | None = project.load("outline") if steps["outline"] else None
    titles: dict[str, str] = {}
    numbers: dict[str, int] = {}
    chapters: list[dict[str, Any]] = []
    rows = lib.conn.execute(
        "SELECT id, status, provider, model, request, result, error, created_at, updated_at FROM generation_jobs "
        "WHERE kind = 'text' AND json_extract(request, '$.project') = ? ORDER BY created_at DESC, id DESC LIMIT 200",
        (config.asset_project,)).fetchall()  # fmt: skip
    now = datetime.now(UTC).timestamp()
    running_tasks: dict[str, dict[str, Any]] = {}
    if plan:
        numbers = chapter_numbers(plan)
        titles = {s.id: s.title for s in plan.scenes}
    for r in rows:
        if r["status"] == "running" and now - _ts(r["created_at"]) < STALE_SECONDS:
            task = (loads(r["request"], {}) or {}).get("task", "")
            running_tasks.setdefault(_base(task), {
                "job_id": r["id"], "task": task, "label": _label(task, titles, numbers),
                "since": r["created_at"], "seconds": round(now - _ts(r["created_at"]))})  # fmt: skip
    if plan:
        ordered, _ = scene_order(plan)
        written = set(project.scenes())
        for s in ordered:
            state = "written" if s.id in written else "planned"
            if f"story.scene.{s.id}" in running_tasks or f"story.refine.{s.id}" in running_tasks:
                state = "writing" if state == "planned" else "revising"
            chapters.append({"id": s.id, "number": numbers[s.id], "title": s.title, "summary": s.summary,
                             "location": s.location, "time": s.time, "state": state,
                             "ending": s.ending, "choices": [c.text for c in s.choices]})  # fmt: skip
    feed = []
    per_chapter: dict[str, int] = {}
    for r in rows:
        if r["status"] == "running":
            continue
        task = (loads(r["request"], {}) or {}).get("task", "")
        seconds = round(_ts(r["updated_at"]) - _ts(r["created_at"]))
        if r["status"] == "succeeded" and task.startswith("story.scene."):
            per_chapter[_base(task)] = per_chapter.get(_base(task), 0) + seconds  # all calls that wrote it
        if len(feed) < 12:
            error = loads(r["error"], None)
            request = loads(r["request"], {}) or {}
            choice = (loads(r["result"], {}) or {}).get("choice") or request.get("choice") or {}
            feed.append({"job_id": r["id"], "label": _label(task, titles, numbers), "status": r["status"],
                         "provider": r["provider"], "model": r["model"], "at": r["updated_at"], "seconds": seconds,
                         "error": (error or {}).get("message") if r["status"] == "failed" else None,
                         "chosen_by": choice.get("chosen_by"), "requested": choice.get("requested"),
                         "fallbacks": choice.get("fallbacks") or [], "billing": (choice.get("used") or {}).get("billing")})  # fmt: skip
    remaining = sum(1 for c in chapters if c["state"] in ("planned", "writing"))
    eta = None
    durations = list(per_chapter.values())
    if remaining and durations and running_tasks:
        per = sum(durations[:10]) / len(durations[:10])
        eta = round(per * remaining / max(1, min(2, len(running_tasks))))
    built = (project.root / "build" / "index.html").exists()
    assets = None
    if with_assets and steps["script"]:
        from assetforge.story.assets import coverage

        assets = coverage(lib, project)
    stage = ("idea" if not steps["world"] else "world" if not steps["cast"] else "cast" if not steps["outline"]
             else "writing" if status["scenes_written"] < status["scenes_planned"] else "written")  # fmt: skip
    return {
        "stage": stage,
        "steps": {"world": steps["world"], "cast": steps["cast"], "outline": steps["outline"],
                  "chapters": [status["scenes_written"], status["scenes_planned"]],
                  "assets": [assets["have"], assets["total"]] if assets else None, "exported": built},
        "chapters": chapters, "running": list(running_tasks.values()), "active": bool(running_tasks),
        "feed": feed, "eta_seconds": eta,
        "playable_chapters": sum(1 for c in chapters if c["state"] in ("written", "revising")),
    }  # fmt: skip


# ---------------------------------------------------------------------- live preview


def _stand_in(plan: PlannedScene) -> dict[str, Any]:
    nodes = [Node(kind="narrate", text=plan.summary)]
    if plan.choices:
        from assetforge.story.models import Choice

        nodes.append(Node(kind="choice", choices=[Choice(text=c.text, goto=c.goto) for c in plan.choices]))
    elif plan.branches:
        nodes.append(Node(kind="branch", branches=plan.branches, goto=plan.next))
    elif plan.ending:
        nodes.append(Node(kind="ending", ending=plan.ending))
    else:
        nodes.append(Node(kind="jump", goto=plan.next))
    return {"id": plan.id, "title": plan.title, "chapter": plan.chapter, "location": plan.location, "time": plan.time, "music": plan.music,
            "nodes": [n.model_dump(mode="json", exclude_none=True, exclude_defaults=True) | {"kind": n.kind} for n in nodes],
            "draft": True, "summary": plan.summary}  # fmt: skip


def preview_script(project: StoryProject) -> dict[str, Any] | None:
    """The playable script as it is right now (None before an outline exists)."""
    if not (project.has("outline") and project.has("cast")):
        return None
    config, cast, plan = project.config, project.load("cast"), project.load("outline")
    written = project.scenes()
    scenes = [written[s.id].model_dump(mode="json", exclude_none=True) if s.id in written else _stand_in(s)
              for s in plan.scenes]  # fmt: skip
    return {"format": "assetforge-vn/1", "title": config.title, "slug": config.slug, "language": config.language,
            "start": plan.start, "variables": [v.model_dump() for v in plan.variables],
            "chapters": [c.model_dump() for c in plan.chapters],
            "characters": [c.model_dump(mode="json") for c in cast.characters],
            "locations": [loc.model_dump(mode="json") for loc in cast.locations],
            "endings": [e.model_dump(mode="json") for e in plan.endings], "scenes": scenes}  # fmt: skip


def library_assets(lib: Library, project: StoryProject) -> dict[str, dict[str, Any]]:
    """Story key → playable URLs served by the app (`/media/variant/<id>.<ext>`), straight from the library."""
    config = project.config
    prefix = f"vn/{config.slug}/"
    rows = lib.conn.execute(
        "SELECT key AS k, id FROM assets WHERE project = ? AND key LIKE ? AND status NOT IN ('rejected', 'archived') "
        "UNION SELECT u.usage_key AS k, u.asset_id AS id FROM project_usage u JOIN assets a ON a.id = u.asset_id "
        "WHERE u.project = ? AND u.usage_key LIKE ? AND a.status NOT IN ('rejected', 'archived')",
        (config.asset_project, prefix + "%", config.asset_project, prefix + "%")).fetchall()  # fmt: skip
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["k"] in out:
            continue
        asset = lib.get_asset(row["id"])
        primary = asset.primary_variant
        if primary is None:
            continue
        url = lambda v: f"/media/variant/{v.id}{v.file.ext}"  # noqa: E731
        if primary.file.kind == "audio":
            m4a = next((v for v in asset.variants if v.file.ext == ".m4a" and v.label.startswith(primary.label.split("_")[0])), None)
            out[row["k"]] = {"audio": [url(primary)] + ([url(m4a)] if m4a else [])}
        elif primary.file.kind == "image":
            out[row["k"]] = {"image": url(primary)}
    return out


def preview_data(lib: Library, project: StoryProject) -> dict[str, Any] | None:
    script = preview_script(project)
    if script is None:
        return None
    world = project.load("world") if project.has("world") else None
    version = hashlib.sha256(json.dumps(script["scenes"], sort_keys=True).encode()).hexdigest()[:16]
    drafts = sum(1 for s in script["scenes"] if s.get("draft"))
    return {"format": "assetforge-vn-export/1", "logline": world.logline if world else "", "script": script,
            "assets": library_assets(lib, project), "missing": [], "preview": drafts > 0, "version": version}  # fmt: skip
