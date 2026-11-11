"""Character acting: story-driven, character-specific performance planning (owner's brief of 2026-11-11).

No fixed matrix of emotions and poses. Each important character gets an **acting profile** derived from the
Character Studio profile (how this person holds themself, what they do with their hands, how they show or hide
feeling, how that changes under stress, around particular people, and over the arc). Then, for the written
chapters, the **performance analysis** reads every significant appearance and decides, moment by moment, what
the character is feeling and trying to show or hide, whom they face, how *this* character would physically
answer, and whether an existing sprite already says it, a face change on an existing body would do, or the
story needs a new performance. Only approved gaps are generated, from the master reference, with the acting
direction as the brief. Stored in `acting.json`; the writer-made expression list is left alone.

A performance has a short `name`; when it is approved the scene lines it covers are retargeted to that name
(it becomes an expression of the character), so the usual asset manifest makes exactly the sprites the scenes
use, with the performance's direction as the image brief (`assets.manifest(acting=…)`).
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.story.models import Scene
from assetforge.story.project import StoryProject

FILE = "acting.json"
Need = Literal["reuse", "face_only", "new_pose", "none"]
Status = Literal["proposed", "approved", "dismissed", "generated"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AroundOthers(Strict):
    who: str
    how: str


class ActingProfile(Strict):
    resting_posture: str = ""
    mannerisms: str = Field("", description="Typical physical habits")
    expression_style: str = Field("", description="How they express or suppress emotion")
    presence: str = Field("", description="Confidence, social behaviour, physical presence")
    gestures: str = Field("", description="Characteristic gestures")
    under_stress: str = Field("", description="How posture and gesture change under stress")
    around_others: list[AroundOthers] = Field(default_factory=list)
    development: str = Field("", description="How body language evolves over the arc, tied to story events")
    out_of_character: list[str] = Field(default_factory=list, description="Gestures or expressions this person would never do")
    state: Literal["proposed", "approved", "changes_requested"] = "proposed"
    note: str = ""
    made_by: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = ""


class Direction(Strict):
    """One coherent performance: face to silhouette."""

    face: str = ""
    eyes: str = Field("", description="Eyes and gaze")
    head: str = ""
    shoulders: str = ""
    torso: str = ""
    hands: str = Field("", description="Arms and hands")
    weight: str = Field("", description="Weight distribution and stance")
    intensity: str = Field("", description="Gesture intensity: still, contained, open, large")
    silhouette: str = ""

    def text(self) -> str:
        parts = [(k, getattr(self, k)) for k in ("face", "eyes", "head", "shoulders", "torso", "hands", "weight", "intensity", "silhouette")]
        return " ".join(f"{k.capitalize()}: {v.rstrip('.')}." for k, v in parts if v)


class Performance(Strict):
    id: str
    character: str
    scene: str
    where: str = Field("", description="Line numbers in the acting view, e.g. '12-19'")
    moment: str = Field("", description="What happens, one line")
    emotional_context: str = ""
    intent: str = Field("", description="What they are trying to communicate or conceal")
    with_whom: str = ""
    name: str = Field("", description="Short snake_case name the sprite gets, e.g. suppressed_anger")
    direction: Direction = Field(default_factory=Direction)
    need: Need = "reuse"
    reuse_sprite: str = Field("", description="Existing sprite (expression name) that already says it, for reuse / the body to keep for face_only")
    justification: str = ""
    status: Status = "proposed"
    asset_key: str = ""
    at: str = ""


class SpriteVerdict(Strict):
    sprite: str
    works: bool
    note: str = ""


class Acting(Strict):
    profiles: dict[str, ActingProfile] = Field(default_factory=dict)
    performances: list[Performance] = Field(default_factory=list)
    verdicts: dict[str, list[SpriteVerdict]] = Field(default_factory=dict, description="character id → verdict per existing sprite")
    analyzed: dict[str, str] = Field(default_factory=dict, description="character id → when its scenes were last analyzed")


# ---------------------------------------------------------------------- storage


def load(project: StoryProject) -> Acting:
    p = project.root / FILE
    return Acting.model_validate_json(p.read_text()) if p.exists() else Acting()


def save(project: StoryProject, data: Acting) -> None:
    from assetforge.story.project import write_atomic

    write_atomic(project.root / FILE, data.model_dump_json(indent=1))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _ident(text: str) -> str:
    v = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "performance"
    return v if v[0].isalpha() else f"p_{v}"


# ---------------------------------------------------------------------- 1. the acting profile


def profile_prompt(project: StoryProject, cid: str) -> str:
    from assetforge.story import characters

    p = characters.get(project, cid)
    cfg = project.config
    direction = ""
    if cfg.direction is not None and cfg.direction.custom.requirements:
        direction = "STORY RULES (for the arc):\n" + cfg.direction.custom.requirements[:1500]
    return "\n\n".join(x for x in [
        "You are the acting director of a visual novel. From this character's approved profile, write their ACTING "
        "PROFILE: how this particular person holds themself and moves, so every sprite is them and not a generic "
        "emotion. Be concrete and physical (jaw, hands, weight, gaze), never theatrical. Each field is one to three "
        "sentences. Body language must come from personality and history; name what they would never do.",
        f"CHARACTER: {p.identity.name or cid} ({p.identity.role})\nIDENTITY: {p.identity.model_dump_json()}\n"
        f"PERSONALITY: {p.personality.model_dump_json()}\nNARRATIVE: {p.narrative.model_dump_json()}\nVISUAL BUILD: {p.visual.build} {p.visual.features}",
        direction,
        "Return JSON: {\"resting_posture\", \"mannerisms\", \"expression_style\", \"presence\", \"gestures\", \"under_stress\", "
        "\"around_others\": [{\"who\", \"how\"}], \"development\", \"out_of_character\": [\"...\"]}.",
    ] if x)


class ProfileAnswer(Strict):
    resting_posture: str = ""
    mannerisms: str = ""
    expression_style: str = ""
    presence: str = ""
    gestures: str = ""
    under_stress: str = ""
    around_others: list[AroundOthers] = Field(default_factory=list)
    development: str = ""
    out_of_character: list[str] = Field(default_factory=list)


def offline_profile(cid: str) -> Any:
    def build(_: Any) -> dict[str, Any]:
        return {"resting_posture": f"{cid} stands square, weight even, hands low.", "mannerisms": "Counts on fingers when nervous.",
                "expression_style": "Shows little; anger goes still.", "presence": "Contained.", "gestures": "A closed hand at the side.",
                "under_stress": "Shoulders rise, jaw sets, gaze drops.", "around_others": [{"who": "family", "how": "softer, hands busier"}],
                "development": "Straightens as the arc earns it.", "out_of_character": ["theatrical pointing", "wide open arms"]}
    return build


def profile(ctx: Any, cid: str, *, fresh: bool = False) -> Acting:
    from assetforge.story.steps import _ask

    project = ctx.project
    answer: ProfileAnswer = _ask(ctx, f"acting.profile.{cid}", profile_prompt(project, cid), ProfileAnswer, offline_profile(cid), fresh=fresh)
    call = ctx.calls[-1] if ctx.calls else {}
    data = load(project)
    old = data.profiles.get(cid)
    data.profiles[cid] = ActingProfile(**answer.model_dump(), state="proposed", note=old.note if old else "",
                                       made_by={"provider": call.get("provider"), "model": call.get("model")}, updated_at=_now())
    save(project, data)
    return data


def set_profile(project: StoryProject, cid: str, *, patch: dict[str, Any] | None = None, state: str | None = None, note: str = "") -> Acting:
    data = load(project)
    p = data.profiles.get(cid) or ActingProfile()
    if patch:
        p = p.model_copy(update={k: v for k, v in patch.items() if k in ActingProfile.model_fields and k not in ("made_by",)})
    if state:
        p = p.model_copy(update={"state": state, "note": note or p.note})
    data.profiles[cid] = p.model_copy(update={"updated_at": _now()})
    save(project, data)
    return data


# ---------------------------------------------------------------------- 2. what the scenes ask for


def acting_view(scene: Scene, cid: str, names: dict[str, str]) -> str:
    """The chapter as numbered lines with the character's current sprite marked: `[Eli appears: worried]`, and
    `Eli (worried): …` on their lines. Everything else as the reader sees it."""
    who = lambda c: names.get(c, c.replace("_", " ").title()) if c else ""  # noqa: E731
    out: list[str] = []
    for n in scene.nodes:
        if n.kind == "show" and n.character == cid:
            out.append(f"{len(out) + 1}. [{who(cid)} appears: {n.expression or 'neutral'}]")
        elif n.kind == "hide" and n.character == cid:
            out.append(f"{len(out) + 1}. [{who(cid)} leaves]")
        elif n.kind == "narrate":
            out.append(f"{len(out) + 1}. {n.text or ''}")
        elif n.kind == "say":
            tag = f" ({n.expression or 'neutral'})" if n.speaker == cid else ""
            out.append(f"{len(out) + 1}. {who(n.speaker)}{tag}: {n.text or ''}")
        elif n.kind == "place":
            out.append(f"{len(out) + 1}. — {(n.location or '').replace('_', ' ')}{', ' + n.time if n.time else ''} — {n.text or ''}")
        elif n.kind == "choice":
            out.append(f"{len(out) + 1}. [CHOICE]")
            for i, c in enumerate(n.choices, 1):
                out.append(f"    ({i}) {c.text}")
                for ln in c.reaction:
                    tag = f" ({ln.expression or 'neutral'})" if ln.speaker == cid else ""
                    out.append(f"        {(who(ln.speaker) + tag + ': ') if ln.speaker else ''}{ln.text}")
    return "\n".join(out)


def appears_in(scene: Scene, cid: str) -> bool:
    return any((n.kind in ("show",) and n.character == cid) or (n.kind == "say" and n.speaker == cid) for n in scene.nodes)


def sprite_usage(project: StoryProject, cid: str) -> dict[str, list[str]]:
    """expression name → scene ids where the character shows or speaks with it (written scenes only)."""
    from assetforge.story.models import Outline

    ids = [s.id for s in project.load("outline", Outline).scenes] if project.has("outline") else []
    usage: dict[str, list[str]] = {}
    for sid in ids:
        sc = project.load_scene(sid)
        if sc is None:
            continue
        names: set[str] = set()
        for n in sc.nodes:
            if n.kind == "show" and n.character == cid:
                names.add(n.expression or "neutral")
            elif n.kind == "say" and n.speaker == cid:
                names.add(n.expression or "neutral")
            for c in n.choices:
                for ln in c.reaction:
                    if ln.speaker == cid:
                        names.add(ln.expression or "neutral")
        for nm in names:
            usage.setdefault(nm, []).append(sid)
    return usage


def existing_sprites(lib: Any, project: StoryProject, cid: str) -> list[dict[str, Any]]:
    """The character's sprites in the library (expression names from the cast list plus performance names) with
    their asset, status and the scenes that use them."""
    from assetforge.request import by_key
    from assetforge.story.assets import story_key

    cfg = project.config
    cast = project.load("cast")
    char = next((c for c in cast.characters if c.id == cid), None)
    data = load(project)
    names = list(dict.fromkeys((char.expressions if char else ["neutral"]) + [p.name for p in data.performances if p.character == cid and p.name]))
    usage = sprite_usage(project, cid)
    out = []
    for nm in names:
        key = story_key(cfg.slug, "char", cid, nm)
        from assetforge.core.models import AssetType
        from assetforge.request import AssetRequest

        probe = AssetRequest(type=AssetType.CHARACTER, description=nm, project=cfg.asset_project, key=key)  # type: ignore[call-arg]
        aid = by_key(lib, probe)
        asset = lib.get_asset(aid) if aid else None
        status = asset.status.value if asset else None
        variant_id = asset.primary_variant.id if asset and asset.primary_variant else None
        out.append({"expression": nm, "key": key, "asset_id": aid, "variant_id": variant_id, "status": status, "scenes": usage.get(nm, []),
                    "performance": next((p.id for p in data.performances if p.character == cid and p.name == nm), None)})
    return out


class PerformanceAnswer(Strict):
    scene: str
    where: str
    moment: str
    emotional_context: str
    intent: str
    with_whom: str
    name: str
    direction: Direction
    need: Need
    reuse_sprite: str = ""
    justification: str


class AnalysisAnswer(Strict):
    verdicts: list[SpriteVerdict] = Field(default_factory=list)
    performances: list[PerformanceAnswer] = Field(default_factory=list)
    summary: str = ""


def analysis_prompt(project: StoryProject, cid: str, prof: ActingProfile, sprites: list[dict[str, Any]], views: list[tuple[str, str, str]]) -> str:
    from assetforge.story import characters

    p = characters.get(project, cid)
    have = "\n".join(f"- {s['expression']}: {'made' if s['asset_id'] else 'not made'}; used in {', '.join(s['scenes']) or 'no scene'}" for s in sprites)
    chapters = "\n\n".join(f"=== {title} ({sid}) ===\n{v}" for sid, title, v in views)
    return "\n\n".join([
        "You are the acting director. Read the chapters below as the player sees them, with this character's current "
        "sprite marked on each appearance. For every significant appearance decide: what are they feeling; what are they "
        "trying to communicate or conceal; whom they face; how would THIS person physically answer (from the acting "
        "profile, not a generic emotion); does the moment need a different performance from what is marked; could an "
        "existing sprite say it; would a new one materially improve the scene. Treat a performance as one coherent "
        "whole from face to silhouette, but do not change the body when the eyes are enough. Prefer reuse. Propose a "
        "new performance only when the story requires it and nothing existing says it; give each a short snake_case "
        "name and a justification that names the scene. Also judge every existing sprite: does it work for the scenes "
        "that use it, given who this person is?",
        f"CHARACTER: {p.identity.name or cid}. PERSONALITY: {p.personality.model_dump_json()}",
        "ACTING PROFILE:\n" + prof.model_dump_json(exclude={"state", "note", "made_by", "updated_at"}),
        "EXISTING SPRITES (expression names; 'neutral' is the master reference):\n" + have,
        "CHAPTERS\n" + chapters,
        "Return JSON: {\"verdicts\": [{\"sprite\", \"works\", \"note\"}], \"performances\": [{\"scene\" (the scene id in "
        "parentheses after the chapter title), \"where\" (the LINE NUMBERS of the chapter text the performance covers, e.g. '12-19', "
        "never a place name), \"moment\", "
        "\"emotional_context\", \"intent\", \"with_whom\", \"name\", \"direction\": {\"face\", \"eyes\", \"head\", \"shoulders\", "
        "\"torso\", \"hands\", \"weight\", \"intensity\", \"silhouette\"}, \"need\": \"reuse\"|\"face_only\"|\"new_pose\", "
        "\"reuse_sprite\", \"justification\"}], \"summary\"}. `reuse` names the existing sprite in reuse_sprite; `face_only` "
        "names the existing body to keep in reuse_sprite; `new_pose` leaves it empty. At most one performance per "
        "scene unless two moments truly differ.",
    ])


def offline_analysis(cid: str, views: list[tuple[str, str, str]]) -> Any:
    def build(_: Any) -> dict[str, Any]:
        first = views[0][0] if views else "s01"
        return {"verdicts": [{"sprite": "neutral", "works": True, "note": "the resting posture"}],
                "performances": [{"scene": first, "where": "1-8", "moment": "the refusal", "emotional_context": "humiliation held down",
                                  "intent": "conceal", "with_whom": "the clerk", "name": "held_anger",
                                  "direction": {"face": "jaw set", "eyes": "direct", "head": "level", "shoulders": "rigid", "torso": "square",
                                                "hands": "one hand closed on the card", "weight": "even", "intensity": "still", "silhouette": "narrow, upright"},
                                  "need": "new_pose", "reuse_sprite": "", "justification": f"{first}: the marked angry sprite is confrontational; this person goes still"}],
                "summary": "offline analysis"}
    return build


def analyze(ctx: Any, cid: str, scene_ids: list[str] | None = None, *, fresh: bool = False) -> Acting:
    """Step 1-4 of the brief for one character: read the written chapters, judge existing sprites, propose only
    what the story needs. Proposals replace earlier *proposed* ones for the same scenes; approved, generated and
    dismissed ones are kept."""
    from assetforge.errors import NotFoundError
    from assetforge.story import clarity
    from assetforge.story.models import Outline
    from assetforge.story.steps import _ask

    project = ctx.project
    data = load(project)
    prof = data.profiles.get(cid)
    if prof is None:
        data = profile(ctx, cid)
        prof = data.profiles[cid]
    ids = scene_ids or ([s.id for s in project.load("outline", Outline).scenes] if project.has("outline") else [])
    names = clarity.scene_names(project)
    views: list[tuple[str, str, str]] = []
    for sid in ids:
        sc = project.load_scene(sid)
        if sc is not None and appears_in(sc, cid):
            views.append((sid, sc.title or sid, acting_view(sc, cid, names)))
    if not views:
        raise NotFoundError(f"{cid} does not appear in any written chapter" + (f" among {ids}" if scene_ids else ""))
    sprites = existing_sprites(ctx.lib, project, cid)
    answer: AnalysisAnswer = _ask(ctx, f"acting.analyze.{cid}", analysis_prompt(project, cid, prof, sprites, views), AnalysisAnswer,
                                  offline_analysis(cid, views), fresh=fresh)
    covered = {v[0] for v in views}
    kept = [p for p in data.performances if not (p.character == cid and p.scene in covered and p.status == "proposed")]
    taken = {p.name for p in kept if p.character == cid} | {s["expression"] for s in sprites}
    new: list[Performance] = []
    for a in answer.performances:
        if a.scene not in covered:
            continue
        name = _ident(a.name or a.moment)
        if a.need == "reuse":
            name = a.reuse_sprite or name
        elif name in taken:
            name = f"{name}_{a.scene[:6]}"
        pid = f"pf{int(time.time() * 1000) % 10_000_000:07d}{len(new)}"
        new.append(Performance(id=pid, character=cid, scene=a.scene, where=a.where, moment=a.moment, emotional_context=a.emotional_context,
                               intent=a.intent, with_whom=a.with_whom, name=name, direction=a.direction, need=a.need,
                               reuse_sprite=a.reuse_sprite, justification=a.justification, status="proposed", at=_now()))
        taken.add(name)
    data.performances = kept + new
    data.verdicts[cid] = answer.verdicts
    data.analyzed[cid] = _now()
    save(project, data)
    return data


# ---------------------------------------------------------------------- 3. decisions


def _retarget(project: StoryProject, perf: Performance, expression: str) -> int:
    """Point the character's lines in the performance's range at `expression`; register it as one of the
    character's expressions so the validator and the manifest know it. Returns the number of lines changed."""
    sc = project.load_scene(perf.scene)
    if sc is None:
        return 0
    lo, hi = _line_range(perf.where)
    # acting_view numbers nodes the same way acting_view prints them: walk again and map numbers to node indexes
    names: dict[str, str] = {}
    number = 0
    targets: set[int] = set()
    for i, n in enumerate(sc.nodes):
        if n.kind in ("show", "hide") and n.character != perf.character:
            continue
        if n.kind in ("set", "jump", "branch", "ending", "music", "sfx", "cg", "ambience"):
            continue
        number += 1
        if lo <= number <= hi:
            targets.add(i)
    changed = 0
    nodes = list(sc.nodes)
    for i in targets:
        n = nodes[i]
        if (n.kind == "show" and n.character == perf.character) or (n.kind == "say" and n.speaker == perf.character):
            nodes[i] = n.model_copy(update={"expression": expression})
            changed += 1
        if n.choices:
            opts = []
            for c in n.choices:
                lines = [ln.model_copy(update={"expression": expression}) if ln.speaker == perf.character else ln for ln in c.reaction]
                changed += sum(1 for ln in c.reaction if ln.speaker == perf.character)
                opts.append(c.model_copy(update={"reaction": lines}))
            nodes[i] = nodes[i].model_copy(update={"choices": opts})
    if changed:
        project.save_scene(sc.model_copy(update={"nodes": nodes}))
    cast = project.load("cast")
    chars = []
    for c in cast.characters:
        if c.id == perf.character and expression not in c.expressions:
            c = c.model_copy(update={"expressions": [*c.expressions, expression]})
        chars.append(c)
    project.save("cast", cast.model_copy(update={"characters": chars}))
    return changed


def _line_range(where: str) -> tuple[int, int]:
    nums = [int(x) for x in re.findall(r"\d+", where or "")]
    if not nums:
        return (0, 0)
    return (min(nums), max(nums))


def decide(project: StoryProject, pid: str, action: str, *, direction: dict[str, Any] | None = None, reuse_sprite: str = "",
           note: str = "") -> Acting:  # fmt: skip
    """approve (retargets the scene lines to the performance's name; `direction` may adjust it first), modify
    (direction/name/need edits, stays proposed), reuse (retargets to an existing sprite, closes the performance),
    dismiss."""
    from assetforge.errors import InvalidInputError, NotFoundError

    data = load(project)
    perf = next((p for p in data.performances if p.id == pid), None)
    if perf is None:
        raise NotFoundError(f"No performance {pid!r}")
    if action == "modify":
        upd: dict[str, Any] = {}
        if direction:
            upd["direction"] = perf.direction.model_copy(update={k: v for k, v in direction.items() if k in Direction.model_fields})
        if reuse_sprite:
            upd["reuse_sprite"] = reuse_sprite
        if note:
            upd["justification"] = note
        perf = perf.model_copy(update=upd)
    elif action == "approve":
        if direction:
            perf = perf.model_copy(update={"direction": perf.direction.model_copy(update={k: v for k, v in direction.items() if k in Direction.model_fields})})
        if perf.need == "reuse":
            _retarget(project, perf, perf.reuse_sprite or "neutral")
            perf = perf.model_copy(update={"status": "generated", "name": perf.reuse_sprite or "neutral"})
        else:
            _retarget(project, perf, perf.name)
            perf = perf.model_copy(update={"status": "approved"})
    elif action == "reuse":
        sprite = reuse_sprite or perf.reuse_sprite
        if not sprite:
            raise InvalidInputError("Which sprite? Pass `reuse_sprite`")
        _retarget(project, perf, sprite)
        perf = perf.model_copy(update={"status": "generated", "need": "reuse", "reuse_sprite": sprite, "name": sprite})
    elif action == "dismiss":
        perf = perf.model_copy(update={"status": "dismissed", "justification": (perf.justification + (f" Dismissed: {note}" if note else "")).strip()})
    else:
        raise InvalidInputError(f"Unknown action {action!r}", known=["approve", "modify", "reuse", "dismiss"])
    data.performances = [perf if p.id == pid else p for p in data.performances]
    save(project, data)
    return data


def directions(project: StoryProject) -> dict[tuple[str, str], str]:
    """(character id, expression name) → the image brief for approved or generated performances."""
    out: dict[tuple[str, str], str] = {}
    perfs = load(project).performances
    for p in perfs:
        if p.status in ("approved", "generated") and p.need != "reuse" and p.name:
            base = f" Same character as the reference image: identical face, hair, outfit and art style; keep the body of the '{p.reuse_sprite}' sprite and change only the face." if p.need == "face_only" and p.reuse_sprite else " Same character as the reference image: identical face, hair, outfit, scale and art style."
            out[(p.character, p.name)] = f"{p.moment}: {p.emotional_context}. {p.direction.text()}{base}"
    # a reuse that points at a sprite not made yet still briefs it: the manifest only makes what is missing, so a
    # sprite that exists is never touched, and one that does not gets the performance's direction instead of a label
    for p in perfs:
        if p.need == "reuse" and p.reuse_sprite and p.status != "dismissed" and (p.character, p.reuse_sprite) not in out:
            out[(p.character, p.reuse_sprite)] = (f"{p.moment}: {p.emotional_context}. {p.direction.text()} Same character as the reference image: "
                                                  "identical face, hair, outfit, scale and art style; change only what the direction says.")
    return out


def generate(lib: Any, project: StoryProject, pid: str, *, quality: str = "auto", image_provider: str = "auto", dry_run: bool = False) -> dict[str, Any]:
    """Make the sprite for one approved performance from the master reference (Step 5)."""
    from assetforge.core.models import AssetType
    from assetforge.errors import InvalidInputError, NotFoundError
    from assetforge.request import AssetRequest, by_key, handle
    from assetforge.story.assets import story_key

    data = load(project)
    perf = next((p for p in data.performances if p.id == pid), None)
    if perf is None:
        raise NotFoundError(f"No performance {pid!r}")
    if perf.status not in ("approved", "generated") or perf.need == "reuse":
        raise InvalidInputError("Approve the performance first; a reuse needs no image")
    cfg = project.config
    cast = project.load("cast")
    char = next((c for c in cast.characters if c.id == perf.character), None)
    cname = char.name if char else perf.character
    key = story_key(cfg.slug, "char", perf.character, perf.name)
    brief = directions(project).get((perf.character, perf.name), perf.direction.text())
    refs_keys = [story_key(cfg.slug, "char", perf.character, "neutral")]
    if perf.need == "face_only" and perf.reuse_sprite and perf.reuse_sprite != "neutral":
        refs_keys.append(story_key(cfg.slug, "char", perf.character, perf.reuse_sprite))
    refs, missing = [], []
    for k in refs_keys:
        aid = by_key(lib, AssetRequest(type=AssetType.CHARACTER, description="ref", project=cfg.asset_project, key=k))  # type: ignore[call-arg]
        (refs.append(aid) if aid else missing.append(k))
    if missing and not dry_run:
        raise NotFoundError(f"Make the master reference first: {', '.join(missing)}")
    req = AssetRequest(type=AssetType.CHARACTER, description=f"{cname}, visual novel character sprite, performance '{perf.name.replace('_', ' ')}'. {brief} Full body, centred, nothing else in frame.",
                       project=cfg.asset_project, key=key, provider=image_provider, aspect="2:3", transparent=True, quality=quality,
                       name=f"{cfg.slug}_{perf.character}_{perf.name}", tags=["visual-novel", cfg.slug, "performance"], process="web-sprite",
                       collection=f"vn-{cfg.slug}", reuse_policy="always_generate", genre=cfg.genre, purpose="visual novel performance",
                       references=refs, dry_run=dry_run)  # type: ignore[call-arg]
    result = handle(lib, req)
    if not dry_run and result.asset_id:
        data.performances = [p.model_copy(update={"status": "generated", "asset_key": key}) if p.id == pid else p for p in data.performances]
        save(project, data)
    return {"performance": pid, "key": key, "state": result.state, "asset_id": result.asset_id, "plan": result.plan, "references": refs}


# ---------------------------------------------------------------------- 4. the view


def plan(lib: Any, project: StoryProject, cid: str | None = None) -> dict[str, Any]:
    """What the Character Studio shows: profile, sprites with usage and verdicts, performances by status."""
    data = load(project)
    cast = project.load("cast") if project.has("cast") else None
    ids = [cid] if cid else ([c.id for c in cast.characters] if cast else [])
    out = []
    for c in ids:
        sprites = existing_sprites(lib, project, c) if cast else []
        verdict = {v.sprite: v for v in data.verdicts.get(c, [])}
        for s in sprites:
            v = verdict.get(s["expression"])
            s["verdict"] = {"works": v.works, "note": v.note} if v else None
        perfs = [p.model_dump(mode="json") for p in data.performances if p.character == c]
        out.append({"character": c, "profile": data.profiles.get(c).model_dump(mode="json") if c in data.profiles else None,
                    "sprites": sprites, "performances": perfs, "analyzed": data.analyzed.get(c),
                    "missing": [p["id"] for p in perfs if p["status"] == "approved" and p["need"] != "reuse"],
                    "proposed": [p["id"] for p in perfs if p["status"] == "proposed"]})
    return {"characters": out, "note": "Performances come from the written chapters; only approved gaps are generated, from the master."}
