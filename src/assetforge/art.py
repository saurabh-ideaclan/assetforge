"""Art direction: what an image should look like, as data an LLM can write, merge and reuse.

Three layers feed a generation request, later ones winning field by field:

1. **Presets**: reusable looks shipped as YAML (`assetforge/art_presets/*.yaml`) or dropped into
   `$ASSETFORGE_HOME/presets/` (same name overrides the built-in). Optional: any free text works as a style.
2. **Project art profile**: a game's visual identity (style, mood, palette, perspective, avoid…), stored
   in the library and editable as YAML with `art profile set/show`.
3. **The request's own `art`**: per-asset overrides and fully custom direction.

`render()` turns the merged direction into labelled prompt lines, so the image model gets specific
guidance on lighting, shapes, shading and composition instead of a bare style name.
"""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from assetforge.core import db
from assetforge.core.styles import normalize_palette
from assetforge.core.util import dumps, loads, normalize_slug, slugify, utcnow
from assetforge.errors import InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library
    from assetforge.core.models import StyleProfile

Detail = Literal["low", "medium", "high"]
TEXT_FIELDS = ("description", "mood", "lighting", "perspective", "shapes", "outline", "shading", "texture",
               "composition", "notes")  # fmt: skip


class ArtDirection(BaseModel):
    """How an asset should look. Every field is optional free text; nothing is limited to presets."""

    model_config = ConfigDict(extra="forbid")

    style: str | None = Field(None, description="A preset name (see `art presets`) or any style description")
    description: str | None = Field(None, description="The overall look in a sentence or two")
    genre: str | None = Field(None, description="Game genre, e.g. survival horror, match-3 puzzle")
    theme: str | None = Field(None, description="Setting/theme, e.g. derelict space station")
    mood: str | None = None
    palette: list[str] = Field(default_factory=list, description="Hex colours, most important first")
    lighting: str | None = None
    perspective: str | None = Field(None, description="Camera/view: side view, top-down, isometric, 3/4 front…")
    shapes: str | None = Field(None, description="Shape language and proportions")
    outline: str | None = Field(None, description="Line work: none, thin dark, thick coloured, sketchy…")
    shading: str | None = None
    texture: str | None = Field(None, description="Surface/brush texture")
    detail: Detail | None = Field(None, description="high also raises generation quality (larger images)")
    composition: str | None = None
    notes: str | None = Field(None, description="Anything else the image model should follow")
    avoid: list[str] = Field(default_factory=list, description="Things to keep out, e.g. toy-like objects")
    references: list[str] = Field(default_factory=list, description="Asset IDs/paths whose look to match")

    @field_validator("palette", mode="before")
    @classmethod
    def _palette(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return normalize_palette([value] if isinstance(value, str) else [str(v) for v in value])

    @field_validator("avoid", "references", mode="before")
    @classmethod
    def _list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        items = [value] if isinstance(value, str) else list(value)
        return [str(v).strip() for v in items if str(v).strip()]

    def is_empty(self) -> bool:
        return not self.model_dump(exclude_defaults=True)


class Preset(ArtDirection):
    name: str
    summary: str = ""
    best_for: list[str] = Field(default_factory=list)
    quality: str | None = Field(None, description="Suggested generation quality")
    process: str | None = Field(None, description="Suggested `process` preset after generation")
    source: str = "built-in"

    def direction(self) -> ArtDirection:
        data = self.model_dump(include=set(ArtDirection.model_fields), exclude_defaults=True)
        data["style"] = self.name
        return ArtDirection(**data)


class ArtProfile(BaseModel):
    project: str
    direction: ArtDirection
    reference_asset_ids: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------- merge + render


def merge(*layers: ArtDirection | None) -> ArtDirection:
    """Later layers win per field; `avoid` and `references` accumulate; `palette` is replaced."""
    result: dict[str, Any] = {}
    for layer in layers:
        if layer is None:
            continue
        for key, value in layer.model_dump(exclude_defaults=True).items():
            if key in ("avoid", "references"):
                result[key] = list(dict.fromkeys([*result.get(key, []), *value]))
            else:
                result[key] = value
    return ArtDirection(**result)


def _words(text: str) -> str:
    return text.replace("_", " ").strip().rstrip(".")


def render(direction: ArtDirection, *, preset: Preset | None = None) -> str:
    """Prompt lines for a merged direction (empty string when there is nothing to say)."""
    d = direction
    lines: list[str] = []
    label = d.style.replace("-", " ") if d.style and " " not in d.style.strip() else d.style  # slug → words
    style = preset.summary if preset and preset.summary else (_words(label) if label else None)
    if style or d.description:
        lines.append("Art direction: " + ". ".join(_words(x) for x in (style, d.description) if x) + ".")
    if d.genre or d.theme:
        lines.append("Made for " + " ".join(x for x in (
            f"a {_words(d.genre)} game" if d.genre else "a game",
            f"with a {_words(d.theme)} theme" if d.theme else "",
        ) if x) + ".")  # fmt: skip
    for label, value in (("Mood", d.mood), ("Lighting", d.lighting), ("Perspective", d.perspective),
                         ("Shapes and proportions", d.shapes), ("Outlines", d.outline), ("Shading", d.shading),
                         ("Surface texture", d.texture), ("Composition", d.composition)):  # fmt: skip
        if value:
            lines.append(f"{label}: {_words(value)}.")
    if d.palette:
        lines.append("Colour palette (keep to these hues): " + ", ".join(d.palette) + ".")
    if d.detail:
        lines.append({"low": "Detail: low, simple readable forms.",
                      "medium": "Detail: moderate.",
                      "high": "Detail: high, intricate surfaces and fine rendering."}[d.detail])  # fmt: skip
    if d.notes:
        lines.append(_words(d.notes) + ".")
    if d.avoid:
        lines.append("Do not include: " + ", ".join(_words(a) for a in d.avoid) + ".")
    return " ".join(lines)


def style_slug(style: str | None) -> str | None:
    """The catalog `style` value for a style name or free-text description."""
    if not style or not style.strip():
        return None
    return slugify(style)[:48].strip("-") or None


def from_style_profile(profile: StyleProfile) -> ArtDirection:
    """A legacy `style create` profile as an art-direction layer."""
    return ArtDirection(description=profile.description or None, palette=profile.palette,
                        notes=profile.instructions or None, references=profile.reference_asset_ids)  # fmt: skip


# ---------------------------------------------------------------------- presets


def _load_preset(text: str, source: str, fallback_name: str) -> Preset:
    try:
        data = yaml.safe_load(text) or {}
        data.setdefault("name", fallback_name)
        data["name"] = normalize_slug(str(data["name"]), "preset")
        return Preset(**data, source=source)
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        raise InvalidInputError(f"Bad art preset {source}: {exc}") from exc


@lru_cache(maxsize=1)
def builtin_presets() -> dict[str, Preset]:
    found: dict[str, Preset] = {}
    for entry in sorted(resources.files("assetforge.art_presets").iterdir(), key=lambda e: e.name):
        if entry.name.endswith((".yaml", ".yml")):
            preset = _load_preset(entry.read_text(), "built-in", entry.name.rsplit(".", 1)[0])
            found[preset.name] = preset
    return found


def presets(lib: Library | None = None) -> dict[str, Preset]:
    """Built-in presets plus the library's own (`$ASSETFORGE_HOME/presets/*.yaml` win on name clashes)."""
    result = dict(builtin_presets())
    if lib is not None:
        folder = lib.paths.home / "presets"
        if folder.is_dir():
            for path in sorted(folder.glob("*.y*ml")):
                preset = _load_preset(path.read_text(), str(path), path.stem)
                result[preset.name] = preset
    return result


def find_preset(lib: Library | None, style: str | None) -> Preset | None:
    slug = style_slug(style)
    return presets(lib).get(slug) if slug else None


def get_preset(lib: Library | None, name: str) -> Preset:
    preset = find_preset(lib, name)
    if preset is None:
        raise NotFoundError(f"No art preset {name!r}", hint=f"Available: {', '.join(presets(lib))}")
    return preset


# ---------------------------------------------------------------------- project profiles


def _profile_row(row: sqlite3.Row) -> ArtProfile:
    return ArtProfile(project=row["project"], direction=ArtDirection(**loads(row["direction"], {})),
                      reference_asset_ids=loads(row["reference_asset_ids"], []), created_at=row["created_at"],
                      updated_at=row["updated_at"])  # fmt: skip


def find_profile(lib: Library, project: str | None) -> ArtProfile | None:
    if not project:
        return None
    row = lib.conn.execute("SELECT * FROM art_profiles WHERE project = ?", (normalize_slug(project, "project"),))
    found = row.fetchone()
    return _profile_row(found) if found else None


def get_profile(lib: Library, project: str) -> ArtProfile:
    profile = find_profile(lib, project)
    if profile is None:
        raise NotFoundError(f"Project {project!r} has no art profile",
                            hint="Create one: assetforge art profile set PROJECT --file art.yaml")  # fmt: skip
    return profile


def list_profiles(lib: Library) -> list[ArtProfile]:
    return [_profile_row(r) for r in lib.conn.execute("SELECT * FROM art_profiles ORDER BY project")]


def parse_profile_yaml(text: str) -> tuple[str | None, ArtDirection]:
    """Read a profile file. `game:`/`project:` names the project; everything else is ArtDirection."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise InvalidInputError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("An art profile must be a YAML mapping")
    project = data.pop("game", None) or data.pop("project", None)
    try:
        return (str(project) if project else None), ArtDirection(**data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise InvalidInputError(f"Invalid art profile: {problems}",
                                hint=f"Allowed keys: game, {', '.join(ArtDirection.model_fields)}") from exc  # fmt: skip


def profile_yaml(profile: ArtProfile) -> str:
    data = {"game": profile.project, **profile.direction.model_dump(exclude_defaults=True)}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def set_profile(lib: Library, project: str, direction: ArtDirection, *, replace: bool = False) -> ArtProfile:
    """Create or update a project's profile. Without `replace`, given fields are merged into the existing one."""
    from assetforge.image.engine import resolve_reference

    project = normalize_slug(project, "project")
    existing = find_profile(lib, project)
    merged = direction if replace or existing is None else merge(existing.direction, direction)
    if merged.style and find_preset(lib, merged.style) is None and len(merged.style) > 80:
        raise InvalidInputError("style should be a preset name or a short label; put long text in description")
    ref_ids = []
    for ref in merged.references:
        resolved = resolve_reference(lib, ref, "style")
        ref_ids.append(resolved.asset_id or resolved.path)
    merged = merged.model_copy(update={"references": ref_ids})
    now = utcnow()
    with db.transaction(lib.conn):
        lib.conn.execute(
            """
            INSERT INTO art_profiles (project, direction, reference_asset_ids, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (project) DO UPDATE SET direction = excluded.direction,
                reference_asset_ids = excluded.reference_asset_ids, updated_at = excluded.updated_at
            """,
            (project, dumps(merged.model_dump(exclude_defaults=True)), dumps(ref_ids), now, now),
        )
    return get_profile(lib, project)


def delete_profile(lib: Library, project: str) -> None:
    get_profile(lib, project)
    with db.transaction(lib.conn):
        lib.conn.execute("DELETE FROM art_profiles WHERE project = ?", (normalize_slug(project, "project"),))


# ---------------------------------------------------------------------- resolution


class Resolved(BaseModel):
    """The art direction a request ends up with, and where each part came from."""

    direction: ArtDirection
    style: str | None = None  # catalog style slug
    preset: Preset | None = None
    layers: list[str] = Field(default_factory=list)
    style_profile: Any = None  # a legacy StyleProfile, when one matched
    profile_updated_at: str | None = None

    def text(self) -> str:
        return render(self.direction, preset=self.preset if self.preset and self.direction.style == self.preset.name
                      else None)  # fmt: skip

    def fingerprint_data(self) -> str:
        return json.dumps(self.direction.model_dump(exclude_defaults=True), sort_keys=True)


def resolve(lib: Library, *, project: str | None, style: str | None, art: ArtDirection | None,
            use_profile: bool = True) -> Resolved:  # fmt: skip
    """Merge preset(profile style) < profile < preset(requested style) < legacy style profile < request art."""
    from assetforge.core.styles import find_style

    profile = find_profile(lib, project) if use_profile else None
    requested_style = (art.style if art and art.style else None) or style
    layers: list[tuple[str, ArtDirection | None]] = []
    if profile:
        base = find_preset(lib, profile.direction.style)
        if base:
            layers.append((f"preset:{base.name}", base.direction()))
        layers.append((f"profile:{profile.project}", profile.direction))
    final_style = requested_style or (profile.direction.style if profile else None)
    preset = find_preset(lib, final_style)
    if requested_style and preset:
        layers.append((f"preset:{preset.name}", preset.direction()))
    legacy = None
    if final_style and (slug := style_slug(final_style)):
        try:
            legacy = find_style(lib, slug)
        except InvalidInputError:
            legacy = None
        if legacy:
            layers.append((f"style:{legacy.name}", from_style_profile(legacy)))
    if requested_style and not preset and not legacy:
        layers.append(("request:style", ArtDirection(style=requested_style)))
    if art and not art.is_empty():
        layers.append(("request:art", art))
    direction = merge(*(layer for _, layer in layers))
    if final_style and not direction.style:
        direction = direction.model_copy(update={"style": final_style})
    return Resolved(direction=direction, style=style_slug(direction.style), preset=preset,
                    layers=[name for name, layer in layers if layer is not None], style_profile=legacy,
                    profile_updated_at=profile.updated_at if profile else None)  # fmt: skip


def read_direction_file(path: str | Path) -> ArtDirection:
    """`--art-file`: a YAML/JSON ArtDirection (a `game:` key is ignored here)."""
    file = Path(path).expanduser()
    if not file.is_file():
        raise InvalidInputError(f"Art direction file not found: {path}")
    _, direction = parse_profile_yaml(file.read_text())
    return direction
