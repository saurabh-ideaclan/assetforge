"""Narrative clarity: can a first-time reader follow the chapter? (Owner's brief of 2026-11-11.)

Three separate gates, because a coherent outline is not a readable script and a readable script is not yet a
playable experience:

1. `reader_view(scene)`: the chapter exactly as the player meets it (narration, labelled speech, every choice with
   its reactions, place changes), with nothing from the planning files. Every audit below reads only this.
2. `terms_plan(ctx)` (Stage A): the knowledge model. For each fictional term the world uses, when the reader first
   needs it, how it is introduced (action, observation, a line someone would say) and what stays hidden until later.
   Stored in `clarity.json`; the writer's prompt gets the slice for the chapter it is writing.
3. `audit(ctx, scene)` (Stages C–F): a cold read by a model that is given the reader view of this chapter and of the
   chapters before it on the path, and nothing else. It answers the ten first-time-reader questions, checks every
   fictional term for context, walks the dialogue exchange by exchange for continuity, checks each choice for
   understood stakes, names unintentional confusion apart from intentional mystery, and proposes the exact fix for
   each passage. `revise(ctx, scene, report)` (Stage G) rewrites the chapter from those fixes, keeping plot, choices
   and ending; the linter and a second audit decide whether the revision is kept. `leaks(scene)` (Stage H) finds
   workflow metadata that would reach the player as text.

`steps.write` runs the audit after the linter and revises once when the verdict is `revise`.
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.story.models import Outline, Scene
from assetforge.story.project import StoryProject

PERSPECTIVE = ("Narration is in the second person, present tense ('You brace the shaft'): the player is the protagonist. "
               "Spoken lines are labelled with the speaker's name; the protagonist's own spoken lines are labelled too.")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------- 1. the reader's view


def reader_view(scene: Scene, names: dict[str, str] | None = None, *, numbered: bool = True) -> str:
    """The chapter as the player reads it. Route-only lines are marked `[on some routes]` so a reviewer knows
    they are not seen by everyone; nothing else from the files appears (no ids, conditions, expressions, cues)."""
    names = names or {}
    who = lambda cid: names.get(cid, cid.replace("_", " ").title()) if cid else ""  # noqa: E731
    out: list[str] = []

    def add(text: str) -> None:
        out.append(f"{len(out) + 1}. {text}" if numbered else text)

    for n in scene.nodes:
        route = " [on some routes]" if n.when else ""
        if n.kind == "narrate":
            add((n.text or "") + route)
        elif n.kind == "say":
            add(f"{who(n.speaker)}: {n.text or ''}{route}")
        elif n.kind == "place":
            where = n.location.replace("_", " ") if n.location else ""
            add(f"— {where}{', ' + n.time if n.time else ''} —" + (f" {n.text}" if n.text else "") + route)
        elif n.kind == "cg":
            add(f"[illustration: {n.text or n.cg}]" + route)
        elif n.kind == "sfx":
            add(f"[sound: {n.text or n.cue}]" + route)
        elif n.kind == "choice":
            exit_choice = bool(n.choices) and all(c.goto for c in n.choices)
            add(("[CHOICE, ends the chapter]" if exit_choice else "[CHOICE]") + route)
            for i, c in enumerate(n.choices, 1):
                out.append(f"    ({i}) {c.text}" + (" [only offered on some routes]" if c.require else ""))
                for ln in c.reaction:
                    out.append(f"        {who(ln.speaker) + ': ' if ln.speaker else ''}{ln.text}")
        elif n.kind == "ending":
            add("[THE END]")
    return "\n".join(out)


def scene_names(project: StoryProject) -> dict[str, str]:
    if not project.has("cast"):
        return {}
    return {c.id: c.name for c in project.load("cast").characters}


def path_before(outline: Outline, scene_id: str) -> list[str]:
    """One route to the chapter: the scene ids a reader could have passed through (first exits), in order."""
    by_id = {s.id: s for s in outline.scenes}
    path: list[str] = []
    seen: set[str] = set()
    cur: str | None = outline.start
    while cur and cur != scene_id and cur in by_id and cur not in seen:
        seen.add(cur)
        path.append(cur)
        s = by_id[cur]
        exits = s.exits()
        cur = next((e for e in exits if e == scene_id), exits[0] if exits else None)
    return path if cur == scene_id else path


# ---------------------------------------------------------------------- 2. the knowledge model


class Term(Strict):
    term: str
    meaning: str = Field(description="What the reader needs to understand about it, in one sentence")
    first_needed: str = Field(description="Scene id of the first chapter where a scene depends on it")
    introduce_by: str = Field(description="How it reaches the reader: an action, an observation, a line someone would naturally say")
    concealed: str = Field("", description="What about it stays hidden for later, and until which chapter")


class TermsPlan(Strict):
    terms: list[Term]
    note: str = ""


class Clarity(Strict):
    terms: TermsPlan | None = None
    made_at: str = ""


FILE = "clarity.json"


def load(project: StoryProject) -> Clarity:
    path = project.root / FILE
    return Clarity.model_validate_json(path.read_text()) if path.exists() else Clarity()


def save(project: StoryProject, data: Clarity) -> None:
    from assetforge.story.project import write_atomic

    write_atomic(project.root / FILE, data.model_dump_json(indent=1))


def terms_prompt(project: StoryProject, outline: Outline) -> str:
    world = project.load("world") if project.has("world") else None
    bible = world.model_dump(exclude={"art_direction", "music_direction"}) if world else {}
    chapters = "\n".join(f"- {s.id}: {s.title}: {s.summary[:400]}" for s in outline.scenes)
    return "\n\n".join([
        "You are the narrative comprehension editor. List every fictional term, institution, rule or ability a reader "
        "must understand to follow this story (names of systems, ranks, costs, factions, places with special meaning, "
        "the protagonist's condition). For each: its meaning for the reader in one plain sentence; the first chapter "
        "whose scene depends on it; how it should reach the reader there (an action seen, an object, a line a character "
        "would naturally say, never a lecture); and what about it must stay concealed, until which chapter. Order by "
        "first_needed. Keep the list to what the reader needs, not everything the bible knows.",
        "WORLD:\n" + str(bible),
        "CHAPTERS:\n" + chapters,
        "Return JSON: {\"terms\": [{\"term\", \"meaning\", \"first_needed\", \"introduce_by\", \"concealed\"}], \"note\"}.",
    ])


def offline_terms(outline: Outline) -> Any:
    def build(_: Any) -> dict[str, Any]:
        first = outline.scenes[0].id if outline.scenes else "s01"
        return {"terms": [{"term": "the trial", "meaning": "a test that decides who may work", "first_needed": first,
                           "introduce_by": "a clerk refusing someone at a desk", "concealed": ""}], "note": "offline"}
    return build


def terms_plan(ctx: Any, *, fresh: bool = False) -> Clarity:
    from assetforge.errors import NotFoundError
    from assetforge.story.steps import _ask

    project = ctx.project
    if not project.has("outline"):
        raise NotFoundError("No outline yet")
    outline = project.load("outline", Outline)
    plan: TermsPlan = _ask(ctx, "clarity.terms", terms_prompt(project, outline), TermsPlan, offline_terms(outline), fresh=fresh)
    data = load(project)
    data.terms = plan
    data.made_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    save(project, data)
    return data


def knowledge_block(project: StoryProject, outline: Outline, scene_id: str) -> str:
    """The slice of the knowledge model the writer of `scene_id` needs: what the reader has met, what this chapter
    introduces and how, what stays hidden. Empty when no plan exists."""
    data = load(project)
    if not data.terms or not data.terms.terms:
        return ""
    from assetforge.story.progress import chapter_numbers

    numbers = chapter_numbers(outline)
    here = numbers.get(scene_id, 1)
    met, now, later = [], [], []
    for t in data.terms.terms:
        n = numbers.get(t.first_needed, 99)
        if n < here:
            met.append(f"{t.term} ({t.meaning})")
        elif n == here:
            now.append(f"{t.term}: {t.meaning} Introduce it by: {t.introduce_by}." + (f" Keep hidden: {t.concealed}" if t.concealed else ""))
        else:
            later.append(t.term + (f" (hidden: {t.concealed})" if t.concealed else ""))
    return "\n".join(x for x in [
        "READER KNOWLEDGE (the reader has NOT read the bible; write for what they have seen):",
        ("- Already met in earlier chapters, may be used without explanation: " + "; ".join(met)) if met else "- Nothing from the world has been shown yet: this chapter must make every term it uses understandable in the moment.",
        ("- Introduce in THIS chapter, the first time a scene depends on it:\n  " + "\n  ".join(now)) if now else "",
        ("- Not yet: do not rely on the reader knowing " + "; ".join(later)) if later else "",
    ] if x)


# ---------------------------------------------------------------------- 3. the cold read, continuity and choice audits


class Passage(Strict):
    where: str = Field(description="Line numbers from the reader view, e.g. '12-15'")
    problem: str
    fix: str = Field(description="The exact revision: new or reordered lines, a reaction, an observation; not 'clarify'")


class TermCheck(Strict):
    term: str
    where: str
    context_given: bool
    fix: str = ""


class ChoiceCheck(Strict):
    where: str
    decision: str = Field(description="What the player is deciding, in the reader's words")
    understood: bool
    missing: str = Field("", description="What the reader needs before this choice and does not have")
    fix: str = ""


class SectionRead(Strict):
    where: str = Field(description="Line range of this scene section")
    place: str = Field(description="Where it happens, as the reader can tell; '?' when they cannot")
    present: str
    wants: str = Field(description="What the protagonist wants here")
    obstacle: str
    why_behave: str = Field(description="Why the characters behave as they do, as far as the reader can tell")
    event: str = Field(description="The important event, and what changed because of it")
    easy: bool = Field(description="A first-time reader follows this section without guessing")
    note: str = ""


class Mystery(Strict):
    question: str
    intentional: bool
    note: str = ""


class ClarityReport(Strict):
    scene: str = ""
    verdict: Literal["ok", "revise"]
    sections: list[SectionRead]
    terms: list[TermCheck]
    dialogue: list[Passage] = Field(default_factory=list, description="Exchanges that sound unnatural or do not answer the line before")
    motivation: list[Passage] = Field(default_factory=list, description="Actions the reader cannot explain")
    choices: list[ChoiceCheck]
    continuity: list[Passage] = Field(default_factory=list, description="Jumps in place, time or logic")
    emotion: list[Passage] = Field(default_factory=list, description="Where the intended feeling does not reach the reader")
    mysteries: list[Mystery] = Field(default_factory=list)
    summary: str = ""
    audited_at: str = ""
    reviewer: dict[str, Any] = Field(default_factory=dict)


def audit_prompt(view: str, earlier: list[tuple[str, str]], title: str) -> str:
    before = "\n\n".join(f"=== EARLIER CHAPTER: {t} ===\n{v}" for t, v in earlier) if earlier else "(this is the first chapter)"
    return "\n\n".join([
        "You are a first-time reader and a developmental editor at once. You have NOT read any story bible, character "
        "profile, outline or later chapter: only the text below, which is exactly what the player sees. Judge "
        "comprehension, not quality of ideas. Intentional mystery is fine; unintentional confusion is not.",
        "WHAT THE PLAYER HAS READ BEFORE THIS CHAPTER\n" + before,
        f"THE CHAPTER UNDER REVIEW: {title}\n{view}",
        "Do all of this, with line numbers:\n"
        "1. SECTIONS: split the chapter into its scene sections (each place or time). For each, answer as the reader: "
        "where, who is present, what the protagonist wants, the obstacle, why the characters behave as they do, the "
        "important event and what changed. Mark `easy` false when any answer needs information the text has not given.\n"
        "2. TERMS: every fictional term, institution, rule or ability the chapter uses; whether the text gives enough "
        "context at that point for the reader to understand its relevance; the fix when it does not (an action, an "
        "observation or a line someone would naturally say, never a lecture).\n"
        "3. DIALOGUE: walk every exchange. Flag lines that do not respond to the line before, cryptic or compressed "
        "lines, dramatic one-liners where a person would speak plainly, replies that ignore what was said, new topics "
        "with no bridge. For each give the exact fix (rewrite, reorder, a reaction or observation to add).\n"
        "4. MOTIVATION: actions the reader cannot explain from what they have seen.\n"
        "5. CHOICES: for each choice, what the player is deciding in the reader's own words, whether the immediate "
        "stakes and trade-offs are understood before deciding, what is missing, and the fix.\n"
        "6. CONTINUITY: jumps in place, time or logic without a bridge.\n"
        "7. EMOTION: where the reader is told nothing of why the moment matters to the protagonist.\n"
        "8. MYSTERIES: unanswered questions, each marked intentional (curiosity) or not (friction).\n"
        "Verdict `revise` when any section is not easy, any choice is not understood, or there are three or more "
        "dialogue/continuity problems; else `ok`. Summary in three sentences.",
        "Return JSON: {\"verdict\", \"sections\": [{\"where\", \"place\", \"present\", \"wants\", \"obstacle\", \"why_behave\", "
        "\"event\", \"easy\", \"note\"}], \"terms\": [{\"term\", \"where\", \"context_given\", \"fix\"}], \"dialogue\": [{\"where\", "
        "\"problem\", \"fix\"}], \"motivation\": [...], \"choices\": [{\"where\", \"decision\", \"understood\", \"missing\", \"fix\"}], "
        "\"continuity\": [...], \"emotion\": [...], \"mysteries\": [{\"question\", \"intentional\", \"note\"}], \"summary\"}.",
    ])


def offline_report(scene: Scene) -> Any:
    def build(_: Any) -> dict[str, Any]:
        n = len(scene.nodes)
        return {"verdict": "revise" if n > 6 else "ok",
                "sections": [{"where": f"1-{n}", "place": scene.location, "present": "the cast", "wants": "to get through the day", "obstacle": "a clerk",
                              "why_behave": "clear enough", "event": "the day turns", "easy": n <= 6, "note": "offline"}],
                "terms": [{"term": "the trial", "where": "1", "context_given": False, "fix": "show the clerk's board before the refusal"}],
                "dialogue": [{"where": "3-4", "problem": "the reply ignores the line before", "fix": "answer the question first"}] if n > 6 else [],
                "motivation": [], "choices": [{"where": "5", "decision": "keep the paper or take the money", "understood": n <= 6,
                                              "missing": "" if n <= 6 else "why the paper matters", "fix": "" if n <= 6 else "one line on what the paper proves"}],
                "continuity": [], "emotion": [], "mysteries": [{"question": "why the card says what it says", "intentional": True, "note": ""}],
                "summary": "offline cold read"}
    return build


def report_path(project: StoryProject, scene_id: str):
    return project.root / "clarity" / f"{scene_id}.json"


def load_report(project: StoryProject, scene_id: str) -> ClarityReport | None:
    p = report_path(project, scene_id)
    return ClarityReport.model_validate_json(p.read_text()) if p.exists() else None


def audit(ctx: Any, scene_id: str, *, fresh: bool = False, provider: str | None = None, model: str | None = None) -> ClarityReport:
    """The cold read of one chapter, from the reader's view only. `provider`/`model` default to the story plan;
    the owner's brief asks for Sonnet for structured comprehension checks, which the caller passes."""
    from assetforge.errors import NotFoundError
    from assetforge.story.project import write_atomic
    from assetforge.story.steps import Ctx, _ask

    project = ctx.project
    scene = project.load_scene(scene_id)
    if scene is None:
        raise NotFoundError(f"Chapter {scene_id!r} is not written yet")
    names = scene_names(project)
    earlier: list[tuple[str, str]] = []
    if project.has("outline"):
        outline = project.load("outline", Outline)
        for sid in path_before(outline, scene_id):
            sc = project.load_scene(sid)
            if sc is not None:
                earlier.append((sc.title or sid, reader_view(sc, names)))
    view = reader_view(scene, names)
    worker = Ctx(lib=ctx.lib, project=project, provider=provider or ctx.provider, model=model or ctx.model, fresh=ctx.fresh,
                 settings=ctx.settings, allow_paid=ctx.allow_paid, calls=ctx.calls)
    report: ClarityReport = _ask(worker, f"clarity.{scene_id}", audit_prompt(view, earlier, scene.title or scene_id), ClarityReport,
                                 offline_report(scene), fresh=fresh)
    call = ctx.calls[-1] if ctx.calls else {}
    report = report.model_copy(update={"scene": scene_id, "audited_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                       "reviewer": {"provider": call.get("provider"), "model": call.get("model")}})
    write_atomic(report_path(project, scene_id), report.model_dump_json(indent=1))
    return report


def problems(report: ClarityReport) -> list[str]:
    """The report's fixes as a numbered revision note."""
    rows: list[str] = []
    for s in report.sections:
        if not s.easy:
            rows.append(f"Section {s.where}: a first-time reader cannot tell {('where (' + s.place + ')') if s.place in ('?', '') else ''} "
                        f"{s.note or 'what is happening'}. Make place, people and what the protagonist wants clear in the first lines of the section.")
    for t in report.terms:
        if not t.context_given:
            rows.append(f"Term '{t.term}' at {t.where}: {t.fix or 'give the reader its relevance through an action, an object or a natural line'}")
    for p in report.dialogue:
        rows.append(f"Dialogue {p.where}: {p.problem} → {p.fix}")
    for p in report.motivation:
        rows.append(f"Motivation {p.where}: {p.problem} → {p.fix}")
    for c in report.choices:
        if not c.understood:
            rows.append(f"Choice {c.where} ({c.decision}): the reader lacks {c.missing} → {c.fix}")
    for p in report.continuity:
        rows.append(f"Continuity {p.where}: {p.problem} → {p.fix}")
    for p in report.emotion:
        rows.append(f"Emotion {p.where}: {p.problem} → {p.fix}")
    for m in report.mysteries:
        if not m.intentional:
            rows.append(f"Unintended confusion: {m.question} → answer it in the scene or cut what raises it")
    return rows


def revision_note(report: ClarityReport, extra: str = "") -> str:
    rows = problems(report)
    return "\n".join(x for x in [
        "CLARITY REVISION. A first-time reader (no bible, no outline) could not follow parts of this chapter. Fix each "
        "point below in the smallest number of lines; keep the plot, every choice and its effects, the chapter's ending "
        "and its length. Clarity without simplification: keep the mystery, add the context the present scene needs. "
        "A line must answer the line before it; people speak plainly most of the time; when the place or time changes, "
        "say so; before a choice, the reader must know what is being decided and the immediate trade-off.",
        "\n".join(f"{i}. {r}" for i, r in enumerate(rows, 1)),
        extra,
    ] if x)


def revise(ctx: Any, scene_id: str, report: ClarityReport, *, extra: str = "", provider: str | None = None, model: str | None = None) -> list[str]:
    """Rewrite the chapter from the audit's fixes (Opus when the caller passes it), keep it only if the linter still
    passes. Returns the fixes applied."""
    from assetforge.story.steps import Ctx, _refine_scene

    worker = Ctx(lib=ctx.lib, project=ctx.project, provider=provider or ctx.provider, model=model or ctx.model, fresh=True,
                 settings=ctx.settings, allow_paid=ctx.allow_paid, calls=ctx.calls)
    return _refine_scene(worker, scene_id, revision_note(report, extra))


# ---------------------------------------------------------------------- Stage H: what must never reach the player


def leaks(scene: Scene, names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Player-facing text that carries workflow metadata: ids, conditions, braces, expression names."""
    ids = set((names or {}).keys())
    out = []
    for i, n in enumerate(scene.nodes):
        texts = [n.text or ""] if n.kind in ("say", "narrate", "place") else []
        for c in n.choices:
            texts.append(c.text)
            texts += [ln.text for ln in c.reaction]
        for t in texts:
            if any(cid in t for cid in ids) or re.search(r"[{}\[\]]|\bIF \w+ (==|>=|<=)|\b\w+_\w+_\w+\b", t):
                out.append({"line": i, "text": t[:120], "why": "an id, a condition or markup in player text"})
    return out
