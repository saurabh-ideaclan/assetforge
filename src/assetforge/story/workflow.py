"""The production workflow as a creative director sees it: twelve stages derived from the story's files and
jobs, approval gates with three modes, an approval history, and change-impact lookups.

`workflow.json` in the story folder holds the mode, the approvals and the history; nothing else is stored, so
the stage states always agree with what is actually on disk (a stage is "done" because its file exists and,
where the mode asks for it, because a person approved it).

Modes: `auto` (only paid art and the final review wait for a person), `guided` (default: characters, story
architecture, visual identities and the final review), `manual` (every stage after the idea).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from assetforge.core.util import utcnow
from assetforge.errors import InvalidInputError
from assetforge.story.project import StoryProject, write_atomic

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

Mode = Literal["auto", "guided", "manual"]
STAGES: list[dict[str, str]] = [
    {"id": "idea", "title": "Story idea", "goal": "Capture the premise, the title and what is at stake."},
    {"id": "direction", "title": "Creative direction", "goal": "Fix the audience, genre, world, naming, narrative style and art style."},
    {"id": "world", "title": "World building", "goal": "Establish the setting, its history and rules, and the premise sheet."},
    {"id": "characters", "title": "Characters", "goal": "Define the protagonist, antagonist and supporting cast with wants, voices and looks."},
    {"id": "architecture", "title": "Story architecture", "goal": "Plan the season: stats, decisions, routes and the endings they earn."},
    {"id": "planning", "title": "Chapter planning", "goal": "Give every chapter a purpose, a hook and a callback to earlier choices."},
    {"id": "routes", "title": "Interactive routes", "goal": "Make sure choices fork, reconverge and are remembered."},
    {"id": "writing", "title": "Story writing", "goal": "Write every chapter to the plan, in the chosen style."},
    {"id": "evaluation", "title": "Narrative evaluation", "goal": "Measure each chapter and check continuity before it is approved."},
    {"id": "visual", "title": "Visual direction", "goal": "Settle the art direction and each character's written visual identity."},
    {"id": "assets", "title": "Asset generation", "goal": "Make sprites, backgrounds, event art, UI and sound from the approved references."},
    {"id": "final", "title": "Final review", "goal": "Play it through, approve it, and export it for the app."},
]
GATES: dict[str, set[str]] = {
    "auto": {"assets", "final"},
    "guided": {"characters", "architecture", "visual", "final"},
    "manual": {s["id"] for s in STAGES} - {"idea"},
}
# which stage must be approved before a pipeline step may run
BEFORE_STEP: dict[str, str] = {"world": "direction", "cast": "world", "outline": "characters", "write": "architecture",
                               "assets": "visual", "export": "final"}  # fmt: skip
STATE_ORDER = ("done", "working", "needs_approval", "needs_revision", "ready", "blocked", "pending")


# ---------------------------------------------------------------------- storage


def load(project: StoryProject) -> dict[str, Any]:
    data = project.read_json("workflow.json")
    data.setdefault("mode", "guided")
    data.setdefault("approvals", {})
    data.setdefault("history", [])
    data.setdefault("paused_at", None)
    return data


def save(project: StoryProject, data: dict[str, Any]) -> None:
    write_atomic(project.root / "workflow.json", json.dumps(data, indent=2), backup=False)


def set_mode(project: StoryProject, mode: str) -> dict[str, Any]:
    if mode not in GATES:
        raise InvalidInputError(f"mode must be one of {sorted(GATES)}")
    data = load(project)
    data["mode"] = mode
    data["history"].append({"at": utcnow(), "stage": None, "action": "mode", "note": mode})
    save(project, data)
    return data


def approve(project: StoryProject, stage: str, *, note: str = "", by: str = "you") -> dict[str, Any]:
    _check_stage(stage)
    data = load(project)
    data["approvals"][stage] = {"state": "approved", "note": note, "at": utcnow(), "by": by}
    data["history"].append({"at": utcnow(), "stage": stage, "action": "approved", "note": note})
    if data.get("paused_at") == stage:
        data["paused_at"] = None
    save(project, data)
    return data


def request_changes(project: StoryProject, stage: str, note: str) -> dict[str, Any]:
    _check_stage(stage)
    data = load(project)
    data["approvals"][stage] = {"state": "changes_requested", "note": note, "at": utcnow(), "by": "you"}
    data["history"].append({"at": utcnow(), "stage": stage, "action": "changes_requested", "note": note})
    save(project, data)
    return data


def unapprove(project: StoryProject, stage: str, note: str = "") -> dict[str, Any]:
    """Reopen a stage (after a change to approved canon, for instance)."""
    _check_stage(stage)
    data = load(project)
    data["approvals"].pop(stage, None)
    data["history"].append({"at": utcnow(), "stage": stage, "action": "reopened", "note": note})
    save(project, data)
    return data


def _check_stage(stage: str) -> None:
    if stage not in {s["id"] for s in STAGES}:
        raise InvalidInputError(f"Unknown stage {stage!r}", stages=[s["id"] for s in STAGES])


# ---------------------------------------------------------------------- gates


def blocked_by(project: StoryProject, step: str) -> str | None:
    """The stage whose approval the pipeline needs before `step` (world/cast/outline/write/assets/export) may run,
    or None. A stage counts as approved when it is not a gate in the current mode, or a person approved it."""
    data = load(project)
    stage = BEFORE_STEP.get(step)
    if stage is None or stage not in GATES[data["mode"]]:
        return None
    record = data["approvals"].get(stage)
    if record and record.get("state") == "approved":
        return None
    return stage


def pause(project: StoryProject, stage: str) -> None:
    data = load(project)
    if data.get("paused_at") != stage:
        data["paused_at"] = stage
        data["history"].append({"at": utcnow(), "stage": stage, "action": "paused", "note": "waiting for your review"})
        save(project, data)


# ---------------------------------------------------------------------- status


def _stage_facts(lib: Library, project: StoryProject, sid: str) -> dict[str, Any]:
    """What is done, decided and still to do in one stage, from the files."""
    config = project.config
    has = project.has
    facts: dict[str, Any] = {"done": [], "working": [], "next": [], "decisions": [], "complete": False, "inputs": []}
    if sid == "idea":
        facts["decisions"] = [f"Title: {config.title}", f"Premise: {config.premise[:160]}"]
        facts["complete"] = True
        facts["inputs"] = ["title", "premise", "rating", "chapters", "endings"]
    elif sid == "direction":
        from assetforge.story import direction as creative

        if config.direction is not None:
            facts["decisions"] = [creative.summary(config.direction)]
            facts["complete"] = True
            facts["done"] = ["Audience, genre, world, naming, narrative style and art style chosen"]
        else:
            facts["next"] = ["Set a creative direction (Settings → Creative direction)"]
        facts["inputs"] = ["creative direction (Settings)"]
    elif sid == "world":
        if has("world"):
            world = project.load("world")
            facts["decisions"] = [f"Title: {world.title or config.title}", f"Logline: {world.logline}", f"Setting: {world.setting[:160]}"]
            facts["done"] = ["Setting, history, culture and rules written", "Art and music direction written"]
            facts["complete"] = True
        else:
            facts["next"] = ["Build the world and the premise sheet"]
        facts["inputs"] = ["world.json (rewrite from Improve)"]
    elif sid == "characters":
        if has("cast"):
            cast = project.load("cast")
            facts["decisions"] = [f"{c.name} · {c.role}" for c in cast.characters]
            facts["done"] = [f"{len(cast.characters)} characters and {len(cast.locations)} places defined"]
            if has("names"):
                facts["done"].append("Naming guide written and followed")
            facts["complete"] = True
            facts["next"] = ["Review each character in the Character Studio"]
        else:
            facts["working"] = ["Protagonist, antagonist and supporting cast", "Wants, voices and looks"] if has("world") else []
            facts["next"] = ["Create the cast"]
        facts["inputs"] = ["Character Studio: identity, personality, narrative, visual identity"]
    elif sid in ("architecture", "planning", "routes"):
        if has("outline"):
            plan = project.load("outline")
            scenes = [s for s in plan.scenes if not s.ending]
            if sid == "architecture":
                facts["decisions"] = [f"{len(plan.scenes)} chapters, {len(plan.endings)} endings",
                                      "Stats: " + ", ".join(v.name for v in plan.variables)]  # fmt: skip
                facts["done"] = ["Season arc, stats and endings planned"]
                facts["complete"] = True
            elif sid == "planning":
                hooked = sum(1 for s in scenes if s.hook)
                facts["decisions"] = [f"{hooked} of {len(scenes)} chapters end on a hook"]
                facts["done"] = ["Every chapter has a purpose and an ending turn"] if hooked == len(scenes) else []
                facts["next"] = [] if hooked == len(scenes) else ["Give the remaining chapters a hook"]
                facts["complete"] = hooked >= max(1, len(scenes) - 1)
            else:
                forks = [s for s in plan.scenes if (s.choices and len({c.goto for c in s.choices}) > 1) or s.branches]
                flags = [v.name for v in plan.variables if v.kind == "flag"]
                facts["decisions"] = [f"{len(forks)} forks or gated exits", f"{len(flags)} decision flags"]
                facts["complete"] = bool(forks)
                facts["done"] = ["Routes fork and reconverge"] if forks else []
                facts["next"] = [] if forks else ["Add at least one route fork or gated ending"]
        else:
            facts["next"] = ["Plan the season"]
        facts["inputs"] = ["outline.json", "Improve with feedback"]
    elif sid == "writing":
        status = project.status()
        n, total = status["scenes_written"], status["scenes_planned"]
        facts["decisions"] = [f"{n} of {total} chapters written"] if total else []
        facts["done"] = [f"{n} chapters written"] if n else []
        facts["next"] = [f"{total - n} chapters to write"] if total and n < total else []
        facts["complete"] = bool(total) and n >= total
        facts["inputs"] = ["Refine a chapter", "Improve with feedback", "Alternatives"]
    elif sid == "evaluation":
        lint = project.read_json("lint.json")
        summary = lint.get("summary") or {}
        if summary:
            facts["decisions"] = [f"{summary.get('passing', 0)} of {summary.get('chapters', 0)} chapters pass the structural checks"]
        facts["done"] = ["Continuity check run"] if has("check") else []
        facts["next"] = [] if has("check") else ["Run the continuity check"]
        facts["complete"] = bool(summary) and has("check") and summary.get("passing") == summary.get("chapters")
        facts["inputs"] = ["Check (with any model)", "Improve"]
    elif sid == "visual":
        from assetforge import art

        profile = art.find_profile(lib, config.asset_project)
        facts["done"] = ["Art direction set for the project"] if profile else []
        facts["decisions"] = [f"Art style: {profile.direction.style or 'custom'}"] if profile else []
        try:
            from assetforge.story.characters import visual_summary

            vis = visual_summary(project)
            facts["decisions"] += [f"{vis['approved']} of {vis['total']} visual identities approved"]
            facts["complete"] = bool(profile) and vis["total"] > 0 and vis["approved"] == vis["total"]
            facts["next"] = [] if facts["complete"] else ["Approve each character's visual identity in the Character Studio"]
        except Exception:  # noqa: BLE001 - the studio is optional here
            facts["complete"] = bool(profile)
        facts["inputs"] = ["Character Studio → Visual identity", "Settings → Creative direction → Art"]
    elif sid == "assets":
        if has("script"):
            from assetforge.story.assets import coverage

            cov = coverage(lib, project)
            facts["decisions"] = [f"{cov['have']} of {cov['total']} assets made"]
            facts["complete"] = cov["total"] > 0 and cov["have"] >= cov["total"]
            facts["next"] = [] if facts["complete"] else [f"{cov['total'] - cov['have']} assets to make (estimate first)"]
        facts["inputs"] = ["Assets tab: estimate, make, redo"]
    elif sid == "final":
        built = (project.root / "build" / "index.html").exists()
        facts["done"] = ["Exported for the app"] if built else []
        facts["complete"] = built
        facts["next"] = [] if built else ["Export"]
        facts["inputs"] = ["Play", "Export"]
    return facts


def status(lib: Library, project: StoryProject, progress: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every stage with its state and plain-language facts, plus what is happening now and what needs a person."""
    data = load(project)
    mode = data["mode"]
    running = (progress or {}).get("running") or []
    active_kind = {r["task"].removeprefix("story.").split(".")[0] for r in running}
    stage_for_task = {"world": "world", "names": "characters", "cast": "characters", "outline": "architecture",
                      "scene": "writing", "refine": "writing", "improve": "writing", "check": "evaluation", "character": "characters"}  # fmt: skip
    working_stages = {stage_for_task.get(k) for k in active_kind} - {None}
    stages: list[dict[str, Any]] = []
    previous_complete = True
    needs_you: list[dict[str, str]] = []
    for s in STAGES:
        sid = s["id"]
        facts = _stage_facts(lib, project, sid)
        record = data["approvals"].get(sid)
        gated = sid in GATES[mode]
        if sid in working_stages:
            state = "working"
        elif record and record.get("state") == "changes_requested":
            state = "needs_revision"
        elif facts["complete"] and gated and not (record and record.get("state") == "approved"):
            state = "needs_approval"
        elif facts["complete"]:
            state = "done"
        elif previous_complete:
            state = "ready"
        else:
            state = "pending"
        if state in ("needs_approval", "needs_revision"):
            needs_you.append({"stage": sid, "title": s["title"], "action": "Review and approve" if state == "needs_approval" else "Revise and re-approve"})
        stages.append({**s, "state": state, "gated": gated, "approval": record, **facts})
        previous_complete = previous_complete and (facts["complete"] and (not gated or (record or {}).get("state") == "approved"))
    current = next((st for st in stages if st["state"] == "working"), None) or next((st for st in stages if st["state"] in ("needs_approval", "needs_revision")), None) \
        or next((st for st in stages if st["state"] == "ready"), None) or stages[-1]
    activity = {"title": f"Working: {current['title'].lower()}" if current["state"] == "working" else
                f"Waiting for you: {current['title'].lower()}" if current["state"] in ("needs_approval", "needs_revision") else
                f"Next: {current['title'].lower()}" if current["state"] == "ready" else f"{current['title']}: done",
                "detail": running[0]["label"] if running else current["goal"], "stage": current["id"]}  # fmt: skip
    return {"mode": mode, "stages": stages, "current": current["id"], "activity": activity, "needs_you": needs_you,
            "paused_at": data.get("paused_at"), "history": data["history"][-20:]}  # fmt: skip


# ---------------------------------------------------------------------- change impact


def character_impact(project: StoryProject, char_id: str) -> dict[str, Any]:
    """What a change to this character touches: planned chapters, written chapters, routes and art keys."""
    planned: list[str] = []
    written: list[str] = []
    routes: list[str] = []
    if project.has("outline"):
        plan = project.load("outline")
        planned = [s.title for s in plan.scenes if char_id in s.characters]
        routes = [v.name for v in plan.variables if v.character == char_id]
    for sid, scene in project.scenes().items():
        if any((n.kind == "say" and n.speaker == char_id) or (n.kind in ("show", "hide") and n.character == char_id) for n in scene.nodes):
            written.append(scene.title or sid)
    slug = project.config.slug
    return {"character": char_id, "planned_chapters": planned, "written_chapters": written, "routes": routes,
            "asset_keys": [f"vn/{slug}/char/{char_id}/*", f"vn/{slug}/cg/* (where on stage)"],
            "summary": (f"Changing this character affects {len(written)} written chapter(s) and {len(planned)} planned one(s)"
                        + (f", and {len(routes)} relationship route(s)" if routes else "") + "."
                        + (" Art already made for them keeps the old look until remade." if written else ""))}  # fmt: skip
