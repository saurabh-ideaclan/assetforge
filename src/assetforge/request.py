"""`assetforge request`: the one call an agent makes to get an asset. It reuses before it generates.

Reuse ladder, tried in order (`reuse_policy` = prefer_existing, the default):
  1. key: the project already has an asset or usage under this key
  2. request: an identical request already finished, or is running right now (join it, don't duplicate)
  3. search: a cataloged asset matches the description well enough (coverage >= reuse_threshold)
Then generate: procedural audio for `sfx` (recipe and preset inferred from the description), or AI
images for visual types. Optionally post-process with a preset, record project usage, and return paths.
`require_existing` stops after step 3 with NOT_FOUND. `always_generate` skips steps 1–3.

Each generating request is a durable job (kind "request") with a lease, so a crash is recoverable,
and concurrent identical requests from different agents produce the asset once.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.audio import process as audio_proc
from assetforge.audio.generate import AudioRequest, generate_audio
from assetforge.audio.recipes import PRESETS as SOUND_PRESETS
from assetforge.audio.recipes import RECIPES, Recipe
from assetforge.config import Settings, load_settings
from assetforge.core import jobs, limits, queue, settings_store
from assetforge.core.catalog import Library
from assetforge.core.classify import tokens
from assetforge.core.models import AssetSummary, AssetType, FileKind, JobStatus, Scope, Status
from assetforge.core.search import SearchQuery, search
from assetforge.core.util import normalize_key, normalize_optional_slug
from assetforge.errors import AssetForgeError, InternalError, InvalidInputError, NotFoundError
from assetforge.image import process as image_proc
from assetforge.image.generate import ImageRequest, generate_image

ReusePolicy = Literal["prefer_existing", "require_existing", "always_generate"]
AUDIO_TYPES = {AssetType.SFX, AssetType.AUDIO}
IMAGE_TYPES = {AssetType.CHARACTER, AssetType.SPRITE, AssetType.BACKGROUND, AssetType.ICON, AssetType.TILE,
               AssetType.UI, AssetType.EFFECT, AssetType.IMAGE}  # fmt: skip
COMPATIBLE: dict[AssetType, list[AssetType]] = {
    AssetType.SFX: [AssetType.SFX, AssetType.AUDIO],
    AssetType.AUDIO: [AssetType.SFX, AssetType.AUDIO],
    AssetType.CHARACTER: [AssetType.CHARACTER, AssetType.SPRITE],
    AssetType.SPRITE: [AssetType.SPRITE, AssetType.CHARACTER],
    AssetType.ICON: [AssetType.ICON, AssetType.UI],
    AssetType.UI: [AssetType.UI, AssetType.ICON],
    AssetType.IMAGE: sorted(IMAGE_TYPES),
}
RETRY_DELAYS = (30.0, 120.0, 600.0)


class AssetRequest(BaseModel):
    """What an agent needs. Also the JSON schema of `assetforge request --file spec.json`."""

    model_config = ConfigDict(extra="forbid")

    type: AssetType
    description: str = Field(min_length=3)
    project: str | None = None
    key: str | None = Field(None, description="How the project refers to it, e.g. enemy/beetle/hit")
    style: str | None = None
    reuse_policy: ReusePolicy = "prefer_existing"
    reuse_threshold: float | None = Field(None, ge=0, le=1)
    accept_candidates: bool = Field(False, description="Allow reusing unreviewed candidates")
    new_variant: bool = False
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
    # output + catalog
    format: str | None = Field(None, description="Audio: wav/ogg/mp3 (default ogg). Images: png/webp (default png)")
    process: str | None = Field(None, description="Processing preset applied to the result (e.g. web-sprite)")
    name: str | None = None
    tags: list[str] = Field(default_factory=list)
    scope: Scope | None = None
    collection: str | None = None
    priority: int = 0


class RequestResult(BaseModel):
    state: Literal["ready", "needs_review", "queued", "running", "failed", "cancelled"]
    reused: bool = False
    reuse_reason: Literal["key", "request", "in_flight", "search"] | None = None
    job_id: str | None = None
    asset_id: str | None = None
    asset: AssetSummary | None = None
    variants: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    match: dict[str, Any] | None = None
    generated: dict[str, Any] | None = None
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
    hint = "Import licensed music with `assetforge import`" if asset_type in (AssetType.MUSIC, AssetType.AMBIENCE) else None
    raise InvalidInputError(f"`request` can't generate {asset_type.value} assets", hint=hint,
                            generatable=sorted(t.value for t in AUDIO_TYPES | IMAGE_TYPES))  # fmt: skip


def fingerprint(req: AssetRequest) -> str:
    data = req.model_dump(mode="json", exclude={"priority", "new_variant"})
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


def by_search(lib: Library, req: AssetRequest) -> tuple[str, dict[str, Any]] | None:
    threshold = req.reuse_threshold if req.reuse_threshold is not None else settings_store.get(lib, "request.reuse_threshold")
    query = SearchQuery(
        text=req.description,
        types=COMPATIBLE.get(req.type, [req.type]),
        kind=FileKind.AUDIO if media_kind(req.type) == "audio" else FileKind.IMAGE,
        style=req.style,
        project=req.project,
        statuses=[Status.APPROVED, *([Status.CANDIDATE] if req.accept_candidates else [])],
        transparent=True if req.transparent else None,
        min_duration_ms=req.duration_ms // 2 if req.duration_ms else None,
        max_duration_ms=req.duration_ms * 2 if req.duration_ms else None,
        min_coverage=threshold,
        limit=5,
    )
    hits = search(lib, query).results
    if not hits:
        return None
    top = hits[0]
    return top.id, {"score": top.score, "coverage": top.coverage, "matched_terms": top.matched_terms,
                    "threshold": threshold}  # fmt: skip


def _closest(lib: Library, req: AssetRequest) -> list[dict[str, Any]]:
    hits = search(lib, SearchQuery(text=req.description, types=COMPATIBLE.get(req.type, [req.type]), limit=3)).results
    return [{"asset_id": h.id, "name": h.name, "coverage": h.coverage, "status": h.status.value} for h in hits]


def _record_use(lib: Library, asset_id: str, req: AssetRequest) -> None:
    if req.project:
        lib.record_usage(asset_id, req.project, usage_key=normalize_key(req.key) if req.key else None)


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


def _generate(lib: Library, job_id: str, req: AssetRequest, fp: str, settings: Settings) -> RequestResult:
    kind = media_kind(req.type)
    project = normalize_optional_slug(req.project, "project")
    style = normalize_optional_slug(req.style, "style")
    scope = req.scope or (Scope.PROJECT if project and not style and req.key else Scope.STYLE if style else Scope.GLOBAL)
    key = _key_free(lib, req)
    produced: list[str] = []
    variants: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    generated: dict[str, Any]

    if kind == "audio":
        recipe = RECIPES[req.recipe] if req.recipe in RECIPES else infer_recipe(req.recipe or req.description)
        preset = req.preset or infer_preset(req.description, recipe)
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
            image = generate_image(lib, ImageRequest(
                prompt=req.prompt or req.description, type=req.type, style=style, provider=req.provider,
                model=req.model, n=req.n, aspect=req.aspect, size=req.size, quality=req.quality,
                transparent=req.transparent, format=req.format or "png", references=req.references,
                negative=req.negative, max_cost=req.max_cost, name=req.name, key=key if req.n == 1 else None,
                description=req.description, tags=req.tags, scope=scope, project=project,
                status=Status.CANDIDATE, collection=req.collection, new_variant=req.new_variant,
            ), settings, on_job=lambda inner: jobs.update_job(lib, job_id, result={"inner_job_id": inner}))  # fmt: skip
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
    return RequestResult(state=state, job_id=job_id, asset_id=primary_id, asset=lib.summaries([primary_id])[0],
                         variants=variants, candidates=candidates if len(produced) > 1 else [], generated=generated)  # fmt: skip


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
    fp = fingerprint(req)
    if req.reuse_policy != "always_generate":
        if asset_id := by_key(lib, req):
            _record_use(lib, asset_id, req)
            return _summary_result(lib, asset_id, "key")
        if not req.new_variant and (done := jobs.find_completed(lib, "request", fp)):
            result = result_from_job(lib, done)
            return result.model_copy(update={"reused": True, "reuse_reason": "request", "job_id": done.id})
        if found := by_search(lib, req):
            asset_id, match = found
            _record_use(lib, asset_id, req)
            return _summary_result(lib, asset_id, "search", match=match)
        if req.reuse_policy == "require_existing":
            raise NotFoundError("No existing asset matches this request", hint="Lower reuse_threshold or allow generation",
                                closest=_closest(lib, req))  # fmt: skip

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
        return RequestResult(state="queued", job_id=job_id)
    return run_job(lib, job_id, owner, settings)

