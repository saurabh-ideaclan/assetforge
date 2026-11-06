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
from assetforge.llm.base import TextCall
from assetforge.llm.structured import Structured, generate
from assetforge.story import offline, prompts
from assetforge.story.models import (Cast, ContinuityReport, Issue, Outline, PlannedScene, RevisionPlan, Scene, SceneDraft,
                                     Script, StoryConfig, World)  # fmt: skip
from assetforge.story.project import StoryProject, write_atomic
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
    result: Structured = generate(ctx.lib, call, model, provider=ctx.provider or config.provider,
                                  fresh=ctx.fresh if fresh is None else fresh, settings=settings,
                                  project=config.asset_project)  # fmt: skip
    ctx.calls.append({"task": task, "job_id": result.job_id, "provider": result.provider, "model": result.model,
                      "reused": result.reused, "billing": result.billing, "usage": result.usage})  # fmt: skip
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


def cast(ctx: Ctx) -> Cast:
    config, world_ = ctx.config, ctx.project.load("world")
    data: Cast = _ask(ctx, "cast", prompts.cast_prompt(config, world_), Cast, offline.cast(config))
    data = _unique_ids(data)
    ctx.project.save("cast", data)
    return data


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
        nxt = s.next if s.next in seen else None
        ending = s.ending if s.ending in endings else None
        if choices:
            nxt, ending = None, None
        elif nxt:
            ending = None
        if not choices and not nxt and not ending:
            if i + 1 < len(ids):
                nxt = ids[i + 1]
            else:
                ending = endings[0]
            fixes.append(f"{s.id}: no exit → {'next ' + nxt if nxt else 'ending ' + str(ending)}")
        update.update(choices=choices, next=nxt, ending=ending)
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
        for target in [c.goto for c in s.choices] + ([s.next] if s.next else []):
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

    def one(planned: PlannedScene, worker: Ctx) -> list[str]:
        prompt = prompts.scene_prompt(config, world_, cast_, plan, planned, _story_so_far(plan, parent, planned.id))
        # an explicit rewrite must not get the stored answer to the same prompt back
        draft: SceneDraft = _ask(worker, f"scene.{planned.id}", prompt, SceneDraft, offline.scene(planned, cast_),
                                 fresh=True if (wanted or rewrite) else None)  # fmt: skip
        scene, scene_fixes = _scene_from(planned, draft, expressions)
        ctx.project.save_scene(scene)
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


def _scene_from(planned: PlannedScene, draft: SceneDraft, expressions: dict[str, list[str]]) -> tuple[Scene, list[str]]:
    scene = Scene(id=planned.id, title=planned.title, location=planned.location, time=planned.time,
                  music=planned.music, nodes=draft.nodes)  # fmt: skip
    return conform_scene(scene, planned, expressions)


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
    scene, fixes = _scene_from(planned, draft, {c.id: c.expressions for c in cast_.characters})
    ctx.project.save_scene(scene)
    return fixes


def refine(ctx: Ctx, scene_id: str, note: str) -> dict[str, Any]:
    fixes = _refine_scene(ctx, scene_id, note)
    return {"scene": scene_id, "fixes": fixes, "backup": str(ctx.project.scene_path(scene_id)) + ".back",
            **assemble(ctx.project)}  # fmt: skip


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
                    variables=plan.variables, characters=cast_.characters, locations=cast_.locations,
                    endings=plan.endings, scenes=[scenes[s.id] for s in plan.scenes])  # fmt: skip
    issues = validate_script(script)
    project.save("script", script)
    write_atomic(project.root / "notes.md", render_notes(project, script, issues), backup=False)
    return {"assembled": True, "script": str(project.path("script")), "issues": [i.model_dump() for i in issues],
            "errors": sum(i.level == "error" for i in issues)}  # fmt: skip


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
            if n.kind == "say":
                lines.append(f"**{names.get(n.speaker or '', n.speaker)}**{f' _({n.expression})_' if n.expression else ''}: {n.text}")
            elif n.kind == "narrate":
                lines.append(f"> {n.text}")
            elif n.kind == "choice":
                lines += [f"- [ ] {c.text} → `{c.goto}`" for c in n.choices]
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
