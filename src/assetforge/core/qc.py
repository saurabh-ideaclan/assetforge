"""Quality checks on cataloged assets, stored on the asset so reviewers and agents can see them.

Issues are split into errors (the file is unusable: blank/empty image, silent/clipped audio,
missing or undecodable file) and warnings (worth a look: padding, no headroom, loop seams…).
Approving an asset whose QC has errors is refused unless forced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from assetforge.core.catalog import Library
from assetforge.core.models import Asset, AssetType, FileKind, Variant
from assetforge.core.util import utcnow
from assetforge.errors import AssetForgeError
from assetforge.validation.audio import analyze_file
from assetforge.validation.image import analyze_image

ERRORS = {"blank", "empty", "silent", "clipping", "missing_file", "undecodable"}
EXPECT_TRANSPARENCY = {AssetType.CHARACTER, AssetType.SPRITE, AssetType.ICON, AssetType.UI, AssetType.EFFECT}
LOOPABLE = {AssetType.MUSIC, AssetType.AMBIENCE}


def check_variant(asset: Asset, variant: Variant) -> dict[str, Any]:
    path = Path(variant.file.path)
    metrics: dict[str, Any] = {}
    if not path.is_file():
        issues = ["missing_file"]
    elif variant.file.kind == FileKind.IMAGE:
        try:
            with Image.open(path) as image:
                metrics = analyze_image(image, asset_type=asset.type,
                                        expect_transparency=asset.type in EXPECT_TRANSPARENCY)  # fmt: skip
            issues = metrics.pop("issues")
            metrics.pop("ok")
        except (OSError, ValueError):
            issues = ["undecodable"]
    elif variant.file.kind == FileKind.AUDIO:
        try:
            metrics = analyze_file(path, loopable=asset.type in LOOPABLE or "loop" in asset.tags)
            issues = metrics.pop("issues")
            metrics.pop("ok")
        except AssetForgeError:
            issues = ["undecodable"]
    else:
        issues = []
    errors = [i for i in issues if i in ERRORS]
    return {
        "variant_id": variant.id,
        "label": variant.label,
        "kind": str(variant.file.kind),
        "ok": not errors,
        "errors": errors,
        "warnings": [i for i in issues if i not in ERRORS],
        "metrics": metrics,
    }


def check_asset(lib: Library, ref: str, *, variant: str | None = None, store: bool = True) -> dict[str, Any]:
    """QC an asset's primary variant (or a chosen one) and record the result in its metadata."""
    asset = lib.get_asset(ref)
    target = next((v for v in asset.variants if v.id == variant or v.label == variant), None) if variant else None
    target = target or asset.primary_variant
    if target is None:
        report = {"variant_id": None, "label": None, "kind": None, "ok": False, "errors": ["missing_file"],
                  "warnings": [], "metrics": {}}  # fmt: skip
    else:
        report = check_variant(asset, target)
    result = {"asset_id": asset.id, "name": asset.name, "type": asset.type.value, "status": asset.status.value,
              **report, "checked_at": utcnow()}  # fmt: skip
    if store:
        lib.merge_metadata(asset.id, {"qc": {k: v for k, v in result.items() if k not in ("name", "type", "status")}},
                           event="qc")  # fmt: skip
    return result
