"""Sound direction: how a game should sound, as data an agent can write, merge and reuse.

The audio counterpart of `art.py`. A project's sound profile (genre, mood, acoustic space, key,
tempo range, instruments, loudness…) is stored in the library and applied to every sound,
ambience and piece of music generated for that project; a request's own fields win.

```yaml
game: crypt-run
style: dark-fantasy        # also picks the SFX preset when one has this name (see `audio recipes`)
genre: orchestral_adventure
mood: tense
space: cave                # dry, room, hall, cave, outdoor, underwater, space
key: D minor
tempo: [90, 130]
instruments: [strings, choir, taiko]
avoid: [chiptune, cartoon]
```
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from assetforge.core import db
from assetforge.core.util import dumps, loads, normalize_slug, utcnow
from assetforge.errors import InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

# Acoustic spaces → effect chain appended to sounds (reverb character of the game world).
SPACES: dict[str, list[dict[str, Any]]] = {
    "dry": [],
    "room": [{"name": "reverb", "params": {"room_size": 0.25, "damping": 0.6, "wet_level": 0.12, "dry_level": 0.9}}],
    "hall": [{"name": "reverb", "params": {"room_size": 0.75, "damping": 0.45, "wet_level": 0.25, "dry_level": 0.8}}],
    "cave": [{"name": "reverb", "params": {"room_size": 0.92, "damping": 0.25, "wet_level": 0.35, "dry_level": 0.75}}],
    "outdoor": [{"name": "reverb", "params": {"room_size": 0.4, "damping": 0.7, "wet_level": 0.07, "dry_level": 0.95}}],
    "underwater": [{"name": "lowpass", "params": {"cutoff_hz": 900}},
                   {"name": "reverb", "params": {"room_size": 0.6, "damping": 0.8, "wet_level": 0.3, "dry_level": 0.8}}],
    "space": [{"name": "reverb", "params": {"room_size": 0.98, "damping": 0.2, "wet_level": 0.4, "dry_level": 0.7}}],
}  # fmt: skip


class SoundDirection(BaseModel):
    """How a game sounds. Every field is optional; free text is fine where names are not listed."""

    model_config = ConfigDict(extra="forbid")

    style: str | None = Field(None, description="Overall sonic style; also the default SFX preset when one matches")
    description: str | None = None
    genre: str | None = Field(None, description="Music genre (see `music genres`) or free text")
    mood: str | None = Field(None, description="e.g. tense, cosy, heroic, melancholy, playful")
    era: str | None = Field(None, description="e.g. 8-bit, 80s, medieval, futuristic")
    space: str | None = Field(None, description=f"Acoustic space: {', '.join(SPACES)}")
    key: str | None = Field(None, description="Musical key for music and tonal stingers, e.g. D minor")
    tempo: list[float] = Field(default_factory=list, description="[bpm] or [min, max]")
    instruments: list[str] = Field(default_factory=list, description="Preferred instruments (see `music instruments`)")
    sfx_preset: str | None = Field(None, description="Default SFX preset (soft, retro, heavy, magical…)")
    loudness: dict[str, float] = Field(default_factory=dict, description="LUFS per kind: sfx, ambience, music")
    sample_rate: int | None = None
    format: str | None = Field(None, description="Delivery format: ogg (default), mp3 or wav")
    avoid: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("tempo", mode="before")
    @classmethod
    def _tempo(cls, value: Any) -> list[float]:
        if value is None:
            return []
        items = [value] if isinstance(value, int | float | str) else list(value)
        tempo = [float(v) for v in items]
        if len(tempo) > 2 or any(not 30 <= t <= 300 for t in tempo):
            raise ValueError("tempo is [bpm] or [min, max] between 30 and 300")
        return sorted(tempo)

    @field_validator("instruments", "avoid", mode="before")
    @classmethod
    def _list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        items = [value] if isinstance(value, str) else list(value)
        return [str(v).strip() for v in items if str(v).strip()]

    @field_validator("space")
    @classmethod
    def _space(cls, value: str | None) -> str | None:
        if value is None:
            return None
        key = value.strip().lower()
        if key not in SPACES:
            raise ValueError(f"space must be one of {', '.join(SPACES)}")
        return key

    @field_validator("key")
    @classmethod
    def _key(cls, value: str | None) -> str | None:
        if not value:
            return None
        from assetforge.audio.music.theory import parse_key

        return parse_key(value).name

    def is_empty(self) -> bool:
        return not self.model_dump(exclude_defaults=True)

    def space_effects(self) -> list[dict[str, Any]]:
        return [dict(e) for e in SPACES.get(self.space or "dry", [])]


class SoundProfile(BaseModel):
    project: str
    direction: SoundDirection
    created_at: str
    updated_at: str


def merge(*layers: SoundDirection | None) -> SoundDirection:
    """Later layers win per field; `avoid` and `instruments` from later layers replace, `loudness` merges."""
    result: dict[str, Any] = {}
    for layer in layers:
        if layer is None:
            continue
        for key, value in layer.model_dump(exclude_defaults=True).items():
            if key == "avoid":
                result[key] = list(dict.fromkeys([*result.get(key, []), *value]))
            elif key == "loudness":
                result[key] = {**result.get(key, {}), **value}
            else:
                result[key] = value
    return SoundDirection(**result)


# ---------------------------------------------------------------------- profiles


def _row(row: sqlite3.Row) -> SoundProfile:
    return SoundProfile(project=row["project"], direction=SoundDirection(**loads(row["direction"], {})),
                        created_at=row["created_at"], updated_at=row["updated_at"])  # fmt: skip


def find_profile(lib: Library, project: str | None) -> SoundProfile | None:
    if not project:
        return None
    found = lib.conn.execute("SELECT * FROM sound_profiles WHERE project = ?",
                             (normalize_slug(project, "project"),)).fetchone()  # fmt: skip
    return _row(found) if found else None


def get_profile(lib: Library, project: str) -> SoundProfile:
    profile = find_profile(lib, project)
    if profile is None:
        raise NotFoundError(f"Project {project!r} has no sound profile",
                            hint="Create one: assetforge sound profile set PROJECT --file sound.yaml")  # fmt: skip
    return profile


def list_profiles(lib: Library) -> list[SoundProfile]:
    return [_row(r) for r in lib.conn.execute("SELECT * FROM sound_profiles ORDER BY project")]


def parse_profile_yaml(text: str) -> tuple[str | None, SoundDirection | None]:
    """A sound profile file, or an art profile file with a `sound:` section. `game:` names the project."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise InvalidInputError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("A sound profile must be a YAML mapping")
    project = data.pop("game", None) or data.pop("project", None)
    if "sound" in data:
        data = data["sound"] or {}
        if not isinstance(data, dict):
            raise InvalidInputError("`sound:` must be a mapping")
    elif set(data) - set(SoundDirection.model_fields):
        return (str(project) if project else None), None  # an art-only profile file
    try:
        return (str(project) if project else None), SoundDirection(**data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise InvalidInputError(f"Invalid sound profile: {problems}",
                                hint=f"Allowed keys: game, {', '.join(SoundDirection.model_fields)}") from exc  # fmt: skip


def profile_yaml(profile: SoundProfile) -> str:
    data = {"game": profile.project, **profile.direction.model_dump(exclude_defaults=True)}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def set_profile(lib: Library, project: str, direction: SoundDirection, *, replace: bool = False) -> SoundProfile:
    """Create or update a project's sound profile. Without `replace`, fields merge into the existing one."""
    project = normalize_slug(project, "project")
    existing = find_profile(lib, project)
    merged = direction if replace or existing is None else merge(existing.direction, direction)
    now = utcnow()
    with db.transaction(lib.conn):
        lib.conn.execute(
            """
            INSERT INTO sound_profiles (project, direction, created_at, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (project) DO UPDATE SET direction = excluded.direction, updated_at = excluded.updated_at
            """,
            (project, dumps(merged.model_dump(exclude_defaults=True)), now, now),
        )
    return get_profile(lib, project)


def delete_profile(lib: Library, project: str) -> None:
    get_profile(lib, project)
    with db.transaction(lib.conn):
        lib.conn.execute("DELETE FROM sound_profiles WHERE project = ?", (normalize_slug(project, "project"),))


def resolve(lib: Library, project: str | None, request: SoundDirection | None = None,
            *, use_profile: bool = True) -> tuple[SoundDirection, list[str]]:  # fmt: skip
    """Profile < request. Returns the merged direction and the layers used."""
    profile = find_profile(lib, project) if use_profile else None
    layers = []
    if profile:
        layers.append(f"profile:{profile.project}")
    if request and not request.is_empty():
        layers.append("request")
    return merge(profile.direction if profile else None, request), layers


def loudness_target(direction: SoundDirection, kind: str) -> float:
    from assetforge.audio.master import LOUDNESS_TARGETS

    return float(direction.loudness.get(kind, LOUDNESS_TARGETS.get(kind, -16.0)))
