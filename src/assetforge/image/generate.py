"""Text(+reference)-to-image generation requests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.config import Settings
from assetforge.core import jobs
from assetforge.core.catalog import Library, normalize_tags, validate_scope
from assetforge.core.models import AssetType, Scope, Status
from assetforge.core.styles import find_style
from assetforge.core.util import normalize_key, normalize_optional_slug
from assetforge.errors import InvalidInputError
from assetforge.image.engine import (
    ENGINE,
    EngineInput,
    ImageResult,
    check_aspect,
    execute,
    parse_size,
    prepare_call,
    resolve_reference,
    result_from_job,
    settings_for,
)
from assetforge.image.prompts import DEFAULT_ASPECT, TRANSPARENT_BY_DEFAULT
from assetforge.image.providers import provider_chain

Quality = Literal["auto", "low", "medium", "high", "max"]


class ImageRequest(BaseModel):
    """An image generation request. Also the schema of `image generate --file request.json`."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=3)
    type: AssetType = AssetType.IMAGE
    style: str | None = Field(None, description="Art direction; a style profile of this name adds its prompt/refs")
    provider: str = "auto"
    model: str | None = None
    fallback: bool = True
    n: int = Field(1, ge=1, le=8, description="Number of candidate images (each becomes its own asset)")
    aspect: str | None = None
    size: str | None = None
    quality: Quality = "auto"
    transparent: bool | None = Field(None, description="Default: on for characters, sprites, icons, UI, effects")
    format: Literal["png", "webp"] = "png"
    references: list[str] = Field(default_factory=list)
    negative: str | None = None
    raw_prompt: bool = False
    seed: int | None = None
    retries: int = Field(2, ge=0, le=5)
    max_cost: float | None = Field(None, ge=0, description="Refuse calls estimated above this many USD")
    new_variant: bool = False
    dry_run: bool = False
    # catalog
    name: str | None = None
    key: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    scope: Scope = Scope.GLOBAL
    project: str | None = None
    status: Status = Status.CANDIDATE
    collection: str | None = None


def engine_input(lib: Library, req: ImageRequest) -> tuple[EngineInput, dict[str, Any]]:
    """Validate the request and resolve references/style into the engine input and the job record."""
    project = normalize_optional_slug(req.project, "project")
    style = normalize_optional_slug(req.style, "style")
    validate_scope(req.scope, project, style)
    if req.key and req.n > 1:
        raise InvalidInputError("--key needs n=1; give the approved candidate a key later with `update`")
    normalize_key(req.key)
    size = parse_size(req.size)
    aspect = check_aspect(req.aspect) or DEFAULT_ASPECT.get(req.type, "1:1")
    profile = find_style(lib, style) if style else None
    references = [resolve_reference(lib, r, "user") for r in req.references]
    style_refs = [resolve_reference(lib, r, "style") for r in (profile.reference_asset_ids if profile else [])]
    transparent = req.transparent if req.transparent is not None else req.type in TRANSPARENT_BY_DEFAULT
    inp = EngineInput(
        operation="generate", prompt=req.prompt, asset_type=req.type, transparent=transparent, n=req.n,
        aspect=aspect, size=size, quality=req.quality, output_format=req.format, provider=req.provider,
        model=req.model, fallback=req.fallback, retries=req.retries, seed=req.seed, negative=req.negative,
        raw_prompt=req.raw_prompt, style_name=style, profile=profile, references=references,
        style_references=style_refs, max_cost=req.max_cost, project=project,
    )  # fmt: skip
    record = {
        "operation": "generate",
        "request": req.model_dump(mode="json"),
        "asset_type": req.type.value,
        "transparent": transparent,
        "scope": req.scope.value,
        "project": project,
        "style": style,
        "tags": normalize_tags([req.type.value, *([style] if style else []), *req.tags]),
        "references": [r.__dict__ for r in references + style_refs],
        "style_profile_updated_at": profile.updated_at if profile else None,
    }
    return inp, record


def fingerprint(record: dict[str, Any]) -> str:
    request = {k: v for k, v in record["request"].items()
               if k not in ("retries", "new_variant", "dry_run", "collection", "status", "fallback", "max_cost")}  # fmt: skip
    return jobs.fingerprint({
        "engine": ENGINE, "operation": record["operation"], "request": request,
        "references": sorted(r["sha256"] for r in record["references"]),
        "style_profile_updated_at": record.get("style_profile_updated_at"),
    })  # fmt: skip


def plan_only(inp: EngineInput, settings: Settings, lib: Library) -> ImageResult:
    """--dry-run: show which provider/model and exact prompt would be used, without calling anything."""
    provider = provider_chain(inp.provider, settings, fallback=inp.fallback)[0]
    model = inp.model or provider.default_model(inp.operation)
    prepared = prepare_call(provider, model, inp, lib.paths.tmp)
    from assetforge.core import metering

    estimate, _ = metering.estimate_call(lib, provider, prepared.call)
    return ImageResult(
        job_id=None, reused=False, dry_run=True, provider=provider.name, model=model,
        prompt=prepared.prompt.final, transparency=prepared.transparency,
        estimated_cost_usd=estimate,
    )  # fmt: skip


def generate_image(
    lib: Library, req: ImageRequest, settings: Settings | None = None, *, on_job: Callable[[str], None] | None = None
) -> ImageResult:
    settings = settings or settings_for(lib)
    inp, record = engine_input(lib, req)
    if req.dry_run:
        return plan_only(inp, settings, lib)
    fp = fingerprint(record)
    if not req.new_variant and (done := jobs.find_completed(lib, "image", fp)):
        return result_from_job(lib, done)
    job_id = jobs.create_job(lib, "image", record, fingerprint=fp, provider=req.provider, model=req.model)
    if on_job:
        on_job(job_id)
    return execute(lib, job_id, inp, record, settings)
