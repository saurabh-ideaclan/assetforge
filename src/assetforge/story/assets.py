"""Turn a story script into the assets it needs and produce them through `request.handle`.

Keys (all under the story's project, default `vn-<slug>`):
    vn/<slug>/char/<character>/<expression>    transparent sprite; neutral first, others edited from it
    vn/<slug>/bg/<location>/<time>             background (9:16 for portrait stories, else 16:9); first time first, others relit from it
    vn/<slug>/cg/<id>                          event illustration (same shape), character sprites as references
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

# Portrait stories (StoryConfig.screen == "portrait"): the picture fills a phone held upright. The dialogue box covers the
# lowest quarter and characters stand in front of the lower half, so the place itself has to read in the upper part.
PORTRAIT_BG = ("composed for a phone held upright (a tall 9:16 frame), eye-level: the place's defining features in the "
               "upper two thirds and inside the central vertical band, so nothing important is lost at the left or right "
               "edge; open floor or ground across the lower third where characters and the dialogue box will sit. "
               "A complete tall view of the place, never a wide picture cropped or letterboxed.")
PORTRAIT_CG = ("a tall 9:16 picture for a phone held upright, the subject large and inside the central vertical band, "
               "nothing essential in the lowest quarter (the dialogue box covers it), composed vertically, never a wide "
               "picture cropped or letterboxed")
PORTRAIT_TITLE = (" A tall 9:16 picture for a phone held upright, composed vertically: the protagonist in the middle of "
                  "the frame, the place rising behind and above.")
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
             quality: str = "auto", sound_plan: SoundPlan | None = None,
             acting: dict[tuple[str, str], str] | None = None, canon: Any = None) -> list[Item]:  # fmt: skip
    """`acting` maps (character id, expression name) to a performance brief (`acting.directions`): those sprites are
    requested with the acting direction instead of the generic "with a <name> expression" line. `canon` is the story's
    `VisualCanon`: every background, illustration and title prompt then uses the location as the chapters use it and
    carries the world's visual rules (what lies outside, what must never be painted)."""
    from assetforge.story.visual_canon import location_text, world_block

    def rules_for(text: str) -> str:  # the world rules, with only the props this picture's own text names
        block = world_block(canon, mention=text)
        return f" {block}" if block else ""
    # the screen the story is read on: portrait (a phone held upright) makes every full-screen picture a tall 9:16
    # frame composed for it; landscape keeps the earlier 16:9 wording byte for byte
    tall = config.screen_shape == "portrait"
    full = "9:16" if tall else "16:9"
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
              "calm neutral expression, centred, nothing else in frame."
              + (" Their body and face are their own: height, build, age and face shape exactly as described above, "
                 "so they could never be mistaken for another character." if config.direction is not None else ""),
              aspect="2:3", transparent=True, name=f"{slug}_{c.id}_neutral")  # fmt: skip
        for expr in c.expressions[1:]:
            if expr not in used_expr.get(c.id, set()):
                continue
            brief = (acting or {}).get((c.id, expr))
            text = (f"{c.name}, performance '{expr.replace('_', ' ')}'. {brief} Full body, centred, nothing else in frame." if brief else
                    f"{c.name} with a {expr.replace('_', ' ')} expression. Same character as the reference image: "
                    "identical face, hair, outfit, body, pose, framing and scale; change only the facial expression.")
            image(story_key(slug, "char", c.id, expr), "expr", f"{c.name} ({expr})", AssetType.CHARACTER, text,
                  aspect="2:3", transparent=True, refs=[base], name=f"{slug}_{c.id}_{expr}")  # fmt: skip

    used_times = _used_times(script)
    for loc in script.locations:
        times = [t for t in loc.times if t in used_times.get(loc.id, set())]
        if not times:
            continue
        first = story_key(slug, "bg", loc.id, times[0])
        rules = rules_for(location_text(canon, loc))
        image(first, "bg", f"{loc.name} ({times[0]})", AssetType.BACKGROUND,
              f"{loc.name}: {location_text(canon, loc)} Visual novel background, {LIGHT[times[0]]}, no people, no text, "
              + (PORTRAIT_BG if tall else "eye-level wide view with room for characters in the foreground.") + rules,
              aspect=full, transparent=False, name=f"{slug}_{loc.id}_{times[0]}")  # fmt: skip
        for t in times[1:]:
            image(story_key(slug, "bg", loc.id, t), "bg_time", f"{loc.name} ({t})", AssetType.BACKGROUND,
                  f"The same place as the reference image ({loc.name}) at {t}: keep the exact composition, camera and "
                  f"every object; change only the lighting to {LIGHT[t]}, and the sky only where the reference already shows sky: "
                  f"never add a window, opening or view that is not in the reference. No people, no text.{rules}",
                  aspect=full, transparent=False, refs=[first], name=f"{slug}_{loc.id}_{t}")  # fmt: skip

    locs = {loc.id: loc for loc in script.locations}
    chars = {c.id: c for c in script.characters}
    for scene in script.scenes:
        on_stage = _scene_characters(scene)
        for node, where, when, _ in walk(scene):
            if node.kind == "cg" and node.cg and not any(i.key == story_key(slug, "cg", node.cg) for i in items):
                loc = locs.get(where)
                # only the people the illustration's own text names (a chapter's whole cast used to be listed, and the
                # model drew all of them into a moment that belongs to one person); without a text, whoever is in the scene
                cast_ids, you = _cg_cast(node.text or "", script.characters) if node.text else ([c for c in on_stage if c in chars], False)
                refs = [story_key(slug, "char", cid, "neutral") for cid in cast_ids][:3]
                who = ", ".join(f"{chars[c].name} ({chars[c].look})" for c in cast_ids)
                image(story_key(slug, "cg", node.cg), "cg", f"CG {node.cg}", AssetType.IMAGE,
                      f"Visual novel event illustration (CG), {PORTRAIT_CG if tall else 'cinematic full scene'}: {node.text or scene.title}. "
                      + (f"'You' in that description is {chars[cast_ids[0]].name}. " if you and cast_ids else "")
                      + f"Setting (its usual state; where the event described above differs, the event wins): "
                        f"{location_text(canon, loc, illustration=True) if loc else where}. Time of day: {when} ({LIGHT.get(when, LIGHT['day'])}). "
                      + (f"The only named people in the picture, matching the reference images: {who}. Nobody else from the "
                         "story's cast appears; unnamed people only where the description asks for them." if who
                         else "No named characters in the picture.")
                      + rules_for(f"{node.text or scene.title} {location_text(canon, loc, illustration=True) if loc else where}"),
                      aspect=full, transparent=False, refs=refs, name=f"{slug}_cg_{node.cg}")  # fmt: skip

    look = world.art_direction if world else ""
    leads = [story_key(slug, "char", c.id, "neutral") for c in script.characters[:2]]
    image(story_key(slug, "ui", "title"), "ui", "Title art", AssetType.IMAGE,
          f"Title screen key art for the visual novel '{script.title}': "
          f"{world.logline if world else ''} The protagonist (first reference image) in the story's defining place and "
          "action, large in the frame; a second lead (second reference image) only smaller and apart, never posed beside "
          f"the protagonist as a pair. Clear space at the top for a logo. No text.{PORTRAIT_TITLE if tall else ''}"
          f"{rules_for(world.logline if world else '')}", aspect=full,
          transparent=False, refs=leads,
          name=f"{slug}_title")  # fmt: skip
    image(story_key(slug, "ui", "textbox"), "ui", "Dialogue box", AssetType.UI,
          f"Empty visual novel dialogue box panel: a wide translucent dark panel with a thin ornamental border "
          f"in this style: {look} No text, no characters, flat front view.", aspect="16:9" if tall else "21:9", transparent=True,
          name=f"{slug}_textbox")  # fmt: skip
    image(story_key(slug, "ui", "choice"), "ui", "Choice button", AssetType.UI,
          f"Empty visual novel choice button: a wide rounded banner panel with a subtle frame in this style: {look} "
          "No text, flat front view.", aspect="21:9", transparent=True, name=f"{slug}_choice")  # fmt: skip

    direction = world.music_direction if world else ""
    cues = {c.mood: c for c in sound_plan.music} if sound_plan else {}
    # a location the plan left empty (no scene, beds or events) falls back to matching its description
    places = {p.location: p for p in sound_plan.locations if p.scene or p.beds or p.events} if sound_plan else {}
    for mood in sorted({st.music for s in script.scenes for st in walk(s)} - {"silence", None}):
        sound(story_key(slug, "music", mood), "music", f"Music: {mood}", AssetType.MUSIC,
              f"{mood} visual novel background music loop. {direction}", name=f"{slug}_music_{mood}",
              sound_opts=music_options(cues[mood]) if mood in cues else {"mood": MUSIC_MOOD.get(mood, mood), "form": "loop"})  # fmt: skip
    for loc in script.locations:
        if loc.ambience and loc.id in used_times:
            sound(story_key(slug, "amb", loc.id), "ambience", f"Ambience: {loc.name}", AssetType.AMBIENCE,
                  f"{loc.ambience} ({loc.name})", name=f"{slug}_amb_{loc.id}",
                  sound_opts=ambience_options(places[loc.id]) if loc.id in places else None)  # fmt: skip
    # ambience nodes that name a catalog scene (not a location): one loop per named scene
    location_ids = {loc.id for loc in script.locations}
    named = {n.ambience for s in script.scenes for n in s.nodes if n.kind == "ambience" and n.ambience}
    for name in sorted(named - location_ids - {"none"}):
        sound(story_key(slug, "amb", name), "ambience", f"Ambience: {name.replace('_', ' ')}", AssetType.AMBIENCE,
              f"{name.replace('_', ' ')} ambience loop for a visual novel", name=f"{slug}_amb_{name}",
              sound_opts={"scene": name, "length": 60.0})  # fmt: skip
    cues: dict[str, str] = {}
    for scene in script.scenes:
        for node in scene.nodes:
            if node.kind == "sfx" and node.cue and node.cue not in cues:
                cues[node.cue] = node.text or node.cue.replace("_", " ")
    for cue, text in cues.items():
        sound(story_key(slug, "sfx", cue), "sfx", f"SFX: {cue}", AssetType.SFX, text, name=f"{slug}_sfx_{cue}",
              reuse=True)  # fmt: skip
    return items


def _cg_cast(text: str, characters: list[Any]) -> tuple[list[str], bool]:
    """Who an illustration's description puts in the picture: characters named by given name, or by family name when
    it is not part of someone else's full name ("Evara Oduya" is not Rafe Oduya). A description in the second person
    puts the protagonist first. Returns (character ids, whether "you" was used)."""
    import re

    found: list[tuple[int, str]] = []
    for c in characters:
        parts = c.name.split()
        hit = re.search(rf"\b{re.escape(parts[0])}\b", text)
        pos = hit.start() if hit else None
        if pos is None and len(parts) > 1:
            for m in re.finditer(rf"\b{re.escape(parts[-1])}\b", text):
                before = text[:m.start()].split()
                prev = before[-1] if before else ""
                if not prev[:1].isupper() or prev.rstrip(".,;:'") in ("Chief", "Conductor", "Keeper", "Inspector", "Director") or prev.endswith((".", "!", "?", ":")):
                    pos = m.start()
                    break
        if pos is not None:
            found.append((pos, c.id))
    ids = [cid for _, cid in sorted(found)]
    you = bool(re.search(r"\b(you|your)\b", text, re.IGNORECASE))
    lead = next((c.id for c in characters if "protagonist" in c.role.lower()), characters[0].id if characters else None)
    if you and lead and lead not in ids:
        ids.insert(0, lead)
    elif you and lead:
        ids.remove(lead)
        ids.insert(0, lead)
    return ids, you


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


# A built-in art preset is shared by every story that picks its style. Its people- and world-shaping lines ("tall slender
# proportions (8 heads), sharp jawlines", one fixed palette, "glowing runes", "wide painted vistas") made the second
# story's cast the first story's cast again (owner, 2026-11-13: Noah came out as Rafe, Bram as Tovan, a stocky Ellis slim).
# A story keeps the preset's rendering (line work, shading, finish) and overrides those lines with its own.
OWN_BODIES = ("Every person's height, build, age, face shape and proportions follow their own description exactly (stocky, "
              "broad, small, older, a child); no shared body type, no shared face, no default heroic proportions.")
OWN_LIGHT = "Light comes from the sources in the place (lamps, windows, fire, daylight), with a clear soft rim on figures."
OWN_TEXTURE = "Smooth digital paint; fabric, metal, stone and skin rendered as the materials they are."
COLOUR_WORDS = {  # colour words a world's art direction uses → a hex for the palette line (prose, never swatches)
    "coal": "#2B2F36", "charcoal": "#33363B", "slate": "#5A6672", "navy": "#1F2A44", "coal blue": "#2E3A4A",
    "steel": "#7A8591", "brass": "#B08D57", "gold": "#C9A646", "amber": "#E0A040", "copper": "#B87333", "rust": "#9A4A2A",
    "ochre": "#C28E3E", "crimson": "#8C1C2C", "red": "#A33A2E", "plum": "#5B2E4A", "violet": "#5E4A8A", "purple": "#5A3F7A",
    "teal": "#2F7A7A", "sea": "#3C7F8C", "green": "#4F7A4A", "moss": "#5D6B3A", "olive": "#6B6B3A", "sky": "#8FB6D8",
    "blue": "#3E6A9A", "cold blue": "#5C8FC7", "ice": "#C8DCEB", "pale stone": "#D9D2C5", "stone": "#B7AFA3", "sand": "#D8C49A",
    "dust": "#B9A68A", "cream": "#EFE6D2", "ivory": "#F1EADB", "white": "#F2F2EE", "bone": "#E6DFCF", "black": "#151515",
    "brown": "#6B4A32", "saffron": "#E3A21A", "coral": "#E07A5F", "pink": "#E3A3B5", "rose": "#C9707E", "silver": "#BFC5CC",
}


def world_palette(look: str) -> list[str]:
    """The palette a world's art direction names in words, in the order it names them (at most six)."""
    import re

    text = look.lower()
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for word in sorted(COLOUR_WORDS, key=len, reverse=True):  # "coal blue" before "coal" and "blue"
        for m in re.finditer(rf"\b{re.escape(word)}\b", text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), COLOUR_WORDS[word]))
    return list(dict.fromkeys(h for _, h in sorted(found)))[:6]


def story_art_direction(config: StoryConfig, world: World | None) -> art.ArtDirection:
    """The one art direction every image of the story follows: the creative direction's visual preset and notes
    (independent of the narrative influence), with the world's own look as the description. Stories with a creative
    direction also replace the preset's body, light, texture, composition and palette lines with their own (above)."""
    look = world.art_direction if world else ""
    if config.direction is not None:
        from assetforge.story import direction as creative

        base = creative.art_direction(config.direction, look)
        tall = config.screen_shape == "portrait"
        own = {"shapes": OWN_BODIES, "lighting": OWN_LIGHT, "texture": OWN_TEXTURE,
               "composition": ("Upright full-body character sprites; backgrounds and illustrations composed for a tall 9:16 "
                               "frame (a phone held upright)") if tall else
                              "Upright full-body character sprites; painted backgrounds with depth"}  # fmt: skip
        palette = world_palette(look)
        if palette:
            own["palette"] = palette
        return base.model_copy(update=own)
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
    from assetforge.story import acting, visual_canon

    items = [i for i in manifest(script, config, world, image_provider=image_provider, quality=quality,
                                 sound_plan=sound_plan, acting=acting.directions(project),
                                 canon=visual_canon.load(project)) if i.group in groups]  # fmt: skip
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


FULL_SCREEN = ("bg", "bg_time", "cg")  # plus the title art


def screen_report(lib: Library, project: Any) -> dict[str, Any]:
    """The story's screen and the full-screen pictures (backgrounds, illustrations, title) already made in the other
    shape: a story switched to portrait keeps its wide pictures until they are remade (`story assets --redo`)."""
    config = project.config
    tall = config.screen_shape == "portrait"
    out: dict[str, Any] = {"screen": config.screen_shape, "set": config.screen is not None, "checked": 0, "wrong": []}
    if not project.has("script"):
        return out
    world = project.load("world") if project.has("world") else None
    title = story_key(config.slug, "ui", "title")
    for item in manifest(project.load("script"), config, world):
        if item.group not in FULL_SCREEN and item.key != title:
            continue
        asset_id = by_key(lib, item.request)
        if not asset_id:
            continue
        row = lib.conn.execute(
            "SELECT f.width, f.height FROM assets a JOIN asset_variants v ON v.id = a.primary_variant_id "
            "JOIN asset_files f ON f.sha256 = v.file_sha256 WHERE a.id = ?", (asset_id,)).fetchone()  # fmt: skip
        if row is None or not row["width"] or not row["height"]:
            continue
        out["checked"] += 1
        if (row["height"] > row["width"]) != tall:
            out["wrong"].append({"key": item.key, "label": item.label, "group": item.group, "asset_id": asset_id,
                                 "width": row["width"], "height": row["height"]})  # fmt: skip
    return out


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
