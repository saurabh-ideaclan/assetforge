"""Registering processed derivatives: a new variant on the same asset, or a new linked asset.

Processing is deterministic, so a derivative is identified by (source variant, ops). Asking for
the same derivative again returns the existing variant instead of failing or duplicating it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assetforge.core.catalog import Library
from assetforge.core.models import Asset, LineageRelation, NewAsset, Origin, Variant
from assetforge.core.util import normalize_name
from assetforge.errors import ConflictError, InvalidInputError


@dataclass(frozen=True)
class Derivative:
    asset_id: str
    variant_id: str
    label: str
    path: str
    reused: bool
    new_asset: bool


def source_variant(asset: Asset, variant_ref: str | None) -> Variant:
    if variant_ref:
        variant = next((v for v in asset.variants if v.id == variant_ref or v.label == variant_ref), None)
        if variant is None:
            raise InvalidInputError(f"{asset.id} has no variant {variant_ref!r}", variants=[v.label for v in asset.variants])
        return variant
    primary = asset.primary_variant
    if primary is None:
        raise InvalidInputError(f"{asset.id} has no files")
    # A primary that is itself a processed derivative is a leaf: process what it was made from, so
    # repeating a command is a no-op and steps never compound (trim of a trim). Use --variant to chain.
    while primary.params.get("processor") and primary.parent_variant_id:
        parent = next((v for v in asset.variants if v.id == primary.parent_variant_id), None)
        if parent is None:
            break
        primary = parent
    return primary


def register_derivative(
    lib: Library,
    asset: Asset,
    source: Variant,
    output: Path,
    *,
    label: str,
    params: dict[str, Any],
    make_primary: bool = False,
    new_asset: bool = False,
    name: str | None = None,
) -> Derivative:
    label = normalize_name(label)
    if new_asset:
        reg = lib.register_file(
            output,
            NewAsset(
                name=name or f"{asset.name}_{label}",
                type=asset.type,
                description=asset.description,
                scope=asset.scope,
                project=asset.project,
                style=asset.style,
                status=asset.status,
                origin=Origin.DERIVED,
                license=asset.license,
                commercial_use=asset.commercial_use,  # restrictions travel with derived files
                tags=[*asset.tags, "derived"],
                metadata={"derived": {"from_asset": asset.id, "from_variant": source.id, **params}},
            ),
            label="original",
            params={**params, "derived_from_variant": source.id},
        )
        if reg.created:
            lib.add_lineage(reg.asset_id, asset.id, LineageRelation.DERIVED_FROM, related_variant_id=source.id)
        derived = lib.get_asset(reg.asset_id)
        variant = next(v for v in derived.variants if v.id == reg.variant_id)
        return Derivative(reg.asset_id, reg.variant_id, variant.label, variant.file.path, not reg.created, True)

    existing = next((v for v in asset.variants if v.label == label), None)
    if existing is not None:
        same = existing.parent_variant_id == source.id and existing.params.get("ops") == params.get("ops")
        if not same:
            raise ConflictError(
                f"{asset.id} already has a different variant labelled {label!r}; pass another --label",
                variant_id=existing.id,
            )
        if make_primary and asset.primary_variant and asset.primary_variant.id != existing.id:
            lib.set_primary_variant(asset.id, existing.id)
        return Derivative(asset.id, existing.id, label, existing.file.path, True, False)
    variant = lib.add_variant(asset.id, output, label=label, parent_variant_id=source.id, params=params,
                              make_primary=make_primary)  # fmt: skip
    return Derivative(asset.id, variant.id, label, variant.file.path, False, False)
