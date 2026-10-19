"""Library location and on-disk layout."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HOME_ENV = "ASSETFORGE_HOME"
DEFAULT_HOME = "~/.assetforge"


def resolve_home(home: str | os.PathLike[str] | None = None) -> Path:
    """Explicit argument wins, then $ASSETFORGE_HOME, then ~/.assetforge."""
    raw = home or os.environ.get(HOME_ENV) or DEFAULT_HOME
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class LibraryPaths:
    home: Path

    @property
    def catalog(self) -> Path:
        return self.home / "catalog.sqlite3"

    @property
    def objects(self) -> Path:
        return self.home / "objects" / "sha256"

    @property
    def tmp(self) -> Path:
        return self.home / "tmp"

    @property
    def previews(self) -> Path:
        return self.home / "previews"

    @property
    def jobs(self) -> Path:
        return self.home / "jobs"

    @property
    def styles(self) -> Path:
        return self.home / "styles"

    @property
    def collections(self) -> Path:
        return self.home / "collections"

    @property
    def exports(self) -> Path:
        return self.home / "exports"

    def directories(self) -> tuple[Path, ...]:
        return (
            self.home,
            self.objects,
            self.tmp,
            self.previews,
            self.jobs,
            self.styles,
            self.collections,
            self.exports,
        )

    def ensure(self) -> None:
        for directory in self.directories():
            directory.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------- settings

SETTING_KEYS = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ASSETFORGE_IMAGE_PROVIDER",
    "ASSETFORGE_OPENAI_IMAGE_MODEL",
    "ASSETFORGE_OPENAI_EDIT_MODEL",
    "ASSETFORGE_GEMINI_IMAGE_MODEL",
    "ASSETFORGE_GEMINI_EDIT_MODEL",
    "ASSETFORGE_ENABLE_FAKE_PROVIDER",
)


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser (KEY=value, optional `export`, quotes, # comments). Only SETTING_KEYS are kept."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key in SETTING_KEYS:
            values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    """Provider keys and model overrides.

    Precedence: process environment > ./.env (the repo the agent runs in) > $ASSETFORGE_HOME/.env.
    Only AssetForge's own keys are read from .env files; nothing is exported to the environment.
    """

    values: dict[str, str]

    def get(self, key: str) -> str | None:
        value = self.values.get(key)
        return value or None

    @property
    def openai_api_key(self) -> str | None:
        return self.get("OPENAI_API_KEY")

    @property
    def gemini_api_key(self) -> str | None:
        return self.get("GEMINI_API_KEY") or self.get("GOOGLE_API_KEY")

    @property
    def fake_provider_enabled(self) -> bool:
        return (self.get("ASSETFORGE_ENABLE_FAKE_PROVIDER") or "").lower() in ("1", "true", "yes")


def load_settings(home: Path | None = None, cwd: Path | None = None) -> Settings:
    values: dict[str, str] = {}
    if home is not None:
        values |= read_env_file(home / ".env")
    values |= read_env_file((cwd or Path.cwd()) / ".env")
    values |= {k: os.environ[k] for k in SETTING_KEYS if os.environ.get(k)}
    return Settings(values)
