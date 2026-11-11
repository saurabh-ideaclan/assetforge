"""The writing workflow (storycraftr's init → worldbuilding → outline → chapters → iterate, extended for
branching visual novels): init, world, cast, outline, write, refine, check.

Every model call goes through `llm.structured.generate`, so it is cached by fingerprint (re-running a step is
free unless `fresh`), persisted under `jobs/`, schema-validated with one repair round, and falls back across
providers. Steps save their output into the story folder; `write` is resumable (finished scenes are kept).
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assetforge.config import Settings, load_settings
from assetforge.core.util import slugify, utcnow
from assetforge.errors import ConflictError, InvalidInputError, NotFoundError
from assetforge.llm import plans
from assetforge.llm.base import TextCall
from assetforge.llm.structured import Structured, generate
from assetforge.story import offline, prompts
from assetforge.story.models import (Cast, ContinuityReport, Issue, NamingGuide, Outline, PlannedScene, RevisionPlan, Scene, SceneDraft,
                                     Script, StoryConfig, World)  # fmt: skip
from assetforge.story.project import StoryProject, write_atomic
from assetforge.story.lint import chapter_numbers as lint_chapter_numbers
from assetforge.story.lint import lint_scene, lint_story, parts_for, repair_note
from assetforge.story.models import Node, is_exit
from assetforge.story.progress import chapter_numbers
from assetforge.story.validate import conform_scene, validate_outline, validate_script

if TYPE_CHECKING:
    from assetforge.core.catalog import Library


@dataclass
class Ctx:
    lib: Library
    project: StoryProject
    provider: str | None = None
    model: str | None = None
    fresh: bool = False
    settings: Settings | None = None
    allow_paid: bool = False  # the operator confirmed a per-token fallback for this run
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def config(self) -> StoryConfig:
        return self.project.config


def _ask(ctx: Ctx, task: str, prompt: str, model: type, builder: Any, *, fresh: bool | None = None) -> Any:
    config = ctx.config
    settings = ctx.settings or load_settings(ctx.lib.paths.home)
    timeout = float(settings.get("ASSETFORGE_TEXT_TIMEOUT") or 600)
    call = TextCall(prompt=prompt, system=prompts.system(config), model=ctx.model or config.model, timeout=timeout,
                    task=f"story.{task}", offline=builder)  # fmt: skip
    resolved = plans.resolve(call.task, settings, home=ctx.lib.paths.home, story_plan=config.ai,
                             override_provider=ctx.provider or config.provider, override_model=ctx.model or config.model,
                             allow_paid=ctx.allow_paid)  # fmt: skip
    result: Structured = generate(ctx.lib, call, model, provider=ctx.provider or config.provider,
                                  fresh=ctx.fresh if fresh is None else fresh, settings=settings,
                                  project=config.asset_project, resolved=resolved)  # fmt: skip
    ctx.calls.append({"task": task, "job_id": result.job_id, "provider": result.provider, "model": result.model,
                      "reused": result.reused, "billing": result.billing, "usage": result.usage, "choice": result.choice})  # fmt: skip
    return result.data


# ---------------------------------------------------------------------- init


def default_dir(title: str) -> Path:
    return Path.cwd() / "story" / slugify(title)


def init(directory: str | Path | None, *, title: str, premise: str, force: bool = False, **options: Any) -> StoryProject:
    slug = slugify(options.pop("slug", None) or title)
    if not slug:
        raise InvalidInputError("The title needs letters or digits")
    project = StoryProject(directory or default_dir(title))
    if project.has("config") and not force:
        raise ConflictError(f"A story already exists in {project.root}", hint="Pass --force to replace its config")
    config = StoryConfig(title=title, slug=slug, premise=premise, **{k: v for k, v in options.items() if v is not None})
    project.save("config", config)
    return project


# ---------------------------------------------------------------------- world, cast, outline


def world(ctx: Ctx) -> World:
    config = ctx.config
    data: World = _ask(ctx, "world", prompts.world_prompt(config), World, offline.world(config))
    ctx.project.save("world", data)
    if config.title_from_writer and data.title.strip():  # the writer names an untitled story (slug/keys stay)
        ctx.project.save("config", config.model_copy(update={"title": data.title.strip(), "title_from_writer": False}))
    return data


def names(ctx: Ctx) -> NamingGuide:
    """The naming guide: written after the world, before the cast, whenever the story has a creative direction."""
    config, world_ = ctx.config, ctx.project.load("world")
    data: NamingGuide = _ask(ctx, "names", prompts.names_prompt(config, world_), NamingGuide, offline.names(config))
    reserved = list(dict.fromkeys([*(config.direction.reserved_names if config.direction else []), *data.reserved]))
    data = data.model_copy(update={"reserved": reserved})
    ctx.project.save("names", data)
    return data


def wants_names(config: StoryConfig) -> bool:
    return config.direction is not None


def cast(ctx: Ctx) -> Cast:
    config, world_ = ctx.config, ctx.project.load("world")
    guide = ctx.project.load("names") if ctx.project.has("names") else None
    if guide is None and wants_names(config):
        guide = names(ctx)
    data: Cast = _ask(ctx, "cast", prompts.cast_prompt(config, world_, guide), Cast, offline.cast(config, guide))
    data = _unique_ids(data)
    if guide is not None:
        data, name_fixes = _apply_names(data, guide, config)
        ctx.calls.append({"task": "cast.names", "fixes": name_fixes}) if name_fixes else None
    ctx.project.save("cast", data)
    return data


def _apply_names(data: Cast, guide: NamingGuide, config: StoryConfig) -> tuple[Cast, list[str]]:
    """Operator-fixed names win over the writer's: `name_overrides` by character id or role, then reserved names
    that the writer dropped are reported."""
    fixes: list[str] = []
    overrides = {k.lower(): v for k, v in (config.direction.name_overrides if config.direction else {}).items()}
    chars = []
    for c in data.characters:
        wanted = overrides.get(c.id.lower()) or overrides.get(c.role.lower())
        if wanted and c.name != wanted:
            fixes.append(f"{c.id}: name {c.name!r} → {wanted!r} (operator override)")
            c = c.model_copy(update={"name": wanted})
        chars.append(c)
    used = " ".join(c.name for c in chars).lower()
    for name in guide.reserved:
        if name.lower() not in used:
            fixes.append(f"reserved name {name!r} is not used by any character")
    return data.model_copy(update={"characters": chars}), fixes


def _unique_ids(data: Cast) -> Cast:
    def dedupe(items: list[Any]) -> list[Any]:
        seen: dict[str, int] = {}
        out = []
        for item in items:
            n = seen.get(item.id, 0)
            seen[item.id] = n + 1
            out.append(item if n == 0 else item.model_copy(update={"id": f"{item.id}_{n + 1}"}))
        return out

    return Cast(characters=dedupe(data.characters), locations=dedupe(data.locations))


def outline(ctx: Ctx) -> tuple[Outline, list[str]]:
    config, world_, cast_ = ctx.config, ctx.project.load("world"), ctx.project.load("cast")
    data: Outline = _ask(ctx, "outline", prompts.outline_prompt(config, world_, cast_), Outline, offline.outline(config, cast_))
    data, fixes = repair_outline(data, cast_)
    ctx.project.save("outline", data)
    return data, fixes


def repair_outline(plan: Outline, cast_: Cast) -> tuple[Outline, list[str]]:
    """Deterministic fixes so the plan is always playable: known ids only, exactly one exit per scene."""
    fixes: list[str] = []
    chars = {c.id for c in cast_.characters}
    locs = {loc.id: loc for loc in cast_.locations}
    scenes: list[PlannedScene] = []
    seen: set[str] = set()
    for s in plan.scenes:
        if s.id in seen:
            fixes.append(f"dropped duplicate scene {s.id}")
            continue
        seen.add(s.id)
        scenes.append(s)
    ids = [s.id for s in scenes]
    endings = [e.id for e in plan.endings]
    variables = []
    for v in plan.variables:  # a relationship stat about a character the cast does not have: keep the stat, drop the link
        if v.character and v.character not in chars:
            fixes.append(f"variable {v.name}: unknown character {v.character!r} → untied")
            v = v.model_copy(update={"character": None})
        variables.append(v)
    plan = plan.model_copy(update={"variables": variables})
    var_names = {v.name for v in plan.variables}
    chapter_ids = {c.id for c in plan.chapters}
    out: list[PlannedScene] = []
    for i, s in enumerate(scenes):
        update: dict[str, Any] = {}
        if s.location not in locs:
            update["location"] = cast_.locations[0].id
            fixes.append(f"{s.id}: unknown location {s.location!r} → {update['location']}")
        loc = locs[update.get("location", s.location)]
        if s.time not in loc.times:
            update["time"] = loc.times[0]
        if unknown := [c for c in s.characters if c not in chars]:
            update["characters"] = [c for c in s.characters if c in chars]
            fixes.append(f"{s.id}: dropped unknown characters {unknown}")
        choices = [c for c in s.choices if c.goto in seen]
        if len(choices) != len(s.choices):
            fixes.append(f"{s.id}: dropped choices to unknown scenes")
        if choices and len({c.goto for c in choices}) == 1:  # every option leads to the same scene: not a choice
            fixes.append(f"{s.id}: exit choice with one destination → next {choices[0].goto}")
            update["next"], choices = choices[0].goto, []
        branches = [b for b in s.branches if b.goto in seen and all(c.var in var_names for c in b.when)]
        if len(branches) != len(s.branches):
            fixes.append(f"{s.id}: dropped gated exits to unknown scenes or variables")
        nxt = update.get("next") or (s.next if s.next in seen else None)
        ending = s.ending if s.ending in endings else s.id if s.id in endings else None
        if s.ending and ending != s.ending:
            fixes.append(f"{s.id}: ending {s.ending!r} is not an ending id → {ending}")
        if choices:
            nxt, ending, branches = None, None, []
        elif branches:
            ending = None
            if not nxt:  # the player must never get stuck: pick the most plausible fallback
                gated = {b.goto for b in branches}
                open_end = {e.id for e in plan.endings if not e.requires}
                targeted = {t for o in scenes for t in o.exits()}
                candidates = ([o.id for o in scenes if o.ending in open_end and o.id not in gated]
                              or [o.id for o in scenes if o.id not in targeted and o.id not in gated
                                  and o.id != plan.start and o.id != s.id])  # fmt: skip
                if candidates:
                    nxt = candidates[0]
                else:
                    nxt, branches = branches[-1].goto, branches[:-1]
                fixes.append(f"{s.id}: gated exits had no fallback → next {nxt}")
        elif nxt:
            ending = None
        if not choices and not nxt and not ending:
            claimed = {o.ending for o in scenes if o.ending in endings} | {o.id for o in scenes if o.id in endings}
            unused = [e for e in endings if e not in claimed]
            dead = lambda o: not (o.choices or o.branches or o.next or o.ending in endings)  # noqa: E731
            tail = s.id != plan.start and all(dead(o) or o.ending in endings for o in scenes[i + 1:])
            if unused and tail:  # a dead end among the final scenes, with an ending nobody reaches: it is that ending
                ending = unused[0]
            elif i + 1 < len(ids):
                nxt = ids[i + 1]
            else:
                ending = endings[0]
            fixes.append(f"{s.id}: no exit → {'next ' + nxt if nxt else 'ending ' + str(ending)}")
        if s.chapter and s.chapter not in chapter_ids:  # no declared chapters: every scene is its own chapter
            update["chapter"] = None
        update.update(choices=choices, branches=branches, next=nxt, ending=ending)
        out.append(s.model_copy(update=update))
    start = plan.start if plan.start in seen else ids[0]
    if start != plan.start:
        fixes.append(f"start {plan.start!r} → {start}")
    fixed = plan.model_copy(update={"scenes": out, "start": start})
    remaining = [i for i in validate_outline(fixed, chars, set(locs)) if i.level == "error"]
    if remaining:
        raise InvalidInputError("The outline is still broken after automatic fixes",
                                problems=[i.model_dump() for i in remaining])  # fmt: skip
    return fixed, fixes


# ---------------------------------------------------------------------- scenes


def scene_order(plan: Outline) -> tuple[list[PlannedScene], dict[str, str]]:
    """Scenes in reading order (breadth-first from the start, then any unreachable ones) and each scene's parent."""
    by_id = {s.id: s for s in plan.scenes}
    order: list[str] = []
    parent: dict[str, str] = {}
    queue = deque([plan.start])
    while queue:
        sid = queue.popleft()
        if sid in order or sid not in by_id:
            continue
        order.append(sid)
        s = by_id[sid]
        for target in s.exits():
            if target not in parent and target not in order:
                parent[target] = sid
            queue.append(target)
    order += [s.id for s in plan.scenes if s.id not in order]
    return [by_id[i] for i in order], parent


def _story_so_far(plan: Outline, parent: dict[str, str], scene_id: str, depth: int = 5) -> list[str]:
    by_id = {s.id: s for s in plan.scenes}
    chain: list[str] = []
    current = parent.get(scene_id)
    while current and len(chain) < depth:
        chain.append(f"{by_id[current].title}: {by_id[current].summary}")
        current = parent.get(current)
    return list(reversed(chain))


def _parallel(ctx: Ctx, items: list[PlannedScene], fn: Any, *, workers: int) -> dict[str, Any]:
    """Run `fn(item, ctx)` for each scene; scene prompts only depend on the outline, so they are independent.
    Each thread gets its own Library connection; `limits.provider_slot` still caps calls per provider."""
    from concurrent.futures import ThreadPoolExecutor

    from assetforge.core.catalog import Library

    results: dict[str, Any] = {}
    if workers <= 1 or len(items) <= 1:
        for item in items:
            try:
                results[item.id] = fn(item, ctx)
            except Exception as exc:  # noqa: BLE001 - reported after the others finish
                results[item.id] = exc
        return results

    def run(item: PlannedScene) -> Any:
        with Library(ctx.lib.paths.home) as lib:
            worker = Ctx(lib=lib, project=ctx.project, provider=ctx.provider, model=ctx.model, fresh=ctx.fresh,
                         settings=ctx.settings, calls=ctx.calls)  # fmt: skip
            try:
                return fn(item, worker)
            except Exception as exc:  # noqa: BLE001
                return exc

    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        for item, outcome in zip(items, pool.map(run, items), strict=True):
            results[item.id] = outcome
    return results


def write(ctx: Ctx, *, only: list[str] | None = None, rewrite: bool = False, workers: int = 2) -> dict[str, Any]:
    config = ctx.config
    world_, cast_, plan = ctx.project.load("world"), ctx.project.load("cast"), ctx.project.load("outline")
    ordered, parent = scene_order(plan)
    wanted = set(only or [])
    if wanted - {s.id for s in ordered}:
        raise NotFoundError(f"Unknown scene ids {sorted(wanted - {s.id for s in ordered})}")
    expressions = {c.id: c.expressions for c in cast_.characters}
    skipped: list[str] = []
    todo: list[PlannedScene] = []
    for planned in ordered:
        if wanted and planned.id not in wanted:
            continue
        if not rewrite and not wanted and ctx.project.load_scene(planned.id) is not None:
            skipped.append(planned.id)
        else:
            todo.append(planned)

    locations = {loc.id for loc in cast_.locations}
    numbers = chapter_numbers(plan)
    variables = [v.name for v in plan.variables]
    fresh = True if (wanted or rewrite) else None  # an explicit rewrite must not get the stored answer back

    by_id = {s.id: s for s in plan.scenes}
    flags = {v.name for v in plan.variables if v.kind == "flag"}

    def upcoming_for(planned: PlannedScene) -> list[str]:
        targets = [c.goto for c in planned.choices] + [b.goto for b in planned.branches] + ([planned.next] if planned.next else [])
        seen: list[str] = []
        for t in targets:
            if t in by_id and t not in seen and t != planned.id:
                seen.append(t)
        return [f"{by_id[t].title}: {by_id[t].summary}" for t in seen]

    def decided_for(planned: PlannedScene) -> list[str]:
        """Flags that chapters before this one (by chapter number) set in their written text."""
        found: list[str] = []
        for other in ordered:
            if numbers.get(other.id, 0) >= numbers.get(planned.id, 0) or other.id == planned.id:
                continue
            scene = ctx.project.load_scene(other.id)
            if scene is None:
                continue
            for node in scene.nodes:
                for a in node.assign:
                    if a.var in flags and a.var not in found:
                        found.append(a.var)
                for c in node.choices:
                    for a in c.set:
                        if a.var in flags and a.var not in found:
                            found.append(a.var)
        return found

    from assetforge.story import clarity

    def one(planned: PlannedScene, worker: Ctx) -> list[str]:
        """A chapter in 1-3 beat passes (a single call drifts short), then the linter, then one targeted repair,
        then the clarity gate: a cold read from the reader's view, and one revision when it says `revise`."""
        so_far = _story_so_far(plan, parent, planned.id)
        parts = 1 if planned.ending else parts_for(config)
        nodes: list[Node] = []
        upcoming, decided = upcoming_for(planned), decided_for(planned)
        knowledge = clarity.knowledge_block(ctx.project, plan, planned.id)
        for part in range(1, parts + 1):
            task = f"scene.{planned.id}" + (f".p{part}" if parts > 1 else "")
            prompt = prompts.scene_prompt(config, world_, cast_, plan, planned, so_far, part=part, parts=parts,
                                          so_far=nodes or None, upcoming=upcoming, decided=decided, knowledge=knowledge)  # fmt: skip
            draft: SceneDraft = _ask(worker, task, prompt, SceneDraft,
                                     offline.scene(planned, cast_, config, part=part, parts=parts, variables=variables),
                                     fresh=fresh)  # fmt: skip
            nodes += draft.nodes if part == parts else [n for n in draft.nodes if not is_exit(n) and n.kind != "branch"]
        scene, scene_fixes = _scene_from(planned, SceneDraft(nodes=nodes), expressions, locations)
        report = lint_scene(scene, config, numbers[planned.id], flags=flags)
        if not report["ok"]:
            prompt = prompts.scene_prompt(config, world_, cast_, plan, planned, so_far, written_before=scene,
                                          note=repair_note(report), upcoming=upcoming, decided=decided)  # fmt: skip
            draft = _ask(worker, f"scene.{planned.id}.fix", prompt, SceneDraft, offline.refine(scene, ""), fresh=fresh)
            candidate, more = _scene_from(planned, draft, expressions, locations)
            again = lint_scene(candidate, config, numbers[planned.id], flags=flags)
            if _better(again, report):
                scene_fixes += [f"linter repair: {', '.join(_codes(report))} → {', '.join(_codes(again)) or 'ok'}", *more]
                scene, report = candidate, again
            else:
                scene_fixes.append(f"linter repair kept the first version ({', '.join(_codes(report))})")
        ctx.project.save_scene(scene)
        scene_fixes += _clarity_gate(worker, planned, numbers[planned.id], flags)
        return scene_fixes

    outcomes = _parallel(ctx, todo, one, workers=workers)
    written = [p.id for p in todo if not isinstance(outcomes[p.id], BaseException)]
    fixes = {sid: f for sid, f in outcomes.items() if f and not isinstance(f, BaseException)}
    failures = [outcomes[p.id] for p in todo if isinstance(outcomes[p.id], BaseException)]
    if failures:  # finished scenes are saved; re-running `write` continues with the rest
        raise failures[0]
    result: dict[str, Any] = {"written": written, "kept": skipped, "fixes": fixes}
    result.update(assemble(ctx.project))
    return result


def _clarity_gate(worker: Ctx, planned: PlannedScene, number: int, flags: set[str]) -> list[str]:
    """Stage E-G of the clarity workflow: audit from the reader's view (Sonnet when Claude is signed in), revise once
    on `revise` (Opus for the rewrite when signed in), keep the revision only if it still lints and reads better."""
    from assetforge.story import clarity

    config = worker.config
    audit_kw, revise_kw = _clarity_models(worker)
    try:
        report = clarity.audit(worker, planned.id, **audit_kw)
    except Exception as exc:  # noqa: BLE001 - the gate never loses a written chapter
        return [f"clarity audit skipped: {type(exc).__name__}: {str(exc)[:120]}"]
    fixes = [f"clarity: {report.verdict} ({len(clarity.problems(report))} points)"]
    if report.verdict != "revise":
        return fixes
    before = worker.project.load_scene(planned.id)
    before_report = clarity.report_path(worker.project, planned.id).read_text()
    try:
        clarity.revise(worker, planned.id, report, **revise_kw)
    except Exception as exc:  # noqa: BLE001
        return fixes + [f"clarity revision failed: {type(exc).__name__}: {str(exc)[:120]}"]
    after = worker.project.load_scene(planned.id)
    lint_after = lint_scene(after, config, number, flags=flags)
    try:
        again = clarity.audit(worker, planned.id, fresh=True, **audit_kw)
    except Exception:  # noqa: BLE001
        again = None
    kept = lint_after["ok"] and (again is None or len(clarity.problems(again)) <= len(clarity.problems(report)))
    if not kept and before is not None:
        worker.project.save_scene(before)
        clarity.report_path(worker.project, planned.id).write_text(before_report)  # the stored report matches the kept text
        fixes.append("clarity revision discarded (it lint-failed or read no better)")
    else:
        fixes.append(f"clarity revision kept ({len(clarity.problems(report))} → {len(clarity.problems(again)) if again else '?'} points)")
    return fixes


def _clarity_models(worker: Ctx) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sonnet audits, Opus revises, when Claude is signed in; otherwise the story's own plan does both."""
    try:
        from assetforge.llm import provider_chain

        names = {p.name for p in provider_chain(None, worker.settings or load_settings(worker.lib.paths.home))}
    except Exception:  # noqa: BLE001
        names = set()
    if "claude-cli" in names:
        return {"provider": "claude-cli", "model": "sonnet"}, {"provider": "claude-cli", "model": "opus"}
    return {}, {}


def _codes(report: dict[str, Any]) -> list[str]:
    return [i["code"] for i in report["issues"] if i["level"] == "error"]


def _lint_errors(report: dict[str, Any]) -> int:
    return len(_codes(report))


def _better(new: dict[str, Any], old: dict[str, Any]) -> bool:
    """A rewrite wins only if it adds no new hard-rule failure, and fixes one or (with the same ones) runs longer."""
    if not set(_codes(new)) <= set(_codes(old)):
        return False
    return _lint_errors(new) < _lint_errors(old) or new["words"] > old["words"]


def _scene_from(planned: PlannedScene, draft: SceneDraft, expressions: dict[str, list[str]],
                locations: set[str] | None = None) -> tuple[Scene, list[str]]:  # fmt: skip
    scene = Scene(id=planned.id, title=planned.title, chapter=planned.chapter, location=planned.location,
                  time=planned.time, music=planned.music, nodes=draft.nodes)  # fmt: skip
    return conform_scene(scene, planned, expressions, locations)


def _refine_scene(ctx: Ctx, scene_id: str, note: str) -> list[str]:
    config = ctx.config
    world_, cast_, plan = ctx.project.load("world"), ctx.project.load("cast"), ctx.project.load("outline")
    ordered, parent = scene_order(plan)
    planned = next((s for s in ordered if s.id == scene_id), None)
    current = ctx.project.load_scene(scene_id)
    if planned is None or current is None:
        raise NotFoundError(f"Scene {scene_id!r} is not written yet", hint="Run `assetforge story write` first")
    prompt = prompts.scene_prompt(config, world_, cast_, plan, planned, _story_so_far(plan, parent, scene_id),
                                  written_before=current, note=note)  # fmt: skip
    draft: SceneDraft = _ask(ctx, f"refine.{scene_id}", prompt, SceneDraft, offline.refine(current, note))
    scene, fixes = _scene_from(planned, draft, {c.id: c.expressions for c in cast_.characters},
                               {loc.id for loc in cast_.locations})  # fmt: skip
    ctx.project.save_scene(scene)
    return fixes


def alternatives(ctx: Ctx, scene_id: str, *, n: int = 3, note: str = "") -> dict[str, Any]:
    """Write `n` alternative versions of one chapter (each a fresh call with its own angle) and keep them as
    proposals in `alternatives/<scene>.json`; `choose_alternative` makes one the chapter."""
    world_, cast_, plan = ctx.project.load("world"), ctx.project.load("cast"), ctx.project.load("outline")
    planned = next((s for s in plan.scenes if s.id == scene_id), None)
    if planned is None:
        raise NotFoundError(f"Unknown scene {scene_id!r}")
    current = ctx.project.load_scene(scene_id)
    _, parent = scene_order(plan)
    so_far = _story_so_far(plan, parent, scene_id)
    expressions = {c.id: c.expressions for c in cast_.characters}
    locations = {loc.id for loc in cast_.locations}
    angles = ["a different opening moment and a sharper first choice", "more pressure from the antagonist and a harder dilemma",
              "a quieter, more character-driven version with the same ending turn", "a faster version with more cuts between places",
              "a version where the romance thread carries the tension"]  # fmt: skip
    out = []
    for i in range(1, max(1, min(5, n)) + 1):
        angle = angles[(i - 1) % len(angles)]
        hint = f"Alternative {i} of {n}: {angle}." + (f" Also: {note.strip()}" if note.strip() else "")
        prompt = prompts.scene_prompt(ctx.config, world_, cast_, plan, planned, so_far, written_before=current, note=hint)
        draft = _ask(ctx, f"alternatives.{scene_id}.a{i}", prompt, SceneDraft, offline.refine(current or Scene(id=scene_id, title=planned.title, location=planned.location, time=planned.time, nodes=[Node(kind="narrate", text=planned.summary)]), hint), fresh=True)  # fmt: skip
        scene, fixes = _scene_from(planned, draft, expressions, locations)
        report = lint_scene(scene, ctx.config, lint_chapter_numbers(ctx.project.load("script"))[scene_id] if ctx.project.has("script") else 1)
        last = ctx.calls[-1] if ctx.calls else {}
        out.append({"id": f"a{i}", "angle": angle, "scene": scene.model_dump(mode="json", exclude_none=True), "fixes": fixes,
                    "words": report["words"], "choices": report["choices"], "ok": report["ok"],
                    "provider": last.get("provider"), "model": last.get("model"), "at": utcnow()})  # fmt: skip
    path = ctx.project.root / "alternatives" / f"{scene_id}.json"
    write_atomic(path, json.dumps({"scene": scene_id, "note": note, "items": out}, indent=2), backup=False)
    return {"scene": scene_id, "alternatives": [{k: v for k, v in a.items() if k != "scene"} for a in out], "path": str(path)}


def choose_alternative(project: StoryProject, scene_id: str, alt_id: str) -> dict[str, Any]:
    path = project.root / "alternatives" / f"{scene_id}.json"
    if not path.exists():
        raise NotFoundError(f"No alternatives written for {scene_id}")
    data = json.loads(path.read_text())
    item = next((a for a in data["items"] if a["id"] == alt_id), None)
    if item is None:
        raise NotFoundError(f"No alternative {alt_id!r} for {scene_id}")
    project.save_scene(Scene(**item["scene"]))  # the previous version is kept as .back
    result = {"scene": scene_id, "chosen": alt_id, "backup": str(project.scene_path(scene_id)) + ".back"}
    result.update(assemble(project))
    return result


def refine(ctx: Ctx, scene_id: str, note: str) -> dict[str, Any]:
    fixes = _refine_scene(ctx, scene_id, note)
    return {"scene": scene_id, "fixes": fixes, "backup": str(ctx.project.scene_path(scene_id)) + ".back",
            **assemble(ctx.project)}  # fmt: skip


def fix_lint(ctx: Ctx, *, scenes: list[str] | None = None, rounds: int = 2, workers: int = 2) -> dict[str, Any]:
    """Rewrite the chapters the linter fails, from its findings, up to `rounds` times each; a rewrite is kept only
    when it lints better (fewer errors, or as many and longer), so a chapter never gets worse."""
    from dataclasses import replace

    ctx = replace(ctx, fresh=True)  # a retry must get a new answer, not the stored one to the same prompt
    script = ctx.project.load("script")
    config = ctx.config
    numbers = lint_chapter_numbers(script)
    by_id = {s.id: s for s in script.scenes}
    failing = [sid for sid in (scenes or list(by_id)) if sid in by_id
               and not lint_scene(by_id[sid], config, numbers[sid])["ok"]]  # fmt: skip
    plan = ctx.project.load("outline")
    items = [p for p in plan.scenes if p.id in failing]

    def one(planned: PlannedScene, worker: Ctx) -> dict[str, Any]:
        best = worker.project.load_scene(planned.id)
        report = lint_scene(best, config, numbers[planned.id])
        start = _codes(report)
        for _ in range(rounds):
            if report["ok"]:
                break
            _refine_scene(worker, planned.id, repair_note(report))
            candidate = worker.project.load_scene(planned.id)
            again = lint_scene(candidate, config, numbers[planned.id])
            if _better(again, report):
                best, report = candidate, again
            else:
                worker.project.save_scene(best)  # the rewrite was not better: put the better version back
        return {"before": start, "after": _codes(report), "words": report["words"], "choices": report["choices"]}

    outcomes = _parallel(ctx, items, one, workers=workers)
    failures = [o for o in outcomes.values() if isinstance(o, BaseException)]
    result: dict[str, Any] = {"fixed": {k: v for k, v in outcomes.items() if not isinstance(v, BaseException)},
                              "failed": [str(f) for f in failures]}  # fmt: skip
    result.update(assemble(ctx.project))
    return result


def improve(ctx: Ctx, feedback: str, *, include_check: bool = False, scenes: list[str] | None = None,
            workers: int = 2) -> dict[str, Any]:  # fmt: skip
    """Feedback loop: an editor call turns free-form feedback (and optionally the last continuity report) into
    per-scene notes, then the scenes are rewritten in parallel (each keeps a .back). Logged in feedback.json."""
    config = ctx.config
    world_, cast_ = ctx.project.load("world"), ctx.project.load("cast")
    script: Script = ctx.project.load("script")
    ids = {s.id for s in script.scenes}
    if scenes and (unknown := set(scenes) - ids):
        raise NotFoundError(f"Unknown scene ids {sorted(unknown)}")
    text = feedback.strip()
    if include_check and ctx.project.has("check"):
        report: ContinuityReport = ctx.project.load("check")
        text += "\n\nContinuity editor's findings:\n" + "\n".join(
            f"- {i.scene}: {i.problem}" + (f" (suggestion: {i.suggestion})" if i.suggestion else "") for i in report.issues)
    if not text.strip():
        raise InvalidInputError("Give some feedback (or use the continuity report)")
    plan: RevisionPlan = _ask(ctx, "improve", prompts.improve_prompt(config, world_, cast_, script.scenes, text, scenes),
                              RevisionPlan, offline.improve(script, text))  # fmt: skip
    notes: dict[str, str] = {}
    for edit in plan.edits:
        if edit.scene in ids and (not scenes or edit.scene in scenes):
            notes[edit.scene] = f"{notes[edit.scene]}\n{edit.note}" if edit.scene in notes else edit.note
    by_id = {s.id: s for s in ctx.project.load("outline").scenes}
    targets = [by_id[sid] for sid in notes if sid in by_id]
    outcomes = _parallel(ctx, targets, lambda planned, worker: _refine_scene(worker, planned.id, notes[planned.id]),
                         workers=workers)  # fmt: skip
    revised = [sid for sid, out in outcomes.items() if not isinstance(out, BaseException)]
    failed = {sid: str(out) for sid, out in outcomes.items() if isinstance(out, BaseException)}
    entry = {"at": utcnow(), "feedback": feedback.strip(), "used_check": include_check, "summary": plan.summary,
             "edits": [{"scene": k, "note": v} for k, v in notes.items()], "revised": revised, "failed": failed}  # fmt: skip
    history = ctx.project.read_json("feedback.json").get("history", [])
    write_atomic(ctx.project.root / "feedback.json", json.dumps({"history": [*history, entry]}, indent=2) + "\n",
                 backup=False)  # fmt: skip
    result = {"summary": plan.summary, "edits": entry["edits"], "revised": revised, "failed": failed,
              **assemble(ctx.project)}  # fmt: skip
    if failed and not revised:
        raise next(out for out in outcomes.values() if isinstance(out, BaseException))
    return result


def check(ctx: Ctx) -> ContinuityReport:
    config = ctx.config
    world_, cast_ = ctx.project.load("world"), ctx.project.load("cast")
    script: Script = ctx.project.load("script")
    report: ContinuityReport = _ask(ctx, "check", prompts.check_prompt(config, world_, cast_, script.scenes),
                                    ContinuityReport, offline.check())  # fmt: skip
    ctx.project.save("check", report)
    return report


def plan_sound(ctx: Ctx) -> tuple[Any, list[str]]:
    """The writer picks each location's ambience layers and each mood's music from the real catalog (sound.json)."""
    from assetforge.story import sound

    script: Script = ctx.project.load("script")
    world_ = ctx.project.load("world") if ctx.project.has("world") else None
    plan = _ask(ctx, "sound", sound.prompt(script, world_, ctx.lib), sound.SoundPlan, sound.offline_builder(script))
    plan, notes = sound.clean(plan, script, ctx.lib)
    ctx.project.save("sound", plan)
    return plan, notes


# ---------------------------------------------------------------------- assembly


def assemble(project: StoryProject) -> dict[str, Any]:
    """Build script.json from the written scenes and validate it. Missing scenes are reported, not invented."""
    config, cast_, plan = project.config, project.load("cast"), project.load("outline")
    scenes = project.scenes()
    missing = [s.id for s in plan.scenes if s.id not in scenes]
    if missing:
        return {"assembled": False, "missing_scenes": missing, "issues": []}
    script = Script(title=config.title, slug=config.slug, language=config.language, start=plan.start,
                    variables=plan.variables, chapters=plan.chapters, characters=cast_.characters,
                    locations=cast_.locations, endings=plan.endings, scenes=[scenes[s.id] for s in plan.scenes])  # fmt: skip
    issues = validate_script(script)
    project.save("script", script)
    quality = lint_story(script, project.config)
    write_atomic(project.root / "lint.json", json.dumps(quality, indent=1) + "\n", backup=False)
    write_atomic(project.root / "notes.md", render_notes(project, script, issues), backup=False)
    return {"assembled": True, "script": str(project.path("script")), "issues": [i.model_dump() for i in issues],
            "errors": sum(i.level == "error" for i in issues), "quality": quality["summary"]}  # fmt: skip


def validate(project: StoryProject) -> list[Issue]:
    return validate_script(project.load("script"))


def render_notes(project: StoryProject, script: Script, issues: list[Issue]) -> str:
    config = project.config
    world_ = project.load("world") if project.has("world") else None
    lines = [f"# {config.title}", "", f"_{config.premise}_", ""]
    if world_:
        lines += ["## World", "", f"**Logline:** {world_.logline}", "", f"**Setting:** {world_.setting}", "",
                  f"**Art direction:** {world_.art_direction}", "", f"**Music:** {world_.music_direction}", ""]  # fmt: skip
    lines += ["## Characters", ""]
    for c in script.characters:
        lines.append(f"- **{c.name}** (`{c.id}`, {c.role}): {c.personality} _Look:_ {c.look} "
                     f"_Expressions:_ {', '.join(c.expressions)}")  # fmt: skip
    lines += ["", "## Locations", ""]
    lines += [f"- **{loc.name}** (`{loc.id}`, {'/'.join(loc.times)}): {loc.description}" for loc in script.locations]
    lines += ["", "## Endings", ""] + [f"- **{e.title}** (`{e.id}`, {e.kind}): {e.summary}" for e in script.endings]
    names = {c.id: c.name for c in script.characters}
    for scene in script.scenes:
        lines += ["", f"## {scene.title} (`{scene.id}` · {scene.location} · {scene.time} · {scene.music})", ""]
        for n in scene.nodes:
            if n.when:
                lines.append("_if " + " and ".join(f"{c.var} {c.op} {c.value}" for c in n.when) + ":_")
            if n.kind == "say":
                lines.append(f"**{names.get(n.speaker or '', n.speaker)}**{f' _({n.expression})_' if n.expression else ''}: {n.text}")
            elif n.kind == "narrate":
                lines.append(f"> {n.text}")
            elif n.kind == "choice":
                for c in n.choices:
                    lines.append(f"- [ ] {c.text}" + (f" → `{c.goto}`" if c.goto else ""))
                    lines += [f"  - {names.get(r.speaker or '', r.speaker) + ': ' if r.speaker else ''}{r.text}" for r in c.reaction]
            elif n.kind == "place":
                lines.append(f"_→ {n.location}{' · ' + n.time if n.time else ''}{' · ' + n.music if n.music else ''}_"
                             + (f" {n.text}" if n.text else ""))
            elif n.kind == "music":
                lines.append(f"_♪ {n.music}_")
            elif n.kind == "branch":
                lines += [f"→ `{b.goto}` if " + " and ".join(f"{c.var} {c.op} {c.value}" for c in b.when) for b in n.branches]
                lines.append(f"→ `{n.goto}` otherwise")
            elif n.kind == "jump":
                lines.append(f"→ `{n.goto}`")
            elif n.kind == "ending":
                lines.append(f"**ENDING:** `{n.ending}`")
            elif n.kind in ("sfx", "cg"):
                lines.append(f"_[{n.kind}: {n.cue or n.cg}] {n.text or ''}_")
            elif n.kind in ("show", "hide"):
                lines.append(f"_[{n.kind} {n.character}{' ' + n.expression if n.expression else ''}]_")
        lines.append("")
    if issues:
        lines += ["## Validation", ""] + [f"- {i.level}: `{i.where}` {i.message}" for i in issues]
    return "\n".join(lines) + "\n"
