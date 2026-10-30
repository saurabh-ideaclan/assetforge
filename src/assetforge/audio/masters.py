"""WAV masters of synthesized sounds: prune them to save space, re-render them when something needs them.

Every procedural take keeps a 16-bit WAV master next to its OGG/M4A delivery copies. Because the stored spec
(recipe or engine spec, seed, params) re-renders it bit for bit, the master can be deleted (`audio prune-masters`)
and rebuilt later: `ensure_file` re-renders a pruned master, checks the bytes hash to the catalogued SHA-256,
and puts it back, so editing, re-encoding, exporting new formats or `audio reproduce` keep working.

Never pruned: imported audio (no spec), masters whose spec can't be re-rendered here (SoundFont music whose
.sf2 is no longer installed), masters that no longer re-render bit for bit (a recipe or engine changed since;
`prune` re-renders each candidate to check), and files that another, non-prunable variant also uses (files are
shared by hash).
Masters are never pruned automatically.
"""

from __future__ import annotations

import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from assetforge.core import db
from assetforge.core.util import dumps, loads, utcnow
from assetforge.errors import StorageError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library
    from assetforge.core.models import Asset, Variant

SYNTH = "assetforge-synth"


def master_kind(asset: Asset, variant: Variant) -> str | None:
    """'synth' / 'engine' / 'engine_extra' for a re-renderable WAV master, else None."""
    from assetforge.audio import pipeline

    if variant.file.ext != ".wav":
        return None
    engine = (asset.metadata.get("generator") or {}).get("engine")
    if engine == SYNTH and variant.params.get("generator") == SYNTH:
        return "synth"
    if engine in pipeline.load_engines():
        if variant.params.get("generator") == engine and re.fullmatch(r"v\d+", variant.label):
            return "engine"
        if variant.params.get("extra_of") and variant.params.get("pcm_sha256"):
            return "engine_extra"
    return None


def can_rerender(lib: Library, asset: Asset) -> tuple[bool, str]:
    spec = (asset.metadata.get("generator") or {}).get("spec") or {}
    if (asset.metadata.get("generator") or {}).get("engine") == "assetforge-creature":
        from assetforge.audio.creatures import pack

        installed = pack.installed_packs(lib.paths.home)
        if any(installed.get(name) != version for name, version in (spec.get("packs") or {}).items()):
            return False, "made from creature recordings that are not installed here"
    if spec.get("backend") == "soundfont":
        from assetforge.audio.music import soundfont

        sha = (spec.get("soundfont") or {}).get("sha256")
        if not soundfont.fluidsynth_path() or soundfont._font_for(sha, lib.paths.home) is None:
            return False, "rendered with a SoundFont that is not installed here"
    return True, ""


def render_master(lib: Library, asset: Asset, variant: Variant, cache: dict | None = None) -> tuple[np.ndarray, int]:
    """Re-render a master's PCM (int16) from its stored spec; raises if it no longer matches.

    `cache` keeps engine takes per (asset, take), so a take's mix and stems are rendered once.
    """
    from assetforge.audio import pipeline
    from assetforge.audio.export import pcm_sha256

    kind = master_kind(asset, variant)
    if kind == "synth":
        from assetforge.audio.generate import render_variant
        from assetforge.audio.recipes import get_recipe

        p = variant.params
        pcm, _ = render_variant(get_recipe(p["recipe"]), p["params"], p["effects"], p["render_seed"],
                                p["sample_rate"], p["peak_db"])  # fmt: skip
        sr = int(p["sample_rate"])
    elif kind in ("engine", "engine_extra"):
        master = variant if kind == "engine" else next(v for v in asset.variants if v.id == variant.params["extra_of"])
        index, seed = master.params["index"], master.params["seed"]
        hit = (cache or {}).get((asset.id, index))
        if hit is None:
            token = pipeline.active_home.set(lib.paths.home)
            try:
                out, _, _ = pipeline.output_from_asset(lib, asset.id)
                take = pipeline.master_take(out.engine.render(out.spec, index, seed, out.sample_rate), out, index, seed)
                pipeline._discard_files([take])
            finally:
                pipeline.active_home.reset(token)
            hit = (take, out.sample_rate)
            if cache is not None:
                cache[(asset.id, index)] = hit
        take, sr = hit
        pcm = take.pcm if kind == "engine" else take.extras[variant.params["extra"]]
    else:
        raise StorageError(f"{variant.id} is not a re-renderable master", variant_id=variant.id)
    if pcm_sha256(pcm) != variant.params.get("pcm_sha256"):
        raise StorageError(f"Re-rendering {variant.id} no longer gives the original sound",
                           hint="The recipe or engine changed since it was made; restore the library from a backup")  # fmt: skip
    return pcm, sr


def ensure_file(lib: Library, variant: Variant) -> Path:
    """The variant's file on disk, re-rendering a pruned master first."""
    from assetforge.audio.export import write_audio

    path = Path(variant.file.path)
    if not path.is_file() and variant.file.withheld:
        raise StorageError(f"{variant.id} ({variant.label}) is not in this copy of the library",
                           variant_id=variant.id, hint="It was left out of the shared snapshot; use its OGG/M4A "
                           "copy, or ask whoever publishes the library for the original")  # fmt: skip
    if path.is_file() or not variant.file.pruned:
        return path
    asset = lib.get_asset(variant.asset_id)
    pcm, sr = render_master(lib, asset, variant)
    with tempfile.TemporaryDirectory(dir=lib.paths.tmp) as tmp:
        wav = write_audio(pcm, sr, Path(tmp) / "master.wav", "wav")
        stored = lib.store.put_file(wav, ".wav")
    if stored.sha256 != variant.file.sha256:
        raise StorageError(f"Re-rendered {variant.id} has the same sound but different file bytes",
                           hint="The WAV writer changed; the sound is intact (pcm_sha256 matches)")  # fmt: skip
    _set_pruned(lib, variant.file.sha256, False)
    return path


def _set_pruned(lib: Library, sha256: str, pruned: bool) -> None:
    with db.transaction(lib.conn):
        row = lib.conn.execute("SELECT extra FROM asset_files WHERE sha256 = ?", (sha256,)).fetchone()
        extra = loads(row["extra"], {}) if row else {}
        if pruned:
            extra |= {"pruned": True, "pruned_at": utcnow()}
        else:
            extra.pop("pruned", None)
            extra.pop("pruned_at", None)
        lib.conn.execute("UPDATE asset_files SET extra = ? WHERE sha256 = ?", (dumps(extra), sha256))


def prune(lib: Library, *, older_than_days: float | None = None, project: str | None = None,
          types: list[str] | None = None, dry_run: bool = False) -> dict[str, Any]:  # fmt: skip
    """Delete WAV masters that can be re-rendered. Returns what was (or, with `dry_run`, would be) freed."""
    from assetforge.core.util import normalize_optional_slug

    where, params = ["a.origin = 'procedural'"], []
    if project := normalize_optional_slug(project, "project"):
        where.append("a.project = ?")
        params.append(project)
    if types:
        where.append(f"a.type IN ({','.join('?' * len(types))})")
        params += list(types)
    cutoff = ((datetime.now(UTC) - timedelta(days=older_than_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
              if older_than_days is not None else None)  # same format as catalog timestamps  # fmt: skip
    ids = [r[0] for r in lib.conn.execute(f"SELECT a.id FROM assets a WHERE {' AND '.join(where)} ORDER BY a.id", params)]
    eligible: dict[str, list[tuple[Asset, Variant]]] = {}
    skipped: list[dict[str, str]] = []
    for asset_id in ids:
        asset = lib.get_asset(asset_id)
        ok, reason = can_rerender(lib, asset)
        masters = [v for v in asset.variants if master_kind(asset, v)
                    and not v.file.pruned and Path(v.file.path).is_file() and not (cutoff and v.created_at > cutoff)]  # fmt: skip
        if masters and not ok:
            skipped.append({"asset_id": asset.id, "reason": reason})
            continue
        cache: dict = {}
        for v in masters:
            try:  # only delete what provably comes back: recipes and engines change over time
                render_master(lib, asset, v, cache)
            except Exception as exc:  # noqa: BLE001 - any failure means "keep it"
                skipped.append({"asset_id": asset.id, "variant_id": v.id, "reason": f"does not re-render identically: {exc}"})
                continue
            eligible.setdefault(v.file.sha256, []).append((asset, v))
    items, freed = [], 0
    for sha, pairs in eligible.items():
        users = lib.conn.execute("SELECT count(*) FROM asset_variants WHERE file_sha256 = ?", (sha,)).fetchone()[0]
        if users != len(pairs):  # some other variant (not a prunable master) still uses these bytes
            skipped.append({"asset_id": pairs[0][0].id, "reason": f"file {sha[:12]} is shared with another variant"})
            continue
        variant = pairs[0][1]
        freed += variant.file.size_bytes
        items.append({"asset_id": pairs[0][0].id, "name": pairs[0][0].name, "variant_id": variant.id,
                      "label": variant.label, "bytes": variant.file.size_bytes})  # fmt: skip
        if not dry_run:
            _set_pruned(lib, sha, True)  # mark first: a crash in between leaves a re-renderable master, not a hole
            Path(variant.file.path).unlink(missing_ok=True)
            with db.transaction(lib.conn):
                for asset, v in pairs:
                    lib._event(asset.id, "master_pruned", {"variant_id": v.id, "bytes": v.file.size_bytes})
    return {"dry_run": dry_run, "count": len(items), "freed_bytes": freed, "freed_mb": round(freed / 1e6, 2),
            "items": items, "skipped": skipped}  # fmt: skip


def restore(lib: Library, ref: str) -> dict[str, Any]:
    """Re-render every pruned master of one asset."""
    asset = lib.get_asset(ref)
    restored = []
    for v in asset.variants:
        if v.file.pruned:
            ensure_file(lib, v)
            restored.append(v.id)
    return {"asset_id": asset.id, "restored": restored}
