"""The visual logic check: look at each finished story image and judge it against its brief and the visual canon.

The owner found a background for *Nobody Rides the Tail* (2026-11-12) that asked for the gap between two carriages of
one train and showed two trains side by side on parallel tracks, with a castle city and green land on a dead dust
continent. The canon (`visual_canon.py`) now goes into every prompt; this module is the other half: a vision-capable
model (the signed-in `codex` or `claude` CLI, never a paid image call) describes what the image shows, counts what the
brief fixes, and lists every broken brief sentence or canon rule, every physical impossibility and any lettering.

- `check(ctx, key)`: one structured verdict (task `visual.check.<key>`, kind `visual`). Not saved.
- `review(ctx, groups=…)`: check every made image in those groups and store the verdicts in `<story>/visual_check.json`
  by asset key (a remade asset id makes the stored verdict stale, so it is checked again).
- `fix(ctx, key)`: archive a failed image and request it again through `request.handle` with the violations appended to
  its description, check again, stop after `max_rounds` and keep the best attempt. A remade `bg` also remakes the
  `bg_time` images painted from it. Only `fix` spends money (image generation).

What it cannot do: the judge is a model looking at a picture. It misses small things, can be talked into seeing what
the brief says, and knows nothing the brief and canon do not say. A person still approves the art.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.errors import AssetForgeError, InvalidInputError, NotFoundError
from assetforge.story.project import StoryProject, write_atomic

FILE = "visual_check.json"
DEFAULT_GROUPS: tuple[str, ...] = ("bg", "bg_time", "cg", "ui")
CHECKABLE = frozenset({"char", "expr", "bg", "bg_time", "cg", "ui"})
WITH_REFERENCE = frozenset({"bg_time", "expr"})  # their brief is "the same as the reference image": show it to the judge
MATCH_SCORE = {"yes": 2, "partly": 1, "no": 0}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Violation(Strict):
    rule: str = Field(description="The brief sentence or canon rule that is broken, quoted or named")
    what: str = Field(description="What the image actually shows instead")


class Survey(Strict):
    """The blind look: what the image shows, from someone who has not read the brief (so cannot be primed by it)."""

    layout: str = Field(description="The spatial layout as a surveyor would state it: separate objects or rows of objects, "
                                    "how they sit relative to each other and to the viewer, what is joined to what and in which direction")
    counts: list[str] = Field(default_factory=list, description="Each countable thing with its number and placement")
    beyond: str = Field(description="Everything visible past the main subject: through openings, in the distance, on the horizon, in the sky")
    writing: str = Field(description="Any letters, numbers, readable signs or watermarks, or 'none'")


class Judgement(Strict):
    """The judge's answer, in the order it must think: look, count, then decide."""

    seen: str = Field(description="What the image shows, described plainly BEFORE judging: structures and their spatial "
                                  "arrangement, foreground/middle/background, what is visible through openings and at the horizon")
    counts: list[str] = Field(default_factory=list, description="Each counted thing with its number and placement, e.g. "
                                                                 "'trains: 2 (left and right, on separate parallel tracks)'")
    matches_brief: Literal["yes", "partly", "no"]
    violations: list[Violation] = Field(default_factory=list)
    logic: list[str] = Field(default_factory=list, description="Physical or spatial impossibilities, whatever the brief says")
    text_in_image: bool = Field(description="Any letters, numbers, writing or watermark visible")
    note: str = Field("", description="One sentence: the verdict and the main reason")


class Verdict(BaseModel):
    key: str
    asset_id: str
    matches_brief: Literal["yes", "partly", "no"]
    violations: list[Violation] = Field(default_factory=list)
    logic: list[str] = Field(default_factory=list)
    text_in_image: bool = False
    ok: bool = False
    note: str = ""
    seen: str = ""
    counts: list[str] = Field(default_factory=list)
    survey: str = ""  # the blind look (no brief), which the judge had to reconcile with what the brief wanted
    model: str = ""
    provider: str = ""
    job_id: str = ""
    checked_at: str = ""
    rounds: int = 0  # regenerations made by `fix` so far

    def score(self) -> tuple[int, int, int]:
        faults = len(self.violations) + len(self.logic) + int(self.text_in_image)
        return int(self.ok), MATCH_SCORE[self.matches_brief], -faults

    def first_fault(self) -> str:
        if self.violations:
            v = self.violations[0]
            return f"{v.rule}: {v.what}"
        if self.logic:
            return self.logic[0]
        return "lettering in the image" if self.text_in_image else ("" if self.ok else self.note or "does not match the brief")


def is_ok(j: Judgement) -> bool:
    return j.matches_brief == "yes" and not j.violations and not j.logic and not j.text_in_image


# ---------------------------------------------------------------------- storage


def load(project: StoryProject) -> dict[str, Any]:
    try:
        data = json.loads((project.root / FILE).read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("verdicts", {})
    data.setdefault("history", {})
    return data


def save(project: StoryProject, data: dict[str, Any]) -> None:
    write_atomic(project.root / FILE, json.dumps(data, indent=1) + "\n", backup=False)


def record(project: StoryProject, verdict: Verdict) -> None:
    data = load(project)
    dumped = verdict.model_dump(mode="json")
    data["verdicts"][verdict.key] = dumped
    data["history"].setdefault(verdict.key, []).append(dumped)
    data["history"][verdict.key] = data["history"][verdict.key][-12:]
    save(project, data)


def stored(project: StoryProject, key: str) -> Verdict | None:
    raw = load(project)["verdicts"].get(key)
    return Verdict.model_validate(raw) if raw else None


# ---------------------------------------------------------------------- the question


def _items(ctx: Any) -> list[Any]:
    from assetforge.story import acting, visual_canon
    from assetforge.story.assets import manifest

    project = ctx.project
    script = project.load("script")
    world = project.load("world") if project.has("world") else None
    return manifest(script, project.config, world, canon=visual_canon.load(project), acting=acting.directions(project))


def _item(ctx: Any, key: str) -> Any:
    item = next((i for i in _items(ctx) if i.key == key), None)
    if item is None:
        raise NotFoundError(f"{key} is not in this story's asset plan", key=key)
    if item.group not in CHECKABLE:
        raise InvalidInputError(f"{key} is a sound ({item.group}); the visual check judges images only")
    return item


def _location(project: StoryProject, item: Any) -> Any:
    """The script location the image shows: `bg/<loc>/<time>`, or where a CG's node happens."""
    from assetforge.story.models import walk

    script = project.load("script")
    locs = {loc.id: loc for loc in script.locations}
    parts = item.key.split("/")
    if item.group in ("bg", "bg_time") and len(parts) >= 5:
        return locs.get(parts[3])
    if item.group == "cg":
        cg = parts[-1]
        for scene in script.scenes:
            for node, where, *_ in walk(scene):
                if node.kind == "cg" and node.cg == cg:
                    return locs.get(where)
    return None


SYSTEM = ("You are a strict continuity checker for an illustrated visual novel. You look at finished images and say "
          "exactly what they show and where they break the brief or the story world's logic. You are never impressed "
          "by beauty, mood or detail: a gorgeous image of the wrong thing fails. Return only the requested JSON.")

STEPS = """Work in this order, and fill the fields in this order:
1. `seen`: describe what Image 1 actually shows BEFORE you judge anything: the main structures and how they are arranged in space (left/right/centre, near/far, what is joined to what, which way the view looks), what is in the foreground, middle and background, everything visible through windows, gaps and openings and on the horizon (land, vegetation, water, buildings, mountains, sky), the light, and any people, creatures or writing.
2. `counts`: count every thing whose number the brief or the canon fixes or implies, and say where each one is: vehicles, trains, carriages, tracks or rails, engines, doors, people, figures, heads, arms, legs, hands, fingers, suns, moons. Example: "trains: 2 (one left, one right, on separate parallel tracks)". If the canon says "one", a count of two is a violation.
3. Compare Image 1 with EVERY sentence of the brief and EVERY canon rule below, one by one. Layout and geometry statements ("one train on one track", "joined end to end", "the view looks forward along the train") are rules: if the arrangement differs, that is a violation even when every object is present. Anything the canon says lies outside (and anything it forbids) must be checked against what is visible beyond the place.
4. `violations`: one entry per broken rule. `rule` quotes or names the brief sentence or canon rule; `what` says what the image shows instead. Do not merge different faults.
5. `logic`: a PHYSICAL PLAUSIBILITY pass, independent of the brief and the canon. Do each check and report every failure:
   a. Axes: for every long or linear thing (a vehicle, a row of them, a street, a corridor, a bridge, a pipe, a table, a body) say which way it runs in the frame: across it (left-right), along the view (receding toward a vanishing point) or vertical. A part that connects two things lying one after another on an axis must run along that same axis: in a view looking along a line of things, the joints between them recede with it and can never be drawn left to right across the frame. Two things joined side by side are not one after another.
   b. Closure: vehicles, rooms, containers and buildings are closed where a real one is closed. The end, wall or roof of a coach, carriage, cabin or box is solid; seeing open track, sky or landscape straight through something that should be closed (a hollow end, a missing wall with no reason) is a fault.
   c. One camera, one moment: motion blur only on things moving relative to the camera, strongest close by and fading with distance (a landscape seen from a moving vehicle streaks near the tracks, the horizon stays sharp; blur all the way to the horizon is wrong); scale falls off consistently with distance; shadows and light all come from the same sources; one horizon line.
   d. Integrity: nothing that could not stand, hang, connect or move; no duplicated, merged or floating objects; no extra or missing limbs, heads or fingers.
6. `text_in_image`: true if any letters, numbers, readable signs, plaques with writing or watermarks appear.
7. `matches_brief`: "yes" only if the image shows the place or subject the brief asks for, in the layout it asks for, with nothing the canon forbids; "partly" if the subject is right but a required element or arrangement is wrong or missing; "no" if it shows another layout, place or subject.
Do not excuse a fault because the image is beautiful, because it is "artistic licence", or because the brief's words appear somewhere in it. When unsure whether something is there, look again and describe it; do not assume the brief was followed."""


def question(ctx: Any, item: Any, survey: Survey | None = None) -> str:
    from assetforge.story import visual_canon

    project = ctx.project
    canon = visual_canon.load(project)
    loc = _location(project, item)
    kind = {"bg": "background", "bg_time": "background (the same place at another time of day)", "cg": "event illustration",
            "ui": "title or interface art", "char": "character sprite", "expr": "character expression sprite"}[item.group]  # fmt: skip
    parts = [f"Image 1 is a finished visual-novel {kind} ({item.label}). A painter (an image model) made it from the brief "
             "below. Painters fill every gap with whatever looks pretty: a second train beside the only train, a city on "
             "the horizon of a dead continent, a lounge where the story has a slum. Your job is to catch that."]
    if item.group in WITH_REFERENCE:
        parts.append("Image 2 is the reference it was made from. Judge Image 1; use Image 2 only to check that Image 1 "
                     "keeps the same place, composition and subject (do not pass Image 1 because Image 2 has the same fault).")
    parts.append(f"THE BRIEF (what the painter was asked for)\n{item.request.description}")
    if loc is not None:
        lc = next((x for x in canon.locations if x.id == loc.id), None) if canon else None
        place = [f"THE PLACE: {loc.name}", f"What it is: {lc.description if lc else loc.description}"]
        if lc and lc.geometry:
            place.append(f"Layout, exactly: {lc.geometry}")
        if lc and lc.never:
            place.append("Never in this place:\n" + "\n".join(f"- {n}" for n in lc.never))
        parts.append("\n".join(place))
    scene_art = item.group in ("bg", "bg_time", "cg") or item.key.endswith("/ui/title")  # frames and sprites: brief only
    if canon is not None and scene_art:
        rules = [f"THE WORLD'S VISUAL CANON\nThe world: {canon.world}", f"Beyond any place (windows, gaps, open doors, horizon): {canon.outside}"]
        if canon.always:
            rules.append("Always true:\n" + "\n".join(f"- {a}" for a in canon.always))
        if canon.never:
            rules.append("Never painted:\n" + "\n".join(f"- {n}" for n in canon.never))
        if canon.props:
            rules.append("Fixed looks:\n" + "\n".join(f"- {p.name}: {p.look}" for p in canon.props))
        parts.append("\n".join(rules))
    elif scene_art and ctx.project.has("world"):
        world = ctx.project.load("world")
        parts.append(f"THE WORLD (no visual canon yet)\nSetting: {world.setting}\nRules: {world.rules}")
    if survey is not None:
        parts.append("A BLIND SURVEY OF IMAGE 1, made by someone who had NOT read the brief or the canon (so it cannot have "
                     "been talked into seeing what the brief wants):\n"
                     f"Layout: {survey.layout}\nCounts: {'; '.join(survey.counts) or '(none)'}\nBeyond: {survey.beyond}\n"
                     f"Writing: {survey.writing}\n"
                     "Treat it as evidence. Where it contradicts the brief (a different number of vehicles, objects side by "
                     "side instead of end to end, things on the horizon the canon forbids), look at Image 1 again: the survey "
                     "is right unless you can name the exact part of the image that shows otherwise, and then say so in `seen`.")
    parts.append(STEPS)
    return "\n\n".join(parts)


SURVEY_SYSTEM = ("You describe images literally and precisely, like a surveyor or a forensic photographer. You report "
                 "only what is visible, never what the scene is probably meant to be. Return only the requested JSON.")
SURVEY = """Open Image 1 and survey it. You are told nothing about what it is meant to show, on purpose: report what is there.
- `layout`: how many separate objects, vehicles, buildings or rows of them there are; how they are arranged relative to each other and to the viewer (left/right/centre, near/far, side by side or one behind the other); what is joined to what, and in which direction the joints run (across the view or along it); which way the view looks.
- `counts`: count every repeated or countable thing and say where it is (vehicles, rows of vehicles, tracks, engines, buildings, doors, windows that matter, people, heads, arms, legs, hands, suns, moons).
- `axes`-style detail inside `layout`: for every long or linear thing say which way it runs in the frame (left-right across it, along the view toward a vanishing point, or vertical), and for every joint or connecting part which way IT runs.
- also in `layout`: anything you can see straight through (open ends, missing walls, holes) and what is visible through it; where things are blurred and where they are sharp (near, middle distance, horizon).
- `beyond`: everything visible past the main subject: through gaps and openings, in the distance, on the horizon, in the sky (land, rock, vegetation, water, snow, mountains, buildings, cities).
- `writing`: any letters, numbers, readable signs, plaques with writing or watermarks; otherwise "none" (blank plaques are not writing)."""


def offline_survey(_: Any) -> dict[str, Any]:
    return {"layout": "offline: nothing was looked at", "counts": [], "beyond": "", "writing": "none"}


def offline_judgement(_: Any) -> dict[str, Any]:
    """The fake provider's answer (tests replace it): a pass that says it never looked."""
    return {"seen": "offline: nothing was looked at", "counts": [], "matches_brief": "yes", "violations": [], "logic": [],
            "text_in_image": False, "note": "offline check"}  # fmt: skip


def _image_of(lib: Any, asset_id: str) -> Path:
    asset = lib.get_asset(asset_id)
    if not asset.path:
        raise NotFoundError(f"{asset_id} has no file to look at", asset_id=asset_id)
    path = Path(asset.path)
    if not path.is_absolute():
        path = lib.paths.home / path
    return path


def judge(ctx: Any, task: str, prompt: str, images: list[Path], *, fresh: bool | None = None,
          answer: type[BaseModel] = Judgement, system: str = SYSTEM, offline: Any = None) -> tuple[Any, Any]:  # fmt: skip
    """One structured vision call through the story's execution plan (subscription CLIs that can see images)."""
    from assetforge.config import load_settings
    from assetforge.llm import plans
    from assetforge.llm.base import TextCall
    from assetforge.llm.structured import generate

    config = ctx.config
    settings = ctx.settings or load_settings(ctx.lib.paths.home)
    timeout = float(settings.get("ASSETFORGE_TEXT_TIMEOUT") or 600)
    provider, model = ctx.provider or config.provider, ctx.model or config.model
    call = TextCall(prompt=prompt, system=system, model=model, timeout=timeout, task=f"story.{task}",
                    offline=offline or offline_judgement, images=list(images))  # fmt: skip
    resolved = plans.resolve(call.task, settings, home=ctx.lib.paths.home, story_plan=config.ai,
                             override_provider=provider, override_model=model, allow_paid=ctx.allow_paid)  # fmt: skip
    result = generate(ctx.lib, call, answer, provider=provider, fresh=ctx.fresh if fresh is None else fresh,
                      settings=settings, project=config.asset_project, resolved=resolved)  # fmt: skip
    ctx.calls.append({"task": task, "job_id": result.job_id, "provider": result.provider, "model": result.model,
                      "reused": result.reused, "billing": result.billing, "usage": result.usage, "choice": result.choice})  # fmt: skip
    return result.data, result


def check(ctx: Any, key: str, *, asset_id: str | None = None, image: Path | None = None,
          references: list[Path] | None = None, rounds: int = 0, fresh: bool | None = None) -> Verdict:  # fmt: skip
    """Judge the image made for `key` (or `asset_id`/`image` when given, e.g. an archived one). Not stored."""
    from assetforge.request import by_key
    from assetforge.story.assets import _resolve_refs

    lib = ctx.lib
    item = _item(ctx, key)
    asset_id = asset_id or by_key(lib, item.request)
    if not asset_id and image is None:
        raise NotFoundError(f"No image made yet for {key}", key=key)
    path = Path(image) if image is not None else _image_of(lib, asset_id)  # type: ignore[arg-type]
    images = [path]
    if item.group in WITH_REFERENCE:
        if references is None:
            refs, _ = _resolve_refs(lib, item)
            references = [_image_of(lib, r) for r in refs[:1]]
        images += list(references)[:1]
    # two looks: a blind survey first (no brief, so it cannot be primed: a judge that has read "two carriages joined
    # end to end" sees them even in a picture of two trains side by side), then the judgement with the survey as evidence
    survey, _ = judge(ctx, f"visual.survey.{key}", SURVEY, [path], fresh=fresh, answer=Survey, system=SURVEY_SYSTEM,
                      offline=offline_survey)  # fmt: skip
    j, result = judge(ctx, f"visual.check.{key}", question(ctx, item, survey), images, fresh=fresh)
    blind = f"{survey.layout} Beyond: {survey.beyond}".strip()
    return Verdict(key=key, asset_id=asset_id or "", matches_brief=j.matches_brief, violations=j.violations, logic=j.logic,
                   text_in_image=j.text_in_image, ok=is_ok(j), note=j.note, seen=j.seen,
                   counts=list(dict.fromkeys([*j.counts, *survey.counts])), survey=blind,
                   model=result.model, provider=result.provider, job_id=result.job_id,
                   checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"), rounds=rounds)  # fmt: skip


def review(ctx: Any, *, groups: tuple[str, ...] | list[str] = DEFAULT_GROUPS, only_unchecked: bool = True,
           limit: int | None = None) -> dict[str, Any]:  # fmt: skip
    """Check every made image in `groups`; store each verdict as it comes (an interrupted run keeps what it did)."""
    from assetforge.request import by_key

    if unknown := set(groups) - CHECKABLE:
        raise InvalidInputError(f"Unknown image groups {sorted(unknown)}", known=sorted(CHECKABLE))
    project = ctx.project
    known = load(project)["verdicts"]
    rows: list[dict[str, Any]] = []
    checked = 0
    for item in _items(ctx):
        if item.group not in groups:
            continue
        row: dict[str, Any] = {"key": item.key, "group": item.group, "label": item.label}
        asset_id = by_key(ctx.lib, item.request)
        if not asset_id:
            rows.append({**row, "state": "not_made"})
            continue
        old = known.get(item.key)
        if only_unchecked and old and old.get("asset_id") == asset_id:
            rows.append({**row, "state": "checked_before", "asset_id": asset_id, "ok": old.get("ok")})
            continue
        if limit is not None and checked >= limit:
            rows.append({**row, "state": "skipped", "reason": "limit", "asset_id": asset_id})
            continue
        try:
            verdict = check(ctx, item.key, asset_id=asset_id, rounds=int((old or {}).get("rounds") or 0))
        except AssetForgeError as exc:
            rows.append({**row, "state": "failed", "asset_id": asset_id, "error": exc.to_dict()})
            if getattr(exc, "kind", None) in ("auth", "quota", "not_configured", "unsupported", "needs_confirmation"):
                break  # every following check would fail the same way
            continue
        checked += 1
        record(project, verdict)
        rows.append({**row, "state": "checked", "asset_id": asset_id, "ok": verdict.ok, "fault": verdict.first_fault()})
    return summary(project) | {"checked": checked, "items": rows}


def summary(project: StoryProject) -> dict[str, Any]:
    verdicts = load(project)["verdicts"]
    failed = [k for k, v in verdicts.items() if not v.get("ok")]
    return {"total": len(verdicts), "ok": len(verdicts) - len(failed), "failed": failed}


def view(lib: Any, project: StoryProject) -> dict[str, Any]:
    """For the app: verdicts by key, each marked `current` when it judged the asset the key holds now."""
    from assetforge.request import AssetRequest, by_key

    data = load(project)
    out: dict[str, Any] = {}
    for key, v in data["verdicts"].items():
        probe = AssetRequest(type="image", description="probe", project=project.config.asset_project, key=key)  # type: ignore[arg-type]
        try:
            now = by_key(lib, probe)
        except AssetForgeError:
            now = None
        out[key] = {**v, "current": bool(now) and now == v.get("asset_id"),
                    "fault": Verdict.model_validate(v).first_fault()}  # fmt: skip
    return {"verdicts": out, **summary(project)}


# ---------------------------------------------------------------------- the remake loop


def feedback(verdicts: list[Verdict]) -> str:
    """What the next painter is told: every fault seen so far (so a remake doesn't trade one for another)."""
    faults: list[str] = []
    rules: list[str] = []
    for v in verdicts:
        for x in v.violations:
            faults.append(f"it broke \"{x.rule.rstrip('.')}\": {x.what.rstrip('.')}")
            rules.append(x.rule.rstrip("."))
        faults += [f"impossible: {x.rstrip('.')}" for x in v.logic]
        if v.text_in_image:
            faults.append("it had lettering or numbers in it")
            rules.append("no letters, numbers or writing anywhere")
        if not (v.violations or v.logic or v.text_in_image) and not v.ok:
            faults.append(f"it did not show what was asked ({(v.note or v.seen)[:200].rstrip('.')})")
    faults = list(dict.fromkeys(faults))
    rules = list(dict.fromkeys(rules))
    # the count also makes every round's request differ, so a remake is never the reuse of the last one
    lead = "The previous attempt was rejected" if len(verdicts) < 2 else f"The previous {len(verdicts)} attempts were rejected"
    text = f"{lead}: " + "; ".join(faults) + "."
    if rules:
        text += " Paint it so that every one of these holds: " + "; ".join(rules) + "."
    return text


def _remake(ctx: Any, item: Any, note: str, *, image_provider: str, quality: str) -> str:
    """Request the item again with `note` appended (its old asset must already be archived); the new asset id."""
    from assetforge.core import review as reviews
    from assetforge.request import handle
    from assetforge.story.assets import _resolve_refs, ensure_art_profile

    lib, project = ctx.lib, ctx.project
    ensure_art_profile(lib, project.config, project.load("world") if project.has("world") else None)
    refs, missing = _resolve_refs(lib, item)
    if missing:
        raise NotFoundError(f"{item.key} needs {', '.join(missing)} first", key=item.key)
    update: dict[str, Any] = {"references": refs, "description": f"{item.request.description.rstrip()} {note}".strip()}
    if image_provider != "auto":
        update["provider"] = image_provider
    if quality != "auto":
        update["quality"] = quality
    result = handle(lib, item.request.model_copy(update=update))
    if not result.asset_id:
        raise AssetForgeError(f"Remaking {item.key} gave no asset ({result.state})", key=item.key, error=result.error)
    if lib.get_asset(result.asset_id).status.value == "archived":
        reviews.restore(lib, [result.asset_id], note="identical remake")
    return result.asset_id


def fix(ctx: Any, key: str, *, max_rounds: int = 2, image_provider: str = "auto", quality: str = "auto") -> dict[str, Any]:
    """Remake a failed image with its faults as feedback until it passes or `max_rounds` remakes are spent; keep the
    best attempt (the others stay archived). Paid: each round is one image generation."""
    from assetforge.core import review as reviews

    lib, project = ctx.lib, ctx.project
    item = _item(ctx, key)
    current = stored(project, key)
    from assetforge.request import by_key

    now = by_key(lib, item.request)
    if current is None or (now and current.asset_id != now):
        current = check(ctx, key, asset_id=now, rounds=current.rounds if current else 0)
        record(project, current)
    if current.ok:
        return {"key": key, "state": "ok", "asset_id": current.asset_id, "rounds": current.rounds, "verdict": current.model_dump(mode="json")}
    attempts = [current]
    rounds = current.rounds
    for _ in range(max_rounds):
        prev = attempts[-1]
        reviews.archive(lib, [prev.asset_id], note=f"visual check: {prev.first_fault()[:300]}")
        new_id = _remake(ctx, item, feedback(attempts), image_provider=image_provider, quality=quality)
        rounds += 1
        verdict = check(ctx, key, asset_id=new_id, rounds=rounds)
        record(project, verdict)
        attempts.append(verdict)
        if verdict.ok:
            break
    best = max(attempts, key=lambda v: (v.score(), attempts.index(v)))
    last = attempts[-1]
    if best is not last:  # keep the best attempt in place, the latest one archived
        reviews.archive(lib, [last.asset_id], note="visual check: an earlier attempt was better")
        reviews.restore(lib, [best.asset_id], note="visual check: best attempt kept")
        best = best.model_copy(update={"rounds": rounds})
        record(project, best)
    out: dict[str, Any] = {"key": key, "state": "ok" if best.ok else "still_failing", "asset_id": best.asset_id,
                           "rounds": rounds, "attempts": [{"asset_id": a.asset_id, "ok": a.ok, "fault": a.first_fault()} for a in attempts],
                           "verdict": best.model_dump(mode="json"), "dependents": []}  # fmt: skip
    if item.group == "bg" and best.asset_id != attempts[0].asset_id:
        out["dependents"] = _remake_dependents(ctx, key, image_provider=image_provider, quality=quality)
    return out


def _remake_dependents(ctx: Any, key: str, *, image_provider: str, quality: str) -> list[dict[str, Any]]:
    """The other times of day were relit from the old base image: remake them from the new one and check them."""
    from assetforge.core import review as reviews
    from assetforge.request import by_key

    lib = ctx.lib
    rows: list[dict[str, Any]] = []
    for dep in (i for i in _items(ctx) if i.group == "bg_time" and key in i.refs):
        old = by_key(lib, dep.request)
        if old:
            reviews.archive(lib, [old], note=f"visual check: base {key} was remade")
        try:
            new_id = _remake(ctx, dep, "", image_provider=image_provider, quality=quality)
            verdict = check(ctx, dep.key, asset_id=new_id)
            record(ctx.project, verdict)
            rows.append({"key": dep.key, "asset_id": new_id, "ok": verdict.ok, "fault": verdict.first_fault()})
        except AssetForgeError as exc:
            rows.append({"key": dep.key, "error": exc.to_dict()})
    return rows
