"""Procedural SFX generation: request -> rendered variants -> QC -> cataloged asset.

One request produces one logical asset whose variants v1..vN are seeded variations of the
same recipe. Each master is a 16-bit WAV ("v1"). Other formats are stored as derived
variants ("v1_ogg") and become the primary file. Every master variant stores everything
needed to re-render it bit-for-bit: recipe, version, params, effects, noise seed.
"""

from __future__ import annotations

import re
import secrets
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from assetforge.audio import synth
from assetforge.audio.effects import apply_effects, resolve_effects
from assetforge.audio.export import finish, pcm_sha256, to_pcm16, write_audio
from assetforge.audio.recipes import Recipe, get_recipe, render_recipe, resolve_params
from assetforge.audio.variations import VariantPlan, plan_variants
from assetforge.core import collections as collections_mod
from assetforge.core import jobs
from assetforge.core.catalog import Library
from assetforge.core.models import (
    AssetSummary,
    CommercialUse,
    LineageRelation,
    NewAsset,
    Origin,
    Scope,
    Status,
)
from assetforge.errors import AssetForgeError, ConflictError, InvalidInputError
from assetforge.validation.audio import analyze

ENGINE = "assetforge-synth"
ENGINE_VERSION = 1
SAMPLE_RATES = (22050, 32000, 44100, 48000)
AudioFormat = Literal["wav", "ogg", "mp3"]
GENERATED_LICENSE = "AssetForge procedural (original work)"


class AudioRequest(BaseModel):
    """A procedural SFX request. Also the schema of `audio generate --file request.json`."""

    model_config = ConfigDict(extra="forbid")

    recipe: str | None = None
    preset: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    duration: float | None = Field(None, gt=0, description="Seconds; shorthand for params.duration")
    duration_ms: int | None = Field(None, gt=0)
    effects: list[dict[str, Any] | str] = Field(default_factory=list)
    variants: int = Field(1, ge=1, le=32)
    variation: float = Field(0.3, ge=0.0, le=1.0)
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    format: AudioFormat = "wav"
    sample_rate: int = 44100
    peak_db: float = Field(-1.0, ge=-24.0, le=0.0)
    new_variant: bool = Field(
        False, description="Make a fresh take: jitter every variant (even v1) and skip request reuse"
    )
    # catalog fields
    name: str | None = None
    key: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    scope: Scope = Scope.GLOBAL
    project: str | None = None
    style: str | None = None
    status: Status = Status.CANDIDATE
    collection: str | None = None

    @field_validator("sample_rate")
    @classmethod
    def _known_rate(cls, value: int) -> int:
        if value not in SAMPLE_RATES:
            raise ValueError(f"sample_rate must be one of {SAMPLE_RATES}")
        return value


class GeneratedVariant(BaseModel):
    index: int
    label: str
    variant_id: str
    master_variant_id: str
    path: str
    format: str
    duration_ms: int | None
    render_seed: int
    params: dict[str, Any]
    qc: dict[str, Any]
    alternates: dict[str, str] = Field(default_factory=dict, description="Other delivery formats: format → path")


def delivery_alternates(fmt: str) -> list[str]:
    """Extra delivery copies made next to the requested one: M4A whenever a compressed format is requested."""
    from assetforge.audio.codecs import skip_unavailable

    return skip_unavailable(["m4a"]) if fmt in ("ogg", "mp3") else []


class AudioResult(BaseModel):
    asset_id: str
    reused: bool
    reuse_reason: Literal["fingerprint", "identical_content"] | None = None
    job_id: str | None = None
    format: str
    asset: AssetSummary
    variants: list[GeneratedVariant]


@dataclass(frozen=True)
class Plan:
    recipe: Recipe
    preset: str | None
    base_params: dict[str, Any]
    effects: list[dict[str, Any]]
    seed: int
    seeded_by_caller: bool
    variation: float
    sample_rate: int
    peak_db: float
    format: str

    def fingerprint(self, variants: int) -> str:
        return jobs.fingerprint(
            {
                "engine": ENGINE,
                "engine_version": ENGINE_VERSION,
                "recipe": self.recipe.name,
                "recipe_version": self.recipe.version,
                "params": self.base_params,
                "effects": self.effects,
                "seed": self.seed,
                "variants": variants,
                "variation": self.variation,
                "sample_rate": self.sample_rate,
                "peak_db": self.peak_db,
                "format": self.format,
            }
        )


@dataclass
class Rendered:
    plan: VariantPlan
    pcm: np.ndarray
    qc: dict[str, Any]


# ---------------------------------------------------------------------- rendering


def apply_brightness(x: np.ndarray, brightness: float, sr: int) -> np.ndarray:
    if brightness < 0:
        return synth.lowpass(x, 16000 * 2 ** (brightness * 5), sr)
    if brightness > 0:
        return synth.highpass(x, 30 * 2 ** (brightness * 6), sr)
    return x


def render_variant(
    recipe: Recipe, params: dict[str, Any], effects: list[dict[str, Any]], render_seed: int, sr: int, peak_db: float
) -> tuple[np.ndarray, dict[str, Any]]:
    """Deterministic: identical inputs always give identical 16-bit PCM."""
    x = render_recipe(recipe, params, sr, render_seed)
    x = apply_brightness(x, float(params["brightness"]), sr)
    x = apply_effects(x, effects, sr)
    pcm = to_pcm16(finish(x, sr, peak_db=peak_db))
    return pcm, analyze(pcm.astype(np.float64) / 32767.0, sr)


# ---------------------------------------------------------------------- planning


def generator_meta(asset_metadata: dict[str, Any], ref: str) -> dict[str, Any]:
    gen = asset_metadata.get("generator")
    if not gen or gen.get("engine") != ENGINE:
        raise InvalidInputError(f"{ref} was not generated by the procedural audio engine", ref=ref)
    return gen


def plan_request(req: AudioRequest, source: dict[str, Any] | None = None) -> Plan:
    """Resolve recipe, preset, params and effects. `source` is a previous sound's generator metadata."""
    if source is None and not req.recipe:
        raise InvalidInputError("A recipe is required (see `assetforge audio recipes`)")
    recipe = get_recipe(source["recipe"] if source else req.recipe)
    if source is not None and req.recipe and get_recipe(req.recipe).name != recipe.name:
        raise InvalidInputError("--from keeps the source's recipe; omit --recipe or use the same one")
    overrides = dict(req.params)
    if req.duration is not None:
        overrides["duration"] = req.duration
    if req.duration_ms is not None:
        overrides["duration"] = req.duration_ms / 1000
    params, preset_effects = resolve_params(
        recipe, preset=req.preset, overrides=overrides, base=source["base_params"] if source else None
    )
    if req.effects:
        effects = resolve_effects(req.effects)
    elif req.preset or source is None:
        effects = resolve_effects(preset_effects)
    else:
        effects = source["effects"]
    seeded = req.seed is not None
    return Plan(
        recipe=recipe,
        preset=req.preset or (source or {}).get("preset"),
        base_params=params,
        effects=effects,
        seed=req.seed if seeded else secrets.randbelow(2**31),
        seeded_by_caller=seeded,
        variation=req.variation,
        sample_rate=req.sample_rate,
        peak_db=req.peak_db,
        format=req.format,
    )


def _render_all(plan: Plan, variant_plans: list[VariantPlan]) -> list[Rendered]:
    rendered = []
    for vp in variant_plans:
        pcm, qc = render_variant(plan.recipe, vp.params, plan.effects, vp.render_seed, plan.sample_rate, plan.peak_db)
        if not qc["ok"]:
            raise InvalidInputError(
                f"Variant {vp.index} of {plan.recipe.name} failed QC: {', '.join(qc['issues'])}",
                params=vp.params,
                qc=qc,
            )
        rendered.append(Rendered(vp, pcm, qc))
    return rendered


def _master_params(plan: Plan, r: Rendered) -> dict[str, Any]:
    return {
        "generator": ENGINE,
        "engine_version": ENGINE_VERSION,
        "recipe": plan.recipe.name,
        "recipe_version": plan.recipe.version,
        "index": r.plan.index,
        "seed": plan.seed,
        "render_seed": r.plan.render_seed,
        "params": r.plan.params,
        "effects": plan.effects,
        "sample_rate": plan.sample_rate,
        "peak_db": plan.peak_db,
        "pcm_sha256": pcm_sha256(r.pcm),
        "qc": r.qc,
    }


def _ensure_variant(lib: Library, asset_id: str, label: str, write: Callable[[], Path], **kwargs: Any) -> str:
    """Add a variant unless that label already exists. Labels are deterministic per index, so a
    concurrent identical request may have added it a moment ago, which counts as done."""

    def lookup() -> str | None:
        row = lib.conn.execute(
            "SELECT id FROM asset_variants WHERE asset_id = ? AND label = ?", (asset_id, label)
        ).fetchone()
        return row["id"] if row else None

    if existing := lookup():
        return existing
    try:
        return lib.add_variant(asset_id, write(), label=label, **kwargs).id
    except ConflictError:
        if existing := lookup():
            return existing
        raise


def _add_rendered_variants(
    lib: Library, asset_id: str, plan: Plan, rendered: list[Rendered], tmp: Path, *, first_registered: str | None
) -> None:
    """Store masters (unless already registered) and encoded copies; the first encoded copy becomes primary."""
    for r in rendered:
        i = r.plan.index
        is_first = first_registered is not None and i == rendered[0].plan.index
        if is_first:
            master_id = first_registered
        else:
            master_id = _ensure_variant(
                lib, asset_id, f"v{i}", lambda r=r, i=i: write_audio(r.pcm, plan.sample_rate, tmp / f"v{i}.wav", "wav"),
                params=_master_params(plan, r),
            )  # fmt: skip
        if plan.format != "wav":
            _ensure_variant(
                lib, asset_id, f"v{i}_{plan.format}",
                lambda r=r, i=i: write_audio(r.pcm, plan.sample_rate, tmp / f"v{i}.{plan.format}", plan.format),
                parent_variant_id=master_id, params={"encoded_from": master_id, "format": plan.format},
                make_primary=is_first,
            )  # fmt: skip
            for alt in delivery_alternates(plan.format):  # M4A next to OGG: the Safari/iOS fallback
                _ensure_variant(
                    lib, asset_id, f"v{i}_{alt}",
                    lambda r=r, i=i, alt=alt: write_audio(r.pcm, plan.sample_rate, tmp / f"v{i}.{alt}", alt),
                    parent_variant_id=master_id, params={"encoded_from": master_id, "format": alt},
                )  # fmt: skip


def result_from_asset(
    lib: Library,
    asset_id: str,
    *,
    reused: bool,
    reuse_reason: str | None = None,
    job_id: str | None = None,
    indices: set[int] | None = None,
) -> AudioResult:
    asset = lib.get_asset(asset_id)
    gen = asset.metadata.get("generator", {})
    fmt = gen.get("format", "wav")
    encoded = {(v.params.get("encoded_from"), v.params.get("format")): v for v in asset.variants
               if v.params.get("encoded_from")}  # fmt: skip
    variants = []
    for master in asset.variants:
        if master.params.get("generator") != ENGINE:
            continue
        index = master.params["index"]
        if indices is not None and index not in indices:
            continue
        delivery = encoded.get((master.id, fmt), master)
        alternates = {f: v.file.path for (src, f), v in encoded.items() if src == master.id and f != fmt}
        variants.append(
            GeneratedVariant(
                index=index,
                label=delivery.label,
                variant_id=delivery.id,
                master_variant_id=master.id,
                path=delivery.file.path,
                format=delivery.file.ext.lstrip("."),
                duration_ms=delivery.file.duration_ms,
                render_seed=master.params["render_seed"],
                params=master.params["params"],
                qc=master.params["qc"],
                alternates=alternates,
            )
        )
    variants.sort(key=lambda v: v.index)
    summary = lib.summaries([asset_id])[0]
    return AudioResult(
        asset_id=asset_id, reused=reused, reuse_reason=reuse_reason, job_id=job_id, format=fmt,
        asset=summary, variants=variants,
    )  # fmt: skip


# ---------------------------------------------------------------------- public API


def generate_audio(lib: Library, req: AudioRequest, *, from_ref: str | None = None) -> AudioResult:
    """Generate a new procedural sound (optionally starting from an existing one's recipe settings)."""
    source_id = lib.resolve_asset_id(from_ref) if from_ref else None
    source = generator_meta(lib.get_asset(source_id).metadata, from_ref) if source_id else None
    plan = plan_request(req, source)
    fingerprint = plan.fingerprint(req.variants)
    if req.new_variant and plan.variation == 0:
        raise InvalidInputError("new_variant needs variation > 0 to produce a different sound")
    reusable = plan.seeded_by_caller and not req.new_variant
    if reusable and (done := jobs.find_completed(lib, "audio", fingerprint)):
        assert done.asset_id
        return result_from_asset(lib, done.asset_id, reused=True, reuse_reason="fingerprint", job_id=done.id)

    collection_id = (
        collections_mod.ensure_collection(lib, req.collection, project=req.project).id if req.collection else None
    )
    request_record = req.model_dump(mode="json") | {"from": source_id, "seed": plan.seed}
    job_id = jobs.create_job(
        lib, "audio", request_record, fingerprint=fingerprint, provider="procedural",
        model=f"{ENGINE}/{ENGINE_VERSION}",
    )  # fmt: skip
    try:
        variant_plans = plan_variants(
            plan.recipe, plan.base_params, seed=plan.seed, indices=range(1, req.variants + 1),
            variation=plan.variation, canonical_first=not req.new_variant,
        )  # fmt: skip
        rendered = _render_all(plan, variant_plans)
        result = _register_new(lib, req, plan, rendered, job_id, collection_id, source_id)
        jobs.complete_job(lib, job_id, asset_id=result.asset_id,
                          result={"reused": result.reused, "variants": [v.variant_id for v in result.variants]})  # fmt: skip
        return result.model_copy(update={"job_id": job_id})
    except AssetForgeError as exc:
        jobs.fail_job(lib, job_id, exc.to_dict())
        raise
    except Exception as exc:
        jobs.fail_job(lib, job_id, {"code": "ERROR", "message": str(exc)})
        raise


def _register_new(
    lib: Library,
    req: AudioRequest,
    plan: Plan,
    rendered: list[Rendered],
    job_id: str,
    collection_id: str | None,
    source_id: str | None,
) -> AudioResult:
    recipe = plan.recipe
    preset_suffix = f"_{plan.preset}" if plan.preset else ""
    description = req.description or recipe.description + (f" ({plan.preset})" if plan.preset else "")
    spec = NewAsset(
        name=req.name or f"{recipe.name}{preset_suffix}",
        type=recipe.type,
        key=req.key,
        description=description,
        scope=req.scope,
        project=req.project,
        style=req.style,
        status=req.status,
        origin=Origin.PROCEDURAL,
        license=GENERATED_LICENSE,
        commercial_use=CommercialUse.ALLOWED,
        tags=[*recipe.tags, *([plan.preset] if plan.preset else []), "procedural", *req.tags],
        metadata={
            "generator": {
                "engine": ENGINE,
                "engine_version": ENGINE_VERSION,
                "recipe": recipe.name,
                "recipe_version": recipe.version,
                "preset": plan.preset,
                "base_params": plan.base_params,
                "effects": plan.effects,
                "seed": plan.seed,
                "variation": plan.variation,
                "sample_rate": plan.sample_rate,
                "peak_db": plan.peak_db,
                "format": plan.format,
                "job_id": job_id,
                "derived_from": source_id,
            }
        },
    )
    tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="audio-"))
    try:
        first = rendered[0]
        master = write_audio(first.pcm, plan.sample_rate, tmp / f"{recipe.name}_v1.wav", "wav")
        reg = lib.register_file(
            master, spec, label=f"v{first.plan.index}", params=_master_params(plan, first),
            collection_id=collection_id,
        )  # fmt: skip
        if not reg.created:
            # Same seed and base params rendered before: top up any variants the earlier request didn't ask for.
            existing = lib.get_asset(reg.asset_id)
            if existing.metadata.get("generator", {}).get("recipe") == recipe.name:
                _add_rendered_variants(lib, reg.asset_id, plan, rendered, tmp, first_registered=reg.variant_id)
            return result_from_asset(lib, reg.asset_id, reused=True, reuse_reason="identical_content")
        _add_rendered_variants(lib, reg.asset_id, plan, rendered, tmp, first_registered=reg.variant_id)
        if source_id:
            lib.add_lineage(reg.asset_id, source_id, LineageRelation.DERIVED_FROM)
        return result_from_asset(lib, reg.asset_id, reused=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def vary_audio(
    lib: Library,
    ref: str,
    *,
    variants: int = 1,
    variation: float | None = None,
    seed: int | None = None,
    params: dict[str, Any] | None = None,
) -> AudioResult:
    """Add more seeded variations to an existing procedural sound. Returns only the new variants."""
    if not 1 <= variants <= 32:
        raise InvalidInputError("variants must be between 1 and 32")
    if variation is not None and not 0.0 <= variation <= 1.0:
        raise InvalidInputError("variation must be between 0 and 1")
    asset = lib.get_asset(ref)
    gen = generator_meta(asset.metadata, ref)
    recipe = get_recipe(gen["recipe"])
    base, _ = resolve_params(recipe, base=gen["base_params"], overrides=params)
    plan = Plan(
        recipe=recipe,
        preset=gen.get("preset"),
        base_params=base,
        effects=gen["effects"],
        seed=seed if seed is not None else secrets.randbelow(2**31),
        seeded_by_caller=seed is not None,
        variation=gen["variation"] if variation is None else variation,
        sample_rate=gen["sample_rate"],
        peak_db=gen["peak_db"],
        format=gen["format"],
    )
    taken = [int(m.group(1)) for v in asset.variants if (m := re.fullmatch(r"v(\d+)", v.label))]
    start = max(taken, default=0) + 1
    indices = range(start, start + variants)
    job_id = jobs.create_job(
        lib, "audio_vary", {"asset_id": asset.id, "variants": variants, "variation": plan.variation,
                            "seed": plan.seed, "params": params or {}},
        provider="procedural", model=f"{ENGINE}/{ENGINE_VERSION}",
    )  # fmt: skip
    try:
        # A different seed per call means variant k never collides with an earlier vk.
        variant_plans = plan_variants(
            recipe, base, seed=plan.seed, indices=indices, variation=plan.variation, canonical_first=False
        )
        rendered = _render_all(plan, variant_plans)
        tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="audio-"))
        try:
            _add_rendered_variants(lib, asset.id, plan, rendered, tmp, first_registered=None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        result = result_from_asset(lib, asset.id, reused=False, job_id=job_id, indices=set(indices))
        jobs.complete_job(lib, job_id, asset_id=asset.id, result={"variants": [v.variant_id for v in result.variants]})
        return result
    except AssetForgeError as exc:
        jobs.fail_job(lib, job_id, exc.to_dict())
        raise
    except Exception as exc:
        jobs.fail_job(lib, job_id, {"code": "ERROR", "message": str(exc)})
        raise


def reproduce_audio(lib: Library, ref: str) -> dict[str, Any]:
    """Re-render every master variant from its stored recipe and compare PCM hashes."""
    asset = lib.get_asset(ref)
    generator_meta(asset.metadata, ref)
    only = None
    if ref.startswith("var_"):
        variant = next(v for v in asset.variants if v.id == ref)
        only = variant.params.get("encoded_from", variant.id)
    checks = []
    for master in asset.variants:
        p = master.params
        if p.get("generator") != ENGINE or (only and master.id != only):
            continue
        recipe = get_recipe(p["recipe"])
        pcm, _ = render_variant(recipe, p["params"], p["effects"], p["render_seed"], p["sample_rate"], p["peak_db"])
        actual = pcm_sha256(pcm)
        checks.append(
            {
                "variant_id": master.id,
                "label": master.label,
                "expected_pcm_sha256": p["pcm_sha256"],
                "actual_pcm_sha256": actual,
                "match": actual == p["pcm_sha256"],
                "recipe_version_changed": recipe.version != p["recipe_version"],
            }
        )
    return {"asset_id": asset.id, "ok": all(c["match"] for c in checks), "variants": checks}
