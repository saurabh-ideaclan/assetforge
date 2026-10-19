"""Style profiles: a named art direction (description, palette, instructions, reference assets).

An image request with `--style cute-jungle` tags the asset with that style and, when a profile
of that name exists, adds its instructions and palette to the prompt and its reference
images to the provider call, which keeps a game's assets visually consistent.
"""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING

from assetforge.core import db
from assetforge.core.models import StyleProfile
from assetforge.core.util import dumps, loads, normalize_slug, utcnow
from assetforge.errors import ConflictError, InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def normalize_palette(colours: list[str]) -> list[str]:
    result = []
    for raw in colours:
        for part in filter(None, (c.strip() for c in raw.split(","))):
            match = _HEX.match(part)
            if not match:
                raise InvalidInputError(f"Palette colours must be hex like #4CAF50, got {part!r}")
            value = "#" + match.group(1).upper()
            if value not in result:
                result.append(value)
    return result


def _row(row: sqlite3.Row) -> StyleProfile:
    return StyleProfile(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        palette=loads(row["palette"], []),
        instructions=row["instructions"],
        reference_asset_ids=loads(row["reference_asset_ids"], []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def find_style(lib: Library, ref: str) -> StyleProfile | None:
    ref = ref.strip()
    if ref.startswith("sty_"):
        row = lib.conn.execute("SELECT * FROM style_profiles WHERE id = ?", (ref,)).fetchone()
    else:
        row = lib.conn.execute(
            "SELECT * FROM style_profiles WHERE name = ?", (normalize_slug(ref, "style"),)
        ).fetchone()
    return _row(row) if row else None


def get_style(lib: Library, ref: str) -> StyleProfile:
    style = find_style(lib, ref)
    if style is None:
        raise NotFoundError(f"No style profile matches {ref!r}", ref=ref)
    return style


def list_styles(lib: Library) -> list[StyleProfile]:
    return [_row(r) for r in lib.conn.execute("SELECT * FROM style_profiles ORDER BY name")]


def create_style(
    lib: Library,
    name: str,
    *,
    description: str = "",
    palette: list[str] | None = None,
    instructions: str = "",
    references: list[str] | None = None,
) -> StyleProfile:
    name = normalize_slug(name, "style")
    colours = normalize_palette(palette or [])
    ref_ids = list(dict.fromkeys(lib.resolve_asset_id(r) for r in references or []))
    now = utcnow()
    with db.transaction(lib.conn):
        if lib.conn.execute("SELECT 1 FROM style_profiles WHERE name = ?", (name,)).fetchone():
            raise ConflictError(f"Style {name!r} already exists", name=name)
        style_id = db.next_id(lib.conn, "sty")
        lib.conn.execute(
            "INSERT INTO style_profiles (id, name, description, palette, instructions, reference_asset_ids, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (style_id, name, description, dumps(colours), instructions, dumps(ref_ids), now, now),
        )
    return get_style(lib, style_id)


def update_style(
    lib: Library,
    ref: str,
    *,
    description: str | None = None,
    palette: list[str] | None = None,
    instructions: str | None = None,
    add_references: list[str] | None = None,
    remove_references: list[str] | None = None,
) -> StyleProfile:
    style = get_style(lib, ref)
    refs = list(style.reference_asset_ids)
    for r in add_references or []:
        asset_id = lib.resolve_asset_id(r)
        if asset_id not in refs:
            refs.append(asset_id)
    for r in remove_references or []:
        asset_id = lib.resolve_asset_id(r)
        refs = [x for x in refs if x != asset_id]
    with db.transaction(lib.conn):
        lib.conn.execute(
            "UPDATE style_profiles SET description = ?, palette = ?, instructions = ?, reference_asset_ids = ?, "
            "updated_at = ? WHERE id = ?",
            (
                style.description if description is None else description,
                dumps(style.palette if palette is None else normalize_palette(palette)),
                style.instructions if instructions is None else instructions,
                dumps(refs),
                utcnow(),
                style.id,
            ),
        )
    return get_style(lib, style.id)


def delete_style(lib: Library, ref: str) -> StyleProfile:
    """Removes the profile only; assets keep their `style` tag."""
    style = get_style(lib, ref)
    with db.transaction(lib.conn):
        lib.conn.execute("DELETE FROM style_profiles WHERE id = ?", (style.id,))
    return style
