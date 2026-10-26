"""Text(+reference)-to-image generation requests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge import art
from assetforge.art import ArtDirection
from assetforge.config import Settings
from assetforge.core import jobs
from assetforge.core.catalog import Library, normalize_tags, validate_scope
from assetforge.core.models import AssetType, Scope, Status
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
from assetforge.image.prompts import DEFAULT_ASPECT, PROMPT_VERSION, TRANSPARENT_BY_DEFAULT, SheetSpec
from assetforge.image.providers import provider_chain

Quality = Literal["auto", "low", "medium", "high", "max"]


class ImageRequest(BaseModel):
    """An image generation request. Also the schema of `image generate --file request.json`."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=3, description="What to draw: subject, pose, action, setting")
    type: AssetType = AssetType.IMAGE
    style: str | None = Field(None, description="A preset name (`art presets`), a style profile, or any style label")
    art: ArtDirection | None = Field(None, description="Custom art direction; overrides the project profile per field")
    purpose: str | None = Field(None, description="What the asset is for in the game, e.g. 'boss of level 3'")
    sheet: SheetSpec | None = Field(None, description="Sprite-sheet grid layout and per-cell poses")
    use_profile: bool = Field(True, description="Apply the project's art profile")
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
    category: str | None = None
    subcategory: str | None = None
    theme: str | None = None
    genre: str | None = None
    scope: Scope = Scope.GLOBAL
    project: str | None = None
    status: Status = Status.CANDIDATE
    collection: str | None = None


def engine_input(lib: Library, req: ImageRequest) -> tuple[EngineInput, dict[str, Any]]:
    """Validate the request and resolve references/art direction into the engine input and the job record."""
    project = normalize_optional_slug(req.project, "project")
    direction = art.resolve(lib, project=project, style=req.style, art=req.art, use_profile=req.use_profile)
    style = direction.style
    validate_scope(req.scope, project, style)
    if req.key and req.n > 1:
        raise InvalidInputError("--key needs n=1; give the approved candidate a key later with `update`")
    normalize_key(req.key)
    size = parse_size(req.size)
    asset_type = AssetType.SPRITESHEET if req.sheet and req.type is AssetType.IMAGE else req.type
    aspect = check_aspect(req.aspect) or DEFAULT_ASPECT.get(asset_type) or _sheet_aspect(req.sheet) or "1:1"
    references = [resolve_reference(lib, r, "user") for r in req.references]
    style_refs = [resolve_reference(lib, r, "style") for r in direction.direction.references]
    transparent = req.transparent if req.transparent is not None else asset_type in TRANSPARENT_BY_DEFAULT
    quality = "high" if req.quality == "auto" and direction.direction.detail == "high" else req.quality
    negative = req.negative
    inp = EngineInput(
        operation="generate", prompt=req.prompt, asset_type=asset_type, transparent=transparent, n=req.n,
        aspect=aspect, size=size, quality=quality, output_format=req.format, provider=req.provider,
        model=req.model, fallback=req.fallback, retries=req.retries, seed=req.seed, negative=negative,
        raw_prompt=req.raw_prompt, style_name=style, references=references, style_references=style_refs,
        max_cost=req.max_cost, project=project, art_text=direction.text(), palette=direction.direction.palette,
        sheet=req.sheet, purpose=req.purpose,
    )  # fmt: skip
    record = {
        "operation": "generate",
        "request": req.model_dump(mode="json"),
        "asset_type": asset_type.value,
        "transparent": transparent,
        "scope": req.scope.value,
        "project": project,
        "style": style,
        "category": req.category,
        "subcategory": req.subcategory,
        "theme": req.theme or direction.direction.theme,
        "genre": req.genre or direction.direction.genre,
        "art": direction.direction.model_dump(exclude_defaults=True),
        "art_layers": direction.layers,
        "tags": normalize_tags([asset_type.value, *([style] if style else []), *req.tags]),
        "references": [r.__dict__ for r in references + style_refs],
        "style_profile_updated_at": direction.style_profile.updated_at if direction.style_profile else None,
        "art_profile_updated_at": direction.profile_updated_at,
    }
    return inp, record


def _sheet_aspect(sheet: SheetSpec | None) -> str | None:
    """The supported aspect closest to the sheet grid (square cells)."""
    if sheet is None:
        return None
    from assetforge.image.providers.base import nearest_aspect

    return nearest_aspect(sheet.columns, sheet.rows)


def fingerprint(record: dict[str, Any]) -> str:
    request = {k: v for k, v in record["request"].items()
               if k not in ("retries", "new_variant", "dry_run", "collection", "status", "fallback", "max_cost")}  # fmt: skip
    return jobs.fingerprint({
        "engine": ENGINE, "operation": record["operation"], "request": request,
        "references": sorted(r["sha256"] for r in record["references"]),
        "style_profile_updated_at": record.get("style_profile_updated_at"),
        "prompt_version": PROMPT_VERSION, "art": record.get("art"),
    })  # fmt: skip


def plan_only(inp: EngineInput, settings: Settings, lib: Library, record: dict[str, Any] | None = None) -> ImageResult:
    """--dry-run: show which provider/model and exact prompt would be used, without calling anything."""
    provider = provider_chain(inp.provider, settings, fallback=inp.fallback)[0]
    model = inp.model or provider.default_model(inp.operation)
    prepared = prepare_call(provider, model, inp, lib.paths.tmp)
    from assetforge.core import metering

    estimate, _ = metering.estimate_call(lib, provider, prepared.call)
    return ImageResult(
        job_id=None, reused=False, dry_run=True, provider=provider.name, model=model,
        prompt=prepared.prompt.final, transparency=prepared.transparency,
        estimated_cost_usd=estimate, art=(record or {}).get("art"), art_layers=(record or {}).get("art_layers") or [],
    )  # fmt: skip


def generate_image(
    lib: Library, req: ImageRequest, settings: Settings | None = None, *, on_job: Callable[[str], None] | None = None
) -> ImageResult:
    settings = settings or settings_for(lib)
    inp, record = engine_input(lib, req)
    if req.dry_run:
        return plan_only(inp, settings, lib, record)
    fp = fingerprint(record)
    if not req.new_variant and (done := jobs.find_completed(lib, "image", fp)):
        return result_from_job(lib, done)
    job_id = jobs.create_job(lib, "image", record, fingerprint=fp, provider=req.provider, model=req.model)
    if on_job:
        on_job(job_id)
    return execute(lib, job_id, inp, record, settings)
