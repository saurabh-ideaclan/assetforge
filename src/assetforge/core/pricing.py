"""Model prices for cost tracking, refreshed from the internet.

Source: a machine-readable price catalog in LiteLLM's `model_prices_and_context_window.json`
format (configurable via `pricing.source_url`), which tracks the providers' official pricing pages.
`update()` fetches it, keeps only OpenAI/Gemini image models, and stores a timestamped snapshot.
Rates resolve field by field: manual override > latest snapshot > built-in defaults. AssetForge
works offline, and you can pin a price you negotiated.

Rates are USD per 1M tokens (`input_text`, `input_image`, `output_image`, `output_text`), plus USD per
image (`per_image`, `per_input_image`) as a fallback when a response carries no token counts.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from assetforge.core import db, settings_store
from assetforge.core.util import dumps, loads, utcnow
from assetforge.errors import InvalidInputError, StorageError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

FIELDS = ("input_text", "input_image", "output_image", "output_text", "per_image", "per_input_image")
OFFLINE_ENV = "ASSETFORGE_PRICING_OFFLINE"
FETCH_TIMEOUT = 20
KEEP_SNAPSHOTS = 20

# Built-in fallback (LiteLLM catalog, 2026-10-19). Used only when nothing fresher is known.
BUILTIN: dict[str, dict[str, float]] = {
    "openai/gpt-image-1": {"input_text": 5.0, "input_image": 10.0, "output_image": 40.0},
    "openai/gpt-image-1-mini": {"input_text": 2.0, "input_image": 2.5, "output_image": 8.0},
    "openai/gpt-image-1.5": {"input_text": 5.0, "input_image": 8.0, "output_image": 32.0, "output_text": 10.0},
    "openai/gpt-image-2": {"input_text": 5.0, "input_image": 8.0, "output_image": 30.0},
    "openai/gpt-image-2.5-flare": {"input_text": 5.0, "input_image": 8.0, "output_image": 30.0},
    "openai/gpt-image-2.5-sunburst": {"input_text": 5.0, "input_image": 8.0, "output_image": 30.0},
    "openai/chatgpt-image-latest": {"input_text": 5.0, "input_image": 8.0, "output_image": 32.0, "output_text": 10.0},
    "gemini/gemini-2.5-flash-image": {"input_text": 0.3, "input_image": 0.3, "output_image": 30.0,
                                      "output_text": 2.5, "per_image": 0.039},
    "gemini/gemini-3.1-flash-image": {"input_text": 0.5, "input_image": 0.5, "output_image": 60.0,
                                      "output_text": 3.0, "per_image": 0.045},
    "gemini/gemini-3.1-flash-lite-image": {"input_text": 0.25, "input_image": 0.25, "output_image": 30.0,
                                           "output_text": 1.5, "per_image": 0.0336, "per_input_image": 0.00028},
    "gemini/gemini-3-pro-image": {"input_text": 2.0, "input_image": 2.0, "output_image": 120.0,
                                  "output_text": 12.0, "per_image": 0.134, "per_input_image": 0.0011},
}  # fmt: skip
_DATED = re.compile(r"-(\d{4}-\d{2}-\d{2})$")


def model_key(provider: str, model: str) -> str:
    return f"{provider}/{model.removeprefix('models/')}"


def _candidates(provider: str, model: str) -> list[str]:
    base = model.removeprefix("models/")
    names = [base, base.removesuffix("-preview"), _DATED.sub("", base), _DATED.sub("", base).removesuffix("-preview")]
    return list(dict.fromkeys(model_key(provider, n) for n in names))


# ---------------------------------------------------------------------- catalog parsing


def parse_catalog(data: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Pick OpenAI and Gemini image-generation models out of a LiteLLM-format catalog."""
    models: dict[str, dict[str, float]] = {}
    for name, entry in data.items():
        if not isinstance(entry, dict) or entry.get("mode") != "image_generation":
            continue
        provider = entry.get("litellm_provider")
        if provider == "openai" and "/" not in name:
            key = f"openai/{name}"
        elif provider == "gemini" and name.startswith("gemini/"):
            key = name
        else:
            continue
        per_token = lambda field: entry.get(field)  # noqa: E731
        rates: dict[str, float] = {}
        if (v := per_token("input_cost_per_token")) is not None:
            rates["input_text"] = round(v * 1e6, 6)
        image_in = per_token("input_cost_per_image_token") or per_token("input_cost_per_token")
        if image_in is not None:
            rates["input_image"] = round(image_in * 1e6, 6)
        if (v := per_token("output_cost_per_image_token")) is not None:
            rates["output_image"] = round(v * 1e6, 6)
        if (v := per_token("output_cost_per_token")) is not None:
            rates["output_text"] = round(v * 1e6, 6)
        if (v := entry.get("output_cost_per_image")) is not None:
            rates["per_image"] = float(v)
        if (v := entry.get("input_cost_per_image")) is not None:
            rates["per_input_image"] = float(v)
        if rates:
            models[key] = rates
    return models


def fetch(url: str) -> dict[str, dict[str, float]]:
    request = urllib.request.Request(url, headers={"User-Agent": "assetforge-pricing/1"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # network, HTTP or JSON errors all mean "no fresh prices"
        raise StorageError(f"Couldn't fetch prices from {url}: {exc}", hint="Check the network or pricing.source_url") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("The price catalog is not a JSON object", url=url)
    models = parse_catalog(data)
    providers = {k.split("/", 1)[0] for k in models}
    if not {"openai", "gemini"} <= providers:
        raise InvalidInputError("The price catalog has no OpenAI/Gemini image models; is the URL right?", url=url)
    return models


# ---------------------------------------------------------------------- snapshots + overrides


def latest_snapshot(lib: Library) -> dict[str, Any] | None:
    row = lib.conn.execute("SELECT * FROM pricing_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {"id": row["id"], "source": row["source"], "fetched_at": row["fetched_at"], "models": loads(row["models"], {})}


def update(lib: Library, url: str | None = None) -> dict[str, Any]:
    """Fetch the catalog now and store a snapshot. Returns the snapshot and what changed."""
    url = url or settings_store.get(lib, "pricing.source_url")
    models = fetch(url)
    previous = latest_snapshot(lib)
    changes = []
    old = previous["models"] if previous else {}
    for key in sorted(set(models) | set(old)):
        before, after = old.get(key, {}), models.get(key, {})
        for field in FIELDS:
            if before.get(field) != after.get(field):
                changes.append({"model": key, "field": field, "before": before.get(field), "after": after.get(field)})
    now = utcnow()
    with db.transaction(lib.conn):
        lib.conn.execute("INSERT INTO pricing_snapshots (source, fetched_at, models) VALUES (?, ?, ?)",
                         (url, now, dumps(models)))  # fmt: skip
        lib.conn.execute(
            "DELETE FROM pricing_snapshots WHERE id NOT IN (SELECT id FROM pricing_snapshots ORDER BY id DESC LIMIT ?)",
            (KEEP_SNAPSHOTS,),
        )
    return {"source": url, "fetched_at": now, "models": len(models), "first": previous is None,
            "changes": changes if previous else []}


def age_days(snapshot: dict[str, Any] | None) -> float | None:
    if not snapshot:
        return None
    fetched = datetime.fromisoformat(snapshot["fetched_at"].replace("Z", "+00:00"))
    return (datetime.now(UTC) - fetched).total_seconds() / 86400


def ensure_fresh(lib: Library) -> dict[str, Any] | None:
    """Refresh prices if they are stale and auto-update is on. Never raises: stale prices beat no generation."""
    if os.environ.get(OFFLINE_ENV) or not settings_store.get(lib, "pricing.auto_update"):
        return None
    age = age_days(latest_snapshot(lib))
    if age is not None and age < settings_store.get(lib, "pricing.max_age_days"):
        return None
    try:
        return update(lib)
    except Exception:
        return None


def set_override(lib: Library, provider: str, model: str, field: str, value: float) -> None:
    if field not in FIELDS:
        raise InvalidInputError(f"Unknown price field {field!r}", allowed=list(FIELDS))
    if value < 0:
        raise InvalidInputError("Prices can't be negative")
    with db.transaction(lib.conn):
        lib.conn.execute(
            "INSERT INTO price_overrides (model, field, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (model, field) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (model_key(provider, model), field, float(value), utcnow()),
        )


def clear_override(lib: Library, provider: str, model: str, field: str | None = None) -> int:
    key = model_key(provider, model)
    with db.transaction(lib.conn):
        if field:
            return lib.conn.execute("DELETE FROM price_overrides WHERE model = ? AND field = ?", (key, field)).rowcount
        return lib.conn.execute("DELETE FROM price_overrides WHERE model = ?", (key,)).rowcount


def rates_for(lib: Library, provider: str, model: str) -> tuple[dict[str, float], str]:
    """Merged rates for a model and a description of where they came from."""
    snapshot = latest_snapshot(lib)
    snap_models = snapshot["models"] if snapshot else {}
    rates: dict[str, float] = {}
    sources: list[str] = []
    for key in reversed(_candidates(provider, model)):  # most specific name wins
        if key in BUILTIN:
            rates.update(BUILTIN[key])
            sources.append("builtin")
    for key in reversed(_candidates(provider, model)):
        if key in snap_models:
            rates.update(snap_models[key])
            sources.append(f"catalog@{snapshot['fetched_at']}")  # type: ignore[index]
    overrides = {r["field"]: r["value"] for key in reversed(_candidates(provider, model))
                 for r in lib.conn.execute("SELECT field, value FROM price_overrides WHERE model = ?", (key,))}  # fmt: skip
    if overrides:
        rates.update(overrides)
        sources.append("override")
    return rates, (sources[-1] if sources else "unknown")


def table(lib: Library) -> dict[str, Any]:
    snapshot = latest_snapshot(lib)
    keys = sorted(set(BUILTIN) | set(snapshot["models"] if snapshot else {}))
    models = {}
    for key in keys:
        provider, model = key.split("/", 1)
        rates, source = rates_for(lib, provider, model)
        models[key] = {"rates": rates, "source": source}
    return {
        "source_url": settings_store.get(lib, "pricing.source_url"),
        "fetched_at": snapshot["fetched_at"] if snapshot else None,
        "age_days": round(age_days(snapshot), 2) if snapshot else None,
        "auto_update": settings_store.get(lib, "pricing.auto_update"),
        "units": "input_*/output_*: USD per 1M tokens; per_image/per_input_image: USD per image",
        "models": models,
    }
