"""Narrative Dynamics: the planned shape of the season, chapter by chapter, against what the written chapters
deliver, and the branching graph the outline implies.

Tracks (one integer per chapter): `valence` (−5 despair … +5 joy), `tension`, `suspense`, `capability` (what the
protagonist can really do), `status` (how the world rates him), `family_conflict`, each 0–10. Milestones name the
victories, setbacks, reveals, awakenings, decisions and relationship turns the chapter carries. For a power
fantasy the engine is the gap between `capability` and `status`: it should open early and close late, in public.

`plan(ctx)` asks the writer to rate the outline (task `dynamics.plan`); `estimate(ctx, chapter)` rates a written
chapter from its lines (task `dynamics.<chapter>`), labelled as an estimate: nothing here measures feeling, a
model reads the text and says what it thinks. `review()` compares the two and applies the genre rules.
`graph(outline)` lays the branching out for a view: scenes by chapter, exits, decisions, convergence points.
Stored in `dynamics.json`.
"""

from __future__ import annotations

import statistics
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.story.models import Id, Outline, Scene
from assetforge.story.project import StoryProject

TRACKS = ("valence", "tension", "suspense", "capability", "status", "family_conflict")
TRACK_HELP = {
    "valence": "how the chapter feels to the reader: -5 despair, 0 mixed, +5 joy",
    "tension": "pressure on the protagonist right now, 0-10",
    "suspense": "how much the reader needs to know what happens next, 0-10",
    "capability": "what the protagonist can really do, 0 helpless - 10 decisive power",
    "status": "how the world rates him, 0 worthless - 10 recognised",
    "family_conflict": "how unresolved the family situation is, 0 at peace - 10 breaking",
}
MilestoneKind = Literal["victory", "setback", "reveal", "resolution", "awakening", "decision", "relationship"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Milestone(Strict):
    kind: MilestoneKind
    text: str = Field(description="What happens, one line")


class ChapterPoint(Strict):
    chapter: Id
    title: str = ""
    valence: int = Field(0, ge=-5, le=5)
    tension: int = Field(5, ge=0, le=10)
    suspense: int = Field(5, ge=0, le=10)
    capability: int = Field(1, ge=0, le=10)
    status: int = Field(1, ge=0, le=10)
    family_conflict: int = Field(5, ge=0, le=10)
    milestones: list[Milestone] = Field(default_factory=list)
    note: str = Field("", description="Why these numbers, one or two sentences")


class DynamicsPlan(Strict):
    points: list[ChapterPoint]
    rationale: str = ""


class Dynamics(Strict):
    planned: DynamicsPlan | None = None
    measured: dict[str, ChapterPoint] = Field(default_factory=dict, description="chapter id → estimate from the written text")
    reviewed: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------- storage


FILE = "dynamics.json"  # kept out of project.FILES: that registry imports its models at import time, and this module imports the project


def load(project: StoryProject) -> Dynamics:
    path = project.root / FILE
    if not path.exists():
        return Dynamics()
    return Dynamics.model_validate_json(path.read_text())


def save(project: StoryProject, data: Dynamics) -> None:
    from assetforge.story.project import write_atomic

    write_atomic(project.root / FILE, data.model_dump_json(indent=1))


# ---------------------------------------------------------------------- chapter order


def chapter_order(outline: Outline) -> list[tuple[str, str, list[Any]]]:
    """(chapter id, title, scenes) in reading order: declared chapters, else every scene is a chapter in BFS order."""
    from assetforge.story.progress import chapter_numbers

    numbers = chapter_numbers(outline)
    if outline.chapters:
        by_ch: dict[str, list[Any]] = {c.id: [] for c in outline.chapters}
        for s in outline.scenes:
            by_ch.setdefault(s.chapter or s.id, []).append(s)
        return [(c.id, c.title, by_ch.get(c.id, [])) for c in outline.chapters]
    ordered = sorted(outline.scenes, key=lambda s: (numbers.get(s.id, 99), s.id))
    return [(s.id, s.title, [s]) for s in ordered]


# ---------------------------------------------------------------------- the writer's ratings


def plan_prompt(outline: Outline, direction_text: str = "") -> str:
    rows = []
    for cid, title, scenes in chapter_order(outline):
        rows.append(f"- {cid}: {title}\n" + "\n".join(f"    · {s.title}: {s.summary}" + (f" Hook: {s.hook}" if s.hook else "") +
                                                     (f" Callback: {s.callback}" if s.callback else "") for s in scenes))
    endings = "\n".join(f"- {e.id} ({e.kind}): {e.summary}" for e in outline.endings)
    tracks = "\n".join(f"- {k}: {v}" for k, v in TRACK_HELP.items())
    return "\n\n".join(x for x in [
        "Rate the planned shape of this season, chapter by chapter, as integers. Rate what the plan says, not what you "
        "would prefer. Then name each chapter's milestones (victory, setback, reveal, resolution, awakening, decision, "
        "relationship) in one line each; a chapter may have none. Keep one point per chapter, in this order.",
        "TRACKS\n" + tracks,
        "CHAPTERS\n" + "\n".join(rows),
        "ENDINGS\n" + endings,
        direction_text,
        "Return JSON: {\"points\": [{\"chapter\", \"title\", \"valence\", \"tension\", \"suspense\", \"capability\", \"status\", "
        "\"family_conflict\", \"milestones\": [{\"kind\", \"text\"}], \"note\"}], \"rationale\"}.",
    ] if x)


def estimate_prompt(chapter_id: str, title: str, text_lines: list[str]) -> str:
    tracks = "\n".join(f"- {k}: {v}" for k, v in TRACK_HELP.items())
    return "\n\n".join([
        f"Read this written chapter ({chapter_id}: {title}) and rate what it actually delivers, as integers. This is an "
        "estimate from the text: rate the chapter the reader gets, not the intention. Name its milestones in one line each.",
        "TRACKS\n" + tracks,
        "CHAPTER\n" + "\n".join(text_lines),
        "Return JSON: {\"chapter\", \"title\", \"valence\", \"tension\", \"suspense\", \"capability\", \"status\", "
        "\"family_conflict\", \"milestones\": [{\"kind\", \"text\"}], \"note\"}.",
    ])


def offline_plan(outline: Outline) -> Any:
    """A deterministic rating for tests and demos: tension climbs, capability steps at awakening words, status lags."""
    def build(_: Any) -> dict[str, Any]:
        order = chapter_order(outline)
        n = max(1, len(order))
        points = []
        cap = 1
        for i, (cid, title, scenes) in enumerate(order):
            blob = " ".join(s.summary.lower() + " " + s.hook.lower() for s in scenes)
            if any(w in blob for w in ("awaken", "power", "discover")):
                cap = min(10, cap + 3)
            ms = []
            if "awaken" in blob:
                ms.append({"kind": "awakening", "text": f"{title}: an awakening"})
            if any(w in blob for w in ("lose", "fail", "cost")):
                ms.append({"kind": "setback", "text": f"{title}: a setback"})
            points.append({"chapter": cid, "title": title, "valence": -2 + (4 * i) // n, "tension": min(10, 3 + (7 * i) // n),
                           "suspense": min(10, 4 + (6 * i) // n), "capability": cap, "status": min(10, 1 + (4 * i) // n),
                           "family_conflict": max(0, 7 - (5 * i) // n), "milestones": ms, "note": "offline rating"})
        return {"points": points, "rationale": "offline: tension climbs, capability steps at awakenings, status lags"}
    return build


def offline_estimate(chapter_id: str, title: str) -> Any:
    def build(_: Any) -> dict[str, Any]:
        return {"chapter": chapter_id, "title": title, "valence": 0, "tension": 5, "suspense": 5, "capability": 2, "status": 1,
                "family_conflict": 6, "milestones": [{"kind": "decision", "text": "a choice the chapter makes the reader take"}], "note": "offline estimate"}
    return build


def plan(ctx: Any, *, fresh: bool = False) -> Dynamics:
    """Rate the outline. Keeps earlier measurements."""
    from assetforge.errors import NotFoundError
    from assetforge.story.steps import _ask

    project = ctx.project
    if not project.has("outline"):
        raise NotFoundError("No outline yet: plan the story before rating its dynamics")
    outline = project.load("outline", Outline)
    direction_text = ""
    d = project.config.direction
    if d is not None and d.custom.requirements:
        direction_text = "SEASON RULES\n" + d.custom.requirements[:2000]
    answer: DynamicsPlan = _ask(ctx, "dynamics.plan", plan_prompt(outline, direction_text), DynamicsPlan, offline_plan(outline), fresh=fresh)
    known = {cid for cid, _, _ in chapter_order(outline)}
    answer = answer.model_copy(update={"points": [p for p in answer.points if p.chapter in known]})
    data = load(project)
    data.planned = answer
    data.reviewed = review(data, outline)
    save(project, data)
    return data


def estimate(ctx: Any, chapter_id: str, *, fresh: bool = False) -> Dynamics:
    """Rate one written chapter (all the scenes the chapter holds) from its text."""
    from assetforge.errors import NotFoundError
    from assetforge.story.audio_direction import lines
    from assetforge.story.steps import _ask

    project = ctx.project
    outline = project.load("outline", Outline) if project.has("outline") else None
    scene_ids = [chapter_id]
    title = chapter_id
    if outline:
        for cid, t, scenes in chapter_order(outline):
            if cid == chapter_id:
                scene_ids, title = [s.id for s in scenes] or [chapter_id], t
                break
    text_lines: list[str] = []
    for sid in scene_ids:
        scene: Scene | None = project.load_scene(sid)
        if scene is None:
            continue
        for ln in lines(scene):
            who = ln.get("who")
            text_lines.append(f"{len(text_lines) + 1}. " + (f"{who}: " if who else "") + ln.get("text", ""))
    if not text_lines:
        raise NotFoundError(f"Chapter {chapter_id!r} is not written yet")
    point: ChapterPoint = _ask(ctx, f"dynamics.{chapter_id}", estimate_prompt(chapter_id, title, text_lines[:400]), ChapterPoint,
                               offline_estimate(chapter_id, title), fresh=fresh)
    data = load(project)
    data.measured[chapter_id] = point.model_copy(update={"chapter": chapter_id, "title": title})
    if outline:
        data.reviewed = review(data, outline)
    save(project, data)
    return data


# ---------------------------------------------------------------------- the rules


def review(data: Dynamics, outline: Outline | None = None) -> dict[str, Any]:
    """Planned vs measured, and the shape rules of the genre. Findings carry the chapters they point at."""
    findings: list[dict[str, Any]] = []
    planned = {p.chapter: p for p in (data.planned.points if data.planned else [])}
    order = [cid for cid, _, _ in chapter_order(outline)] if outline else list(planned)
    pts = [planned[c] for c in order if c in planned]

    def warn(code: str, message: str, chapters: list[str], level: str = "warning") -> None:
        findings.append({"level": level, "code": code, "message": message, "chapters": chapters})

    if len(pts) >= 4:
        tension = [p.tension for p in pts]
        if statistics.pstdev(tension) < 1.0:
            warn("flat_tension", f"tension stays around {statistics.mean(tension):.0f} for the whole season: no rise, no release", order)
        # a plateau at the top is as flat as one in the middle: nothing can rise from 10. Branches of one chapter
        # (same reading-order number) count once, at the higher of their tensions.
        numbers: dict[str, int] = {}
        if outline is not None:
            from assetforge.story.progress import chapter_numbers

            nums = chapter_numbers(outline)
            for cid, _, scenes in chapter_order(outline):
                numbers[cid] = min((nums.get(sc.id, 99) for sc in scenes), default=99)
        by_number: dict[int, list[Any]] = {}
        num_of: dict[str, int] = {}
        for i, p in enumerate(pts):
            num_of[p.chapter] = numbers.get(p.chapter, i + 1)
            by_number.setdefault(num_of[p.chapter], []).append(p)
        run_hi: list[str] = []
        for _, group in sorted(by_number.items()):
            run_hi = run_hi + [g.chapter for g in group] if max(g.tension for g in group) >= 9 else []
            if len({num_of[c] for c in run_hi}) >= 4:
                warn("tension_plateau", "four chapters in a row at tension 9-10: the reader needs a release before the next rise", run_hi)
                break
        ahead = [p.chapter for p in pts if p.status > p.capability + 1]
        if ahead:
            warn("status_ahead_of_capability", "the world rates him above what he can do: the genre's engine runs the other way (power first, recognition later)", ahead)
        gaps = [p.capability - p.status for p in pts]
        if max(gaps) >= 3 and gaps[-1] >= max(gaps) - 1:
            warn("no_recognition", "capability pulls far ahead of status and the gap never closes: the season owes the reader a recognition scene", [pts[-1].chapter])
        kinds = [{m.kind for m in p.milestones} for p in pts]
        run = 0
        for p, ks in zip(pts, kinds):
            run = run + 1 if "victory" in ks and "setback" not in ks else 0
            if run >= 3:
                warn("constant_victory", "three chapters of wins without a cost: tension cannot build", [p.chapter])
                break
        low = [p.chapter for p in pts if p.valence <= -2]
        if len(low) >= 3 and not any("victory" in ks for ks in kinds[: len(low)]):
            warn("constant_suffering", "the opening chapters stay bleak with no win: readers need hope early", low[:3])
        if not any("awakening" in ks for ks in kinds):
            warn("no_awakening", "no chapter is marked as an awakening", [], level="note")
        if not any("decision" in ks for ks in kinds):
            warn("no_decision_milestone", "no chapter names a decision the season remembers", [], level="note")
    deviations = []
    for cid, m in data.measured.items():
        p = planned.get(cid)
        if not p:
            continue
        for t in TRACKS:
            delta = getattr(m, t) - getattr(p, t)
            if abs(delta) >= 3:
                deviations.append({"chapter": cid, "track": t, "planned": getattr(p, t), "measured": getattr(m, t), "delta": delta})
    for dv in deviations:
        warn("deviation", f"{dv['chapter']}: {dv['track']} planned {dv['planned']}, the text reads as {dv['measured']} (an estimate)", [dv["chapter"]])
    return {"ok": not any(f["level"] == "error" for f in findings), "findings": findings, "deviations": deviations,
            "planned_chapters": len(pts), "measured_chapters": len(data.measured), "method": "the writer rated the plan; the text ratings are a model's estimate, not a measurement"}


# ---------------------------------------------------------------------- branching graph


def graph(outline: Outline, script_scenes: dict[str, Scene] | None = None) -> dict[str, Any]:
    """Scenes grouped by chapter with every exit, the decisions (what each option sets, when the scene is written),
    convergence points (two or more ways in) and endings."""
    from assetforge.story.progress import chapter_numbers

    numbers = chapter_numbers(outline)
    nodes = []
    edges = []
    indeg: dict[str, int] = {}
    for s in outline.scenes:
        nodes.append({"id": s.id, "title": s.title, "chapter": s.chapter or s.id, "number": numbers.get(s.id), "ending": s.ending,
                      "location": s.location, "hook": s.hook, "callback": s.callback})
        sets_by_goto: dict[str, list[str]] = {}
        if script_scenes and s.id in script_scenes:
            for n in script_scenes[s.id].nodes:
                if n.kind == "choice":
                    for o in n.choices or []:
                        if o.goto:
                            sets_by_goto.setdefault(o.goto, []).extend(f"{a.var} {a.op} {a.value}" for a in (o.set or []))
        for c in s.choices:
            edges.append({"from": s.id, "to": c.goto, "kind": "choice", "label": c.text, "sets": sets_by_goto.get(c.goto, [])})
            indeg[c.goto] = indeg.get(c.goto, 0) + 1
        for b in s.branches:
            edges.append({"from": s.id, "to": b.goto, "kind": "branch", "label": " and ".join(f"{w.var} {w.op} {w.value}" for w in b.when), "sets": []})
            indeg[b.goto] = indeg.get(b.goto, 0) + 1
        if s.next:
            edges.append({"from": s.id, "to": s.next, "kind": "next", "label": "", "sets": []})
            indeg[s.next] = indeg.get(s.next, 0) + 1
    decisions = [{"scene": s.id, "title": s.title, "options": [{"text": c.text, "goto": c.goto} for c in s.choices]} for s in outline.scenes if s.choices]
    convergence = [sid for sid, n in indeg.items() if n >= 2]
    chapters = [{"id": cid, "title": title, "scenes": [s.id for s in scenes]} for cid, title, scenes in chapter_order(outline)]
    return {"start": outline.start, "chapters": chapters, "nodes": nodes, "edges": edges, "decisions": decisions, "convergence": convergence,
            "endings": [{"id": e.id, "title": e.title, "kind": e.kind, "requires": [f"{c.var} {c.op} {c.value}" for c in e.requires]} for e in outline.endings],
            "variables": [{"name": v.name, "kind": v.kind, "meaning": v.meaning} for v in outline.variables]}


def view(project: StoryProject) -> dict[str, Any]:
    """Everything the Dynamics tab shows."""
    data = load(project)
    out: dict[str, Any] = {"tracks": list(TRACKS), "help": TRACK_HELP, "planned": data.planned.model_dump(mode="json") if data.planned else None,
                           "measured": {k: v.model_dump(mode="json") for k, v in data.measured.items()}, "reviewed": data.reviewed, "graph": None}
    if project.has("outline"):
        outline = project.load("outline", Outline)
        written = {}
        for s in outline.scenes:
            sc = project.load_scene(s.id)
            if sc is not None:
                written[s.id] = sc
        out["graph"] = graph(outline, written)
        out["chapters"] = [{"id": cid, "title": t, "written": all(s.id in written for s in scenes) and bool(scenes)} for cid, t, scenes in chapter_order(outline)]
        if data.planned and not data.reviewed:
            out["reviewed"] = review(data, outline)
    return out
