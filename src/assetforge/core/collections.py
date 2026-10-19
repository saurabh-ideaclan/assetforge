"""Collections: named sets of assets (a game, a character, a style, a purpose)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from assetforge.core import db
from assetforge.core.models import AssetSummary, Collection
from assetforge.core.util import normalize_optional_slug, normalize_slug, utcnow
from assetforge.errors import ConflictError, NotFoundError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

COLLECTION_KINDS = ("game", "character", "style", "type", "purpose", "custom")


def _row_to_collection(lib: Library, row: sqlite3.Row) -> Collection:
    count = lib.conn.execute(
        "SELECT count(*) FROM collection_assets WHERE collection_id = ?", (row["id"],)
    ).fetchone()[0]
    return Collection(**dict(row), asset_count=count)


def _find(lib: Library, ref: str) -> sqlite3.Row | None:
    ref = ref.strip()
    if ref.startswith("col_"):
        return lib.conn.execute("SELECT * FROM collections WHERE id = ?", (ref,)).fetchone()
    return lib.conn.execute(
        "SELECT * FROM collections WHERE name = ?", (normalize_slug(ref, "collection name"),)
    ).fetchone()


def get_collection(lib: Library, ref: str) -> Collection:
    row = _find(lib, ref)
    if row is None:
        raise NotFoundError(f"No collection matches {ref!r}", ref=ref)
    return _row_to_collection(lib, row)


def create_collection(
    lib: Library, name: str, *, kind: str = "custom", description: str = "", project: str | None = None
) -> Collection:
    name = normalize_slug(name, "collection name")
    if kind not in COLLECTION_KINDS:
        kind = normalize_slug(kind, "collection kind")
    now = utcnow()
    with db.transaction(lib.conn):
        if lib.conn.execute("SELECT 1 FROM collections WHERE name = ?", (name,)).fetchone():
            raise ConflictError(f"Collection {name!r} already exists", name=name)
        collection_id = db.next_id(lib.conn, "col")
        lib.conn.execute(
            "INSERT INTO collections (id, name, kind, description, project, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (collection_id, name, kind, description, normalize_optional_slug(project, "project"), now, now),
        )
    return get_collection(lib, collection_id)


def ensure_collection(lib: Library, name: str, *, project: str | None = None) -> Collection:
    """Get a collection by name or ID, creating it when it does not exist yet."""
    if row := _find(lib, name):
        return _row_to_collection(lib, row)
    kind = "game" if project else "custom"
    try:
        return create_collection(lib, name, kind=kind, project=project)
    except ConflictError:  # created concurrently by another process
        return get_collection(lib, name)


def list_collections(lib: Library) -> list[Collection]:
    rows = lib.conn.execute("SELECT * FROM collections ORDER BY name").fetchall()
    return [_row_to_collection(lib, row) for row in rows]


def add_asset_ids(lib: Library, collection_id: str, asset_ids: list[str], *, in_transaction: bool = False) -> int:
    """Idempotently add assets; returns how many were newly added."""

    def run() -> int:
        now = utcnow()
        added = 0
        for asset_id in asset_ids:
            cursor = lib.conn.execute(
                "INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) VALUES (?, ?, ?)",
                (collection_id, asset_id, now),
            )
            added += cursor.rowcount
        if added:
            lib.conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (now, collection_id))
        return added

    if in_transaction:
        return run()
    with db.transaction(lib.conn):
        return run()


def add_assets(lib: Library, ref: str, asset_refs: list[str]) -> tuple[Collection, int]:
    collection = get_collection(lib, ref)
    asset_ids = [lib.resolve_asset_id(a) for a in asset_refs]
    added = add_asset_ids(lib, collection.id, asset_ids)
    return get_collection(lib, collection.id), added


def remove_assets(lib: Library, ref: str, asset_refs: list[str]) -> tuple[Collection, int]:
    collection = get_collection(lib, ref)
    asset_ids = [lib.resolve_asset_id(a) for a in asset_refs]
    removed = 0
    with db.transaction(lib.conn):
        for asset_id in asset_ids:
            removed += lib.conn.execute(
                "DELETE FROM collection_assets WHERE collection_id = ? AND asset_id = ?", (collection.id, asset_id)
            ).rowcount
        if removed:
            lib.conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (utcnow(), collection.id))
    return get_collection(lib, collection.id), removed


def delete_collection(lib: Library, ref: str) -> Collection:
    """Deletes the grouping only; the assets themselves are untouched."""
    collection = get_collection(lib, ref)
    with db.transaction(lib.conn):
        lib.conn.execute("DELETE FROM collections WHERE id = ?", (collection.id,))
    return collection


def collection_assets(lib: Library, ref: str) -> tuple[Collection, list[AssetSummary]]:
    collection = get_collection(lib, ref)
    asset_ids = [
        r[0]
        for r in lib.conn.execute(
            "SELECT asset_id FROM collection_assets WHERE collection_id = ? ORDER BY added_at, asset_id",
            (collection.id,),
        )
    ]
    return collection, lib.summaries(asset_ids)
