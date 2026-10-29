"""The shared produce → master → QC → catalog path for designed sounds, ambience and music.

Each engine (`audio/design.py`, `audio/ambience/`, `audio/music/`) turns a fully resolved spec plus a
seed into a `Take`: the main mix and optional extras (stems, intensity versions) and side files
(a MIDI score). This module does everything else the same way for all of them:

- request reuse by fingerprint when the caller fixed the seed;
- mastering to the kind's loudness target (extras get the mix's gain, so stems still sum to the mix);
- QC (silent and clipping are errors; loops also get a seam check);
- cataloging: one asset, takes `v1..vN` as 16-bit WAV masters, extras `vN_<name>`, delivery copies
  `vN_ogg` (the first is primary), and side files `vN_midi`. Every master stores what re-renders it.

Engines register a render function with `register_engine`, which is how `reproduce` and `vary`
work for any of them.
"""

from __future__ import annotations

import functools
import re
import secrets
import shutil
import tempfile
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from assetforge.audio.export import pcm_sha256, to_pcm16, write_audio
from assetforge.audio.master import CEILING_DB, master, soft_limit
from assetforge.audio.variations import STREAM_RENDER, derive_seed
from assetforge.core import collections as collections_mod
from assetforge.core import jobs
from assetforge.core.catalog import Library
from assetforge.core.models import AssetType, CommercialUse, LineageRelation, NewAsset, Origin, Scope, Status
from assetforge.errors import AssetForgeError, ConflictError, InvalidInputError
from assetforge.validation.audio import analyze, loop_seam

GENERATED_LICENSE = "AssetForge procedural (original work)"


@dataclass
class Take:
    """One rendering of a spec: the mix plus optional extras and side files."""

    mix: np.ndarray
    extras: dict[str, np.ndarray] = field(default_factory=dict)  # stem_drums, calm, intense…
    files: dict[str, Path] = field(default_factory=dict)  # midi → path
    info: dict[str, Any] = field(default_factory=dict)  # facts about this take (score summary, layers…)


RenderFn = Callable[[dict[str, Any], int, int, int], Take]  # (spec, index, seed, sample_rate) → Take


@dataclass(frozen=True)
class Engine:
    name: str
    version: int
    render: RenderFn
    job_kind: str


ENGINES: dict[str, Engine] = {}
# The library being rendered for, so engines can find library-held resources (installed SoundFonts).
active_home: ContextVar[Path | None] = ContextVar("active_home", default=None)


def for_library(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(lib: Library, *args: Any, **kwargs: Any) -> Any:
        token = active_home.set(lib.paths.home)
        try:
            return fn(lib, *args, **kwargs)
        finally:
            active_home.reset(token)

    return wrapper
ENGINE_MODULES = ("assetforge.audio.design", "assetforge.audio.ambience.engine", "assetforge.audio.music.render",
                  "assetforge.audio.creatures.engine")


def load_engines() -> dict[str, Engine]:
    """Import every engine module so its render function is registered."""
    import importlib

    for module in ENGINE_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if not exc.name or not module.startswith(exc.name):
                raise
    return ENGINES


def register_engine(name: str, version: int, render: RenderFn, job_kind: str) -> Engine:
    engine = Engine(name, version, render, job_kind)
    ENGINES[name] = engine
    return engine


class Common(BaseModel):
    """Catalog and delivery fields shared by design, ambience and music requests."""

    model_config = ConfigDict(extra="forbid")

    variants: int = Field(1, ge=1, le=16, description="Takes to render (v1..vN), each with its own seed")
    seed: int | None = Field(None, ge=0, le=2**32 - 1, description="Fix for reproducible output and request reuse")
    new_take: bool = Field(False, description="Render fresh even if an identical seeded request exists")
    format: Literal["ogg", "mp3", "wav"] | None = Field(None, description="Delivery format (WAV masters always kept)")
    sample_rate: Literal[22050, 32000, 44100, 48000] | None = None
    name: str | None = None
    key: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    scope: Scope = Scope.GLOBAL
    project: str | None = None
    style: str | None = None
    status: Status = Status.CANDIDATE
    collection: str | None = None
    category: str | None = None
    subcategory: str | None = None
    theme: str | None = None
    genre: str | None = None
    use_profile: bool = Field(True, description="Apply the project's sound profile")

    def catalog(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("key", "scope", "project", "style", "status", "collection", "category",
                                              "subcategory", "theme", "genre")}  # fmt: skip


@dataclass
class Output:
    """Everything about the asset to make, besides the audio itself."""

    engine: Engine
    spec: dict[str, Any]  # fully resolved and JSON-able: this is what gets fingerprinted and stored
    kind: str  # sfx | stinger | ambience | music → loudness target
    asset_type: AssetType
    name: str
    description: str
    target_lufs: float
    loop: bool = False
    sample_rate: int = 44100
    format: str = "ogg"
    tags: list[str] = field(default_factory=list)
    audio_meta: dict[str, Any] = field(default_factory=dict)  # bpm, key, loop points… (searchable)
    key: str | None = None
    scope: Scope = Scope.GLOBAL
    project: str | None = None
    style: str | None = None
    status: Status = Status.CANDIDATE
    collection: str | None = None
    category: str | None = None
    subcategory: str | None = None
    theme: str | None = None
    genre: str | None = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self, seed: int, variants: int) -> str:
        return jobs.fingerprint({"engine": self.engine.name, "engine_version": self.engine.version, "spec": self.spec,
                                 "seed": seed, "variants": variants, "sample_rate": self.sample_rate,
                                 "format": self.format, "loop": self.loop, "target_lufs": self.target_lufs})  # fmt: skip


@dataclass
class Mastered:
    index: int
    seed: int
    pcm: np.ndarray
    extras: dict[str, np.ndarray]
    files: dict[str, Path]
    info: dict[str, Any]
    qc: dict[str, Any]
    report: dict[str, Any]


# ---------------------------------------------------------------------- render + master


def take_seed(seed: int, index: int) -> int:
    return derive_seed(seed, index, STREAM_RENDER)


def master_take(take: Take, out: Output, index: int, seed: int) -> Mastered:
    sr = out.sample_rate
    mix, report = master(take.mix, sr, target_lufs=out.target_lufs, loop=out.loop)
    gain = 10 ** (report["gain_db"] / 20)
    ceiling = 10 ** (CEILING_DB / 20)
    extras = {}
    for label, signal in take.extras.items():
        y = np.asarray(signal, dtype=np.float64)
        y = (y - y.mean(axis=0)) * gain
        if not out.loop:
            y = y[: len(mix)]
        extras[label] = to_pcm16(soft_limit(y, ceiling))
    pcm = to_pcm16(mix)
    qc = analyze(pcm.astype(np.float64) / 32767.0, sr)
    if out.loop:
        qc |= loop_seam(pcm.astype(np.float64) / 32767.0, sr)
        if qc["loop_click"]:
            qc["issues"].append("loop_click")
    if not qc["ok"]:
        raise InvalidInputError(f"Take {index} failed QC: {', '.join(qc['issues'])}", qc=qc)
    return Mastered(index, seed, pcm, extras, dict(take.files), dict(take.info), qc, report)


def render_takes(out: Output, *, seed: int, indices: range) -> list[Mastered]:
    takes: list[Mastered] = []
    for index in indices:
        s = take_seed(seed, index)
        takes.append(master_take(out.engine.render(out.spec, index, s, out.sample_rate), out, index, s))
    return takes


# ---------------------------------------------------------------------- catalog


def _master_params(out: Output, m: Mastered) -> dict[str, Any]:
    return {"generator": out.engine.name, "engine_version": out.engine.version, "index": m.index, "seed": m.seed,
            "sample_rate": out.sample_rate, "loop": out.loop, "target_lufs": out.target_lufs,
            "pcm_sha256": pcm_sha256(m.pcm), "qc": m.qc, "master": m.report, "info": m.info,
            "extras": {label: pcm_sha256(pcm) for label, pcm in m.extras.items()}}  # fmt: skip


def _ensure(lib: Library, asset_id: str, label: str, write: Callable[[], Path], **kwargs: Any) -> str:
    def lookup() -> str | None:
        row = lib.conn.execute("SELECT id FROM asset_variants WHERE asset_id = ? AND label = ?",
                               (asset_id, label)).fetchone()  # fmt: skip
        return row["id"] if row else None

    if existing := lookup():
        return existing
    try:
        return lib.add_variant(asset_id, write(), label=label, **kwargs).id
    except ConflictError:
        if existing := lookup():
            return existing
        raise


def _store_take(lib: Library, asset_id: str, out: Output, m: Mastered, tmp: Path, *, master_id: str | None,
                primary: bool) -> None:  # fmt: skip
    sr, fmt, i = out.sample_rate, out.format, m.index
    if master_id is None:
        master_id = _ensure(lib, asset_id, f"v{i}", lambda: write_audio(m.pcm, sr, tmp / f"v{i}.wav", "wav"),
                            params=_master_params(out, m))  # fmt: skip
    from assetforge.audio.generate import delivery_alternates

    alternates = delivery_alternates(fmt)
    if fmt != "wav":
        _ensure(lib, asset_id, f"v{i}_{fmt}", lambda: write_audio(m.pcm, sr, tmp / f"v{i}.{fmt}", fmt),
                parent_variant_id=master_id, params={"encoded_from": master_id, "format": fmt},
                make_primary=primary)  # fmt: skip
        for alt in alternates:
            _ensure(lib, asset_id, f"v{i}_{alt}", lambda alt=alt: write_audio(m.pcm, sr, tmp / f"v{i}.{alt}", alt),
                    parent_variant_id=master_id, params={"encoded_from": master_id, "format": alt})  # fmt: skip
    for label, pcm in m.extras.items():
        extra_id = _ensure(lib, asset_id, f"v{i}_{label}",
                           lambda pcm=pcm, label=label: write_audio(pcm, sr, tmp / f"v{i}_{label}.wav", "wav"),
                           parent_variant_id=master_id, params={"extra_of": master_id, "extra": label,
                                                                "pcm_sha256": pcm_sha256(pcm)})  # fmt: skip
        for out_fmt in ([fmt] if fmt != "wav" else []) + alternates:
            _ensure(lib, asset_id, f"v{i}_{label}_{out_fmt}",
                    lambda pcm=pcm, label=label, out_fmt=out_fmt: write_audio(pcm, sr, tmp / f"v{i}_{label}.{out_fmt}", out_fmt),
                    parent_variant_id=extra_id, params={"encoded_from": extra_id, "extra": label, "format": out_fmt})  # fmt: skip
    for label, path in m.files.items():
        _ensure(lib, asset_id, f"v{i}_{label}", lambda path=path: path, parent_variant_id=master_id,
                params={"file_of": master_id, "file": label})  # fmt: skip


def _discard_files(takes: list[Mastered]) -> None:
    """Side files are temporary renders; once stored (copied) they can go."""
    for m in takes:
        for path in m.files.values():
            path.unlink(missing_ok=True)
            if path.parent.name.startswith("assetforge-"):
                shutil.rmtree(path.parent, ignore_errors=True)


def _new_asset(out: Output, seed: int, variants: int, job_id: str, takes: list[Mastered],
               source_id: str | None) -> NewAsset:  # fmt: skip
    extras = sorted({label for m in takes for label in m.extras})
    audio_meta = {"kind": out.kind, "loop": out.loop, **out.audio_meta}
    if extras:
        audio_meta["extras"] = extras
    return NewAsset(
        name=out.name, type=out.asset_type, key=out.key, description=out.description, scope=out.scope,
        project=out.project, style=out.style, category=out.category, subcategory=out.subcategory, theme=out.theme,
        genre=out.genre, status=out.status, origin=Origin.PROCEDURAL, license=GENERATED_LICENSE,
        commercial_use=CommercialUse.ALLOWED, tags=list(dict.fromkeys([*out.tags, "procedural"])),
        metadata={"generator": {"engine": out.engine.name, "engine_version": out.engine.version, "spec": out.spec,
                                "seed": seed, "variants": variants, "sample_rate": out.sample_rate,
                                "format": out.format, "loop": out.loop, "kind": out.kind,
                                "target_lufs": out.target_lufs, "job_id": job_id, "derived_from": source_id},
                  "audio": audio_meta, **out.extra_metadata},
    )  # fmt: skip


@for_library
def produce(lib: Library, out: Output, *, variants: int = 1, seed: int | None = None, new_take: bool = False,
            source_id: str | None = None, seeded: bool | None = None) -> dict[str, Any]:  # fmt: skip
    """Render, master, QC and catalog `variants` takes. Returns a result dict (see `result`).

    Identical requests are reused only when the caller fixed the seed (`seeded` overrides that
    when the engine had to pick a seed itself before calling).
    """
    if not 1 <= variants <= 16:
        raise InvalidInputError("variants must be between 1 and 16")
    seeded = seed is not None if seeded is None else seeded
    seed = seed if seed is not None else secrets.randbelow(2**31)
    fp = out.fingerprint(seed, variants)
    if seeded and not new_take and (done := jobs.find_completed(lib, out.engine.job_kind, fp)):
        assert done.asset_id
        return result(lib, done.asset_id, reused=True, reuse_reason="fingerprint", job_id=done.id)
    collection_id = collections_mod.ensure_collection(lib, out.collection, project=out.project).id if out.collection else None
    job_id = jobs.create_job(lib, out.engine.job_kind, {"spec": out.spec, "seed": seed, "variants": variants,
                                                        "name": out.name, "project": out.project},
                             fingerprint=fp, provider="procedural",
                             model=f"{out.engine.name}/{out.engine.version}")  # fmt: skip
    try:
        takes = render_takes(out, seed=seed, indices=range(1, variants + 1))
        tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="audio-"))
        try:
            first = takes[0]
            master_path = write_audio(first.pcm, out.sample_rate, tmp / f"{out.name}_v1.wav", "wav")
            reg = lib.register_file(master_path, _new_asset(out, seed, variants, job_id, takes, source_id), label="v1",
                                    params=_master_params(out, first), collection_id=collection_id)  # fmt: skip
            existing = lib.get_asset(reg.asset_id)
            if not reg.created and existing.metadata.get("generator", {}).get("engine") != out.engine.name:
                raise ConflictError(f"Rendered audio is identical to {reg.asset_id}, which another engine made")
            for m in takes:
                _store_take(lib, reg.asset_id, out, m, tmp, master_id=reg.variant_id if m is first else None,
                            primary=m is first and reg.created)  # fmt: skip
            _discard_files(takes)
            if source_id and reg.created:
                lib.add_lineage(reg.asset_id, source_id, LineageRelation.DERIVED_FROM)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        res = result(lib, reg.asset_id, reused=not reg.created,
                     reuse_reason=None if reg.created else "identical_content", job_id=job_id)  # fmt: skip
        jobs.complete_job(lib, job_id, asset_id=reg.asset_id, result={"reused": res["reused"],
                                                                      "variants": [v["variant_id"] for v in res["variants"]]})  # fmt: skip
        return res
    except AssetForgeError as exc:
        jobs.fail_job(lib, job_id, exc.to_dict())
        raise
    except Exception as exc:
        jobs.fail_job(lib, job_id, {"code": "ERROR", "message": str(exc)})
        raise


# ---------------------------------------------------------------------- results


def generator(asset_metadata: dict[str, Any], ref: str) -> dict[str, Any]:
    gen = asset_metadata.get("generator") or {}
    if gen.get("engine") not in ENGINES:
        raise InvalidInputError(f"{ref} was not made by a procedural audio engine", ref=ref)
    return gen


def result(lib: Library, asset_id: str, *, reused: bool, reuse_reason: str | None = None,
           job_id: str | None = None, indices: set[int] | None = None) -> dict[str, Any]:  # fmt: skip
    asset = lib.get_asset(asset_id)
    gen = asset.metadata.get("generator", {})
    by_label = {v.label: v for v in asset.variants}
    fmt = gen.get("format", "wav")
    takes = []
    for v in asset.variants:
        m = re.fullmatch(r"v(\d+)", v.label)
        if not m or v.params.get("generator") != gen.get("engine"):
            continue
        index = int(m.group(1))
        if indices is not None and index not in indices:
            continue
        delivery = by_label.get(f"v{index}_{fmt}", v)
        extras = {}
        for label in v.params.get("extras", {}):
            extra = by_label.get(f"v{index}_{label}_{fmt}") or by_label.get(f"v{index}_{label}")
            if extra:
                extras[label] = {"variant_id": extra.id, "path": extra.file.path}
                if m4a := by_label.get(f"v{index}_{label}_m4a"):
                    extras[label]["alternates"] = {"m4a": m4a.file.path}
        alternates = {lab.rsplit("_", 1)[1]: x.file.path for lab, x in by_label.items()
                      if x.params.get("encoded_from") == v.id and x.params.get("format") not in (None, fmt)}  # fmt: skip
        files = {lab.split("_", 1)[1]: {"variant_id": x.id, "path": x.file.path} for lab, x in by_label.items()
                 if lab.startswith(f"v{index}_") and x.params.get("file_of") == v.id}  # fmt: skip
        takes.append({"index": index, "label": delivery.label, "variant_id": delivery.id, "master_variant_id": v.id,
                      "path": delivery.file.path, "format": delivery.file.ext.lstrip("."),
                      "duration_ms": delivery.file.duration_ms, "seed": v.params.get("seed"),
                      "qc": v.params.get("qc"), "info": v.params.get("info", {}), "extras": extras, "files": files,
                      "alternates": alternates})  # fmt: skip
    takes.sort(key=lambda t: t["index"])
    summary = lib.summaries([asset_id])[0]
    return {"asset_id": asset_id, "reused": reused, "reuse_reason": reuse_reason, "job_id": job_id,
            "engine": gen.get("engine"), "format": fmt, "audio": asset.metadata.get("audio", {}),
            "asset": summary.model_dump(mode="json"), "variants": takes}  # fmt: skip


# ---------------------------------------------------------------------- vary + reproduce


def output_from_asset(lib: Library, ref: str) -> tuple[Output, dict[str, Any], str]:
    asset = lib.get_asset(ref)
    gen = generator(asset.metadata, ref)
    out = Output(engine=ENGINES[gen["engine"]], spec=gen["spec"], kind=gen["kind"], asset_type=asset.type,
                 name=asset.name, description=asset.description, target_lufs=gen["target_lufs"], loop=gen["loop"],
                 sample_rate=gen["sample_rate"], format=gen["format"])  # fmt: skip
    return out, gen, asset.id


@for_library
def vary(lib: Library, ref: str, *, variants: int = 1, seed: int | None = None) -> dict[str, Any]:
    """Add new takes (fresh seeds) to an engine-made asset. Returns only the new takes."""
    if not 1 <= variants <= 16:
        raise InvalidInputError("variants must be between 1 and 16")
    out, gen, asset_id = output_from_asset(lib, ref)
    if out.engine.version != gen["engine_version"]:
        out.engine = Engine(out.engine.name, gen["engine_version"], out.engine.render, out.engine.job_kind)
    asset = lib.get_asset(asset_id)
    taken = [int(m.group(1)) for v in asset.variants if (m := re.fullmatch(r"v(\d+)", v.label))]
    start = max(taken, default=0) + 1
    indices = range(start, start + variants)
    seed = seed if seed is not None else secrets.randbelow(2**31)
    job_id = jobs.create_job(lib, f"{out.engine.job_kind}_vary", {"asset_id": asset_id, "variants": variants, "seed": seed},
                             provider="procedural", model=f"{out.engine.name}/{out.engine.version}")  # fmt: skip
    try:
        takes = render_takes(out, seed=seed, indices=indices)
        tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="audio-"))
        try:
            for m in takes:
                _store_take(lib, asset_id, out, m, tmp, master_id=None, primary=False)
            _discard_files(takes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        res = result(lib, asset_id, reused=False, job_id=job_id, indices=set(indices))
        jobs.complete_job(lib, job_id, asset_id=asset_id, result={"variants": [v["variant_id"] for v in res["variants"]]})
        return res
    except AssetForgeError as exc:
        jobs.fail_job(lib, job_id, exc.to_dict())
        raise
    except Exception as exc:
        jobs.fail_job(lib, job_id, {"code": "ERROR", "message": str(exc)})
        raise


@for_library
def reproduce(lib: Library, ref: str) -> dict[str, Any]:
    """Re-render every take from its stored spec and seed and compare PCM hashes."""
    out, gen, asset_id = output_from_asset(lib, ref)
    asset = lib.get_asset(asset_id)
    checks = []
    for v in asset.variants:
        if v.params.get("generator") != out.engine.name or not re.fullmatch(r"v\d+", v.label):
            continue
        m = master_take(out.engine.render(out.spec, v.params["index"], v.params["seed"], out.sample_rate), out,
                        v.params["index"], v.params["seed"])  # fmt: skip
        _discard_files([m])
        actual = pcm_sha256(m.pcm)
        checks.append({"variant_id": v.id, "label": v.label, "expected_pcm_sha256": v.params["pcm_sha256"],
                       "actual_pcm_sha256": actual, "match": actual == v.params["pcm_sha256"],
                       "engine_version_changed": out.engine.version != v.params.get("engine_version")})  # fmt: skip
    return {"asset_id": asset_id, "ok": all(c["match"] for c in checks), "variants": checks}
