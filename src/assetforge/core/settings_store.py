"""Library-wide settings shared by every agent using the library (budgets, provider limits, queue tuning).

Values live in the catalog's `settings` table, so one `assetforge config set` applies to all
agents and processes. Only the keys declared in SETTINGS are accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from assetforge.core import db
from assetforge.core.util import utcnow
from assetforge.errors import InvalidInputError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library


@dataclass(frozen=True)
class SettingSpec:
    type: type
    default: Any
    description: str
    minimum: float | None = None


SETTINGS: dict[str, SettingSpec] = {
    "budget.daily_usd": SettingSpec(float, None, "Max provider spend per UTC day (unset = no limit)", 0),
    "budget.monthly_usd": SettingSpec(float, None, "Max provider spend per UTC month (unset = no limit)", 0),
    "budget.per_request_usd": SettingSpec(float, None, "Default max estimated cost of one generation call", 0),
    "limits.default.max_concurrent": SettingSpec(int, 2, "Concurrent calls per provider (all agents)", 1),
    "limits.default.rpm": SettingSpec(int, 30, "Requests per minute per provider (all agents)", 1),
    "queue.lease_seconds": SettingSpec(int, 300, "How long a job lease lasts without a heartbeat", 30),
    "queue.max_attempts": SettingSpec(int, 3, "Attempts before a queued request job fails", 1),
    "request.reuse_threshold": SettingSpec(float, 0.7, "Min search coverage (0-1) for `request` to reuse", 0),
    "request.wait_seconds": SettingSpec(int, 900, "How long `request` waits for an identical in-flight job", 1),
    "pricing.auto_update": SettingSpec(bool, True, "Refresh model prices from the internet when they get stale"),
    "pricing.max_age_days": SettingSpec(float, 7.0, "Refresh prices older than this many days", 0),
    "pricing.source_url": SettingSpec(str, "https://raw.githubusercontent.com/BerriAI/litellm/main/"
                                      "model_prices_and_context_window.json",
                                      "Machine-readable price catalog (LiteLLM format)"),
}


def _parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"not a boolean: {raw!r}")
_PROVIDER_KEY = re.compile(r"^limits\.(?P<provider>[a-z0-9_-]+)\.(?P<name>max_concurrent|rpm)$")


def spec_for(key: str) -> SettingSpec:
    if key in SETTINGS:
        return SETTINGS[key]
    if m := _PROVIDER_KEY.match(key):
        return SETTINGS[f"limits.default.{m.group('name')}"]
    raise InvalidInputError(f"Unknown setting {key!r}", known=[*SETTINGS, "limits.<provider>.max_concurrent",
                                                                 "limits.<provider>.rpm"])  # fmt: skip


def coerce(key: str, raw: Any) -> Any:
    spec = spec_for(key)
    try:
        value = _parse_bool(raw) if spec.type is bool else spec.type(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"{key} must be a {spec.type.__name__}", value=raw) from exc
    if spec.minimum is not None and spec.type in (int, float) and value < spec.minimum:
        raise InvalidInputError(f"{key} must be at least {spec.minimum}", value=raw)
    return value


def get(lib: Library, key: str) -> Any:
    spec = spec_for(key)
    row = lib.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None:
        return _parse_bool(row["value"]) if spec.type is bool else spec.type(row["value"])
    if key not in SETTINGS and (m := _PROVIDER_KEY.match(key)):  # per-provider limit falls back to the default
        return get(lib, f"limits.default.{m.group('name')}")
    return spec.default


def set_value(lib: Library, key: str, raw: Any) -> Any:
    value = coerce(key, raw)
    with db.transaction(lib.conn):
        lib.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, str(value), utcnow()),
        )
    return value


def unset(lib: Library, key: str) -> None:
    spec_for(key)
    with db.transaction(lib.conn):
        lib.conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def all_settings(lib: Library) -> dict[str, dict[str, Any]]:
    stored = {r["key"]: r["value"] for r in lib.conn.execute("SELECT key, value FROM settings ORDER BY key")}
    out = {}
    for key, spec in SETTINGS.items():
        out[key] = {"value": get(lib, key), "default": spec.default, "set": key in stored, "description": spec.description}
    for key in stored:
        if key not in out:
            out[key] = {"value": get(lib, key), "default": None, "set": True, "description": spec_for(key).description}
    return out
