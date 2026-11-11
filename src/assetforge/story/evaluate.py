"""Independent editorial evaluation of written chapters against the owner's fifteen questions (brief of
2026-11-11), with scene evidence for every answer and a targeted change for every "no" or "partly".

The evaluator is meant to be a different model from the writer (the writer is ChatGPT through the Codex CLI;
the evaluation runs on Claude Opus when it is signed in), so the review is not the author grading itself. Nothing
here claims to be a reader: it is one careful editor's reading, labelled as such. Stored in `evaluation.json`;
`improve` can take the proposed changes as feedback.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.story.models import Outline, Scene
from assetforge.story.project import StoryProject

QUESTIONS: list[tuple[str, str]] = [
    ("protagonist", "Is the protagonist emotionally compelling before he has power?"),
    ("family_backstory", "Is the family backstory believable?"),
    ("father", "Does the father's situation have real moral complexity (a real mistake, real exploitation, no easy excuse)?"),
    ("mother", "Does the mother feel like a genuine character with her own decisions, not a person to be rescued?"),
    ("power_system", "Is the power system coherent and distinctive, with rules, costs and limits the text respects?"),
    ("first_awakening", "Is the first awakening meaningful: an opportunity that also brings danger or obligation?"),
    ("second_awakening_prep", "Is the later second awakening being prepared (seeds, rules the reader can feel)?"),
    ("progression", "Does the story give satisfying progression: earned, with failure and cost?"),
    ("supporting_cast", "Are the supporting characters memorable, with their own wants?"),
    ("antagonists", "Are the antagonists more than generic obstacles?"),
    ("choices", "Do the player's decisions matter, with observable consequences?"),
    ("pacing", "Are the chapters paced for mobile reading: hardship shown through events, hope early, no long exposition?"),
    ("payoff", "Does each chapter end with an emotional payoff and a reason to continue?"),
    ("originality", "Does the story avoid the distinctive elements of Solo Leveling (system interface, shadows, letter ranks, gates, its scenes)?"),
    ("continue", "Would an unfamiliar reader voluntarily continue past these chapters?"),
]
Verdict = Literal["yes", "partly", "no"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Answer(Strict):
    id: str
    verdict: Verdict
    evidence: str = Field(description="Concrete lines or moments from the chapters, quoted or precisely described")
    problem: str = Field("", description="What is wrong, when the verdict is not yes")
    change: str = Field("", description="A targeted change: which chapter, what to add, cut or move")


class Evaluation(Strict):
    answers: list[Answer]
    strongest: str = Field("", description="The single best thing in these chapters, with its moment")
    weakest: str = Field("", description="The single biggest problem, with its moment")
    summary: str = ""
    evaluated_at: str = ""
    scenes: list[str] = Field(default_factory=list)
    evaluator: dict[str, Any] = Field(default_factory=dict)


FILE = "evaluation.json"


def load(project: StoryProject) -> Evaluation | None:
    path = project.root / FILE
    return Evaluation.model_validate_json(path.read_text()) if path.exists() else None


def save(project: StoryProject, data: Evaluation) -> None:
    from assetforge.story.project import write_atomic

    write_atomic(project.root / FILE, data.model_dump_json(indent=1))


def chapter_text(scene: Scene, *, limit: int = 420) -> list[str]:
    from assetforge.story.audio_direction import lines

    out = []
    for ln in lines(scene):
        who = ln.get("who")
        if ln.get("kind") == "choice":
            out.append(f"{len(out) + 1}. [CHOICE] {ln.get('text', '')}" + (f" (sets {', '.join(ln['sets'])})" if ln.get("sets") else ""))
        elif ln.get("kind") == "place":
            out.append(f"{len(out) + 1}. [PLACE] {ln.get('text', '')}")
        elif ln.get("kind") in ("say", "narrate"):
            out.append(f"{len(out) + 1}. " + (f"{who}: " if who else "") + ln.get("text", ""))
    return out[:limit]


def prompt(project: StoryProject, scenes: list[Scene], outline: Outline | None) -> str:
    cfg = project.config
    qs = "\n".join(f"- {qid}: {q}" for qid, q in QUESTIONS)
    plan = ""
    if outline:
        plan = "THE SEASON PLAN (for context; judge the written chapters, not the plan)\n" + "\n".join(
            f"- {s.title}: {s.summary[:300]}" for s in outline.scenes)
    chapters = "\n\n".join(f"=== {s.title or s.id} ({s.id}) ===\n" + "\n".join(chapter_text(s)) for s in scenes)
    return "\n\n".join(x for x in [
        "You are an independent editor reading the opening chapters of an interactive visual novel. Answer each question "
        "with yes, partly or no, and for every answer give concrete evidence from the text (quote a line or name the exact "
        "moment). For every partly or no, name the problem and a targeted change (which chapter, what to add, cut or move). "
        "Do not grade generously; a reader who stops reading is the only failure that matters. Name the single strongest "
        "thing and the single weakest thing, each with its moment.",
        f"TITLE: {cfg.title}\nPREMISE: {cfg.premise}",
        "QUESTIONS\n" + qs,
        plan,
        "CHAPTERS\n" + chapters,
        "Return JSON: {\"answers\": [{\"id\", \"verdict\", \"evidence\", \"problem\", \"change\"}] (one per question id, in order), "
        "\"strongest\", \"weakest\", \"summary\"}.",
    ] if x)


def offline(scenes: list[Scene]) -> Any:
    def build(_: Any) -> dict[str, Any]:
        first = scenes[0].id if scenes else "s01"
        return {"answers": [{"id": qid, "verdict": "partly" if i % 3 == 0 else "yes", "evidence": f"{first}: line 1", "problem": "offline" if i % 3 == 0 else "",
                             "change": f"{first}: tighten" if i % 3 == 0 else ""} for i, (qid, _) in enumerate(QUESTIONS)],
                "strongest": f"{first}: the opening", "weakest": f"{first}: the exposition", "summary": "offline evaluation"}
    return build


def evaluate(ctx: Any, scene_ids: list[str] | None = None, *, fresh: bool = False) -> Evaluation:
    """Evaluate the written chapters (all, or the given ids) and store the result."""
    from assetforge.errors import NotFoundError
    from assetforge.story.steps import _ask

    project = ctx.project
    outline = project.load("outline", Outline) if project.has("outline") else None
    ids = scene_ids or ([s.id for s in outline.scenes] if outline else [])
    scenes = [sc for sid in ids if (sc := project.load_scene(sid)) is not None]
    if not scenes:
        raise NotFoundError("No written chapters to evaluate")
    answer: Evaluation = _ask(ctx, "evaluation", prompt(project, scenes, outline), Evaluation, offline(scenes), fresh=fresh)
    known = {qid for qid, _ in QUESTIONS}
    answers = [a for a in answer.answers if a.id in known]
    seen = {a.id for a in answers}
    answers += [Answer(id=qid, verdict="partly", evidence="", problem="the evaluator did not answer this question", change="") for qid, _ in QUESTIONS if qid not in seen]
    call = ctx.calls[-1] if ctx.calls else {}
    result = answer.model_copy(update={"answers": answers, "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "scenes": [s.id for s in scenes],
                                       "evaluator": {"provider": call.get("provider"), "model": call.get("model"), "choice": call.get("choice")}})
    save(project, result)
    return result


def summary(data: Evaluation) -> dict[str, Any]:
    counts = {"yes": 0, "partly": 0, "no": 0}
    for a in data.answers:
        counts[a.verdict] += 1
    changes = [{"id": a.id, "verdict": a.verdict, "change": a.change} for a in data.answers if a.verdict != "yes" and a.change]
    return {"counts": counts, "changes": changes, "strongest": data.strongest, "weakest": data.weakest, "evaluator": data.evaluator,
            "note": "one editor's reading with evidence, not a measurement; the playtest is the test"}


def as_feedback(data: Evaluation) -> str:
    """The evaluation's changes as a note the `improve` step can take."""
    rows = [f"- {a.id} ({a.verdict}): {a.problem} → {a.change}" for a in data.answers if a.verdict != "yes" and (a.problem or a.change)]
    return "Editorial evaluation found these problems; fix them without changing what the chapters do well:\n" + "\n".join(rows)
