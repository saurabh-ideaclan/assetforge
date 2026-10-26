"""`assetforge add`: catalog a file the agent made itself (SVG, procedural script output, hand-edited art).

Not every asset should come from an image model. A coding agent can write a crisp SVG icon or a
NumPy/Pillow script for a pattern or particle; `add` stores that output with the same discovery
metadata, art direction, QC and provenance as generated assets, so it is found and reused later.
SVG keeps the vector master (variant `svg`) and gets a PNG rendering (resvg) as the primary.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge import art
from assetforge.art import ArtDirection
from assetforge.core import collections as collections_mod
from assetforge.core import qc, visual
from assetforge.core.catalog import Library, normalize_tags
from assetforge.core.metadata import kind_for_ext
from assetforge.core.models import AssetType, CommercialUse, FileKind, NewAsset, Origin, Scope, Status
from assetforge.core.util import normalize_optional_slug
from assetforge.errors import InvalidInputError, NotFoundError

AddMethod = Literal["svg", "procedural", "file", "manual"]
SCRIPT_LIMIT = 64_000  # characters of generator source kept as provenance


class AddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    method: AddMethod = "file"
    type: AssetType = AssetType.IMAGE
    name: str | None = None
    key: str | None = None
    description: str = ""
    style: str | None = None
    art: ArtDirection | None = None
    project: str | None = None
    scope: Scope | None = None
    tags: list[str] = Field(default_factory=list)
    category: str | None = None
    subcategory: str | None = None
    theme: str | None = None
    genre: str | None = None
    script: str | None = Field(None, description="Path to the generator script (kept as provenance)")
    collection: str | None = None
    size: str | None = Field(None, description="SVG render size WxH or a longest side like 512")
    status: Status = Status.CANDIDATE
    license: str | None = None
    commercial_use: CommercialUse | None = None


class AddResult(BaseModel):
    asset_id: str
    variant_id: str
    path: str
    created: bool
    method: str
    master_variant_id: str | None = None
    qc: dict = Field(default_factory=dict)
    near_duplicates: list[dict] = Field(default_factory=list)


def _render_size(size: str | None, native: tuple[int | None, int | None]) -> dict[str, int]:
    if size:
        text = size.lower().strip()
        if "x" in text:
            w, h = (int(v) for v in text.split("x"))
            return {"width": w, "height": h}
        return {"size": int(text)}
    w, h = native
    if w and h and max(w, h) >= 128:
        return {"width": w, "height": h}
    return {"size": 512}  # tiny viewBoxes (24x24 icons) render at a useful game size


def add_file(lib: Library, req: AddRequest) -> AddResult:
    src = Path(req.path).expanduser()
    if not src.is_file():
        raise NotFoundError(f"File not found: {req.path}", hint="Write the file first, then pass its path")
    kind = kind_for_ext(src.suffix)
    if req.method == "svg" and kind != FileKind.VECTOR:
        raise InvalidInputError("method svg needs an .svg file")
    if kind not in (FileKind.IMAGE, FileKind.VECTOR, FileKind.AUDIO):
        raise InvalidInputError(f"Can't add {src.suffix} files here; use `assetforge import`")
    project = normalize_optional_slug(req.project, "project")
    resolved = art.resolve(lib, project=project, style=req.style, art=req.art) if kind != FileKind.AUDIO else None
    style = resolved.style if resolved else art.style_slug(req.style)
    scope = req.scope or (Scope.STYLE if style else Scope.GLOBAL)
    generator: dict = {"engine": "agent", "method": req.method, "source_file": src.name}
    if resolved and not resolved.direction.is_empty():
        generator |= {"art": resolved.direction.model_dump(exclude_defaults=True), "art_layers": resolved.layers}
    if req.script:
        script = Path(req.script).expanduser()
        if not script.is_file():
            raise NotFoundError(f"Script not found: {req.script}")
        generator |= {"script_name": script.name, "script": script.read_text(errors="replace")[:SCRIPT_LIMIT]}
    origin = Origin.IMPORT if req.method in ("file", "manual") else Origin.PROCEDURAL
    collection_id = collections_mod.ensure_collection(lib, req.collection, project=project).id if req.collection else None
    spec = NewAsset(
        name=req.name or src.stem, type=req.type, key=req.key, description=req.description, scope=scope,
        project=project, style=style, category=req.category, subcategory=req.subcategory,
        theme=req.theme or (resolved.direction.theme if resolved else None),
        genre=req.genre or (resolved.direction.genre if resolved else None), status=req.status, origin=origin,
        license=req.license or ("Agent-made" if origin is Origin.PROCEDURAL else None),
        commercial_use=req.commercial_use or (CommercialUse.ALLOWED if origin is Origin.PROCEDURAL else CommercialUse.UNKNOWN),
        tags=normalize_tags([req.type.value, *([style] if style else []), req.method, *req.tags]),
        metadata={"generator": generator, "expect": {"size": req.size} if req.size and kind == FileKind.IMAGE else {}},
    )  # fmt: skip
    label = "svg" if kind == FileKind.VECTOR else "original"
    reg = lib.register_file(src, spec, label=label, params={"method": req.method}, collection_id=collection_id,
                            original_filename=src.name)  # fmt: skip
    master_id = reg.variant_id if kind == FileKind.VECTOR else None
    if kind == FileKind.VECTOR:
        asset = lib.get_asset(reg.asset_id)
        png = next((v for v in asset.variants if v.label == "png"), None)
        if png is None:
            from assetforge.image.svg import render_svg

            options = _render_size(req.size, (asset.primary_variant.file.width, asset.primary_variant.file.height)
                                   if asset.primary_variant else (None, None))  # fmt: skip
            image = render_svg(src, **options)
            tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="svg-"))
            try:
                out = tmp / f"{asset.name}.png"
                image.save(out, optimize=True)
                png = lib.add_variant(reg.asset_id, out, label="png", parent_variant_id=master_id,
                                      params={"rendered_from": "svg", "renderer": "resvg", **options}, make_primary=True)  # fmt: skip
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        primary = png
    else:
        primary = lib.get_asset(reg.asset_id).primary_variant
    assert primary is not None
    report = qc.check_asset(lib, reg.asset_id)
    return AddResult(asset_id=reg.asset_id, variant_id=primary.id, path=primary.file.path, created=reg.created,
                     method=req.method, master_variant_id=master_id, qc=report,
                     near_duplicates=visual.near_duplicates(lib, reg.asset_id) if kind != FileKind.AUDIO else [])  # fmt: skip
