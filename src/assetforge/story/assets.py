"""Turn a story script into the assets it needs and produce them through `request.handle`.

Keys (all under the story's project, default `vn-<slug>`):
    vn/<slug>/char/<character>/<expression>    transparent sprite; neutral first, others edited from it
    vn/<slug>/bg/<location>/<time>             16:9 background; first time first, others relit from it
    vn/<slug>/cg/<id>                          16:9 event illustration, character sprites as references
    vn/<slug>/ui/{title,textbox,choice}        title art and dialogue UI
    vn/<slug>/music/<mood>  vn/<slug>/amb/<location>  vn/<slug>/sfx/<cue>

Story assets are bound to the story by key: a re-run finds them by key and never regenerates (rejected or
archived ones are made again). Images are generated fresh rather than reused from search, because identity
and one look across the novel matter more than a near match; sound effects do use search-before-generate.
Every asset joins the collection `vn-<slug>`, which the exporter reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from assetforge import art
from assetforge.core.models import AssetType
from assetforge.errors import AssetForgeError
from assetforge.request import AssetRequest, by_key, handle
from assetforge.story.models import Script, StoryConfig, World, walk
from assetforge.story.project import StoryProject
from assetforge.story.sound import SoundPlan, ambience_options, music_options

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

Group = Literal["char", "expr", "bg", "bg_time", "cg", "ui", "music", "ambience", "sfx"]
IMAGE_GROUPS = frozenset({"char", "expr", "bg", "bg_time", "cg", "ui"})
GROUPS: tuple[Group, ...] = ("char", "expr", "bg", "bg_time", "cg", "ui", "music", "ambience", "sfx")
ONLY = {"chars": ("char", "expr"), "bg": ("bg", "bg_time"), "cg": ("cg",), "ui": ("ui",),
        "audio": ("music", "ambience", "sfx"), "music": ("music",), "ambience": ("ambience",), "sfx": ("sfx",)}  # fmt: skip
LIGHT = {"day": "bright natural daylight", "evening": "warm orange sunset light, long shadows, lamps starting to glow",
         "night": "night: dark blue moonlit sky, warm artificial lights, deep shadows"}  # fmt: skip
MUSIC_MOOD = {"romantic": "peaceful", "eerie": "scary"}


@dataclass
class Item:
    key: str
    group: Group
    label: str
    request: AssetRequest
    refs: list[str] = field(default_factory=list)  # keys whose assets become reference images


def story_key(slug: str, *parts: str) -> str:
    return "/".join(["vn", slug, *parts])


def manifest(script: Script, config: StoryConfig, world: World | None, *, image_provider: str = "auto",
             quality: str = "auto", sound_plan: SoundPlan | None = None) -> list[Item]:  # fmt: skip
    slug, project, collection = config.slug, config.asset_project, f"vn-{config.slug}"
    tags = ["visual-novel", slug]
    items: list[Item] = []

    def image(key: str, group: Group, label: str, type_: AssetType, description: str, *, aspect: str,
              transparent: bool, refs: list[str] | None = None, name: str) -> None:  # fmt: skip
        # transparent pieces (sprites, UI frames) are trimmed to their content so they anchor and stretch cleanly
        req = AssetRequest(type=type_, description=description, project=project, key=key, provider=image_provider,
                           aspect=aspect, transparent=transparent, quality=quality, name=name, tags=tags,
                           process="web-sprite" if transparent else None,
                           collection=collection, reuse_policy="always_generate", genre=config.genre,
                           purpose=f"visual novel {group}")  # type: ignore[arg-type]  # fmt: skip
        items.append(Item(key, group, label, req, refs or []))

    def sound(key: str, group: Group, label: str, type_: AssetType, description: str, *, name: str,
              sound_opts: dict[str, Any] | None = None, reuse: bool = False) -> None:  # fmt: skip
        req = AssetRequest(type=type_, description=description, project=project, key=key, name=name, tags=tags,
                           collection=collection, sound=sound_opts or {}, accept_candidates=True,
                           reuse_policy="prefer_existing" if reuse else "always_generate")  # fmt: skip
        items.append(Item(key, group, label, req))

    used_expr = _used_expressions(script)
    for c in script.characters:
        base = story_key(slug, "char", c.id, "neutral")
        image(base, "char", f"{c.name} (neutral)", AssetType.CHARACTER,
              f"{c.name}, visual novel character sprite. {c.look} Full body, standing, facing the viewer, arms relaxed, "
              "calm neutral expression, centred, nothing else in frame.",
              aspect="2:3", transparent=True, name=f"{slug}_{c.id}_neutral")  # fmt: skip
        for expr in c.expressions[1:]:
            if expr not in used_expr.get(c.id, set()):
                continue
            image(story_key(slug, "char", c.id, expr), "expr", f"{c.name} ({expr})", AssetType.CHARACTER,
                  f"{c.name} with a {expr.replace('_', ' ')} expression. Same character as the reference image: "
                  "identical face, hair, outfit, body, pose, framing and scale; change only the facial expression.",
                  aspect="2:3", transparent=True, refs=[base], name=f"{slug}_{c.id}_{expr}")  # fmt: skip

    used_times = _used_times(script)
    for loc in script.locations:
        times = [t for t in loc.times if t in used_times.get(loc.id, set())]
        if not times:
            continue
        first = story_key(slug, "bg", loc.id, times[0])
        image(first, "bg", f"{loc.name} ({times[0]})", AssetType.BACKGROUND,
              f"{loc.name}: {loc.description} Visual novel background, {LIGHT[times[0]]}, no people, no text, "
              "eye-level wide view with room for characters in the foreground.",
              aspect="16:9", transparent=False, name=f"{slug}_{loc.id}_{times[0]}")  # fmt: skip
        for t in times[1:]:
            image(story_key(slug, "bg", loc.id, t), "bg_time", f"{loc.name} ({t})", AssetType.BACKGROUND,
                  f"The same place as the reference image ({loc.name}) at {t}: keep the exact composition, camera and "
                  f"every object; change only the lighting and sky to {LIGHT[t]}. No people, no text.",
                  aspect="16:9", transparent=False, refs=[first], name=f"{slug}_{loc.id}_{t}")  # fmt: skip

    locs = {loc.id: loc for loc in script.locations}
    chars = {c.id: c for c in script.characters}
    for scene in script.scenes:
        on_stage = _scene_characters(scene)
        for node, where, when, _ in walk(scene):
            if node.kind == "cg" and node.cg and not any(i.key == story_key(slug, "cg", node.cg) for i in items):
                loc = locs.get(where)
                refs = [story_key(slug, "char", cid, "neutral") for cid in on_stage if cid in chars][:3]
                who = ", ".join(f"{chars[c].name} ({chars[c].look})" for c in on_stage if c in chars)
                image(story_key(slug, "cg", node.cg), "cg", f"CG {node.cg}", AssetType.IMAGE,
                      f"Visual novel event illustration (CG), cinematic full scene: {node.text or scene.title}. "
                      f"Setting: {loc.description if loc else where}, {LIGHT.get(when, LIGHT['day'])}. "
                      + (f"Characters, matching the reference images: {who}." if who else ""),
                      aspect="16:9", transparent=False, refs=refs, name=f"{slug}_cg_{node.cg}")  # fmt: skip

    look = world.art_direction if world else ""
    leads = [story_key(slug, "char", c.id, "neutral") for c in script.characters[:2]]
    image(story_key(slug, "ui", "title"), "ui", "Title art", AssetType.IMAGE,
          f"Title screen key art for the visual novel '{script.title}': "
          f"{world.logline if world else ''} The main characters (as in the reference images) in a striking composition, "
          "leaving clear space at the top for a logo. No text.", aspect="16:9", transparent=False, refs=leads,
          name=f"{slug}_title")  # fmt: skip
    image(story_key(slug, "ui", "textbox"), "ui", "Dialogue box", AssetType.UI,
          f"Empty visual novel dialogue box panel: a wide translucent dark panel with a thin ornamental border "
          f"in this style: {look} No text, no characters, flat front view.", aspect="21:9", transparent=True,
          name=f"{slug}_textbox")  # fmt: skip
    image(story_key(slug, "ui", "choice"), "ui", "Choice button", AssetType.UI,
          f"Empty visual novel choice button: a wide rounded banner panel with a subtle frame in this style: {look} "
          "No text, flat front view.", aspect="21:9", transparent=True, name=f"{slug}_choice")  # fmt: skip

    direction = world.music_direction if world else ""
    cues = {c.mood: c for c in sound_plan.music} if sound_plan else {}
    # a location the plan left empty (no scene, beds or events) falls back to matching its description
    places = {p.location: p for p in sound_plan.locations if p.scene or p.beds or p.events} if sound_plan else {}
    for mood in sorted({st.music for s in script.scenes for st in walk(s)}):
        sound(story_key(slug, "music", mood), "music", f"Music: {mood}", AssetType.MUSIC,
              f"{mood} visual novel background music loop. {direction}", name=f"{slug}_music_{mood}",
              sound_opts=music_options(cues[mood]) if mood in cues else {"mood": MUSIC_MOOD.get(mood, mood), "form": "loop"})  # fmt: skip
    for loc in script.locations:
        if loc.ambience and loc.id in used_times:
            sound(story_key(slug, "amb", loc.id), "ambience", f"Ambience: {loc.name}", AssetType.AMBIENCE,
                  f"{loc.ambience} ({loc.name})", name=f"{slug}_amb_{loc.id}",
                  sound_opts=ambience_options(places[loc.id]) if loc.id in places else None)  # fmt: skip
    cues: dict[str, str] = {}
    for scene in script.scenes:
        for node in scene.nodes:
            if node.kind == "sfx" and node.cue and node.cue not in cues:
                cues[node.cue] = node.text or node.cue.replace("_", " ")
    for cue, text in cues.items():
        sound(story_key(slug, "sfx", cue), "sfx", f"SFX: {cue}", AssetType.SFX, text, name=f"{slug}_sfx_{cue}",
              reuse=True)  # fmt: skip
    return items


def _used_expressions(script: Script) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for scene in script.scenes:
        for node, *_ in walk(scene):
            who = node.speaker if node.kind == "say" else node.character if node.kind == "show" else None
            if who and node.expression:
                used.setdefault(who, set()).add(node.expression)
    return used


def _used_times(script: Script) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for scene in script.scenes:
        for _, where, when, _ in walk(scene):
            used.setdefault(where, set()).add(when)
    return used


def _scene_characters(scene: Any) -> list[str]:
    seen: list[str] = []
    for node, *_ in walk(scene):
        who = node.speaker if node.kind == "say" else node.character if node.kind == "show" else None
        if who and who not in seen:
            seen.append(who)
    return seen


def ensure_art_profile(lib: Library, config: StoryConfig, world: World | None) -> bool:
    """Give the story's project one art direction (from the world's art_direction) unless it already has one."""
    if art.find_profile(lib, config.asset_project) is not None or world is None:
        return False
    art.set_profile(lib, config.asset_project, story_art_direction(config, world))
    return True


def story_art_direction(config: StoryConfig, world: World | None) -> art.ArtDirection:
    """The one art direction every image of the story follows: the creative direction's visual preset and notes
    (independent of the narrative influence), with the world's own look as the description."""
    look = world.art_direction if world else ""
    if config.direction is not None:
        from assetforge.story import direction as creative

        return creative.art_direction(config.direction, look)
    return art.ArtDirection(style=config.art_style, description=look or None, genre=config.genre,
                            notes="Visual novel: consistent character design and painting style across all images.")  # fmt: skip


def load_sound_plan(lib: Library, project: StoryProject, *, make: bool, fresh: bool = False) -> tuple[SoundPlan | None, str | None]:
    """The story's sound plan: read sound.json, or ask the writer for one (free on a subscription). A failure to plan
    is reported, not fatal: sound then falls back to guessing from each location's description."""
    if project.has("sound") and not fresh:
        return project.load("sound"), None
    if not make:
        return None, None
    from assetforge.errors import AssetForgeError
    from assetforge.story import steps

    try:
        plan, _ = steps.plan_sound(steps.Ctx(lib=lib, project=project, fresh=fresh))
        return plan, None
    except AssetForgeError as exc:
        return None, f"sound plan unavailable ({exc.message}); using description matching"


def produce(lib: Library, project: StoryProject, *, only: list[str] | None = None, limit: int | None = None,
            dry_run: bool = False, image_provider: str = "auto", quality: str = "auto",
            redo: bool = False, replan: bool = False) -> dict[str, Any]:  # fmt: skip
    """Make the missing assets. `redo` archives the selected groups' existing assets first and makes them again
    (non-destructive: the old ones stay in the library, archived)."""
    config: StoryConfig = project.config
    script: Script = project.load("script")
    world = project.load("world") if project.has("world") else None
    groups: set[str] = set(GROUPS)
    if only:
        groups = {g for o in only for g in ONLY.get(o, (o,))}
        if unknown := groups - set(GROUPS):
            from assetforge.errors import InvalidInputError

            raise InvalidInputError(f"Unknown asset groups {sorted(unknown)}", known=sorted(ONLY))
    wants_sound = bool(groups & {"music", "ambience"})
    sound_plan, sound_note = load_sound_plan(lib, project, make=wants_sound and not dry_run, fresh=replan and wants_sound)
    items = [i for i in manifest(script, config, world, image_provider=image_provider, quality=quality,
                                 sound_plan=sound_plan) if i.group in groups]  # fmt: skip
    if redo and not dry_run:
        from assetforge.core import review

        old_ids = [a for a in (by_key(lib, i.request) for i in items) if a]
        if old_ids:
            review.archive(lib, list(dict.fromkeys(old_ids)), note="story assets remade")
    profile_set = False if dry_run else ensure_art_profile(lib, config, world)
    results: list[dict[str, Any]] = []
    made = 0
    images_blocked: dict[str, Any] | None = None  # set when the image provider can't work at all (no key, quota)
    for item in items:
        row: dict[str, Any] = {"key": item.key, "group": item.group, "label": item.label}
        if images_blocked and item.group in IMAGE_GROUPS:
            results.append({**row, "state": "skipped", "reason": f"image provider unavailable: {images_blocked['message']}"})
            continue
        existing = by_key(lib, item.request)
        if existing:
            results.append({**row, "state": "exists", "asset_id": existing})
            continue
        if limit is not None and made >= limit:
            results.append({**row, "state": "skipped", "reason": "limit"})
            continue
        refs, missing = _resolve_refs(lib, item)
        if missing and not dry_run:
            results.append({**row, "state": "blocked", "reason": f"needs {', '.join(missing)} first"})
            continue
        req = item.request.model_copy(update={"references": refs, "dry_run": dry_run})
        try:
            result = handle(lib, req)
        except AssetForgeError as exc:
            results.append({**row, "state": "failed", "error": exc.to_dict()})
            if item.group in IMAGE_GROUPS and getattr(exc, "kind", None) in ("auth", "not_configured", "quota"):
                images_blocked = exc.to_dict()  # every following image would fail the same way; sound still works
            continue
        if result.asset_id and lib.get_asset(result.asset_id).status.value == "archived":
            # a remake that renders identical bytes resolves to the copy we just archived: bring it back
            from assetforge.core import review

            review.restore(lib, [result.asset_id], note="identical remake")
        made += 0 if result.reused else 1
        plan = result.plan or {}
        results.append({**row, "state": result.state, "asset_id": result.asset_id, "reused": result.reused,
                        "would": result.would, "references": refs or ([f"(after) {m}" for m in missing] if missing else []),
                        "estimated_cost_usd": plan.get("estimated_cost_usd", plan.get("cost_usd")),
                        "provider": plan.get("provider")})  # fmt: skip
    counts: dict[str, int] = {}
    for r in results:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    estimate = sum(r.get("estimated_cost_usd") or 0 for r in results)
    return {"project": config.asset_project, "collection": f"vn-{config.slug}", "art_profile_created": profile_set,
            "sound_plan": "story" if sound_plan else "guessed", "notes": [sound_note] if sound_note else [],
            "total": len(items), "counts": counts, "estimated_cost_usd": round(estimate, 4) if dry_run else None,
            "items": results}  # fmt: skip


def _resolve_refs(lib: Library, item: Item) -> tuple[list[str], list[str]]:
    refs, missing = [], []
    for key in item.refs:
        probe = item.request.model_copy(update={"key": key})
        asset_id = by_key(lib, probe)
        (refs.append(asset_id) if asset_id else missing.append(key))
    return refs, missing


def coverage(lib: Library, project: StoryProject) -> dict[str, Any]:
    """Which manifest keys already have an asset (for `story status`)."""
    script: Script = project.load("script")
    world = project.load("world") if project.has("world") else None
    items = manifest(script, project.config, world)
    have = {i.key: by_key(lib, i.request) for i in items}
    return {"total": len(items), "have": sum(1 for v in have.values() if v),
            "missing": [k for k, v in have.items() if not v]}
