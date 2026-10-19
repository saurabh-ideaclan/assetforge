"""Export cataloged assets into a game repository as ordinary, meaningfully named files.

Names come from the asset key (`enemy/beetle/hit` → `enemy/beetle/hit.ogg`) or from type and name
(`characters/beetle.webp`). Web formats are produced as cached derivative variants, so the
catalog records exactly which file each game uses. The export directory is owned through
`assetforge-export.json`: re-exporting rewrites only changed files, `clean` removes only files
AssetForge exported before, and unknown files already in the way are never overwritten
unless `overwrite` is set.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, Field, field_validator

from assetforge import __version__
from assetforge.audio import process as audio_proc
from assetforge.core import collections as coll
from assetforge.core.catalog import Library
from assetforge.core.models import Asset, AssetType, CommercialUse, FileKind, Status, Variant
from assetforge.core.search import SearchQuery, search
from assetforge.core.util import normalize_optional_slug, slugify
from assetforge.errors import ConflictError, InvalidInputError, NotFoundError
from assetforge.exporters import atlas as atlas_mod
from assetforge.exporters import pixijs
from assetforge.image import process as image_proc

EXPORT_MANIFEST = "assetforge-export.json"
PIXI_MANIFEST = "manifest.json"
SOUNDS_FILE = "sounds.json"
CREDITS_FILE = "ASSET_CREDITS.md"

TYPE_DIRS = {
    AssetType.CHARACTER: "characters", AssetType.SPRITE: "sprites", AssetType.SPRITESHEET: "spritesheets",
    AssetType.BACKGROUND: "backgrounds", AssetType.TILE: "tiles", AssetType.ICON: "icons", AssetType.UI: "ui",
    AssetType.EFFECT: "effects", AssetType.IMAGE: "images", AssetType.SFX: "sfx", AssetType.MUSIC: "music",
    AssetType.AMBIENCE: "ambience", AssetType.VOICE: "voice", AssetType.AUDIO: "audio", AssetType.FONT: "fonts",
    AssetType.SOURCE: "source", AssetType.DATA: "data", AssetType.OTHER: "misc",
}  # fmt: skip
ATLAS_TYPES = {AssetType.CHARACTER, AssetType.SPRITE, AssetType.ICON, AssetType.UI, AssetType.EFFECT}
OPAQUE_TYPES = {AssetType.BACKGROUND, AssetType.TILE, AssetType.IMAGE}
IMAGE_FORMATS = ("webp", "png")
AUDIO_FORMATS = ("ogg", "mp3", "wav")
OWN_PROVENANCE = ("AssetForge procedural", "AI-generated")


class ExportRequest(BaseModel):
    out: str
    refs: list[str] = Field(default_factory=list)
    collection: str | None = None
    project: str | None = Field(None, description="Select what the project uses (if nothing else) and record usage")
    query: str | None = None
    types: list[AssetType] = Field(default_factory=list)
    include_candidates: bool = False
    allow_restricted: bool = False
    layout: Literal["by-type", "flat"] = "by-type"
    image_formats: list[str] = Field(default_factory=lambda: ["webp", "png"])
    audio_formats: list[str] = Field(default_factory=lambda: ["ogg", "mp3"])
    max_image_size: int | None = Field(None, ge=16, le=8192)
    atlas: str | None = Field(None, description="Pack sprite-like images into an atlas with this name")
    atlas_max_size: int = Field(2048, ge=64, le=8192)
    atlas_padding: int = Field(2, ge=0, le=64)
    atlas_power_of_two: bool = False
    pixi: bool = True
    bundle: str | None = None
    typescript: str | None = None
    record_usage: bool = True
    clean: bool = False
    overwrite: bool = False
    dry_run: bool = False

    @field_validator("image_formats")
    @classmethod
    def _image_formats(cls, value: list[str]) -> list[str]:
        value = list(dict.fromkeys(v.lower() for v in value))
        if not value or any(v not in IMAGE_FORMATS for v in value):
            raise ValueError(f"image formats must be some of {IMAGE_FORMATS}")
        return value

    @field_validator("audio_formats")
    @classmethod
    def _audio_formats(cls, value: list[str]) -> list[str]:
        value = list(dict.fromkeys(v.lower() for v in value))
        if not value or any(v not in AUDIO_FORMATS for v in value):
            raise ValueError(f"audio formats must be some of {AUDIO_FORMATS}")
        return value


class ExportedFile(BaseModel):
    path: str
    alias: str
    asset_id: str
    variant_id: str
    sha256: str
    kind: str
    action: Literal["written", "unchanged", "planned"]


class ExportResult(BaseModel):
    out: str
    bundle: str
    dry_run: bool
    counts: dict[str, int]
    files: list[ExportedFile]
    atlases: list[dict[str, Any]]
    sounds: list[dict[str, Any]]
    skipped: list[dict[str, str]]
    removed: list[str]
    outputs: dict[str, str | None]


# ---------------------------------------------------------------------- selection + naming


def select_assets(lib: Library, req: ExportRequest) -> list[Asset]:
    if req.refs:
        ids = [lib.resolve_asset_id(r) for r in req.refs]
    elif req.collection:
        ids = [a.id for a in coll.collection_assets(lib, req.collection)[1]]
    elif req.query is not None or req.types:
        hits = search(lib, SearchQuery(text=req.query or "", types=req.types, project=req.project, limit=500,
                                       statuses=[Status.APPROVED, Status.CANDIDATE], include_restricted=True))  # fmt: skip
        ids = [h.id for h in hits.results]
    elif req.project:
        ids = [u.asset_id for u, _ in lib.project_usage(req.project)]
    else:
        raise InvalidInputError("Choose what to export: asset refs, --collection, --query/--type or --project")
    ids = list(dict.fromkeys(ids))
    if req.types:
        wanted = set(req.types)
        ids = [i for i in ids if lib.get_asset(i).type in wanted]
    if not ids:
        raise NotFoundError("Nothing selected for export")
    return [lib.get_asset(i) for i in ids]


def alias_for(asset: Asset, layout: str) -> str:
    if asset.key:
        return asset.key
    if layout == "flat":
        return asset.name
    return f"{TYPE_DIRS.get(asset.type, 'misc')}/{asset.name}"


def _unique_aliases(assets: list[Asset], layout: str) -> dict[str, str]:
    taken: dict[str, str] = {}
    result = {}
    for asset in sorted(assets, key=lambda a: a.id):
        base = alias_for(asset, layout)
        alias, n = base, 2
        while alias in taken:
            alias, n = f"{base}_{n}", n + 1
        taken[alias] = asset.id
        result[asset.id] = alias
    return result


# ---------------------------------------------------------------------- variants per format


def _image_variant(lib: Library, asset: Asset, fmt: str, max_size: int | None) -> Variant:
    primary = asset.primary_variant
    assert primary is not None
    too_big = max_size is not None and max(primary.file.width or 0, primary.file.height or 0) > max_size
    if primary.file.ext.lstrip(".") == fmt and not too_big:
        return primary
    ops: list[dict[str, Any]] = []
    if too_big:
        ops.append({"op": "resize", "width": max_size, "height": max_size, "mode": "contain", "upscale": False})
    if fmt == "webp" and asset.type in OPAQUE_TYPES and not primary.file.has_alpha:
        ops.append({"op": "format", "format": "webp", "quality": 88})
    else:
        ops.append({"op": "format", "format": fmt})
    label = f"{image_proc.ops_label(ops)}_of_{primary.label}"
    derived = image_proc.process_image_asset(lib, asset.id, ops, variant=primary.id, label=label)
    return next(v for v in lib.get_asset(asset.id).variants if v.id == derived.variant_id)


def _audio_variant(lib: Library, asset: Asset, fmt: str) -> Variant:
    primary = asset.primary_variant
    assert primary is not None
    if primary.file.ext.lstrip(".") == fmt:
        return primary
    ops = [{"op": "format", "format": fmt, **({"quality": 0.6} if fmt != "wav" else {})}]
    derived = audio_proc.process_audio_asset(lib, asset.id, ops, variant=primary.id, label=f"{fmt}_of_{primary.label}")
    return next(v for v in lib.get_asset(asset.id).variants if v.id == derived.variant_id)


# ---------------------------------------------------------------------- writing


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Writer:
    root: Path
    owned: set[str]  # paths listed in the previous export manifest
    overwrite: bool
    dry_run: bool
    written: dict[str, str] = field(default_factory=dict)  # rel path -> action

    def target(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if self.root.resolve() not in path.parents:
            raise InvalidInputError(f"Refusing to write outside the export directory: {rel}")
        return path

    def check(self, rel: str, data_sha: str) -> None:
        path = self.target(rel)
        if path.exists() and rel not in self.owned and not self.overwrite and _file_sha(path) != data_sha:
            raise ConflictError(
                f"{path} exists and was not exported by AssetForge; move it or pass --overwrite", path=str(path)
            )

    def write(self, rel: str, data: bytes) -> str:
        path = self.target(rel)
        digest = _sha(data)
        self.check(rel, digest)
        if self.dry_run:
            self.written[rel] = "planned"
        elif path.exists() and _file_sha(path) == digest:
            self.written[rel] = "unchanged"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".af-")
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
            self.written[rel] = "written"
        return self.written[rel]


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _previous(root: Path) -> dict[str, Any]:
    try:
        return json.loads((root / EXPORT_MANIFEST).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _json_bytes(data: Any) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()


def _credits(assets: list[Asset], aliases: dict[str, str]) -> str | None:
    rows = []
    for a in sorted(assets, key=lambda a: aliases[a.id]):
        if a.attribution or (a.license and not a.license.startswith(OWN_PROVENANCE)):
            rows.append(f"| `{aliases[a.id]}` | {a.license or 'unknown'} | {a.attribution or ''} | "
                        f"{a.source_uri or ''} |")  # fmt: skip
    if not rows:
        return None
    return ("# Asset credits\n\nGenerated by AssetForge. Keep this file with the game.\n\n"
            "| Asset | License | Attribution | Source |\n|---|---|---|---|\n" + "\n".join(rows) + "\n")  # fmt: skip


# ---------------------------------------------------------------------- export


def export(lib: Library, req: ExportRequest) -> ExportResult:
    root = Path(req.out).expanduser()
    project = normalize_optional_slug(req.project, "project")
    bundle = slugify(req.bundle or req.collection or project or "game") or "game"
    selected = select_assets(lib, req)

    skipped: list[dict[str, str]] = []
    assets: list[Asset] = []
    allowed = {Status.APPROVED} | ({Status.CANDIDATE} if req.include_candidates else set())
    for a in selected:
        if a.status not in allowed:
            skipped.append({"asset_id": a.id, "reason": f"status {a.status.value}"
                            + ("" if a.status != Status.CANDIDATE else " (approve it or pass --include-candidates)")})  # fmt: skip
        elif a.commercial_use == CommercialUse.RESTRICTED and not req.allow_restricted:
            skipped.append({"asset_id": a.id, "reason": "commercial use restricted (pass --allow-restricted)"})
        elif a.primary_variant is None:
            skipped.append({"asset_id": a.id, "reason": "no file"})
        else:
            assets.append(a)
    if not assets:
        raise NotFoundError("No exportable assets after filtering", skipped=skipped)

    aliases = _unique_aliases(assets, req.layout)
    if req.atlas:
        reserved = {req.atlas} | {f"{req.atlas}-{i}" for i in range(64)}
        clash = [aliases[a.id] for a in assets if aliases[a.id] in reserved]
        if clash:
            raise ConflictError(f"Atlas name {req.atlas!r} collides with asset alias {clash[0]!r}")
    previous = _previous(root)
    owned = {f["path"] for f in previous.get("files", [])} | set(previous.get("generated", []))
    if previous.get("generator") == "assetforge":
        owned.add(EXPORT_MANIFEST)
    writer = Writer(root, owned, req.overwrite, req.dry_run)

    files: list[ExportedFile] = []
    textures: list[dict[str, Any]] = []
    plain: list[dict[str, Any]] = []
    sound_entries: list[dict[str, Any]] = []
    frames: list[atlas_mod.Frame] = []
    frame_assets: dict[str, Asset] = {}
    usage: dict[str, str] = {}

    def emit(rel: str, data: bytes, asset: Asset, variant: Variant, kind: str) -> None:
        action = writer.write(rel, data)
        files.append(ExportedFile(path=rel, alias=aliases[asset.id], asset_id=asset.id, variant_id=variant.id,
                                  sha256=_sha(data), kind=kind, action=action))  # fmt: skip
        usage.setdefault(asset.id, variant.id)

    for asset in sorted(assets, key=lambda a: aliases[a.id]):
        alias = aliases[asset.id]
        primary = asset.primary_variant
        assert primary is not None
        kind = primary.file.kind
        if kind == FileKind.IMAGE and req.atlas and asset.type in ATLAS_TYPES:
            with Image.open(primary.file.path) as image:
                frames.append(atlas_mod.make_frame(alias, image))
            frame_assets[alias] = asset
            usage.setdefault(asset.id, primary.id)
        elif kind == FileKind.IMAGE:
            for fmt in req.image_formats:
                variant = primary if req.dry_run else _image_variant(lib, asset, fmt, req.max_image_size)
                emit(f"{alias}.{fmt}", Path(variant.file.path).read_bytes(), asset, variant, "texture")
            data = {"scaleMode": "nearest"} if "pixel-art" in asset.tags else None
            textures.append({"alias": alias, "formats": req.image_formats, "data": data})
        elif kind == FileKind.AUDIO:
            paths = []
            for fmt in req.audio_formats:
                variant = primary if req.dry_run else _audio_variant(lib, asset, fmt)
                emit(f"{alias}.{fmt}", Path(variant.file.path).read_bytes(), asset, variant, "sound")
                paths.append(f"{alias}.{fmt}")
            sound_entries.append({"alias": alias, "paths": paths, "asset_id": asset.id})
        else:
            rel = f"{alias}{primary.file.ext}"
            emit(rel, Path(primary.file.path).read_bytes(), asset, primary, str(kind))
            data = {"family": asset.name} if kind == FileKind.FONT else None
            plain.append({"alias": alias, "path": rel, "data": data})

    atlases: list[dict[str, Any]] = []
    generated: list[str] = []
    if frames:
        atlas_files, summary = atlas_mod.build_atlas(
            req.atlas or "atlas", frames, formats=req.image_formats, max_size=req.atlas_max_size,
            padding=req.atlas_padding, power_of_two=req.atlas_power_of_two,
        )  # fmt: skip
        for f in atlas_files:
            writer.write(f.path.as_posix(), f.data)
            generated.append(f.path.as_posix())
        summary["frame_assets"] = {name: frame_assets[name].id for name in summary["frames"]}
        atlases.append(summary)

    outputs: dict[str, str | None] = {"export_manifest": EXPORT_MANIFEST, "pixi_manifest": None, "sounds": None,
                                      "typescript": None, "credits": None}  # fmt: skip
    if req.pixi:
        writer.write(PIXI_MANIFEST, _json_bytes(pixijs.manifest(bundle, textures=textures, atlases=atlases, files=plain)))
        generated.append(PIXI_MANIFEST)
        outputs["pixi_manifest"] = PIXI_MANIFEST
    if sound_entries:
        writer.write(SOUNDS_FILE, _json_bytes(pixijs.sounds(sound_entries)))
        generated.append(SOUNDS_FILE)
        outputs["sounds"] = SOUNDS_FILE
    if credits := _credits(assets, aliases):
        writer.write(CREDITS_FILE, credits.encode())
        generated.append(CREDITS_FILE)
        outputs["credits"] = CREDITS_FILE
    if req.typescript:
        ts = pixijs.typescript(
            bundle, textures=[t["alias"] for t in textures] + [p["alias"] for p in plain],
            frames=[n for a in atlases for n in a["frames"]],
            animations={k: v for a in atlases for k, v in a["animations"].items()},
            atlases=[a["name"] for a in atlases], sounds_map={s["alias"]: s["paths"] for s in sound_entries},
        )  # fmt: skip
        ts_path = Path(req.typescript).expanduser()
        if not req.dry_run:
            ts_path.parent.mkdir(parents=True, exist_ok=True)
            ts_path.write_text(ts)
        outputs["typescript"] = str(ts_path)

    keep = {f.path for f in files} | set(generated) | {EXPORT_MANIFEST}
    removed: list[str] = []
    if req.clean:
        for rel in sorted(writer.owned - keep):
            path = writer.target(rel)
            if path.is_file():
                if not req.dry_run:
                    path.unlink()
                    _prune_empty_dirs(path.parent, root)
                removed.append(rel)

    record = {
        "generator": "assetforge",
        "version": __version__,
        "bundle": bundle,
        "project": project,
        "collection": req.collection,
        "files": [f.model_dump(exclude={"action"}) for f in sorted(files, key=lambda f: f.path)],
        "generated": sorted(generated),
        "atlases": atlases,
        "sounds": sorted(sound_entries, key=lambda s: s["alias"]),
    }
    writer.write(EXPORT_MANIFEST, _json_bytes(record))

    if project and req.record_usage and not req.dry_run:
        for asset in assets:
            if asset.id in usage:
                lib.record_usage(asset.id, project, variant_id=usage[asset.id], usage_key=aliases[asset.id])

    actions = list(writer.written.values())
    return ExportResult(
        out=str(root), bundle=bundle, dry_run=req.dry_run,
        counts={"assets": len(assets), "files": len(files), "written": actions.count("written"),
                "unchanged": actions.count("unchanged"), "skipped": len(skipped), "removed": len(removed),
                "atlas_frames": len(frames)},
        files=files, atlases=atlases, sounds=record["sounds"], skipped=skipped, removed=removed, outputs=outputs,
    )  # fmt: skip


def _prune_empty_dirs(directory: Path, root: Path) -> None:
    root = root.resolve()
    directory = directory.resolve()
    while directory != root and root in directory.parents and not any(directory.iterdir()):
        directory.rmdir()
        directory = directory.parent

