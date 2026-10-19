"""Small shared helpers: timestamps, identifier normalization, JSON columns."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from assetforge.errors import InvalidInputError

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*$")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def slugify(text: str, sep: str = "-") -> str:
    return _NON_ALNUM.sub(sep, text.lower()).strip(sep)


def normalize_name(text: str) -> str:
    """Asset and variant names: lowercase snake_case, e.g. 'Blue Button (2)' -> 'blue_button_2'."""
    name = slugify(text, "_")
    if not name:
        raise InvalidInputError(f"Invalid name {text!r}: it must contain letters or digits", value=text)
    return name


def normalize_slug(text: str, what: str) -> str:
    """Tags, projects, styles and collection names: lowercase kebab-case."""
    slug = slugify(text, "-")
    if not slug:
        raise InvalidInputError(f"Invalid {what} {text!r}: it must contain letters or digits", value=text)
    return slug


def normalize_optional_slug(text: str | None, what: str) -> str | None:
    if text is None or not text.strip():
        return None
    return normalize_slug(text, what)


def normalize_key(key: str | None) -> str | None:
    """Logical keys look like 'enemy/beetle/hit'. They are validated, never rewritten."""
    if key is None or not key.strip():
        return None
    value = key.strip().strip("/").lower()
    if not _KEY_RE.match(value):
        raise InvalidInputError(
            f"Invalid key {key!r}: use '/'-separated segments of a-z, 0-9, '.', '_' or '-'",
            value=key,
        )
    return value


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def chunks(items: list[Any], size: int = 500) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def qmarks(count: int) -> str:
    return ",".join("?" * count)
