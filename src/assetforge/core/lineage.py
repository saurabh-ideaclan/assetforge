"""Lineage graphs: where an asset came from, what was made from it, and how its variants relate."""

from __future__ import annotations

from typing import Any

from assetforge.core.catalog import Library
from assetforge.core.models import Asset

MAX_DEPTH = 10


def _brief(asset: Asset) -> dict[str, Any]:
    return {"asset_id": asset.id, "name": asset.name, "type": asset.type.value, "status": asset.status.value,
            "origin": asset.origin.value}  # fmt: skip


def _walk(lib: Library, asset_id: str, direction: str, depth: int, seen: set[str]) -> list[dict[str, Any]]:
    if depth <= 0:
        return []
    if direction == "up":
        rows = lib.conn.execute(
            "SELECT related_asset_id AS other, relation FROM asset_lineage WHERE asset_id = ? ORDER BY id", (asset_id,)
        ).fetchall()
    else:
        rows = lib.conn.execute(
            "SELECT asset_id AS other, relation FROM asset_lineage WHERE related_asset_id = ? ORDER BY id", (asset_id,)
        ).fetchall()
    nodes = []
    for row in rows:
        if row["other"] in seen:
            continue
        seen.add(row["other"])
        node = {**_brief(lib.get_asset(row["other"])), "relation": row["relation"]}
        node["parents" if direction == "up" else "children"] = _walk(lib, row["other"], direction, depth - 1, seen)
        nodes.append(node)
    return nodes


def variant_tree(asset: Asset) -> list[dict[str, Any]]:
    by_parent: dict[str | None, list[Any]] = {}
    ids = {v.id for v in asset.variants}
    for v in asset.variants:
        parent = v.parent_variant_id if v.parent_variant_id in ids else None
        by_parent.setdefault(parent, []).append(v)

    def node(v: Any) -> dict[str, Any]:
        ops = v.params.get("ops")
        return {
            "variant_id": v.id,
            "label": v.label,
            "primary": asset.primary_variant is not None and asset.primary_variant.id == v.id,
            "file": f"{v.file.ext.lstrip('.')} {v.file.width}x{v.file.height}" if v.file.width
            else f"{v.file.ext.lstrip('.')} {v.file.duration_ms}ms" if v.file.duration_ms is not None
            else v.file.ext.lstrip("."),
            "ops": [o["op"] for o in ops] if ops else None,
            "children": [node(c) for c in by_parent.get(v.id, [])],
        }  # fmt: skip

    return [node(v) for v in by_parent.get(None, [])]


def lineage(lib: Library, ref: str, *, depth: int = MAX_DEPTH) -> dict[str, Any]:
    asset = lib.get_asset(ref)
    return {
        **_brief(asset),
        "ancestors": _walk(lib, asset.id, "up", depth, {asset.id}),
        "descendants": _walk(lib, asset.id, "down", depth, {asset.id}),
        "variants": variant_tree(asset),
    }
