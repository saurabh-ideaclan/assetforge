"""Audio direction per chapter: a plan of cues (ambience, music, sound events, accents, deliberate silence) tied to
the lines they belong to, with a purpose each; an editorial review against the story's audio profile; and the
step that writes the approved cues into the chapter as nodes the player plays.

Workflow: `plan` (a model call that reads the written chapter, the sound profile and the catalog) → `review`
(deterministic rules: budgets per profile, silence, repetition, sync) → the operator approves, edits, replaces or
removes cues in the app → `apply` writes `ambience`/`music`/`sfx` nodes (marked with `why`) into the scene →
`story/assets.py` makes or reuses the sounds (search-before-generate for SFX) → the player triggers them on the
exact line. Statuses come from the library: planned, needs_generation, generated, needs_review, approved.

Plans live in `audio/<scene>.json`.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from assetforge.core.util import utcnow
from assetforge.errors import InvalidInputError, NotFoundError
from assetforge.story.models import Condition, Id, Mood, Node, Scene, Strict, StoryConfig, World
from assetforge.story.project import StoryProject, write_atomic

if TYPE_CHECKING:
    from assetforge.core.catalog import Library
    from assetforge.story.steps import Ctx

PROMPT_VERSION = 1
CueKind = Literal["ambience", "music", "sfx", "accent", "silence", "stop"]
CueStatus = Literal["planned", "needs_generation", "generated", "needs_review", "approved", "revision", "rejected"]
MOODS = [m for m in Mood.__args__ if m != "silence"]  # type: ignore[attr-defined]


class Cue(Strict):
    id: Id
    kind: CueKind
    what: str = Field(description="ambience: a catalog scene or the location id; music: a mood; sfx/accent: a snake_case cue id; "
                                  "stop: the looping cue to stop; silence: 'music'")  # fmt: skip
    text: str = Field("", description="What it sounds like (for synthesis), e.g. 'three slow wooden knocks on a thick door'")
    purpose: str = Field(description="Why this sound is here (narrative, emotional, environmental or interaction)")
    at: int = Field(description="Index of the chapter line it plays with (0 = before the first line)")
    loop: bool = Field(False, description="sfx: keeps playing until a `stop` cue names it")
    fade: float | None = Field(None, description="Transition seconds for music/ambience/stop")
    level: float | None = Field(None, description="0.1-1 relative level")
    when: list[Condition] = Field(default_factory=list, description="Only on paths where these hold (branch-aware audio)")
    status: CueStatus = "planned"
    note: str = ""


class AudioPlan(Strict):
    scene: Id
    environment: str = Field("", description="Where we are, in a phrase")
    mood: str = Field("", description="The intended emotional state of the chapter")
    summary: str = Field("", description="The soundscape in 2-4 sentences, including where silence is used")
    cues: list[Cue] = Field(default_factory=list)
    reviewed: dict[str, Any] = Field(default_factory=dict)
    applied_at: str | None = None
    prompt_version: int = PROMPT_VERSION


class PlanAnswer(Strict):
    environment: str
    mood: str
    summary: str
    cues: list[Cue]


# ---------------------------------------------------------------------- storage


def _path(project: StoryProject, scene_id: str):  # noqa: ANN202
    return project.root / "audio" / f"{scene_id}.json"


def load(project: StoryProject, scene_id: str) -> AudioPlan | None:
    path = _path(project, scene_id)
    if not path.exists():
        return None
    return AudioPlan(**json.loads(path.read_text()))


def save(project: StoryProject, plan: AudioPlan) -> None:
    write_atomic(_path(project, plan.scene), json.dumps(plan.model_dump(mode="json"), indent=2))


# ---------------------------------------------------------------------- the lines a plan points at


def lines(scene: Scene) -> list[dict[str, Any]]:
    """The chapter as numbered lines the planner and the panel both use (one entry per node, choices included)."""
    out = []
    for i, n in enumerate(scene.nodes):
        if n.kind in ("say", "narrate"):
            out.append({"i": i, "kind": n.kind, "who": n.speaker, "text": n.text or ""})
        elif n.kind == "choice":
            out.append({"i": i, "kind": "choice", "text": " / ".join(c.text for c in n.choices),
                        "sets": sorted({a.var for c in n.choices for a in c.set})})  # fmt: skip
        elif n.kind == "place":
            out.append({"i": i, "kind": "place", "text": f"→ {n.location} {n.time or ''} {n.text or ''}".strip()})
        elif n.kind in ("cg", "sfx", "music", "ambience"):
            out.append({"i": i, "kind": n.kind, "text": n.text or n.cue or n.music or n.ambience or "", "why": n.why})
    return out


def catalog(lib: Any = None) -> dict[str, Any]:
    from assetforge.audio.ambience.engine import scenes
    from assetforge.audio.recipes import RECIPES

    return {"sfx_recipes": {name: r.description for name, r in sorted(RECIPES.items())},
            "ambience_scenes": {s.name: s.description for s in scenes(lib).values()},
            "music_moods": MOODS,
            "sfx_presets": ["distant", "muffled", "huge", "heavy", "soft", "realistic", "spacious", "metallic"]}  # fmt: skip


# ---------------------------------------------------------------------- prompt


def prompt(config: StoryConfig, world: World | None, scene: Scene, *, lib: Any = None, sound_plan: Any = None,
           number: int = 1, note: str = "") -> str:  # fmt: skip
    from assetforge.story import direction as creative
    from assetforge.story.prompts import bible

    d = config.direction or creative.CreativeDirection()
    cat = catalog(lib)
    numbered = [{k: v for k, v in ln.items() if k in ("i", "kind", "who", "text", "sets")} for ln in lines(scene)]
    example = {"environment": "Protagonist's bedroom during heavy rain", "mood": "Unease and anticipation",
               "summary": "Quiet rain, no music while they talk; the ring breaks it; the secret thins the rain and brings a "
                          "suspense texture; the knock cuts the music for a beat of silence.",
               "cues": [
                   {"id": "rain", "kind": "ambience", "what": "rain_light", "purpose": "establish the room and the night", "at": 0, "level": 0.5},
                   {"id": "ring", "kind": "sfx", "what": "phone_ring", "text": "an electronic ringtone, repeating", "purpose": "the call is the turn", "at": 14, "loop": True},
                   {"id": "ring_stop", "kind": "stop", "what": "ring", "purpose": "she answers", "at": 16, "fade": 0.2},
                   {"id": "suspense", "kind": "music", "what": "tense", "purpose": "the secret changes what the night means", "at": 23, "fade": 4},
                   {"id": "knock", "kind": "sfx", "what": "knock", "text": "three distinct wooden knocks on the bedroom door", "purpose": "the interruption", "at": 31},
                   {"id": "hush", "kind": "silence", "what": "music", "purpose": "the knock lands in silence", "at": 31, "fade": 0.5}]}  # fmt: skip
    blocks = [
        bible(config, world),
        f"CATALOG (use only these names; `what` for sfx is a recipe name, for ambience a scene or the chapter's location id, "
        f"for music a mood):\n{json.dumps(cat, ensure_ascii=False, separators=(',', ':'))}",
        creative.audio_block(d),
        f"CHAPTER {number} · {scene.title} · location {scene.location} ({scene.time}) · planned music mood {scene.music}.\n"
        "LINES (the `at` of a cue is the `i` of the line it plays with; 0 = before the first line):\n"
        + json.dumps(numbered, ensure_ascii=False, separators=(",", ":")),
        "You are the sound designer. Decide where sound makes this chapter stronger and where silence does. Rules: "
        "one ambience at the start (the location id, or a catalog scene that fits better) and a new one after every "
        "`place` move; music only where the emotion turns, with a `fade`; `silence` (what: music) when a moment is "
        "stronger without music; sfx only for events the lines show and the reader should hear (a knock, a bell, a "
        "door, a weapon, a spell), placed on the exact line, with `text` describing material and distance; `loop` + a "
        "`stop` cue for sounds that continue (a ringing phone, a bell tolling on); `accent` at most once, for the "
        "chapter's biggest turn; `when` conditions for sounds that belong to one branch only (use the stats the "
        "choices set). Every cue has a `purpose`. Fewer, better cues: the reader reads for minutes between sounds.",
        "EXAMPLE OF THE SHAPE (another story):\n" + json.dumps(example, ensure_ascii=False, separators=(",", ":")),
    ]  # fmt: skip
    if sound_plan is not None:
        blocks.append("The story's location sound plan already defines each location's ambience; name a different catalog "
                      "scene only when this chapter needs another sound there.")  # fmt: skip
    if note:
        blocks.append(f"REVISION NOTE: {note}")
    return "\n\n".join(blocks)


def offline_builder(scene: Scene) -> Any:
    """A deterministic plan for tests and demos: ambience first, an sfx on the first line that names a door, bell,
    knock or step, music at the first choice, silence near the end."""
    words = {"door": "door", "knock": "knock", "bell": "bell", "step": "footstep", "glass": "glass_break", "phone": "phone_ring"}

    def build(_: Any) -> dict[str, Any]:
        ls = lines(scene)
        cues: list[dict[str, Any]] = [{"id": "room", "kind": "ambience", "what": scene.location, "purpose": "establish the place", "at": 0, "level": 0.5}]
        for ln in ls:
            hit = next((v for k, v in words.items() if k in ln["text"].lower()), None)
            if hit and ln["kind"] in ("say", "narrate"):
                cues.append({"id": f"{hit}_1", "kind": "sfx", "what": hit, "text": f"a {hit.replace('_', ' ')}", "purpose": "the line shows it", "at": ln["i"]})
                break
        choice = next((ln for ln in ls if ln["kind"] == "choice"), None)
        if choice:
            cues.append({"id": "turn", "kind": "music", "what": "tense", "purpose": "the decision", "at": choice["i"], "fade": 3.0})
            if choice.get("sets"):
                cues.append({"id": "aftermath", "kind": "sfx", "what": "cloth", "text": "a quiet rustle", "purpose": "only on the bold path",
                             "at": min(choice["i"] + 1, len(scene.nodes) - 1), "when": [{"var": choice["sets"][0], "op": ">=", "value": 1}]})  # fmt: skip
        if len(scene.nodes) > 4:
            cues.append({"id": "hush", "kind": "silence", "what": "music", "purpose": "let the ending land", "at": len(scene.nodes) - 2, "fade": 1.0})
        return {"environment": scene.location.replace("_", " "), "mood": scene.music, "summary": "Ambience, one event, music at the turn, silence at the end.", "cues": cues}
    return build


# ---------------------------------------------------------------------- planning


def plan(ctx: Ctx, scene_id: str, *, note: str = "") -> AudioPlan:
    from assetforge.story.steps import _ask

    project = ctx.project
    scene = project.load_scene(scene_id)
    if scene is None:
        raise NotFoundError(f"Chapter {scene_id!r} is not written yet")
    world = project.load("world") if project.has("world") else None
    sound_plan = project.load("sound") if project.has("sound") else None
    number = 1
    if project.has("outline"):
        from assetforge.story.progress import chapter_numbers

        number = chapter_numbers(project.load("outline")).get(scene_id, 1)
    bare = scene.model_copy(update={"nodes": [n for n in scene.nodes if not n.why]})  # plan from the story, not old cues
    text = prompt(ctx.config, world, bare, lib=ctx.lib, sound_plan=sound_plan, number=number, note=note)
    answer: PlanAnswer = _ask(ctx, f"audio.{scene_id}", text, PlanAnswer, offline_builder(bare), fresh=bool(note))
    cleaned, notes = clean(answer, bare, ctx.lib)
    result = AudioPlan(scene=scene_id, environment=cleaned.environment, mood=cleaned.mood, summary=cleaned.summary, cues=cleaned.cues)
    result.reviewed = review(result, bare, ctx.config)
    result.reviewed["cleaned"] = notes
    save(project, result)
    return result


def clean(answer: PlanAnswer, scene: Scene, lib: Any = None) -> tuple[PlanAnswer, list[str]]:
    """Drop cues that name things the catalog lacks, clamp indices and levels, de-duplicate ids."""
    cat = catalog(lib)
    notes: list[str] = []
    seen: set[str] = set()
    cues: list[Cue] = []
    n = len(scene.nodes)
    for c in answer.cues:
        cid = c.id
        k = 2
        while cid in seen:
            cid = f"{c.id}_{k}"
            k += 1
        seen.add(cid)
        c = c.model_copy(update={"id": cid, "at": max(0, min(n, c.at)), "level": None if c.level is None else max(0.1, min(1.0, c.level)),
                                 "fade": None if c.fade is None else max(0.05, min(12.0, c.fade))})  # fmt: skip
        if c.kind in ("sfx", "accent"):
            base = re.sub(r"[^a-z0-9_]", "_", c.what.lower())
            recipe = base if base in cat["sfx_recipes"] else next((r for r in cat["sfx_recipes"] if r in base or base in r), None)
            if recipe is None:
                notes.append(f"{c.id}: no recipe for {c.what!r}; kept as a described sound (search-before-generate)")
            else:
                c = c.model_copy(update={"what": recipe})
        elif c.kind == "ambience":
            if c.what not in cat["ambience_scenes"] and c.what != scene.location and c.what != "none":
                notes.append(f"{c.id}: unknown ambience {c.what!r} → the chapter's location")
                c = c.model_copy(update={"what": scene.location})
        elif c.kind == "music":
            if c.what not in MOODS:
                notes.append(f"{c.id}: unknown mood {c.what!r} dropped")
                continue
        elif c.kind == "silence":
            c = c.model_copy(update={"what": "music"})
        elif c.kind == "stop" and c.what not in {x.id for x in answer.cues if x.kind in ("sfx", "accent")}:
            notes.append(f"{c.id}: stop names no looping cue; dropped")
            continue
        cues.append(c)
    return answer.model_copy(update={"cues": cues}), notes


# ---------------------------------------------------------------------- editorial review (deterministic)


def review(plan_: AudioPlan, scene: Scene, config: StoryConfig) -> dict[str, Any]:
    """Rules a sound designer would check: budgets for the profile, an ambience at the start, no stinger spam,
    silence used, loops stopped, cues on lines that exist, repeated sounds."""
    from assetforge.story import direction as creative

    prefs = (config.direction or creative.CreativeDirection()).audio
    budgets = prefs.budgets()
    words = sum(len((n.text or "").split()) for n in scene.nodes if n.kind in ("say", "narrate")) or 1
    per_k = words / 1000
    issues: list[dict[str, str]] = []
    kinds = {k: [c for c in plan_.cues if c.kind == k] for k in ("ambience", "music", "sfx", "accent", "silence", "stop")}

    def warn(code: str, message: str) -> None:
        issues.append({"level": "warning", "code": code, "message": message})

    if not any(c.at == 0 for c in kinds["ambience"]):
        warn("no_opening_ambience", "No ambience at the start: the reader should know where they are from the first line")
    for name, key in (("music", "music"), ("sfx", "sfx"), ("accent", "accent")):
        allowed = budgets[key] * per_k
        if len(kinds[name]) > max(1, round(allowed)):
            warn(f"too_many_{name}", f"{len(kinds[name])} {name} cues for ~{words} words; the {prefs.profile} profile allows about {max(1, round(allowed))}")
    if len(kinds["accent"]) > 1:
        warn("accent_spam", "More than one dramatic accent in a chapter; keep one for the biggest turn")
    if not kinds["silence"] and (kinds["music"] or scene.music in ("tense", "dark", "eerie", "sad")) and prefs.silence != "low":
        warn("no_silence", "No deliberate silence: a tense or sad chapter usually has a moment stronger without music")
    looping = {c.id for c in kinds["sfx"] if c.loop}
    stopped = {c.what for c in kinds["stop"]}
    for cid in looping - stopped:
        warn("loop_never_stops", f"Looping sound {cid!r} is never stopped in this chapter")
    counts: dict[str, int] = {}
    for c in kinds["sfx"] + kinds["accent"]:
        counts[c.what] = counts.get(c.what, 0) + 1
    for what, n in counts.items():
        if n > 3:
            warn("repeated_sound", f"{what!r} plays {n} times; repetition wears thin")
    by_line: dict[int, int] = {}
    for c in plan_.cues:
        by_line[c.at] = by_line.get(c.at, 0) + 1
        if c.at > len(scene.nodes):
            warn("cue_off_end", f"{c.id} points past the last line")
        if c.kind in ("sfx", "accent") and 0 < c.at < len(scene.nodes):
            node = scene.nodes[c.at]
            if node.kind in ("say", "narrate") and c.what.split("_")[0] not in (node.text or "").lower() and not c.text:
                warn("sync_unclear", f"{c.id} ({c.what}) sits on a line that does not mention it; check it plays on the right beat")
    for at, n in by_line.items():
        if n > 2:
            warn("crowded_line", f"{n} cues on line {at}; sounds compete with each other and the text")
    for c in plan_.cues:
        if not c.purpose.strip():
            issues.append({"level": "error", "code": "no_purpose", "message": f"{c.id} has no purpose"})
    silent_ok = all(c.kind != "sfx" or not c.purpose.lower().startswith("plot") for c in plan_.cues)
    return {"ok": not any(i["level"] == "error" for i in issues), "issues": issues, "words": words,
            "budget": {k: round(v * per_k, 1) for k, v in budgets.items()}, "readable_without_sound": silent_ok,
            "counts": {k: len(v) for k, v in kinds.items()}}  # fmt: skip


# ---------------------------------------------------------------------- editing, statuses, apply


def update_cue(project: StoryProject, scene_id: str, cue_id: str, patch: dict[str, Any]) -> AudioPlan:
    plan_ = load(project, scene_id)
    if plan_ is None:
        raise NotFoundError(f"No audio plan for {scene_id}")
    cues = []
    found = False
    for c in plan_.cues:
        if c.id == cue_id:
            found = True
            c = Cue(**{**c.model_dump(), **{k: v for k, v in patch.items() if k in Cue.model_fields and k != "id"}})
        cues.append(c)
    if not found:
        raise NotFoundError(f"No cue {cue_id!r} in {scene_id}")
    plan_.cues = cues
    save(project, plan_)
    return plan_


def remove_cue(project: StoryProject, scene_id: str, cue_id: str) -> AudioPlan:
    plan_ = load(project, scene_id)
    if plan_ is None:
        raise NotFoundError(f"No audio plan for {scene_id}")
    plan_.cues = [c for c in plan_.cues if c.id != cue_id]
    save(project, plan_)
    return plan_


def add_cue(project: StoryProject, scene_id: str, cue: dict[str, Any]) -> AudioPlan:
    plan_ = load(project, scene_id) or AudioPlan(scene=scene_id)
    new = Cue(**cue)
    if any(c.id == new.id for c in plan_.cues):
        raise InvalidInputError(f"Cue {new.id!r} already exists")
    plan_.cues.append(new)
    save(project, plan_)
    return plan_


def asset_key(config: StoryConfig, scene: Scene, cue: Cue) -> str | None:
    from assetforge.story.assets import story_key

    slug = config.slug
    if cue.kind in ("sfx", "accent"):
        return story_key(slug, "sfx", cue.id if cue.text else cue.what)
    if cue.kind == "ambience":
        return None if cue.what == "none" else story_key(slug, "amb", cue.what)
    if cue.kind == "music":
        return story_key(slug, "music", cue.what)
    return None


def statuses(lib: Library, project: StoryProject, plan_: AudioPlan) -> list[dict[str, Any]]:
    """Each cue with its library status (needs_generation / generated / needs_review / approved) and preview id."""
    from assetforge.request import AssetRequest, by_key

    scene = project.load_scene(plan_.scene)
    config = project.config
    out = []
    for c in plan_.cues:
        key = asset_key(config, scene, c) if scene else None
        row: dict[str, Any] = {**c.model_dump(mode="json"), "asset_key": key, "asset": None, "library_status": None}
        if key:
            probe = AssetRequest(type="sfx", description="probe", project=config.asset_project, key=key)  # type: ignore[arg-type]
            aid = by_key(lib, probe)
            if aid:
                a = lib.get_asset(aid)
                row["asset"] = {"id": aid, "status": a.status.value, "variant_id": a.primary_variant.id if a.primary_variant else None,
                                "name": a.name, "duration_ms": (a.primary_variant.file.duration_ms if a.primary_variant and a.primary_variant.file else None)}  # fmt: skip
                row["library_status"] = "approved" if a.status.value == "approved" else "needs_review" if a.status.value == "candidate" else "revision"
            else:
                row["library_status"] = "needs_generation"
        out.append(row)
    return out


def apply(project: StoryProject, scene_id: str, *, approved_only: bool = False) -> dict[str, Any]:
    """Write the plan's cues into the chapter as nodes (replacing cues applied earlier), so the player and the
    asset manifest see them. Cues keep their `why` so a re-plan can replace them cleanly."""
    from assetforge.story.steps import assemble

    plan_ = load(project, scene_id)
    scene = project.load_scene(scene_id)
    if plan_ is None or scene is None:
        raise NotFoundError(f"No audio plan or chapter for {scene_id}")
    body = [n for n in scene.nodes if not n.why]  # drop the cues applied before
    inserts: dict[int, list[Node]] = {}
    used = 0
    for c in plan_.cues:
        if c.status == "rejected" or (approved_only and c.status != "approved"):
            continue
        node = _node_for(project.config, scene, c)
        if node is None:
            continue
        inserts.setdefault(min(c.at, len(body)), []).append(node)
        used += 1
    out: list[Node] = []
    for i, n in enumerate(body):
        out += inserts.get(i, [])
        out.append(n)
    out += inserts.get(len(body), [])
    # an exit must stay last: move the exit node after any trailing cues
    exit_i = next((i for i, n in enumerate(out) if n.kind in ("jump", "ending", "branch") or (n.kind == "choice" and any(ch.goto for ch in n.choices))), None)
    if exit_i is not None and exit_i != len(out) - 1:
        node = out.pop(exit_i)
        out.append(node)
    project.save_scene(scene.model_copy(update={"nodes": out}))
    plan_.applied_at = utcnow()
    save(project, plan_)
    result = {"scene": scene_id, "applied": used}
    if project.has("outline"):
        try:
            result.update(assemble(project))
        except Exception as exc:  # noqa: BLE001 - a half-written story may not assemble; the scene is still saved
            result["assemble_error"] = str(exc)
    return result


def _node_for(config: StoryConfig, scene: Scene, c: Cue) -> Node | None:
    why = f"{c.kind}: {c.purpose}"
    common = {"when": c.when, "why": why, "fade": c.fade, "level": c.level}
    if c.kind in ("sfx", "accent"):
        cue_id = c.id if c.text else c.what
        return Node(kind="sfx", cue=cue_id, text=c.text or c.what.replace("_", " "), loop=c.loop, **common)
    if c.kind == "stop":
        target = c.what
        return Node(kind="sfx", stop=target, **common)
    if c.kind == "ambience":
        return Node(kind="ambience", ambience=c.what, **common)
    if c.kind == "music":
        return Node(kind="music", music=c.what, **common)  # type: ignore[arg-type]
    if c.kind == "silence":
        return Node(kind="music", music="silence", **common)
    return None


def intensity(plan_: AudioPlan, scene: Scene) -> list[dict[str, Any]]:
    """An audio-intensity strip per line: music level, ambience level and events, so the operator sees where music
    starts and stops, where accents land and which stretches rely on silence. An artistic reading, not a formula."""
    music, amb = 0.0, 0.0
    strip = []
    by_at: dict[int, list[Cue]] = {}
    for c in plan_.cues:
        by_at.setdefault(c.at, []).append(c)
    for i, n in enumerate(scene.nodes):
        events = []
        for c in by_at.get(i, []):
            if c.kind == "music":
                music = {"tense": 0.7, "dark": 0.7, "heroic": 0.9, "triumphant": 1.0, "eerie": 0.6, "sad": 0.5}.get(c.what, 0.5)
            elif c.kind == "silence":
                music = 0.0
            elif c.kind == "ambience":
                amb = 0.0 if c.what == "none" else (c.level or 0.5)
            elif c.kind in ("sfx", "accent"):
                events.append({"id": c.id, "kind": c.kind, "what": c.what})
        if n.kind in ("say", "narrate", "choice", "place"):
            strip.append({"i": i, "music": round(music, 2), "ambience": round(amb, 2), "events": events, "silent": music == 0 and amb <= 0.3})
    return strip
