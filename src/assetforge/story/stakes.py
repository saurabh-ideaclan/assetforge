"""Choices, promises and rules: the P0 artifacts of the research handoff of 2026-11-11 that the pipeline lacked.

1. **Choice QA** (`choice_report`, deterministic): every choice in a chapter is classified A (expression: no state
   written), B (state or relationship: options write different stats) or C (macro: an exit to different scenes).
   For B and C the options must differ in what they set, and the stat a decision writes must be read somewhere
   later (`when`, `require` or a gate), within a horizon, so the player sees an acknowledgement. Findings:
   `same_delta` (a B choice whose options all write the same thing), `no_acknowledgement` (a flag or stat set
   here that nothing ever reads), `late_acknowledgement` (read only after `ACK_HORIZON` chapters), `all_expression`
   (a chapter whose choices never touch state). The clarity cold read already judges whether the trade-off is
   understood before choosing; this is the state side of the same question.

2. **Payoff ledger** (`payoffs.json`): the promises the story makes (family money, the father's record, the NULL
   anomaly…), each with the chapter that introduced it, the horizons at which it should be reinforced and paid,
   and its status. Drafted once from the outline by a model (`draft_ledger`), then advanced deterministically
   from the cold reads: each clarity report names the tensions the chapter changed and the questions it opened
   (`clarity.ClarityReport.tensions_changed/opened`), and `advance_ledger` matches them to promises. A chapter
   whose read changed no tension is flagged `no_payoff`: a cliffhanger is not a payoff.

3. **World rules with belief layers** (`rules.json`): for each fictional rule the registry keeps the actual rule,
   what institutions believe, what the protagonist believes, the exceptions, and when the reader must understand
   it; the **reader knowledge state** per chapter is derived from the clarity reports' term checks and the terms
   plan: unknown | observed | partially_explained | understood | intentionally_misunderstood | revealed. A scene
   may depend on a term only when its state at that chapter is at least `partially_explained`
   (`knowledge_violations`).
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.story.models import Outline, Scene, Script
from assetforge.story.project import StoryProject

ACK_HORIZON = 2  # chapters within which a decision should be acknowledged


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------- 1. choices


def _deltas(choice: Any) -> list[tuple[tuple[str, str, int], ...]]:
    return [tuple(sorted((a.var, a.op, a.value) for a in c.set)) for c in choice.choices]


def _reads(scene: Scene) -> set[str]:
    out: set[str] = set()
    for n in scene.nodes:
        for c in n.when:
            out.add(c.var)
        for b in n.branches:
            for c in b.when:
                out.add(c.var)
        for ch in n.choices:
            if ch.require:
                out.add(ch.require.var)
    return out


ENDING = 10_000  # the chapter number that stands for the ending gates


def choice_report(script: Script, *, unwritten_after: int = 0) -> dict[str, Any]:
    """Classify every choice and check its state side. Chapter numbers come from reading order. `unwritten_after`
    is how many planned chapters follow the last written one: a stat written near the end of a partial script may
    still be read later, so those findings are `unverified` rather than faults."""
    from assetforge.story.lint import chapter_numbers

    numbers = chapter_numbers(script)
    reads_by_number: dict[int, set[str]] = {}
    for s in script.scenes:
        reads_by_number.setdefault(numbers.get(s.id, 0), set()).update(_reads(s))
    for e in script.endings:
        for c in e.requires:
            reads_by_number.setdefault(ENDING, set()).add(c.var)
    last = max(numbers.values(), default=0)
    seen: set[tuple[str, str, str]] = set()

    def finding(code: str, scene: str, var: str, message: str, **extra: Any) -> None:
        if (code, scene, var) in seen:
            return
        seen.add((code, scene, var))
        findings.append({"code": code, "scene": scene, "var": var, "message": message, **extra})
    choices: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for s in script.scenes:
        n = numbers.get(s.id, 0)
        state_touching = 0
        for i, node in enumerate(s.nodes):
            if node.kind != "choice" or not node.choices:
                continue
            exit_choice = all(c.goto for c in node.choices)
            deltas = _deltas(node)
            writes = {v for d in deltas for (v, _, _) in d}
            klass = "C" if exit_choice else ("B" if writes else "A")
            row = {"scene": s.id, "chapter": n, "index": i, "class": klass, "options": [c.text for c in node.choices],
                   "writes": sorted(writes), "distinct_deltas": len(set(deltas))}
            if klass in ("B", "C") and writes and len(set(deltas)) == 1:
                finding("same_delta", s.id, "", "every option writes the same state: the choice is expression only; declare it so or differentiate the effects", index=i)
            for var in sorted(writes):
                read_at = sorted(k for k, vs in reads_by_number.items() if var in vs and k > n)
                read_same = var in reads_by_number.get(n, set())
                later_unwritten = unwritten_after > 0 and n + ACK_HORIZON > last
                if not read_at and not read_same:
                    if later_unwritten:
                        finding("unverified", s.id, var, f"{var} is written here; the chapters that could read it are not written yet", index=i)
                    else:
                        finding("no_acknowledgement", s.id, var, f"{var} is written here and never read: the player will not see it mattered", index=i)
                elif read_at and read_at[0] == ENDING and not read_same and not later_unwritten:
                    finding("only_at_ending", s.id, var, f"{var} is read only by the ending gate; the player gets no acknowledgement on the way", index=i)
                elif read_at and read_at[0] != ENDING and read_at[0] - n > ACK_HORIZON and not read_same:
                    finding("late_acknowledgement", s.id, var, f"{var} is first read {read_at[0] - n} chapters later; acknowledge it within {ACK_HORIZON}", index=i)
            if klass != "A":
                state_touching += 1
            choices.append(row)
        if any(c["scene"] == s.id for c in choices) and state_touching == 0:
            finding("all_expression", s.id, "", "every choice in this chapter is expression only; nothing the player decides changes state")
    counts = {k: sum(1 for c in choices if c["class"] == k) for k in ("A", "B", "C")}
    faults = [f for f in findings if f["code"] != "unverified"]
    return {"ok": not faults, "counts": counts, "choices": choices, "findings": findings}


# ---------------------------------------------------------------------- 2. payoffs


class Promise(Strict):
    id: str
    text: str = Field(description="The promise or tension, in the reader's words")
    introduced: str = Field(description="Scene id where it is introduced")
    near: str = Field("", description="How it is reinforced or partly paid within a chapter or two")
    mid: str = Field("", description="The mid-season change")
    long: str = Field("", description="The season payoff")
    status: Literal["open", "advancing", "paid", "stale"] = "open"
    advanced_in: list[str] = Field(default_factory=list, description="Scene ids whose cold read changed this tension")
    opened_in: list[str] = Field(default_factory=list)


class Ledger(Strict):
    promises: list[Promise] = Field(default_factory=list)
    chapters: dict[str, dict[str, Any]] = Field(default_factory=dict, description="scene id → {changed, opened, no_payoff}")
    drafted_at: str = ""


LEDGER_FILE = "payoffs.json"


def load_ledger(project: StoryProject) -> Ledger:
    p = project.root / LEDGER_FILE
    return Ledger.model_validate_json(p.read_text()) if p.exists() else Ledger()


def save_ledger(project: StoryProject, data: Ledger) -> None:
    from assetforge.story.project import write_atomic

    write_atomic(project.root / LEDGER_FILE, data.model_dump_json(indent=1))


class LedgerAnswer(Strict):
    promises: list[Promise]


def ledger_prompt(project: StoryProject, outline: Outline) -> str:
    chapters = "\n".join(f"- {s.id}: {s.title}: {s.summary[:350]}" + (f" Hook: {s.hook}" if s.hook else "") for s in outline.scenes)
    return "\n\n".join([
        "You are the story editor keeping the payoff ledger. List the promises this season makes to the reader: every "
        "tension, debt, question or threat the story raises and must pay (family money, a parent's record, an anomaly "
        "the hero's status should not allow, a cost the power takes, a relationship, a faction, each awakening). For "
        "each give the scene that introduces it, how it is reinforced or partly paid within a chapter or two (near), the "
        "mid-season change (mid) and the season payoff (long). Six to twelve promises; the reader's words, not jargon.",
        f"PREMISE: {project.config.premise}",
        "CHAPTERS\n" + chapters,
        "Return JSON: {\"promises\": [{\"id\" (snake_case), \"text\", \"introduced\", \"near\", \"mid\", \"long\", \"status\": \"open\", \"advanced_in\": [], \"opened_in\": []}]}.",
    ])


def offline_ledger(outline: Outline) -> Any:
    def build(_: Any) -> dict[str, Any]:
        first = outline.scenes[0].id if outline.scenes else "s01"
        return {"promises": [{"id": "money", "text": "the family needs money", "introduced": first, "near": "a few coins", "mid": "paid work", "long": "the shop is safe",
                              "status": "open", "advanced_in": [], "opened_in": []}]}
    return build


def draft_ledger(ctx: Any, *, fresh: bool = False) -> Ledger:
    from assetforge.errors import NotFoundError
    from assetforge.story.steps import _ask

    project = ctx.project
    if not project.has("outline"):
        raise NotFoundError("No outline yet")
    outline = project.load("outline", Outline)
    answer: LedgerAnswer = _ask(ctx, "payoffs.ledger", ledger_prompt(project, outline), LedgerAnswer, offline_ledger(outline), fresh=fresh)
    known = {s.id for s in outline.scenes}
    old = load_ledger(project)
    keep = {p.id: p for p in old.promises}
    promises = []
    for p in answer.promises:
        if p.introduced not in known and known:
            p = p.model_copy(update={"introduced": outline.scenes[0].id})
        prev = keep.get(p.id)
        promises.append(p.model_copy(update={"status": prev.status if prev else "open", "advanced_in": prev.advanced_in if prev else [],
                                             "opened_in": prev.opened_in if prev else []}))
    data = Ledger(promises=promises, chapters=old.chapters, drafted_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    save_ledger(project, data)
    return data


def _match(text: str, promise: Promise) -> bool:
    words = {w for w in promise.text.lower().replace("'", " ").split() if len(w) > 3}
    hit = sum(1 for w in words if w in text.lower())
    return hit >= max(1, len(words) // 3)


class MatchAnswer(Strict):
    advanced: dict[str, list[str]] = Field(default_factory=dict, description="scene id → promise ids its changed tensions advance")
    opened: dict[str, list[str]] = Field(default_factory=dict, description="scene id → promise ids its opened questions belong to")


def match_prompt(data: Ledger, chapters: dict[str, dict[str, Any]]) -> str:
    promises = "\n".join(f"- {p.id}: {p.text}" for p in data.promises)
    rows = "\n".join(f"- {sid}: changed: {' | '.join(c['changed']) or '-'}; opened: {' | '.join(c['opened']) or '-'}" for sid, c in chapters.items())
    return "\n\n".join([
        "Match what each chapter's cold read says it changed or opened to the story's promises. A promise is advanced when the "
        "chapter materially moved that tension (paid, reinforced, reversed); it is opened when the chapter raised it. Use only "
        "the promise ids given; leave a chapter empty when nothing matches.",
        "PROMISES\n" + promises, "CHAPTERS\n" + rows,
        "Return JSON: {\"advanced\": {\"<scene id>\": [\"<promise id>\"]}, \"opened\": {\"<scene id>\": [\"<promise id>\"]}}.",
    ])


def offline_match(data: Ledger, chapters: dict[str, dict[str, Any]]) -> Any:
    def build(_: Any) -> dict[str, Any]:
        adv = {sid: [p.id for p in data.promises if any(_match(t, p) for t in c["changed"])] for sid, c in chapters.items()}
        opn = {sid: [p.id for p in data.promises if any(_match(t, p) for t in c["opened"])] for sid, c in chapters.items()}
        return {"advanced": adv, "opened": opn}
    return build


def advance_ledger(project: StoryProject, ctx: Any = None) -> Ledger:
    """Read every clarity report's `tensions_changed` / `opened`, flag chapters without payoff, and move the promises:
    with a `ctx`, a model matches the reads' wording to the promise ids (task `payoffs.match`); without one, a word
    overlap does, roughly."""
    from assetforge.story import clarity

    data = load_ledger(project)
    ids = [s.id for s in project.load("outline", Outline).scenes] if project.has("outline") else []
    chapters: dict[str, dict[str, Any]] = {}
    for sid in ids:
        rep = clarity.load_report(project, sid)
        if rep is None:
            continue
        chapters[sid] = {"changed": list(rep.tensions_changed), "opened": list(rep.opened), "no_payoff": not rep.tensions_changed}
    data.chapters = chapters
    if ctx is not None and data.promises and chapters:
        from assetforge.story.steps import _ask

        answer: MatchAnswer = _ask(ctx, "payoffs.match", match_prompt(data, chapters), MatchAnswer, offline_match(data, chapters), fresh=True)
        adv, opn = answer.advanced, answer.opened
    else:
        built = offline_match(data, chapters)(None)
        adv, opn = built["advanced"], built["opened"]
    known = {p.id for p in data.promises}
    for p in data.promises:
        p.advanced_in = [sid for sid in ids if p.id in (adv.get(sid) or []) and p.id in known]
        p.opened_in = [sid for sid in ids if p.id in (opn.get(sid) or [])]
        p.status = "advancing" if p.advanced_in else p.status if p.status != "advancing" else "open"
    save_ledger(project, data)
    return data


def ledger_view(project: StoryProject) -> dict[str, Any]:
    data = load_ledger(project)
    # stale: introduced three or more read chapters ago and never advanced since (a promise opened late is not stale yet)
    read_order = list(data.chapters)
    def age(p: Promise) -> int:
        return len(read_order) - read_order.index(p.introduced) - 1 if p.introduced in read_order else 0
    stale = [p.id for p in data.promises if not p.advanced_in and age(p) >= 3]
    return {"promises": [p.model_dump(mode="json") for p in data.promises], "chapters": data.chapters,
            "no_payoff": [s for s, c in data.chapters.items() if c.get("no_payoff")], "stale": stale, "drafted_at": data.drafted_at,
            "note": "promises come from the outline; what a chapter changed comes from its cold read (a model's reading, cited to lines)"}


# ---------------------------------------------------------------------- 3. rules and reader knowledge


KnowledgeState = Literal["unknown", "observed", "partially_explained", "understood", "intentionally_misunderstood", "revealed"]
ORDER = ["unknown", "observed", "partially_explained", "understood", "revealed"]


class Rule(Strict):
    id: str
    term: str
    actual_rule: str = ""
    institutions_believe: str = ""
    protagonist_believes: str = ""
    exceptions: list[str] = Field(default_factory=list)
    must_understand_by: str = Field("", description="Scene id by which the reader needs usable context")
    concealed_until: str = Field("", description="Scene id where the hidden side is revealed, if any")


class Rules(Strict):
    rules: list[Rule] = Field(default_factory=list)
    drafted_at: str = ""


RULES_FILE = "rules.json"


def load_rules(project: StoryProject) -> Rules:
    p = project.root / RULES_FILE
    return Rules.model_validate_json(p.read_text()) if p.exists() else Rules()


def save_rules(project: StoryProject, data: Rules) -> None:
    from assetforge.story.project import write_atomic

    write_atomic(project.root / RULES_FILE, data.model_dump_json(indent=1))


class RulesAnswer(Strict):
    rules: list[Rule]


def rules_prompt(project: StoryProject, outline: Outline) -> str:
    from assetforge.story import clarity

    terms = clarity.load(project).terms
    listed = "\n".join(f"- {t.term}: {t.meaning} (first needed {t.first_needed}; concealed: {t.concealed or 'nothing'})" for t in (terms.terms if terms else []))
    world = project.load("world") if project.has("world") else None
    return "\n\n".join(x for x in [
        "You are the continuity editor keeping the world-rule registry. For each fictional rule, institution or ability "
        "below, separate four things that must never be confused: the ACTUAL rule of the world; what INSTITUTIONS believe "
        "or claim; what the PROTAGONIST believes at the start; the exceptions. Say by which scene the reader needs usable "
        "context (must_understand_by) and, if the rule has a hidden side, the scene where it is revealed (concealed_until). "
        "Keep each field to one or two plain sentences.",
        "WORLD RULES:\n" + (world.rules if world and getattr(world, "rules", None) else (world.model_dump_json() if world else "")),
        "TERMS:\n" + listed,
        "CHAPTERS:\n" + "\n".join(f"- {s.id}: {s.title}" for s in outline.scenes),
        "Return JSON: {\"rules\": [{\"id\" (snake_case), \"term\", \"actual_rule\", \"institutions_believe\", \"protagonist_believes\", "
        "\"exceptions\": [], \"must_understand_by\", \"concealed_until\"}]}.",
    ] if x)


def offline_rules(outline: Outline) -> Any:
    def build(_: Any) -> dict[str, Any]:
        first = outline.scenes[0].id if outline.scenes else "s01"
        return {"rules": [{"id": "trial", "term": "the trial", "actual_rule": "a test decides who may work", "institutions_believe": "the test is fair",
                           "protagonist_believes": "it is rigged", "exceptions": [], "must_understand_by": first, "concealed_until": ""}]}
    return build


def draft_rules(ctx: Any, *, fresh: bool = False) -> Rules:
    from assetforge.errors import NotFoundError
    from assetforge.story.steps import _ask

    project = ctx.project
    if not project.has("outline"):
        raise NotFoundError("No outline yet")
    outline = project.load("outline", Outline)
    answer: RulesAnswer = _ask(ctx, "rules.registry", rules_prompt(project, outline), RulesAnswer, offline_rules(outline), fresh=fresh)
    data = Rules(rules=answer.rules, drafted_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    save_rules(project, data)
    return data


def knowledge_state(project: StoryProject) -> dict[str, Any]:
    """term → per chapter (reading order) the reader's state, from the terms plan (first needed, concealed) and the
    cold reads (a term checked with context → understood; without → observed). Violations: a chapter whose read
    says the term lacked context although a scene depended on it."""
    from assetforge.story import clarity
    from assetforge.story.progress import chapter_numbers

    if not project.has("outline"):
        return {"terms": {}, "violations": []}
    outline = project.load("outline", Outline)
    numbers = chapter_numbers(outline)
    order = sorted(outline.scenes, key=lambda s: (numbers.get(s.id, 99), s.id))
    plan = clarity.load(project).terms
    reports = {s.id: clarity.load_report(project, s.id) for s in order}
    rules = {r.term.lower(): r for r in load_rules(project).rules}
    terms: dict[str, dict[str, str]] = {}
    violations: list[dict[str, Any]] = []
    names = [t.term for t in (plan.terms if plan else [])]
    for term in names:
        tp = next(t for t in plan.terms if t.term == term)
        first_n = numbers.get(tp.first_needed, 99)
        state = "unknown"
        row: dict[str, str] = {}
        for s in order:
            n = numbers.get(s.id, 99)
            rep = reports.get(s.id)
            check = None
            if rep is not None:
                check = next((t for t in rep.terms if t.term.lower() == term.lower() or term.lower() in t.term.lower()), None)
            if check is not None:
                new = "understood" if check.context_given else "observed"
                if state not in ORDER or ORDER.index(new) > ORDER.index(state):
                    state = new
                if not check.context_given and n >= first_n:
                    violations.append({"term": term, "scene": s.id, "message": f"the chapter depends on '{term}' and the cold read found no context for it"})
            elif n >= first_n and state == "unknown" and rep is not None:
                state = "observed"
            rule = rules.get(term.lower())
            reveal_n = numbers.get(rule.concealed_until, 99) if rule and rule.concealed_until else None
            if reveal_n is not None and reveal_n <= n:
                state = "revealed"
            elif reveal_n is not None and tp.concealed and state == "understood":
                state = "intentionally_misunderstood"  # the reader has the usable half; the hidden half waits
            row[s.id] = state
        terms[term] = row
    return {"terms": terms, "order": [s.id for s in order], "violations": violations}


def knowledge_violations(project: StoryProject) -> list[dict[str, Any]]:
    return knowledge_state(project)["violations"]
