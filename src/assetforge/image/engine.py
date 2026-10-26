"""Shared machinery for image generation and editing.

Lifecycle of one request (one job):
1. resolve references/style, fingerprint the request, reuse a finished identical job unless asked not to
2. create the job record, then try providers in order with retries (rate limits, outages) and fallback
3. write the provider's raw bytes + response.json into jobs/<job_id>/ *before* touching the catalog,
   so a crash after a paid call never loses the images (`jobs resume` picks them up)
4. finalize: key out backgrounds if needed, QC, convert formats, register assets/variants and lineage
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, Field

from assetforge.config import Settings, load_settings
from assetforge.core import budget, jobs, limits, metering, pricing
from assetforge.core import collections as collections_mod
from assetforge.core.catalog import Library
from assetforge.core.metadata import FileKind
from assetforge.core.models import (
    AssetType,
    CommercialUse,
    LineageRelation,
    NewAsset,
    Origin,
    Scope,
    Status,
    StyleProfile,
)
from assetforge.core.storage import hash_file
from assetforge.core.util import normalize_name
from assetforge.errors import AssetForgeError, InvalidInputError, ProviderError
from assetforge.image.prompts import PromptParts, SheetSpec, build_prompt
from assetforge.image.providers import provider_chain
from assetforge.image.providers.base import ASPECTS, ImageProvider, ProviderCall, ProviderResponse, nearest_aspect
from assetforge.image.transparency import remove_background
from assetforge.validation.image import analyze_image

ENGINE = "ai-image"
RETRY_BACKOFF = (2.0, 6.0, 15.0, 30.0, 60.0)
_SIZE_RE = re.compile(r"^(\d{2,5})x(\d{2,5})$")

sleep: Callable[[float], None] = time.sleep  # replaced in tests


# ---------------------------------------------------------------------- inputs


@dataclass
class Reference:
    path: str
    sha256: str
    asset_id: str | None
    role: Literal["source", "user", "style"]


def resolve_reference(lib: Library, ref: str, role: Literal["source", "user", "style"]) -> Reference:
    candidate = Path(ref).expanduser()
    if candidate.is_file():
        return Reference(str(candidate.resolve()), hash_file(candidate), None, role)
    asset = lib.get_asset(ref)
    variant = next((v for v in asset.variants if v.id == ref), asset.primary_variant)
    if variant is None or variant.file.kind != FileKind.IMAGE:
        raise InvalidInputError(f"{ref} is not an image asset", ref=ref)
    return Reference(variant.file.path, variant.file.sha256, asset.id, role)


def parse_size(size: str | None) -> tuple[int, int] | None:
    if not size:
        return None
    match = _SIZE_RE.match(size.strip().lower())
    if not match:
        raise InvalidInputError(f"Size must look like 1024x1024, got {size!r}")
    return int(match.group(1)), int(match.group(2))


def check_aspect(aspect: str | None) -> str | None:
    if aspect is not None and aspect not in ASPECTS:
        raise InvalidInputError(f"Unsupported aspect {aspect!r}", allowed=list(ASPECTS))
    return aspect


@dataclass
class EngineInput:
    operation: Literal["generate", "edit"]
    prompt: str
    asset_type: AssetType
    transparent: bool
    n: int
    aspect: str
    size: tuple[int, int] | None
    quality: str
    output_format: str
    provider: str
    model: str | None
    fallback: bool
    retries: int
    seed: int | None = None
    negative: str | None = None
    raw_prompt: bool = False
    style_name: str | None = None
    profile: StyleProfile | None = None
    source: Reference | None = None
    references: list[Reference] = field(default_factory=list)
    style_references: list[Reference] = field(default_factory=list)
    mask: str | None = None
    max_cost: float | None = None
    project: str | None = None
    art_text: str = ""  # rendered art direction (preset + project profile + request)
    palette: list[str] = field(default_factory=list)
    sheet: SheetSpec | None = None
    purpose: str | None = None


# ---------------------------------------------------------------------- results


class ImageCandidate(BaseModel):
    asset_id: str
    variant_id: str  # the primary (game-ready) variant
    original_variant_id: str  # exactly what the provider returned
    path: str
    width: int
    height: int
    has_alpha: bool
    qc: dict[str, Any]
    revised_prompt: str | None = None


class ImageResult(BaseModel):
    job_id: str | None
    reused: bool
    dry_run: bool = False
    provider: str
    model: str
    prompt: str
    transparency: Literal["native", "keyed", "none"]
    cost_usd: float | None = None
    estimated_cost_usd: float | None = None
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    assets: list[ImageCandidate] = Field(default_factory=list)
    art: dict[str, Any] | None = None  # the merged art direction (dry runs)
    art_layers: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------- provider calls


@dataclass
class Prepared:
    call: ProviderCall
    prompt: PromptParts
    transparency: Literal["native", "keyed", "none"]


def prepare_call(provider: ImageProvider, model: str, inp: EngineInput, tmp: Path) -> Prepared:
    caps = provider.capabilities(model)
    if inp.mask and not caps.supports_mask:
        raise ProviderError(f"{provider.name} does not support edit masks", provider=provider.name, kind="unsupported")
    required = ([inp.source] if inp.source else []) + inp.references
    if len(required) > caps.max_references:
        raise ProviderError(
            f"{provider.name}/{model} accepts at most {caps.max_references} input images, got {len(required)}",
            provider=provider.name, kind="unsupported",
        )  # fmt: skip
    style_refs = inp.style_references[: caps.max_references - len(required)]
    native = inp.transparent and caps.native_transparency
    parts = build_prompt(
        inp.prompt, asset_type=inp.asset_type, transparent=inp.transparent,
        native_transparency=caps.native_transparency, art_text=inp.art_text, palette=inp.palette,
        style_name=inp.style_name, profile=inp.profile, user_refs=len(inp.references), style_refs=len(style_refs),
        negative=inp.negative, raw=inp.raw_prompt, edit=inp.operation == "edit", sheet=inp.sheet,
        purpose=inp.purpose,
    )  # fmt: skip
    images = [Path(r.path) for r in [*required, *style_refs]]
    if inp.source and parts.key_colour:
        images[0] = _flatten_onto(Path(inp.source.path), parts.key_colour, tmp)
    aspect = inp.aspect if inp.aspect in caps.aspects else nearest_aspect(*ASPECTS[inp.aspect], list(caps.aspects))
    call = ProviderCall(
        operation=inp.operation, model=model, prompt=parts.final, n=inp.n, aspect=aspect, size=inp.size,
        quality=inp.quality, transparent=native, output_format=inp.output_format, images=images,
        mask=Path(inp.mask) if inp.mask else None, seed=inp.seed if caps.supports_seed else None,
    )  # fmt: skip
    method: Literal["native", "keyed", "none"] = "native" if native else "keyed" if inp.transparent else "none"
    return Prepared(call, parts, method)


def _flatten_onto(path: Path, key_hex: str, tmp: Path) -> Path:
    """Providers without alpha see transparent pixels as black/white; give them the key colour instead."""
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
    backdrop = Image.new("RGBA", rgba.size, key_hex)
    backdrop.alpha_composite(rgba)
    out = tmp / f"flattened-{path.stem}.png"
    backdrop.convert("RGB").save(out)
    return out


@dataclass
class Outcome:
    response: ProviderResponse
    prepared: Prepared
    attempts: list[dict[str, Any]]


def run_chain(
    inp: EngineInput,
    settings: Settings,
    tmp: Path,
    *,
    on_attempt: Callable[[list[dict[str, Any]]], None],
    lib: Library | None = None,
    job_id: str | None = None,
) -> Outcome:
    chain = provider_chain(inp.provider, settings, fallback=inp.fallback)
    attempts: list[dict[str, Any]] = []
    errors: list[ProviderError] = []
    last: ProviderError | None = None
    for position, provider in enumerate(chain):
        model = inp.model if inp.model and position == 0 else provider.default_model(inp.operation)
        for attempt in range(inp.retries + 1):
            try:
                provider.require_configured()
                prepared = prepare_call(provider, model, inp, tmp)
                if lib is not None and job_id is not None:
                    # Shared across agents: reserve budget, respect the provider's rate and concurrency limits.
                    estimate, _basis = metering.estimate_call(lib, provider, prepared.call)
                    budget.reserve(lib, job_id, estimate, max_cost=inp.max_cost)
                    limits.take_token(lib, provider.name)
                    with limits.provider_slot(lib, provider.name):
                        response = provider.run(prepared.call)
                    # Meter tokens and price them with current rates (replaces the adapter's rough figure).
                    usage = metering.record_call(
                        lib, job_id=job_id, operation=inp.operation, provider=provider.name, model=response.model,
                        raw_usage=response.usage, images=len(response.images), input_images=len(prepared.call.images),
                        quality=inp.quality, size=provider.size_key(prepared.call), estimated_cost=estimate,
                        fallback_cost=response.cost_usd, project=inp.project,
                    )  # fmt: skip
                    response.cost_usd = usage["cost_usd"]
                else:
                    response = provider.run(prepared.call)
                return Outcome(response, prepared, attempts)
            except ProviderError as exc:
                last = exc
                errors.append(exc)
                attempts.append({"provider": provider.name, "model": model, "attempt": attempt + 1,
                                 "code": exc.code, "kind": exc.kind, "message": exc.message})  # fmt: skip
                on_attempt(attempts)
                if exc.retryable and attempt < inp.retries:
                    sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue
                break
        assert last is not None
        if not last.allows_fallback:
            break
    assert last is not None
    # Report the most useful failure: a retryable one (so queues retry later), else the primary provider's.
    # A fallback that merely isn't configured shouldn't hide why the preferred provider failed.
    primary = [e for e in errors if e.provider == chain[0].name]
    chosen = next((e for e in reversed(errors) if e.retryable), primary[-1] if primary else last)
    chosen.details["attempts"] = attempts
    raise chosen


# ---------------------------------------------------------------------- persistence + registration


def job_dir(lib: Library, job_id: str) -> Path:
    return lib.paths.jobs / job_id


def persist_outputs(lib: Library, job_id: str, outcome: Outcome) -> Path:
    """Save provider bytes and response facts next to the job before cataloging anything."""
    directory = job_dir(lib, job_id)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = []
    for i, image in enumerate(outcome.response.images):
        name = f"output_{i + 1}{image.ext}"
        (directory / name).write_bytes(image.data)
        outputs.append({"file": name, "mime_type": image.mime_type, "revised_prompt": image.revised_prompt})
    response = {
        "provider": outcome.response.provider,
        "model": outcome.response.model,
        "prompt": outcome.prepared.prompt.final,
        "key_colour": outcome.prepared.prompt.key_colour,
        "transparency": outcome.prepared.transparency,
        "provider_request": outcome.response.request,
        "usage": outcome.response.usage,
        "cost_usd": outcome.response.cost_usd,
        "attempts": outcome.attempts,
        "outputs": outputs,
    }
    path = directory / "response.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(response, indent=2, default=str))
    tmp.replace(path)
    return path


# Words that describe the look or the request rather than the subject; left out of automatic names.
NAME_FILLER = {
    "a", "an", "the", "of", "with", "and", "in", "on", "for", "to", "at", "its", "his", "her", "their", "very",
    "cute", "chunky", "small", "little", "big", "single", "game", "asset", "sprite", "art", "style", "styled",
    "2d", "3d", "cartoon", "illustration", "drawing", "image", "picture", "render", "rendered", "detailed",
    "simple", "friendly", "soft", "glossy", "isolated", "transparent", "background", "centered", "centred",
    "full", "body", "front", "view", "side", "high", "quality", "mobile", "casual", "one",
}  # fmt: skip


def subject_name(prompt: str, limit: int = 4) -> str:
    """'A cute chunky cartoon beetle enemy, front view' → 'beetle_enemy'."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", prompt.split(".")[0].split(",")[0].lower()) if w not in NAME_FILLER]
    if not words:
        words = [w for w in re.findall(r"[A-Za-z0-9]+", prompt.lower()) if w not in NAME_FILLER]
    return "_".join(words[:limit]) or "image"


def _candidate_name(record: dict[str, Any], index: int, total: int) -> str:
    request = record["request"]
    if request.get("name"):
        base = request["name"]
    elif record["operation"] == "edit":
        base = f"{record['source_name']}_edit"
    else:
        base = subject_name(request["prompt"])
    return normalize_name(f"{base}_{index}" if total > 1 else base)


def finalize(lib: Library, job_id: str, record: dict[str, Any]) -> ImageResult:
    """Turn the raw outputs saved for `job_id` into cataloged assets. Safe to run twice (content dedupe)."""
    directory = job_dir(lib, job_id)
    response = json.loads((directory / "response.json").read_text())
    request = record["request"]
    asset_type = AssetType(record["asset_type"])
    transparent = record["transparent"]
    method = response["transparency"]
    collection_id = (
        collections_mod.ensure_collection(lib, request["collection"], project=record.get("project")).id
        if request.get("collection") else None
    )  # fmt: skip
    references = [Reference(**r) for r in record["references"]]
    candidates: list[ImageCandidate] = []
    total = len(response["outputs"])
    tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="image-"))
    try:
        for index, output in enumerate(response["outputs"], start=1):
            raw = directory / output["file"]
            with Image.open(raw) as opened:
                image = opened.copy()
            processed, key_info = image, None
            if method == "keyed":
                keyed = remove_background(image, response.get("key_colour"))
                processed = keyed.image
                key_info = {"background_rgb": keyed.background_rgb, "removed_fraction": round(keyed.removed_fraction, 4),
                            "border_uniformity": round(keyed.border_uniformity, 2)}  # fmt: skip
            qc = analyze_image(processed, asset_type=asset_type, expect_transparency=transparent)
            generation = {
                "engine": ENGINE,
                "operation": record["operation"],
                "provider": response["provider"],
                "model": response["model"],
                "prompt": request["prompt"],
                "final_prompt": response["prompt"],
                "revised_prompt": output.get("revised_prompt"),
                "provider_request": response["provider_request"],
                "transparency": method,
                "key": key_info,
                "references": [asdict(r) for r in references],
                "style": record.get("style") or request.get("style"),
                "art": record.get("art"),
                "art_layers": record.get("art_layers"),
                "seed": request.get("seed"),
                "candidate": index,
                "job_id": job_id,
                "cost_usd": round(response["cost_usd"] / total, 4) if response.get("cost_usd") is not None else None,
                "qc": qc,
            }
            if record["operation"] == "edit" and request.get("as_variant"):
                candidates.append(_add_as_variant(lib, record, raw, processed, method, generation, tmp))
                continue
            spec = NewAsset(
                name=_candidate_name(record, index, total),
                type=asset_type,
                key=request.get("key") if total == 1 else None,
                description=request.get("description") or request["prompt"],
                scope=Scope(record["scope"]),
                project=record.get("project"),
                style=record.get("style"),
                category=record.get("category"),
                subcategory=record.get("subcategory"),
                theme=record.get("theme"),
                genre=record.get("genre"),
                status=Status(request.get("status") or Status.CANDIDATE),
                origin=Origin.AI,
                license=f"AI-generated ({response['provider']} {response['model']})",
                commercial_use=CommercialUse.ALLOWED,
                tags=[*record["tags"], "ai-generated", response["provider"]],
                metadata={"generator": generation, "expect": {k: v for k, v in {
                    "size": request.get("size"), "aspect": request.get("aspect"),
                    "format": request.get("format") if request.get("format") != "png" else None,
                    "transparent": transparent}.items() if v is not None}},
            )  # fmt: skip
            reg = lib.register_file(raw, spec, label="original", params=generation, collection_id=collection_id)
            primary_id = reg.variant_id
            if reg.created and method == "keyed":
                keyed_path = tmp / f"{job_id}_{index}_transparent.png"
                processed.save(keyed_path, optimize=True)
                primary_id = lib.add_variant(reg.asset_id, keyed_path, label="transparent", parent_variant_id=reg.variant_id,
                                             params={"method": "key", **(key_info or {})}, make_primary=True).id  # fmt: skip
            if reg.created and request.get("format") == "webp" and not raw.suffix == ".webp":
                webp_path = tmp / f"{job_id}_{index}.webp"
                processed.save(webp_path, format="WEBP", lossless=True)
                primary_id = lib.add_variant(reg.asset_id, webp_path, label="webp", parent_variant_id=primary_id,
                                             params={"format": "webp", "lossless": True}, make_primary=True).id  # fmt: skip
            if reg.created:
                for ref in references:
                    if ref.asset_id and ref.asset_id != reg.asset_id:
                        relation = LineageRelation.EDITED_FROM if ref.role == "source" else LineageRelation.DERIVED_FROM
                        lib.add_lineage(reg.asset_id, ref.asset_id, relation)
            if reg.created:
                from assetforge.core.qc import check_asset

                full = check_asset(lib, reg.asset_id)  # adds requested-size, near-duplicate… warnings
                qc = {**qc, "issues": list(dict.fromkeys([*qc["issues"], *full["errors"], *full["warnings"]])),
                      **({"near_duplicates": full["near_duplicates"]} if full.get("near_duplicates") else {})}  # fmt: skip
            asset = lib.get_asset(reg.asset_id)
            primary = asset.primary_variant
            assert primary is not None
            candidates.append(
                ImageCandidate(
                    asset_id=asset.id, variant_id=primary.id, original_variant_id=reg.variant_id, path=primary.file.path,
                    width=primary.file.width or 0, height=primary.file.height or 0, has_alpha=bool(primary.file.has_alpha),
                    qc=qc, revised_prompt=output.get("revised_prompt"),
                )  # fmt: skip
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ImageResult(
        job_id=job_id, reused=False, provider=response["provider"], model=response["model"], prompt=response["prompt"],
        transparency=method, cost_usd=response.get("cost_usd"), attempts=response.get("attempts", []),
        assets=candidates,
    )  # fmt: skip


def _add_as_variant(
    lib: Library, record: dict[str, Any], raw: Path, processed: Image.Image, method: str,
    generation: dict[str, Any], tmp: Path,
) -> ImageCandidate:  # fmt: skip
    """`image edit --as-variant`: keep the edit on the source asset as edit_N (+ edit_N_transparent)."""
    source_id = record["source_asset_id"]
    asset = lib.get_asset(source_id)
    taken = [int(m.group(1)) for v in asset.variants if (m := re.fullmatch(r"edit_(\d+)", v.label))]
    label = f"edit_{max(taken, default=0) + 1}"
    raw_variant = lib.add_variant(source_id, raw, label=label, params=generation)
    variant = raw_variant
    if method == "keyed":
        keyed_path = tmp / f"{label}_transparent.png"
        processed.save(keyed_path, optimize=True)
        variant = lib.add_variant(source_id, keyed_path, label=f"{label}_transparent",
                                  parent_variant_id=raw_variant.id, params={"method": "key"})  # fmt: skip
    return ImageCandidate(
        asset_id=source_id, variant_id=variant.id, original_variant_id=raw_variant.id, path=variant.file.path,
        width=variant.file.width or 0, height=variant.file.height or 0, has_alpha=bool(variant.file.has_alpha),
        qc=generation["qc"], revised_prompt=generation.get("revised_prompt"),
    )  # fmt: skip


def result_from_job(lib: Library, job: Any) -> ImageResult:
    """Rebuild a result for a finished job (fingerprint reuse, or `jobs resume` on a done job)."""
    candidates = []
    ids = zip(job.result.get("asset_ids", []), job.result.get("variant_ids", []),
              job.result.get("original_variant_ids", []), strict=False)  # fmt: skip
    for asset_id, variant_id, original_id in ids:
        try:
            asset = lib.get_asset(asset_id)
        except AssetForgeError:
            continue
        variant = next((v for v in asset.variants if v.id == variant_id), asset.primary_variant)
        if variant is None:
            continue
        candidates.append(
            ImageCandidate(
                asset_id=asset.id, variant_id=variant.id,
                original_variant_id=original_id,
                path=variant.file.path, width=variant.file.width or 0, height=variant.file.height or 0,
                has_alpha=bool(variant.file.has_alpha), qc=asset.metadata.get("generator", {}).get("qc", {}),
            )  # fmt: skip
        )
    return ImageResult(
        job_id=job.id, reused=True, provider=job.provider or "", model=job.model or "",
        prompt=job.result.get("prompt", ""), transparency=job.result.get("transparency", "none"),
        cost_usd=0.0, assets=candidates,
    )  # fmt: skip


def execute(lib: Library, job_id: str, inp: EngineInput, record: dict[str, Any], settings: Settings) -> ImageResult:
    """Run providers for an existing job, persist outputs, finalize and close the job."""
    pricing.ensure_fresh(lib)  # refresh model prices from the internet if stale (never fails the job)
    tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="image-call-"))
    try:
        try:
            outcome = run_chain(
                inp, settings, tmp, lib=lib, job_id=job_id,
                on_attempt=lambda attempts: jobs.update_job(lib, job_id, attempts=len(attempts) + 1,
                                                           error={"attempts": attempts}),
            )  # fmt: skip
        except AssetForgeError as exc:
            jobs.fail_job(lib, job_id, exc.to_dict())
            raise
        jobs.update_job(lib, job_id, provider=outcome.response.provider, model=outcome.response.model,
                        cost_usd=outcome.response.cost_usd)  # fmt: skip
        persist_outputs(lib, job_id, outcome)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return close_job(lib, job_id, record)


def close_job(lib: Library, job_id: str, record: dict[str, Any]) -> ImageResult:
    try:
        result = finalize(lib, job_id, record)
    except AssetForgeError as exc:
        jobs.fail_job(lib, job_id, {**exc.to_dict(), "stage": "finalize", "outputs_saved": True})
        raise
    except Exception as exc:
        jobs.fail_job(lib, job_id, {"code": "ERROR", "message": str(exc), "stage": "finalize", "outputs_saved": True})
        raise
    jobs.complete_job(
        lib, job_id, asset_id=result.assets[0].asset_id if result.assets else None,  # type: ignore[arg-type]
        result={
            "asset_ids": [c.asset_id for c in result.assets],
            "variant_ids": [c.variant_id for c in result.assets],
            "original_variant_ids": [c.original_variant_id for c in result.assets],
            "prompt": result.prompt,
            "transparency": result.transparency,
        },
        cost_usd=result.cost_usd,
    )  # fmt: skip
    return result


def settings_for(lib: Library) -> Settings:
    return load_settings(lib.paths.home)
