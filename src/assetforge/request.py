"""`assetforge request`: the one call an agent makes to get an asset. It reuses before it generates.

Reuse ladder, tried in order (`reuse_policy` = prefer_existing, the default):
  1. key: the project already has an asset or usage under this key
  2. request: an identical request already finished, or is running right now (join it, don't duplicate)
  3. search: a cataloged asset matches the description well enough (coverage >= reuse_threshold);
     category/genre/theme and the project's art style break ties
A reused image is *adapted* when the request asks for a size, format or processing preset: a derived
variant is made (the original is never touched) and its path returned. Then generate: procedural audio
for `sfx` (a recipe inferred from the description, else a synth patch built from its words), `ambience`
(a scene plus the layers the words ask for) and `music` (genre, mood, tempo, key, length, stingers, all from
the words, `sound` options or the project's sound profile), AI images for visual types, or register a
file the agent made itself (`method: svg|procedural|file`). `dry_run` reports the decision and the cost
without spending anything.
`require_existing` stops after step 3 with NOT_FOUND. `always_generate` skips steps 1–3.

Each generating request is a durable job (kind "request") with a lease, so a crash is recoverable,
and concurrent identical requests from different agents produce the asset once.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge import art
from assetforge.art import ArtDirection
from assetforge.audio import process as audio_proc
from assetforge.audio.generate import AudioRequest, generate_audio
from assetforge.audio.recipes import PRESETS as SOUND_PRESETS
from assetforge.audio.recipes import RECIPES, Recipe
from assetforge.config import Settings, load_settings
from assetforge.core import jobs, limits, queue, settings_store
from assetforge.core.catalog import Library
from assetforge.core.classify import tokens
from assetforge.core import visual
from assetforge.core.models import IMAGE_TYPES as VISUAL_TYPES
from assetforge.core.models import AssetPatch, AssetSummary, AssetType, FileKind, JobStatus, Scope, Status
from assetforge.core.search import SearchQuery, search
from assetforge.core.util import normalize_key, normalize_optional_slug
from assetforge.errors import AssetForgeError, InternalError, InvalidInputError, NotFoundError
from assetforge.image import process as image_proc
from assetforge.image.generate import ImageRequest, generate_image
from assetforge.image.prompts import SheetSpec

ReusePolicy = Literal["prefer_existing", "require_existing", "always_generate"]
AUDIO_TYPES = {AssetType.SFX, AssetType.AUDIO, AssetType.AMBIENCE, AssetType.MUSIC}
IMAGE_TYPES = set(VISUAL_TYPES)
COMPATIBLE: dict[AssetType, list[AssetType]] = {
    AssetType.SFX: [AssetType.SFX, AssetType.AUDIO],
    AssetType.AUDIO: [AssetType.SFX, AssetType.AUDIO],
    AssetType.AMBIENCE: [AssetType.AMBIENCE],
    AssetType.MUSIC: [AssetType.MUSIC],
    AssetType.CHARACTER: [AssetType.CHARACTER, AssetType.SPRITE, AssetType.ENEMY],
    AssetType.ENEMY: [AssetType.ENEMY, AssetType.CHARACTER, AssetType.SPRITE],
    AssetType.SPRITE: [AssetType.SPRITE, AssetType.OBJECT, AssetType.CHARACTER, AssetType.ENEMY],
    AssetType.OBJECT: [AssetType.OBJECT, AssetType.SPRITE],
    AssetType.ICON: [AssetType.ICON, AssetType.UI],
    AssetType.UI: [AssetType.UI, AssetType.ICON],
    AssetType.TILE: [AssetType.TILE, AssetType.TEXTURE],
    AssetType.TEXTURE: [AssetType.TEXTURE, AssetType.TILE],
    AssetType.SPRITESHEET: [AssetType.SPRITESHEET, AssetType.ANIMATION],
    AssetType.ANIMATION: [AssetType.ANIMATION, AssetType.SPRITESHEET],
    AssetType.IMAGE: sorted(IMAGE_TYPES),
}
FILL_TYPES = {AssetType.BACKGROUND, AssetType.TILE, AssetType.TEXTURE}  # resized with "cover", others padded
DISCOVERY_BONUS = 0.05  # per matching category/subcategory/theme/genre when ranking reuse candidates
Method = Literal["auto", "ai", "procedural", "svg", "file"]
RETRY_DELAYS = (30.0, 120.0, 600.0)


class AssetRequest(BaseModel):
    """What an agent needs. Also the JSON schema of `assetforge request --file spec.json`."""

    model_config = ConfigDict(extra="forbid")

    type: AssetType
    description: str = Field(min_length=3)
    project: str | None = None
    key: str | None = Field(None, description="How the project refers to it, e.g. enemy/beetle/hit")
    style: str | None = Field(None, description="Preset name (`art presets`) or any style label")
    art: ArtDirection | None = Field(None, description="Custom art direction (overrides the project art profile)")
    purpose: str | None = Field(None, description="What the asset is for in the game")
    use_profile: bool = True
    category: str | None = None
    subcategory: str | None = None
    theme: str | None = None
    genre: str | None = None
    method: Method = Field("auto", description="auto: AI image or procedural audio by type; svg/procedural/file: "
                                               "register `file`, which the agent made itself")  # fmt: skip
    file: str | None = Field(None, description="Agent-made file to register (method svg/procedural/file)")
    script: str | None = Field(None, description="Generator script to keep as provenance (method procedural)")
    reuse_policy: ReusePolicy = "prefer_existing"
    reuse_threshold: float | None = Field(None, ge=0, le=1)
    accept_candidates: bool = Field(False, description="Allow reusing unreviewed candidates")
    new_variant: bool = False
    sound: dict[str, Any] = Field(default_factory=dict, description=(
        "Engine options for sounds, merged over what the description implies. sfx: design|patch|recipe…; "
        "ambience: scene, length, intensity, time_of_day, beds, events, stems, intensities; music: genre, mood, "
        "tempo, key, bars|seconds, form, stinger, energy, instruments, stems, intensity_layers, score, midi"))  # fmt: skip
    # audio
    recipe: str | None = None
    preset: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = Field(None, gt=0)
    variants: int = Field(1, ge=1, le=32)
    variation: float = Field(0.3, ge=0, le=1)
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    effects: list[dict[str, Any] | str] = Field(default_factory=list)
    # image
    prompt: str | None = None
    provider: str = "auto"
    model: str | None = None
    n: int = Field(1, ge=1, le=8)
    aspect: str | None = None
    size: str | None = None
    quality: Literal["auto", "low", "medium", "high", "max"] = "auto"
    transparent: bool | None = None
    references: list[str] = Field(default_factory=list)
    negative: str | None = None
    max_cost: float | None = Field(None, ge=0)
    sheet: SheetSpec | None = Field(None, description="Sprite-sheet grid and poses (type spritesheet/animation)")
    slice: bool = Field(False, description="Cut a generated sheet into aligned frame assets (needs `sheet`)")
    # output + catalog
    format: str | None = Field(None, description="Audio: wav/ogg/mp3 (default ogg). Images: png/webp (default png)")
    process: str | None = Field(None, description="Processing preset applied to the result (e.g. web-sprite)")
    name: str | None = None
    tags: list[str] = Field(default_factory=list)
    scope: Scope | None = None
    collection: str | None = None
    priority: int = 0
    adapt: bool = Field(True, description="Resize/convert a reused asset to `size`/`format`/`process` as a variant")
    adapt_as_new_asset: bool = Field(False, description="Make the adapted copy its own asset owned by `project`")
    dry_run: bool = Field(False, description="Report what would happen (reuse or generate, cost) without doing it")


class RequestResult(BaseModel):
    state: Literal["ready", "needs_review", "queued", "running", "failed", "cancelled", "dry_run"]
    reused: bool = False
    reuse_reason: Literal["key", "request", "in_flight", "search"] | None = None
    job_id: str | None = None
    asset_id: str | None = None
    asset: AssetSummary | None = None
    variants: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    match: dict[str, Any] | None = None
    generated: dict[str, Any] | None = None
    adapted: dict[str, Any] | None = None
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    near_duplicates: list[dict[str, Any]] = Field(default_factory=list)
    would: Literal["reuse", "generate", "register"] | None = None
    plan: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


# ---------------------------------------------------------------------- inference


def _matches(word: str, vocabulary: set[str]) -> bool:
    return word in vocabulary or (len(word) >= 4 and any(v.startswith(word) or word.startswith(v) for v in vocabulary
                                                         if len(v) >= 4))  # fmt: skip


def infer_recipe(description: str) -> Recipe:
    words = [w for w in tokens(description) if len(w) > 2]
    best, best_score = None, 0
    for recipe in sorted(RECIPES.values(), key=lambda r: r.name):
        name_words = set(tokens(recipe.name))
        tag_words = {t for tag in recipe.tags for t in tokens(tag)}
        desc_words = set(tokens(recipe.description))
        score = sum(3 * _matches(w, name_words) + 2 * _matches(w, tag_words) + _matches(w, desc_words) for w in words)
        if score > best_score:
            best, best_score = recipe, score
    if best is None or best_score < 2:
        raise InvalidInputError(
            "Couldn't pick a sound recipe from the description; pass `recipe`",
            hint="See `assetforge audio recipes`", available=sorted(RECIPES),
        )  # fmt: skip
    return best


def infer_preset(description: str, recipe: Recipe) -> str | None:
    reserved = set(tokens(recipe.name))
    for word in tokens(description):
        if word in SOUND_PRESETS and word not in reserved:
            return word
    return None


def media_kind(asset_type: AssetType) -> Literal["audio", "image"]:
    if asset_type in AUDIO_TYPES:
        return "audio"
    if asset_type in IMAGE_TYPES:
        return "image"
    raise InvalidInputError(f"`request` can't generate {asset_type.value} assets",
                            generatable=sorted(t.value for t in AUDIO_TYPES | IMAGE_TYPES))  # fmt: skip


def sound_plan(req: AssetRequest, lib: Library | None = None) -> dict[str, Any]:
    """Which audio engine a request goes to and with what settings (description inference + `sound` overrides)."""
    from assetforge.audio import infer

    options = dict(req.sound)
    if req.type is AssetType.AMBIENCE:
        return {"engine": "ambience", "settings": infer.ambience_from_words(req.description, lib) | options}
    if req.type is AssetType.MUSIC:
        inferred = {} if ("score" in options or "midi" in options) else infer.music_from_words(req.description)
        return {"engine": "music", "settings": inferred | options}
    if "design" in options or "patch" in options:
        return {"engine": "design", "settings": options}
    if not (options.get("recipe") or req.recipe) and (creature := _creature_plan(req, lib, options)) is not None:
        return creature
    recipe_name = options.get("recipe") or req.recipe
    if recipe_name in RECIPES:
        recipe = RECIPES[recipe_name]
    else:
        try:
            recipe = infer_recipe(recipe_name or req.description)
        except InvalidInputError:
            return {"engine": "design", "settings": {"patch": infer.patch_from_words(req.description)}, "inferred": "patch"}
    return {"engine": "recipe", "recipe": recipe.name, "preset": options.get("preset") or req.preset or
            infer_preset(req.description, recipe)}  # fmt: skip


def _creature_plan(req: AssetRequest, lib: Library | None, options: dict[str, Any]) -> dict[str, Any] | None:
    """Creature voices go to the layered creature engine when its recordings are installed (else the synth recipes)."""
    from assetforge.audio.creatures import engine as creatures
    from assetforge.audio.creatures import pack

    inferred = creatures.creature_from_words(req.description)
    if not ("creature" in options or inferred) or lib is None or not pack.installed_packs(lib.paths.home):
        return None
    return {"engine": "creature", "settings": (inferred or {}) | options}


def _engine_audio(lib: Library, req: AssetRequest, planned: dict[str, Any], *, seed: int, key: str | None,
                  project: str | None, style: str | None, scope: Scope) -> dict[str, Any]:  # fmt: skip
    """Run the design/ambience/music engine for a request; returns the pipeline result."""
    common = {"variants": min(req.variants, 16), "seed": seed, "new_take": req.new_variant, "name": req.name,
              "key": key, "description": req.description, "tags": req.tags, "scope": scope, "project": project,
              "style": style, "status": Status.CANDIDATE, "collection": req.collection, "category": req.category,
              "subcategory": req.subcategory, "theme": req.theme, "use_profile": req.use_profile}  # fmt: skip
    if req.format in ("ogg", "mp3", "wav"):
        common["format"] = req.format
    settings = dict(planned["settings"])
    try:
        if planned["engine"] == "ambience":
            from assetforge.audio.ambience.engine import AmbienceRequest, generate_ambience

            return generate_ambience(lib, AmbienceRequest(**(settings | common)))
        if planned["engine"] == "music":
            from assetforge.audio.music.generate import MusicRequest, generate_music

            return generate_music(lib, MusicRequest(**(settings | common)))
        if planned["engine"] == "creature":
            from assetforge.audio.creatures.engine import CreatureRequest, generate_creature

            return generate_creature(lib, CreatureRequest(**(settings | common)))
        from assetforge.audio.design import DesignRequest, generate_design

        kind = "ui" if req.type is AssetType.UI else "sfx"
        return generate_design(lib, DesignRequest(**({k: settings[k] for k in ("design", "patch") if k in settings}
                                                     | common | {"type": kind})))  # fmt: skip
    except ValueError as exc:
        raise InvalidInputError(f"Invalid sound options: {exc}") from exc


def fingerprint(req: AssetRequest) -> str:
    data = req.model_dump(mode="json", exclude={"priority", "new_variant", "dry_run"})
    return jobs.fingerprint({"engine": "request", **data})


# ---------------------------------------------------------------------- reuse


def _summary_result(lib: Library, asset_id: str, reason: str, *, match: dict[str, Any] | None = None) -> RequestResult:
    asset = lib.get_asset(asset_id)
    primary = asset.primary_variant
    variants = [{"variant_id": primary.id, "label": primary.label, "path": primary.file.path,
                 "format": primary.file.ext.lstrip(".")}] if primary else []  # fmt: skip
    return RequestResult(state="ready", reused=True, reuse_reason=reason, asset_id=asset.id,  # type: ignore[arg-type]
                         asset=lib.summaries([asset.id])[0], variants=variants, match=match)  # fmt: skip


def _usable(lib: Library, asset_id: str, req: AssetRequest) -> bool:
    asset = lib.get_asset(asset_id)
    allowed = {Status.APPROVED} | ({Status.CANDIDATE} if req.accept_candidates else set())
    return asset.status in allowed and asset.commercial_use != "restricted" and asset.primary_variant is not None


def by_key(lib: Library, req: AssetRequest) -> str | None:
    if not req.key:
        return None
    key = normalize_key(req.key)
    project = normalize_optional_slug(req.project, "project")
    rows = lib.conn.execute("SELECT id FROM assets WHERE key = ? AND ifnull(project, '') = ?", (key, project or "")).fetchall()
    if project:
        rows += lib.conn.execute(
            "SELECT asset_id AS id FROM project_usage WHERE project = ? AND usage_key = ? ORDER BY created_at DESC",
            (project, key),
        ).fetchall()
    for row in rows:
        if _usable(lib, row["id"], req.model_copy(update={"accept_candidates": True})):
            return row["id"]
    return None


def _preferred_style(lib: Library, req: AssetRequest) -> str | None:
    """The style to favour when reusing: the request's, else the project's art profile style."""
    if req.style or (req.art and req.art.style):
        return art.style_slug(req.style or (req.art.style if req.art else None))
    profile = art.find_profile(lib, req.project) if req.use_profile else None
    return art.style_slug(profile.direction.style) if profile else None


def _search_query(lib: Library, req: AssetRequest, *, min_coverage: float, limit: int) -> SearchQuery:
    size = image_proc_size(req.size)
    return SearchQuery(
        text=req.description,
        types=COMPATIBLE.get(req.type, [req.type]),
        kind=FileKind.AUDIO if media_kind(req.type) == "audio" else None,
        style=_preferred_style(lib, req),
        project=req.project,
        statuses=[Status.APPROVED, *([Status.CANDIDATE] if req.accept_candidates else [])],
        transparent=req.transparent,
        min_duration_ms=req.duration_ms // 2 if req.duration_ms else None,
        max_duration_ms=req.duration_ms * 2 if req.duration_ms else None,
        min_width=size[0] // 2 if size else None,  # don't blow a tiny image up to the requested size
        min_height=size[1] // 2 if size else None,
        min_coverage=min_coverage,
        limit=limit,
    )


def _ranked(lib: Library, req: AssetRequest, hits: list[Any]) -> list[tuple[float, Any]]:
    """Search hits re-ranked with a bonus for matching category/subcategory/theme/genre."""
    wanted = {f: normalize_optional_slug(getattr(req, f), f) for f in ("category", "subcategory", "theme", "genre")}
    if req.art:
        wanted["theme"] = wanted["theme"] or normalize_optional_slug(req.art.theme, "theme")
        wanted["genre"] = wanted["genre"] or normalize_optional_slug(req.art.genre, "genre")
    ranked = [((h.score or 0) + DISCOVERY_BONUS * sum(1 for f, v in wanted.items() if v and getattr(h, f) == v), h)
              for h in hits]  # fmt: skip
    return sorted(ranked, key=lambda item: (-item[0], item[1].id))


def _match(top: Any, score: float, threshold: float) -> dict[str, Any]:
    return {"score": round(score, 4), "coverage": top.coverage, "matched_terms": top.matched_terms,
            "threshold": threshold}  # fmt: skip


def by_search(lib: Library, req: AssetRequest) -> tuple[str, dict[str, Any], list[dict[str, Any]]] | None:
    threshold = req.reuse_threshold if req.reuse_threshold is not None else settings_store.get(lib, "request.reuse_threshold")
    hits = search(lib, _search_query(lib, req, min_coverage=threshold, limit=10)).results
    if media_kind(req.type) == "image":
        hits = [h for h in hits if h.primary_variant and h.primary_variant.file.kind in (FileKind.IMAGE, FileKind.VECTOR)]
    if not hits:
        return None
    ranked = _ranked(lib, req, hits)
    score, top = ranked[0]
    return top.id, _match(top, score, threshold), [_brief(h, s) for s, h in ranked[1:4]]


def _brief(hit: Any, score: float | None = None) -> dict[str, Any]:
    return {"asset_id": hit.id, "name": hit.name, "type": hit.type.value, "score": round(score or hit.score or 0, 4),
            "coverage": hit.coverage, "status": hit.status.value, "path": hit.path}  # fmt: skip


def _closest(lib: Library, req: AssetRequest) -> list[dict[str, Any]]:
    """Near misses (any coverage) so the agent can decide to adapt one instead of generating."""
    hits = search(lib, SearchQuery(text=req.description, types=COMPATIBLE.get(req.type, [req.type]),
                                   style=_preferred_style(lib, req), limit=3)).results  # fmt: skip
    return [_brief(h) for h in hits]


# ---------------------------------------------------------------------- adaptation


def image_proc_size(size: str | None) -> tuple[int, int] | None:
    from assetforge.image.engine import parse_size

    return parse_size(size)


def adapt_ops(lib: Library, asset_id: str, req: AssetRequest) -> list[dict[str, Any]]:
    """Processing that turns a reused asset into what the request asked for (empty: use it as is)."""
    if not req.adapt:
        return []
    asset = lib.get_asset(asset_id)
    primary = asset.primary_variant
    if primary is None:
        return []
    if media_kind(req.type) == "audio":
        ops = audio_proc.expand_preset(req.process) if req.process else []
        if req.format and primary.file.ext.lstrip(".") != req.format and not any(o["op"] == "format" for o in ops):
            ops.append({"op": "format", "format": req.format})
        return ops
    if primary.file.kind != FileKind.IMAGE:
        return []
    ops = []
    size = image_proc_size(req.size)
    if size and (primary.file.width, primary.file.height) != size:
        mode = "cover" if req.type in FILL_TYPES or asset.type in FILL_TYPES else "pad"
        ops.append({"op": "resize", "width": size[0], "height": size[1], "mode": mode})
    if req.process:
        ops += image_proc.expand_preset(req.process)
    fmt = req.format
    if fmt and primary.file.ext.lstrip(".").replace("jpeg", "jpg") != fmt:
        ops = [o for o in ops if o["op"] != "format"] + [{"op": "format", "format": fmt}]
    return ops


def _adapt(lib: Library, asset_id: str, req: AssetRequest) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    """Make (or find) the adapted derivative. Returns (asset_id, variant record, adapted info)."""
    ops = adapt_ops(lib, asset_id, req)
    if not ops:
        return None
    module = audio_proc.process_audio_asset if media_kind(req.type) == "audio" else image_proc.process_image_asset
    name = f"{lib.get_asset(asset_id).name}_{normalize_optional_slug(req.project, 'project') or 'adapted'}"
    derived = module(lib, asset_id, ops, new_asset=req.adapt_as_new_asset, name=name if req.adapt_as_new_asset else None)
    if req.adapt_as_new_asset and req.project:
        lib.update_asset(derived.asset_id, AssetPatch(project=req.project))
    variant = next(v for v in lib.get_asset(derived.asset_id).variants if v.id == derived.variant_id)
    record = {"variant_id": variant.id, "label": variant.label, "path": variant.file.path,
              "format": variant.file.ext.lstrip("."), "width": variant.file.width, "height": variant.file.height}  # fmt: skip
    info = {"from_asset": asset_id, "ops": ops, "variant_id": variant.id, "new_asset": derived.new_asset,
            "reused_existing": derived.reused}  # fmt: skip
    return derived.asset_id, record, info


def _record_use(lib: Library, asset_id: str, req: AssetRequest, variant_id: str | None = None) -> None:
    if req.project:
        lib.record_usage(asset_id, req.project, variant_id=variant_id,
                         usage_key=normalize_key(req.key) if req.key else None)  # fmt: skip


def _reuse(lib: Library, asset_id: str, req: AssetRequest, reason: str, *, match: dict[str, Any] | None = None,
           alternatives: list[dict[str, Any]] | None = None) -> RequestResult:  # fmt: skip
    adapted = _adapt(lib, asset_id, req)
    if adapted is None:
        _record_use(lib, asset_id, req)
        result = _summary_result(lib, asset_id, reason, match=match)
    else:
        target, variant, info = adapted
        _record_use(lib, target, req, variant_id=variant["variant_id"])
        result = _summary_result(lib, target, reason, match=match).model_copy(update={"variants": [variant],
                                                                                      "adapted": info})  # fmt: skip
    return result.model_copy(update={"alternatives": alternatives or []})


# ---------------------------------------------------------------------- generation


def _key_free(lib: Library, req: AssetRequest) -> str | None:
    if not req.key:
        return None
    key = normalize_key(req.key)
    project = normalize_optional_slug(req.project, "project")
    taken = lib.conn.execute("SELECT 1 FROM assets WHERE key = ? AND ifnull(project, '') = ?", (key, project or "")).fetchone()
    return None if taken else key


def _seed(req: AssetRequest, fp: str) -> int:
    if req.seed is not None:
        return req.seed
    if req.new_variant:
        return secrets.randbelow(2**31)
    return int(hashlib.sha256(fp.encode()).hexdigest()[:8], 16)  # retries reproduce the same sound


def _scope(req: AssetRequest) -> tuple[str | None, str | None, Scope]:
    project = normalize_optional_slug(req.project, "project")
    style = art.style_slug(req.style or (req.art.style if req.art else None))
    scope = req.scope or (Scope.PROJECT if project and not style and req.key else Scope.STYLE if style else Scope.GLOBAL)
    return project, style, scope


def _image_request(req: AssetRequest, *, key: str | None, dry_run: bool = False) -> ImageRequest:
    project, style, scope = _scope(req)
    return ImageRequest(
        prompt=req.prompt or req.description, type=req.type, style=req.style, art=req.art, purpose=req.purpose,
        sheet=req.sheet, use_profile=req.use_profile, provider=req.provider, model=req.model, n=req.n,
        aspect=req.aspect, size=req.size, quality=req.quality, transparent=req.transparent,
        format=req.format if req.format in ("png", "webp") else "png", references=req.references,
        negative=req.negative, max_cost=req.max_cost, name=req.name, key=key if req.n == 1 else None,
        description=req.description, tags=req.tags, category=req.category, subcategory=req.subcategory,
        theme=req.theme, genre=req.genre, scope=scope, project=project, status=Status.CANDIDATE,
        collection=req.collection, new_variant=req.new_variant, dry_run=dry_run,
    )  # fmt: skip


def _generate(lib: Library, job_id: str, req: AssetRequest, fp: str, settings: Settings) -> RequestResult:
    kind = media_kind(req.type)
    project, style, scope = _scope(req)
    key = _key_free(lib, req)
    produced: list[str] = []
    variants: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    generated: dict[str, Any]

    if req.method in ("svg", "procedural", "file"):
        from assetforge.add import AddRequest, add_file

        added = add_file(lib, AddRequest(
            path=req.file or "", method=req.method, type=req.type, name=req.name, key=key,
            description=req.description, style=req.style, art=req.art, project=project, scope=scope, tags=req.tags,
            category=req.category, subcategory=req.subcategory, theme=req.theme, genre=req.genre,
            script=req.script, collection=req.collection, size=req.size,
        ))  # fmt: skip
        produced = [added.asset_id]
        generated = {"kind": req.method, "cost_usd": 0.0, "reused_content": not added.created}
    elif kind == "audio" and (planned := sound_plan(req, lib))["engine"] != "recipe":
        res = _engine_audio(lib, req, planned, seed=_seed(req, fp), key=key, project=project, style=style, scope=scope)
        produced = [res["asset_id"]]
        variants = [{"variant_id": v["variant_id"], "label": v["label"], "path": v["path"], "format": v["format"],
                     "duration_ms": v["duration_ms"], **({"extras": v["extras"]} if v["extras"] else {})}
                    for v in res["variants"]]  # fmt: skip
        generated = {"kind": planned["engine"], "job_id": res["job_id"], "engine": res["engine"],
                     "reused_content": res["reused"], "audio": res["audio"], "cost_usd": 0.0,
                     **({"inferred": planned["inferred"]} if planned.get("inferred") else {})}  # fmt: skip
    elif kind == "audio":
        recipe = RECIPES[planned["recipe"]]
        preset = planned["preset"]
        result = generate_audio(lib, AudioRequest(
            recipe=recipe.name, preset=preset, params=req.params, duration_ms=req.duration_ms, effects=req.effects,
            variants=req.variants, variation=req.variation, seed=_seed(req, fp), format=req.format or "ogg",
            name=req.name, key=key, description=req.description, tags=req.tags, scope=scope, project=project,
            style=style, status=Status.CANDIDATE, collection=req.collection, new_variant=req.new_variant,
        ))  # fmt: skip
        produced = [result.asset_id]
        variants = [{"variant_id": v.variant_id, "label": v.label, "path": v.path, "format": v.format,
                     "duration_ms": v.duration_ms} for v in result.variants]  # fmt: skip
        generated = {"kind": "audio", "job_id": result.job_id, "recipe": recipe.name, "preset": preset,
                     "reused_content": result.reused, "cost_usd": 0.0}  # fmt: skip
    else:
        from assetforge.image.engine import result_from_job
        from assetforge.image.resume import resume_image_job

        inner_id = (jobs.get_job(lib, job_id).result or {}).get("inner_job_id")
        if inner_id:  # a retry after a crash: finish the earlier (possibly paid) image job instead of re-calling
            inner = jobs.get_job(lib, inner_id)
            image = result_from_job(lib, inner) if inner.status == JobStatus.SUCCEEDED else resume_image_job(lib, inner_id, settings)
        else:
            image = generate_image(lib, _image_request(req, key=key), settings,
                                   on_job=lambda inner: jobs.update_job(lib, job_id, result={"inner_job_id": inner}))  # fmt: skip
        produced = [c.asset_id for c in image.assets]
        candidates = [{"asset_id": c.asset_id, "variant_id": c.variant_id, "path": c.path, "width": c.width,
                       "height": c.height, "has_alpha": c.has_alpha, "qc": c.qc} for c in image.assets]  # fmt: skip
        generated = {"kind": "image", "job_id": image.job_id, "provider": image.provider, "model": image.model,
                     "cost_usd": image.cost_usd, "reused_job": image.reused}  # fmt: skip

    if req.process:
        module = audio_proc if kind == "audio" else image_proc
        ops = module.expand_preset(req.process)
        for asset_id in produced:
            if kind == "audio":
                audio_proc.process_audio_asset(lib, asset_id, ops, make_primary=True)
            else:
                image_proc.process_image_asset(lib, asset_id, ops, make_primary=True)
        generated["process"] = req.process

    if req.slice and req.sheet and kind == "image":
        from assetforge.image.sprites import slice_asset

        frame_type = AssetType.ANIMATION if req.sheet.one_character else AssetType.OBJECT
        sliced = [slice_asset(lib, asset_id, columns=req.sheet.columns, rows=req.sheet.rows,
                              names=req.sheet.poses or None, frame_type=frame_type) for asset_id in produced]  # fmt: skip
        generated["sliced"] = [{"collection": r.collection, "frames": len(r.frames), "report": r.report} for r in sliced]

    primary_id = produced[0]
    asset = lib.get_asset(primary_id)
    if kind == "image" or req.process:
        p = asset.primary_variant
        assert p is not None
        variants = [{"variant_id": p.id, "label": p.label, "path": p.file.path, "format": p.file.ext.lstrip(".")}]
        if req.process and kind == "image":
            candidates = [{**c, "path": lib.get_asset(c["asset_id"]).path} for c in candidates]
    if len(produced) == 1:
        _record_use(lib, primary_id, req)
    state = "needs_review" if len(produced) > 1 else "ready"
    duplicates = [{"asset_id": a, **d} for a in produced for d in visual.near_duplicates(lib, a) if d["asset_id"] not in
                  produced] if kind == "image" else []  # fmt: skip
    return RequestResult(state=state, job_id=job_id, asset_id=primary_id, asset=lib.summaries([primary_id])[0],
                         variants=variants, candidates=candidates if len(produced) > 1 else [], generated=generated,
                         near_duplicates=duplicates)  # fmt: skip


def run_job(lib: Library, job_id: str, owner: str, settings: Settings | None = None, *,
            requeue_on_retryable: bool = False) -> RequestResult:  # fmt: skip
    """Execute a request job we hold the lease for, and close it (or requeue it, for workers)."""
    settings = settings or load_settings(lib.paths.home)
    job = jobs.get_job(lib, job_id)
    req = AssetRequest(**job.request["request"])
    with queue.keep_alive(lib, job_id, owner):
        try:
            result = _generate(lib, job_id, req, job.fingerprint or fingerprint(req), settings)
        except AssetForgeError as exc:
            error = exc.to_dict()
            fresh = jobs.get_job(lib, job_id)
            if requeue_on_retryable and exc.retryable and fresh.attempts < (fresh.max_attempts or 1):
                delay = RETRY_DELAYS[min(fresh.attempts - 1, len(RETRY_DELAYS) - 1)]
                queue.requeue(lib, job_id, owner, delay=delay, error=error)
            else:
                queue.finish(lib, job_id, owner, status=JobStatus.FAILED, error=error,
                             result=fresh.result)  # fmt: skip
            raise
        except Exception as exc:
            error = InternalError(f"{type(exc).__name__}: {exc}").to_dict()
            queue.finish(lib, job_id, owner, status=JobStatus.FAILED, error=error, result=jobs.get_job(lib, job_id).result)
            raise
    stored = result.model_dump(mode="json")
    stored["inner_job_id"] = (jobs.get_job(lib, job_id).result or {}).get("inner_job_id")
    queue.finish(lib, job_id, owner, status=JobStatus.SUCCEEDED, result=stored, asset_id=result.asset_id)
    return result


def result_from_job(lib: Library, job: Any) -> RequestResult:
    if job.status == JobStatus.SUCCEEDED:
        data = {k: v for k, v in job.result.items() if k in RequestResult.model_fields}
        result = RequestResult(**data)
        if result.asset_id:
            try:
                result.asset = lib.summaries([result.asset_id])[0]
            except (AssetForgeError, IndexError):
                pass
        return result
    state = {"queued": "queued", "running": "running", "cancelled": "cancelled"}.get(job.status.value, "failed")
    return RequestResult(state=state, job_id=job.id, error=job.error)  # type: ignore[arg-type]


def handle(lib: Library, req: AssetRequest, *, settings: Settings | None = None, enqueue: bool = False,
           wait_seconds: float | None = None) -> RequestResult:  # fmt: skip
    """Satisfy a request: reuse if possible, otherwise generate now (or enqueue for a worker)."""
    media_kind(req.type)  # fail fast on types we can't generate
    if req.key:
        normalize_key(req.key)
    if req.process:
        (audio_proc if media_kind(req.type) == "audio" else image_proc).expand_preset(req.process)
    if req.method in ("svg", "procedural", "file") and not req.file:
        raise InvalidInputError(f"method {req.method} registers a file you made: pass `file`")
    if req.slice and not req.sheet:
        raise InvalidInputError("`slice` needs a `sheet` grid (columns/rows)")
    fp = fingerprint(req)
    if req.dry_run:
        return plan(lib, req, settings)
    alternatives: list[dict[str, Any]] = []
    if req.reuse_policy != "always_generate":
        if asset_id := by_key(lib, req):
            return _reuse(lib, asset_id, req, "key")
        if not req.new_variant and (done := jobs.find_completed(lib, "request", fp)):
            result = result_from_job(lib, done)
            return result.model_copy(update={"reused": True, "reuse_reason": "request", "job_id": done.id})
        if found := by_search(lib, req):
            asset_id, match, others = found
            return _reuse(lib, asset_id, req, "search", match=match, alternatives=others)
        alternatives = _closest(lib, req)
        if req.reuse_policy == "require_existing":
            raise NotFoundError("No existing asset matches this request", hint="Lower reuse_threshold or allow generation",
                                closest=alternatives)  # fmt: skip

    owner = limits.owner_id()
    record = {"request": req.model_dump(mode="json")}
    reuse_finished = not req.new_variant and req.reuse_policy != "always_generate"
    job_id, active = queue.create_request_job(lib, record, fp, owner=owner, queued=enqueue, priority=req.priority,
                                              reuse_finished=reuse_finished)  # fmt: skip
    if active is not None and active.status == JobStatus.SUCCEEDED:  # finished between our check and now
        return result_from_job(lib, active).model_copy(update={"reused": True, "reuse_reason": "request",
                                                               "job_id": active.id})  # fmt: skip
    if active is not None:
        if enqueue:
            return result_from_job(lib, active).model_copy(update={"reused": True, "reuse_reason": "in_flight"})
        if active.status == JobStatus.QUEUED and queue.claim(lib, active.id, owner):
            return run_job(lib, active.id, owner, settings)
        timeout = wait_seconds if wait_seconds is not None else settings_store.get(lib, "request.wait_seconds")
        finished = queue.wait(lib, active.id, timeout=timeout)
        result = result_from_job(lib, finished)
        return result.model_copy(update={"reused": True, "reuse_reason": "in_flight", "job_id": active.id})
    assert job_id is not None
    if enqueue:
        return RequestResult(state="queued", job_id=job_id, alternatives=alternatives)
    return run_job(lib, job_id, owner, settings).model_copy(update={"alternatives": alternatives})


def plan(lib: Library, req: AssetRequest, settings: Settings | None = None) -> RequestResult:
    """`dry_run`: what `handle` would do (reuse which asset and how it would be adapted, or what generating would
    send and cost), without writing anything or calling a paid API."""
    if req.reuse_policy != "always_generate":
        found: tuple[str, str, dict[str, Any] | None, list[dict[str, Any]]] | None = None
        if asset_id := by_key(lib, req):
            found = (asset_id, "key", None, [])
        elif hit := by_search(lib, req):
            found = (hit[0], "search", hit[1], hit[2])
        if found:
            asset_id, reason, match, others = found
            result = _summary_result(lib, asset_id, reason, match=match)
            ops = adapt_ops(lib, asset_id, req)
            return result.model_copy(update={"state": "dry_run", "would": "reuse", "alternatives": others,
                                             "plan": {"adapt_ops": ops} if ops else None})  # fmt: skip
    alternatives = _closest(lib, req)
    if req.reuse_policy == "require_existing":
        raise NotFoundError("No existing asset matches this request", closest=alternatives)
    if req.method in ("svg", "procedural", "file"):
        return RequestResult(state="dry_run", would="register", alternatives=alternatives,
                             plan={"method": req.method, "file": req.file, "cost_usd": 0.0})  # fmt: skip
    if media_kind(req.type) == "audio":
        planned = sound_plan(req, lib)
        details: dict[str, Any] = dict(planned)
        if planned["engine"] == "music" and not {"score", "midi"} & set(planned["settings"]):
            from assetforge.audio import pipeline
            from assetforge.audio.music import composer
            from assetforge.audio.music.generate import MusicRequest, build_brief

            settings = planned["settings"]
            brief, _ = build_brief(lib, MusicRequest(**settings, project=req.project, use_profile=req.use_profile))
            score = composer.compose(brief, pipeline.take_seed(_seed(req, fingerprint(req)), 1))
            details["preview"] = score.summary()
        elif planned["engine"] == "ambience":
            from assetforge.audio.ambience.engine import AmbienceRequest, resolve_spec

            spec, _ = resolve_spec(lib, AmbienceRequest(**planned["settings"]))
            details["preview"] = {"scene": spec.scene, "beds": [b.bed for b in spec.beds],
                                  "events": [e.event for e in spec.events], "length": spec.length,
                                  "space": spec.space, "intensity": spec.intensity}  # fmt: skip
        return RequestResult(state="dry_run", would="generate", alternatives=alternatives, plan={
            "method": "procedural_audio", **details, "variants": req.variants, "cost_usd": 0.0})  # fmt: skip
    image = generate_image(lib, _image_request(req, key=None, dry_run=True), settings)
    return RequestResult(state="dry_run", would="generate", alternatives=alternatives, plan={
        "method": "ai_image", "provider": image.provider, "model": image.model, "prompt": image.prompt,
        "transparency": image.transparency, "art": image.art, "art_layers": image.art_layers, "n": req.n,
        "estimated_cost_usd": image.estimated_cost_usd, "slice": req.slice})  # fmt: skip

