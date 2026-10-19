"""Image edits: revise an existing image (plus optional references/mask) without touching the original."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.config import Settings
from assetforge.core import jobs
from assetforge.core.catalog import Library, normalize_tags
from assetforge.core.models import Status
from assetforge.core.styles import find_style
from assetforge.errors import InvalidInputError
from assetforge.image.engine import (
    EngineInput,
    ImageResult,
    check_aspect,
    execute,
    parse_size,
    resolve_reference,
    result_from_job,
    settings_for,
)
from assetforge.image.generate import Quality, fingerprint, plan_only
from assetforge.image.providers.base import nearest_aspect


class EditRequest(BaseModel):
    """An edit of an existing image asset. Also the schema of `image edit --file request.json`."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="Asset ID, variant ID, key or image path to edit")
    prompt: str = Field(min_length=3, description="What to change")
    references: list[str] = Field(default_factory=list)
    mask: str | None = Field(None, description="PNG whose transparent pixels mark the area to edit")
    provider: str = "auto"
    model: str | None = None
    fallback: bool = True
    n: int = Field(1, ge=1, le=8)
    aspect: str | None = None
    size: str | None = None
    quality: Quality = "auto"
    transparent: bool | None = Field(None, description="Default: keep transparency if the source has it")
    format: Literal["png", "webp"] = "png"
    negative: str | None = None
    raw_prompt: bool = False
    seed: int | None = None
    retries: int = Field(2, ge=0, le=5)
    max_cost: float | None = Field(None, ge=0, description="Refuse calls estimated above this many USD")
    new_variant: bool = False
    dry_run: bool = False
    as_variant: bool = Field(False, description="Store the edit as a variant of the source instead of a new asset")
    # catalog (defaults inherit from the source asset)
    name: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: Status = Status.CANDIDATE
    collection: str | None = None


def engine_input(lib: Library, req: EditRequest) -> tuple[EngineInput, dict[str, Any]]:
    source = resolve_reference(lib, req.source, "source")
    if req.as_variant and source.asset_id is None:
        raise InvalidInputError("--as-variant needs a cataloged source asset, not a file path")
    asset = lib.get_asset(source.asset_id) if source.asset_id else None
    references = [resolve_reference(lib, r, "user") for r in req.references]
    mask = None
    if req.mask:
        mask_path = Path(req.mask).expanduser()
        if not mask_path.is_file():
            raise InvalidInputError(f"Mask not found: {req.mask}")
        mask = str(mask_path.resolve())

    from PIL import Image

    with Image.open(source.path) as image:
        width, height = image.size
        source_alpha = image.mode in ("RGBA", "LA") and image.getchannel("A").getextrema()[0] < 255
    size = parse_size(req.size)
    aspect = check_aspect(req.aspect) or nearest_aspect(width, height)
    style = asset.style if asset else None
    profile = find_style(lib, style) if style else None
    transparent = req.transparent if req.transparent is not None else bool(source_alpha)
    asset_type = asset.type if asset else "image"
    inp = EngineInput(
        operation="edit", prompt=req.prompt, asset_type=asset_type, transparent=transparent, n=req.n,  # type: ignore[arg-type]
        aspect=aspect, size=size, quality=req.quality, output_format=req.format, provider=req.provider,
        model=req.model, fallback=req.fallback, retries=req.retries, seed=req.seed, negative=req.negative,
        raw_prompt=req.raw_prompt, style_name=style, profile=profile, source=source, references=references,
        mask=mask, max_cost=req.max_cost, project=asset.project if asset else None,
    )  # fmt: skip
    record = {
        "operation": "edit",
        "request": req.model_dump(mode="json"),
        "asset_type": str(asset_type),
        "transparent": transparent,
        "scope": asset.scope.value if asset else "global",
        "project": asset.project if asset else None,
        "style": style,
        "tags": normalize_tags([*(asset.tags if asset else []), "edited", *req.tags]),
        "references": [r.__dict__ for r in [source, *references]],
        "source_asset_id": source.asset_id,
        "source_name": asset.name if asset else Path(source.path).stem,
        "style_profile_updated_at": profile.updated_at if profile else None,
    }
    return inp, record


def edit_image(lib: Library, req: EditRequest, settings: Settings | None = None) -> ImageResult:
    settings = settings or settings_for(lib)
    inp, record = engine_input(lib, req)
    if req.dry_run:
        return plan_only(inp, settings, lib)
    fp = fingerprint(record)
    if not req.new_variant and not req.as_variant and (done := jobs.find_completed(lib, "image_edit", fp)):
        return result_from_job(lib, done)
    job_id = jobs.create_job(lib, "image_edit", record, fingerprint=fp, provider=req.provider, model=req.model)
    return execute(lib, job_id, inp, record, settings)
